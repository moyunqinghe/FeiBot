# 自然语言 Skill 安装（工具化）设计

> **日期**：2026-08-20
> **状态**：已评审（brainstorming 逐项确认）
> **涉及目录**：`backend/app/agent/tools/`、`backend/app/agent/engine.py`、对应测试与 `backend/README.md`

---

## 0. 背景与目标

skill 宿主层（`SkillManager` + `/skill` 指令族）已上线并实测可用。但实测发现：管理员在微信里发「安装这个skill：<链接>」这类**自然语言**消息时，模型不知道有 `/skill add`，反而试图用 shell 工具自己 sparse checkout，最终工具轮次耗尽、安装失败。

**目标**：让模型通过工具直接完成 skill 的安装/卸载/列举，自然语言表达即可触发；`/skill` 指令族保留为确定性通道。

**已确认决策（brainstorming）**：

| # | 决策点 | 结论 |
|---|---|---|
| 1 | 触发行为 | **直接安装**，不加确认步骤（管理员即机主，链接即其本人发出） |
| 2 | 能力范围 | `install_skill` / `uninstall_skill` / `list_skills` 三件套；启停仍走 `/skill` 指令（低频） |
| 3 | 实现机制 | **内置工具**（方案 A）：注册进现有工具注册表，白名单门控免费继承；不做 engine 正则启发式 |

## 1. 架构

新增宿主模块 **`app/agent/tools/skill_tools.py`**（仿 `builtin.py` 的"导入即注册"模式），集中三件事：

1. **单例装配**：`skill_manager = SkillManager(SqliteSkillStore(), SKILLS_DIR)` 由 `engine.py` **迁入**本模块；engine 改为 `from app.agent.tools.skill_tools import skill_manager`，装配点唯一。
2. **友好错误映射**：`_IMPORT_ERROR_HINTS` 与 `friendly_import_error(exc)` 由 engine 迁入本模块（函数去掉下划线前缀，成为模块内共用函数）；engine 的 `_skill_add` 与 `install_skill` handler 共用，DRY。
3. **三个工具**：handler 函数 + 模块底部 `register_tool` 三连。

`app/agent/tools/__init__.py` 增加：

```python
from app.agent.tools import skill_tools  # noqa: F401  导入即注册 skill 工具
```

**依赖方向核验**：`skill_tools → app.agent.skills.manager + app.db.skill_store + app.config + skill_importer`，全部向下；`engine → skill_tools` 同层引用；无循环（skill_tools 不 import engine）。基座层（`manager.py` / `loader.py` / `skill_importer/`）**零改动**。

指令面（`_handle_skill` 及其子命令）保留不动：指令与自然语言两条路共享同一个 `skill_manager` 与同一套错误映射。

## 2. 工具定义

| 工具 | 参数 | description（注入提示词） | handler 行为 |
|---|---|---|---|
| `install_skill` | `source`：技能来源（GitHub URL/tree、raw SKILL.md、zip、平台 slug、owner/repo） | 「安装技能。当用户要求安装/添加/导入技能并给出来源或链接时使用此工具；不要用 shell 自行下载技能内容。」 | `skill_manager.install(source)`：成功 → `安装成功：slug=<slug>，文件数=<n>，位置=.feibot/skills/<slug>/`；`SkillImporterError` → `friendly_import_error(exc)`；`SkillManagerError`/`OSError` → `安装失败:<exc>` |
| `uninstall_skill` | `slug`：技能注册名 | 「卸载技能。当用户要求卸载/删除/移除某个已安装的技能时使用此工具。」 | True → `已卸载技能 <slug>`；False → `没有名为「<slug>」的技能`；`SkillManagerError`（非法 slug）或 `OSError`（删目录失败）→ `卸载失败:<exc>` |
| `list_skills` | 无 | 「列出已安装的技能。当用户询问装了哪些技能/技能列表时使用此工具。」 | 空 → `当前没有安装任何技能`；否则逐行 `<slug>（启用，文件完整）— <source>`，启用/停用、文件完整/缺失按行内实况渲染 |

handler 返回**事实文本**，由模型组织成对用户的自然语言回复。安装成功的文件数取自落盘目录递归计数（`sum(1 for p in target.rglob("*") if p.is_file())`）。

工具名 `list_skills` 与基座 `loader.list_skills` 同名但无冲突：注册表以字符串为键，`skill_tools` 不 import loader；刻意保持一致命名以表达同一语义。

## 3. 提示词与引导

- 触发时机与纪律写在**工具 description** 里（模型看见能力的地方），`system.md` 保持极简不动。
- `engine.HELP_TEXT` 增加一行：

```
安装技能也可以直接发:安装这个skill:<链接>(仅管理员)
```

作为 echo 模式（无模型则无工具调用）与模型未识别意图时的兜底引导。

## 4. 安全边界

- **门控继承现有机制**：工具说明只注入白名单会话（`TOOL_ADMIN_CONV_KEYS`），非管理员看不见、调不到这三个工具——与 shell/MCP 工具同机制，handler 内不重复鉴权（与现有全部工具一致）。
- manager 层的 frontmatter name 强校验、路径越界防线、包大小上限照常生效。
- 无确认步骤（已确认决策）：管理员即机主，误触代价与普通消息撤回同级。
- 非管理员发「安装这个skill：…」：无工具可见，消息按普通聊天由模型处理，不产生安装动作。

## 5. 测试（全离线）

| 文件 | 覆盖 |
|---|---|
| `tests/test_skill_tools.py`（新，~10 例） | monkeypatch `skill_tools.skill_manager` 为 fake：安装成功（slug/文件数/位置文案）/导入错误转友好文案/管理错误与 OSError 转「安装失败」；卸载存在/不存在/非法 slug；列表空/非空（含停用与文件缺失渲染）；`TOOL_REGISTRY` 含三工具且 description 含纪律句 |
| `tests/test_engine.py`（改） | 3 个友好文案用例随 `friendly_import_error` 迁移继续通过（改从 `skill_tools` 导入验证）；新增 HELP 提及自然语言安装 1 例；`engine.skill_manager` 绑定仍来自 skill_tools（`/skill` 指令用例不变） |

收尾：全量 `pytest tests -q` 通过；Ruff 对改动文件零发现。

## 6. 边界与非目标

- 不做 enable/disable 工具（启停走 `/skill enable|disable`）。
- 不改基座：`manager.py` / `loader.py` / `skill_importer/` / `pyproject.toml` 零改动。
- 不加安装确认步骤；不做 engine 层正则启发式拦截；不动 `/skill` 指令族行为。
- echo 模式不实现工具调用（无模型即无调用方），仅 HELP 文案引导。

## 7. 验收清单

- [ ] `skill_tools.py` 就位：单例装配 + 友好映射 + 三工具注册；engine 无重复装配
- [ ] 管理员自然语言「安装这个skill：<链接>」→ 模型调用 `install_skill` → 安装完成（提示词与工具描述支撑，离线测试覆盖 handler 与注册）
- [ ] 非管理员看不到工具（既有机制，无需新代码）
- [ ] 友好错误映射单一来源，`/skill add` 与 `install_skill` 共用
- [ ] HELP_TEXT 含自然语言安装引导
- [ ] 全量测试通过；改动文件 Ruff 零发现；基座零差异
