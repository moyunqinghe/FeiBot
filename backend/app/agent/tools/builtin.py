"""内置工具:当前时间、工作目录、目录列表、shell 命令。

可用性由 engine 按白名单(config.TOOL_ADMIN_CONV_KEYS)门控,
非白名单会话根本看不到工具说明,本层不做授权判断。

shell 的底线防护:30s 超时、输出截断、stdin 关闭、工作目录固定为 backend/。
它是白名单专属的高危能力——bot 进程有什么权限,它就能做什么。
"""

from __future__ import annotations

import subprocess
from datetime import datetime
from pathlib import Path

from app.agent.tools.registry import ToolSpec, register_tool
from app.config import BASE_DIR

SHELL_TIMEOUT_SECONDS = 30.0
OUTPUT_LIMIT = 4000  # 回填给模型的工具输出上限,防超长上下文
LIST_DIR_LIMIT = 200  # list_dir 单次最多列出的条目数


def _truncate(text: str) -> str:
    if len(text) <= OUTPUT_LIMIT:
        return text
    return text[:OUTPUT_LIMIT] + f"\n……(已截断,共 {len(text)} 字符)"


def current_time() -> str:
    """当前本地日期时间(含星期与时区名)。"""
    now = datetime.now().astimezone()
    weekday = "一二三四五六日"[now.weekday()]
    return f"{now:%Y-%m-%d %H:%M:%S} 星期{weekday}({now.tzname()})"


def pwd() -> str:
    """bot 的工作目录(固定为 backend/,与 shell 的 cwd 一致)。"""
    return str(BASE_DIR)


def list_dir(path: str = "") -> str:
    """列目录:相对路径相对 backend/ 解析,缺省列 backend/ 本身。"""
    target = Path(path).expanduser() if path else BASE_DIR
    if not target.is_absolute():
        target = BASE_DIR / target
    if not target.exists():
        return f"路径不存在:{target}"
    if not target.is_dir():
        return f"不是目录:{target}"
    entries = sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name))
    lines = [f"{e.name}/" if e.is_dir() else e.name for e in entries[:LIST_DIR_LIMIT]]
    result = "\n".join(lines) or "(空目录)"
    if len(entries) > LIST_DIR_LIMIT:
        result += f"\n……(仅显示前 {LIST_DIR_LIMIT} 项,共 {len(entries)} 项)"
    return result


def shell(command: str = "") -> str:
    """执行一条 shell 命令,返回 stdout/stderr 与退出码。"""
    if not command.strip():
        return "缺少要执行的命令(args 里传 command)。"
    try:
        proc = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=SHELL_TIMEOUT_SECONDS,
            cwd=str(BASE_DIR),
            stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired:
        return f"命令超时(超过 {SHELL_TIMEOUT_SECONDS:.0f}s):{command}"
    parts = []
    if proc.stdout.strip():
        parts.append(proc.stdout.strip())
    if proc.stderr.strip():
        parts.append(f"[stderr]\n{proc.stderr.strip()}")
    result = "\n".join(parts) or "(无输出)"
    if proc.returncode != 0:
        result += f"\n[exit code: {proc.returncode}]"
    return _truncate(result)


register_tool(ToolSpec(
    name="current_time",
    description="获取当前日期和时间",
    parameters={},
    handler=current_time,
))
register_tool(ToolSpec(
    name="pwd",
    description="获取 bot 的当前工作目录",
    parameters={},
    handler=pwd,
))
register_tool(ToolSpec(
    name="list_dir",
    description="列出目录内容(目录名带 / 后缀)",
    parameters={"path": "目录路径,可省略,缺省为工作目录"},
    handler=list_dir,
))
register_tool(ToolSpec(
    name="shell",
    description="执行一条 shell 命令并返回输出",
    parameters={"command": "要执行的命令"},
    handler=shell,
))
