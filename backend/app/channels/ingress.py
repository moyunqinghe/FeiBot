"""微信渠道 ingress:扫码登录 + 长轮询收消息 + 回复,循环管理。

逻辑从 mvp_wechat_bot.py 迁移,行为保持一致:
- 无 token 时扫码登录(打印二维码链接),token 加密后存 sqlite kv
- 长轮询收消息,回复经 agent 层(engine)处理后分片发送
- 断线退避重连(2s 起步,上限 60s)、-14 会话过期自动重新扫码
- event_id 幂等去重(deque 容量 200)
- 游标整批处理完才推进并持久化;Ctrl+C 优雅退出

登录态与游标存 db 的 kv 表(key 前缀 wechat.),不再用独立状态文件。
"""

from __future__ import annotations

import logging
import time
from collections import deque
from collections.abc import Callable

import httpx
from wechat_ilink import (
    SESSION_EXPIRED_ERRCODE,
    WeChatApiError,
    WeChatClient,
    decrypt_secret,
    derive_fernet_key,
    encrypt_secret,
    normalize_wechat_message,
    sanitize_wechat_baseurl,
    split_wechat_text,
)

from app.config import CHANNEL_SECRET, WECHAT_BASE_URL
from app.db import store

logger = logging.getLogger(__name__)

FERNET_KEY = derive_fernet_key(CHANNEL_SECRET)

MAX_BACKOFF_SECONDS = 60.0  # 重连退避上限
DEDUP_CAPACITY = 200  # 最近已处理 event_id 的缓存容量(防崩溃重拉导致重复回复)

# kv 表里的状态 key
KV_BASE_URL = "wechat.base_url"
KV_BOT_ID = "wechat.ilink_bot_id"
KV_TOKEN_ENC = "wechat.bot_token_enc"
KV_CURSOR = "wechat.cursor"


def _new_client(base_url: str, bot_token: str = "") -> WeChatClient:
    """构造渠道客户端:显式 transport,永远直连、不吃环境变量代理。

    微信 iLink 是国内服务,请求又带着 bot_token 凭证:流量被 Clash 等代理
    劫持时 TLS 握手会间歇性失败,凭证也不应流经第三方节点。
    """
    return WeChatClient(base_url, bot_token, transport=httpx.HTTPTransport())


def get_bot_token() -> str:
    """从 kv 中解密 bot_token;没有则返回空串。"""
    enc = store.get_kv(KV_TOKEN_ENC)
    return decrypt_secret(enc, FERNET_KEY) if enc else ""


def scan_login() -> dict:
    """走完整扫码流程,返回含新 token 的状态 dict。"""
    client = _new_client(WECHAT_BASE_URL)
    qr = client.get_bot_qrcode()
    print("\n请用微信扫码登录(在浏览器打开下面链接查看二维码):")
    print(qr["qrcode_img_content"])

    while True:
        status = client.get_qrcode_status(qr["qrcode"])
        code = status.get("status")
        if code == "confirmed":
            break
        if code in {"expired", "cancelled"}:
            raise RuntimeError(f"二维码已失效(status={code}),请重新运行脚本")
        time.sleep(2)

    bot_token = status["bot_token"]
    # 服务端可能下发区域化 baseurl,必须校验后才可使用(防凭证被引到恶意域名)
    base_url = sanitize_wechat_baseurl(status.get("baseurl", ""), default=WECHAT_BASE_URL)
    client.close()  # 登录用的无 token 客户端到此为止,后续由调用方新建带 token 的客户端
    print("扫码确认,登录成功")
    return {
        "base_url": base_url,
        "ilink_bot_id": status.get("ilink_bot_id", ""),
        "bot_token_enc": encrypt_secret(bot_token, FERNET_KEY),
        "cursor": "",
    }


def save_state(state: dict) -> None:
    """把登录态与游标写入 kv 表。"""
    store.set_kv(KV_BASE_URL, state.get("base_url", WECHAT_BASE_URL))
    store.set_kv(KV_BOT_ID, state.get("ilink_bot_id", ""))
    store.set_kv(KV_TOKEN_ENC, state.get("bot_token_enc", ""))
    store.set_kv(KV_CURSOR, state.get("cursor", ""))


def load_state() -> dict:
    """从 kv 表读出登录态与游标。"""
    return {
        "base_url": store.get_kv(KV_BASE_URL, WECHAT_BASE_URL),
        "ilink_bot_id": store.get_kv(KV_BOT_ID),
        "bot_token_enc": store.get_kv(KV_TOKEN_ENC),
        "cursor": store.get_kv(KV_CURSOR),
    }


def reply(client: WeChatClient, msg, text: str) -> None:
    """按 2000 字上限分片回复;client_id 留空由客户端生成,保证服务端幂等。"""
    for chunk in split_wechat_text(text):
        client.send_message(msg.from_user_id, msg.context_token, chunk)


def run_wechat_ingress(on_message: Callable[[str, str], str]) -> None:
    """微信接入主循环(阻塞)。

    on_message(conv_key, text) -> 回复文本;由 agent 层注入,
    本层只负责协议交互、去重、游标与重连管理。
    """
    state = load_state()
    if not get_bot_token():
        state = scan_login()
        save_state(state)

    client = _new_client(state["base_url"], get_bot_token())
    seen_event_ids: deque[str] = deque(maxlen=DEDUP_CAPACITY)
    backoff = 2.0

    logger.info("开始长轮询(base_url=%s),Ctrl+C 退出", state["base_url"])
    try:
        while True:
            try:
                resp = client.get_updates(state.get("cursor", ""))
            except (WeChatApiError, OSError, httpx.HTTPError) as exc:
                # 网络/协议错误(含 TLS 失败、超时、代理抽风):指数退避后重试。
                # 注意 httpx 的传输错误不是 OSError,必须显式捕获,否则循环直接崩
                logger.warning("轮询异常:%s,%.0fs 后重试", exc, backoff)
                time.sleep(backoff)
                backoff = min(backoff * 2, MAX_BACKOFF_SECONDS)
                continue
            backoff = 2.0  # 成功一轮后重置退避

            errcode = resp.get("errcode") or resp.get("ret") or 0
            if errcode == SESSION_EXPIRED_ERRCODE:
                # -14:会话过期,清空 token 重新扫码
                logger.warning("会话已过期(-14),需要重新扫码")
                state = scan_login()
                save_state(state)
                client.close()
                client = _new_client(state["base_url"], get_bot_token())
                continue

            for raw in resp.get("msgs") or []:
                msg = normalize_wechat_message(
                    raw, ilink_bot_id=state.get("ilink_bot_id", "")
                )
                if msg is None or msg.event_id in seen_event_ids:
                    continue
                seen_event_ids.append(msg.event_id)

                text = msg.text or "[非文本消息]"
                logger.info("收到(%s): %s", msg.from_user_id, text)
                try:
                    answer = on_message(msg.from_user_id, text)
                except Exception:  # noqa: BLE001 — agent 层任何异常都不能杀死轮询循环
                    logger.exception("agent 处理消息失败(conv=%s)", msg.from_user_id)
                    try:
                        reply(client, msg, "助理内部出了点问题,请稍后再试。")
                    except Exception:  # noqa: BLE001
                        logger.exception("错误提示发送也失败")
                else:
                    try:
                        reply(client, msg, answer)
                    except Exception:  # noqa: BLE001
                        logger.exception("回复发送失败(conv=%s)", msg.from_user_id)
                    else:
                        # 发送成功后才记"回复":日志反映用户实际收到的内容
                        logger.info("回复(%s): %s", msg.from_user_id, answer)

            # 整批处理完才推进游标:批内崩溃会在下轮重拉,由 seen_event_ids 去重
            state["cursor"] = str(resp.get("get_updates_buf") or state.get("cursor", ""))
            save_state(state)
    except KeyboardInterrupt:
        print("\n退出")
    finally:
        client.close()
