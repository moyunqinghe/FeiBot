"""skill 管理工具:把 skill 宿主管理器暴露为模型可调用的工具。

与 builtin 同款"导入即注册"。工具说明只注入白名单会话(engine 门控),
非白名单会话看不到这些工具,本层不做授权判断。

skill_manager 单例在此装配:engine(/skill 指令面)与本模块的工具共享
同一实例与同一套友好错误映射(friendly_import_error)。
"""

from __future__ import annotations

from skill_importer import (
    ERROR_CONNECT_FAILED,
    ERROR_GITHUB_API_ERROR,
    ERROR_HTML_NOT_SKILL,
    ERROR_HTTP_ERROR,
    ERROR_PACKAGE_INVALID,
    ERROR_REDIRECT_LOOP,
    ERROR_SKILL_MD_MISSING,
    ERROR_SOURCE_INVALID,
    ERROR_TIMEOUT,
    ERROR_TOO_LARGE,
    SkillImporterError,
)

from app.agent.skills.manager import SkillManager, SkillManagerError
from app.agent.tools.registry import ToolSpec, register_tool
from app.config import SKILLS_DIR
from app.db.skill_store import SqliteSkillStore

# skill 宿主管理器:基座 SkillManager + sqlite 持久化 + 运行时目录
skill_manager = SkillManager(SqliteSkillStore(), SKILLS_DIR)

_IMPORT_ERROR_HINTS = {
    ERROR_TIMEOUT: "下载超时,请稍后重试。",
    ERROR_CONNECT_FAILED: "连接来源失败,请检查网络后重试。",
    ERROR_TOO_LARGE: "技能包过大,已拒绝下载。",
    ERROR_SKILL_MD_MISSING: "该目录不是有效的技能包(缺少 SKILL.md)。",
    ERROR_HTML_NOT_SKILL: "链接指向的内容不是技能包。",
    ERROR_REDIRECT_LOOP: "来源重定向次数过多,已拒绝。",
    ERROR_SOURCE_INVALID: "无法识别的来源,请检查链接或 slug 是否正确。",
    ERROR_PACKAGE_INVALID: "技能包内容非法,已拒绝。",
    ERROR_GITHUB_API_ERROR: "GitHub API 返回异常,请稍后重试。",
}


def friendly_import_error(exc: SkillImporterError) -> str:
    """把协议层错误码翻译成对用户有行动指引的文案。"""
    if exc.code == ERROR_HTTP_ERROR:
        return f"来源链接无法访问或不存在,请检查仓库名与路径是否拼写正确({exc})。"
    return _IMPORT_ERROR_HINTS.get(exc.code, f"安装失败:{exc}")


def install_skill(source: str = "") -> str:
    """安装技能:导入来源,落盘 + 落库。"""
    source = source.strip()
    if not source:
        return "缺少技能来源(args 里传 source)。"
    try:
        slug = skill_manager.install(source)
    except SkillImporterError as exc:
        return friendly_import_error(exc)
    except (SkillManagerError, OSError) as exc:
        return f"安装失败:{exc}"
    target = SKILLS_DIR / slug
    file_count = sum(1 for path in target.rglob("*") if path.is_file())
    return f"安装成功:slug={slug},文件数={file_count},位置={target}"


register_tool(ToolSpec(
    name="install_skill",
    description=(
        "安装技能。当用户要求安装/添加/导入技能并给出来源或链接时使用此工具;"
        "不要用 shell 自行下载技能内容"
    ),
    parameters={"source": "技能来源:GitHub URL/tree、raw SKILL.md、zip、平台 slug 或 owner/repo"},
    handler=install_skill,
))
