"""入口:装配并启动渠道 ingress(阻塞循环,UX 与 MVP 一致)。

运行:
    cd backend && .venv/bin/python -m app.main
"""

from __future__ import annotations

from app.agent.engine import handle_message
from app.channels.ingress import run_wechat_ingress


def main() -> None:
    """装配 agent 回调并启动微信长轮询;Ctrl+C 退出。"""
    run_wechat_ingress(handle_message)


if __name__ == "__main__":
    main()
