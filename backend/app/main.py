"""入口:装配并启动渠道 ingress(阻塞循环,UX 与 MVP 一致)。

运行:
    cd backend && .venv/bin/python -m app.main
"""

from __future__ import annotations

import logging

from app.agent.engine import handle_message
from app.agent.tools.mcp_plugins import load_enabled_plugins
from app.channels.ingress import run_wechat_ingress


def main() -> None:
    """装配 agent 回调并启动微信长轮询;Ctrl+C 退出。"""
    # 统一日志出口:收到/回复、提炼失败、发送失败等必须可见,不能静默
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    # httpx 每个请求刷一行 "HTTP Request: ... 200 OK"(长轮询每十几秒两条),
    # 纯噪音,压到 WARNING;openai._base_client 的重试 INFO 有诊断意义,保留
    for noisy in ("httpx", "httpx2"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    # 启动时重载启用的 MCP 插件
    load_enabled_plugins()
    run_wechat_ingress(handle_message)


if __name__ == "__main__":
    main()
