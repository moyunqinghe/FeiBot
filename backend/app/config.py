"""全局配置:路径、密钥、渠道常量。

密钥沿用 MVP 的方式:从环境变量读取,缺省用固定开发值;
生产环境必须改成环境变量注入的强随机值。
"""

from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent  # backend/
DATA_DIR = BASE_DIR / ".feibot"  # 运行时数据目录(sqlite 库等),不进 git
DATA_DIR.mkdir(parents=True, exist_ok=True)


def _load_dotenv(path: Path | None = None) -> None:
    """把 backend/.env 的 KEY=VALUE 载入环境变量(标准库实现,不引新依赖)。

    真实环境变量优先:已存在的键不覆盖;.env 本身在 .gitignore 里,不进仓库。
    """
    path = path if path is not None else BASE_DIR / ".env"
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip().removeprefix("export ").strip()
        value = value.strip().strip("'\"")
        if key and key not in os.environ:
            os.environ[key] = value


_load_dotenv()

SKILLS_DIR = DATA_DIR / "skills"  # 已安装 skill 运行时目录,不进 git

# GitHub 认证 token(可选):匿名 API 仅 60 次/小时,配置后提升到认证配额,
# 避免 skill 导入撞限流。来源:真实环境变量优先,其次 backend/.env;不进仓库。
GITHUB_TOKEN = os.environ.get("FEIBOT_GITHUB_TOKEN", "")

# 渠道 token 落地加密的密钥
CHANNEL_SECRET = os.environ.get("FEIBOT_CHANNEL_SECRET", "feibot-dev-secret")

# 微信 ilink 默认接入地址(扫码确认后服务端可能下发区域化 baseurl)
WECHAT_BASE_URL = "https://ilinkai.weixin.qq.com"

# 工具白名单:只有这些会话 conv key 能触发工具(逗号分隔),空串 = 全员禁用。
# 任何能给 bot 发消息的人都进得了对话,工具(尤其 shell)必须只对主人开放。
TOOL_ADMIN_CONV_KEYS = frozenset(
    k.strip()
    for k in os.environ.get(
        "FEIBOT_TOOL_ADMINS", "o9cq806k_eukyFS6hHA5FzseIugA@im.wechat"
    ).split(",")
    if k.strip()
)


def is_tool_admin(conv_key: str) -> bool:
    """该会话是否在工具白名单里。"""
    return conv_key in TOOL_ADMIN_CONV_KEYS
