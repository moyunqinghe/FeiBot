# Skill 安装/卸载宿主层设计

> **日期**：2026-08-20
> **状态**：已评审（brainstorming 逐项确认）
> **涉及目录**：`backend/app/agent/skills/`（基座层）、`backend/app/db/`、`backend/app/agent/slash.py`、`backend/app/agent/engine.py`、两处 README

---

## 0. 背景与目标

- `skill-importer` 纯协议基座已就位并实测通过（GitHub tree 来源导入 Daily-AI-News 成功，6 个文件归一化为 `SkillPackage`）。
- MCP 域已打通「协议基座（`mcp_discovery`）→ 宿主管理（`tools/mcp_plugins.py`）→ 渠道指令（`/mcp` 族，管理员门控）」闭环；skill 域只有协议基座与文件系统发现原语（`loader.list_skills()`，零调用方），缺宿主生命周期管理与渠道指令面。
- 已安装 skill 的运行时目录已迁移至 `DATA_DIR / "skills"`（`.feibot/skills/`，不进 git）。

**目标**：为 skill 域补齐宿主层——导入 → 校验 → 落盘 → 元数据入库 → 卸载/启停，并接入 `/skill` 渠道指令族；同时**确保 `app/agent/skills/` 整个目录保持基座级纯净、可随时抽取为独立基座包**。

**参考范式**：`app/agent/mcp/` + `app/agent/tools/mcp_plugins.py` + `engine._handle_mcp` 的既有分层。

## 1. 已确认决策（brainstorming 结论）

| # | 决策点 | 结论 |
|---|---|---|
| 1 | 元数据存储 | sqlite `installed_skills` 表 + 文件落盘 `SKILLS_DIR/<slug>/` |
| 2 | 注册名（slug） | **强不变式 `slug == SKILL.md frontmatter 的 name`**；name 必须匹配 `^[a-z0-9]+(?:-[a-z0-9]+)*$`（小写字母/数字/连字符）；缺失或不合规 → 拒绝安装，无显式指定名字的逃生口；同名重装 = 幂等覆盖（即升级路径） |
| 3 | 启用/停用 | 包含（`enabled` 标志；文件保留、发现中隐藏） |
| 4 | 宿主层位置 | 方案 A：`app/agent/skills/manager.py`（领域目录内），与 loader、skill_importer 同目录 |
| 5 | 基座纯净性 | `app/agent/skills/` 下所有 `.py` **零 `app.*` 导入**，依赖全部显式注入；`loader.py` 一并去除对 `app.config` 的耦合（目录改参数传入） |
| 6 | README | `app/agent/skills/README.md` 必须注明：可作独立基座、如何赋能新项目、宿主还缺什么（清单） |

## 2. 架构

```
基座层  app/agent/skills/                 ← 零 app.* 导入,整体可抽取
├── skill_importer/                       # 导入协议(勿改):import_skill(source) -> SkillPackage
├── loader.py                             # 发现原语:list_skills(skills_dir) -> [slug]
├── manager.py                            # 新增:SkillStore 协议 + SkillManager 生命周期
└── __init__.py

宿主层  feibot 接线
├── app/db/store.py                       # installed_skills 表 + CRUD 函数
├── app/db/skill_store.py                 # 新增:SqliteSkillStore 适配器(实现 SkillStore)
├── app/agent/slash.py                    # /skill 解析(kind="skill")
├── app/agent/engine.py                   # 装配 skill_manager + _handle_skill 指令面
└── backend/README.md                     # 文档更新
```

**依赖方向核验**（仓库规则 `channels → agent → llm / db`）：

- `engine`（agent 层）→ skills 基座、`db.skill_store`、`config` ✓
- `db.skill_store` → `db.store`（db 层内部，不反向引用 agent）✓
- 基座层 → 仅标准库 + `skill_importer`（包内互引）✓

## 3. 数据模型（app/db/store.py）

`_SCHEMA` 追加：

```sql
CREATE TABLE IF NOT EXISTS installed_skills (
    slug        TEXT PRIMARY KEY,      -- == frontmatter name(强不变式,不另存 name)
    source      TEXT NOT NULL,         -- 安装来源原文(将来升级的前提)
    source_kind TEXT NOT NULL,         -- platform / github / url(来自 SkillPackage.source_kind)
    enabled     INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0, 1)),
    added_at    REAL NOT NULL,
    updated_at  REAL NOT NULL
);
```

CRUD 函数（命名镜像 mcp_plugins 一族）：

| 函数 | 签名 |
|---|---|
| `upsert_skill` | `(slug: str, source: str, source_kind: str, enabled: int) -> None` |
| `get_skill` | `(slug: str) -> dict \| None` |
| `list_skills` | `() -> list[dict]`（按 slug 排序；与 loader 的同名函数不同模块，不冲突） |
| `delete_skill` | `(slug: str) -> bool` |
| `set_skill_enabled` | `(slug: str, enabled: int) -> bool` |

行 dict 键：`slug / source / source_kind / enabled / added_at / updated_at`。

## 4. 基座层：manager.py（新增）

### 4.1 SkillStore 协议

```python
class SkillStore(Protocol):
    """持久化接口,由宿主实现(sqlite/内存/其他均可)。"""
    def upsert(self, slug: str, source: str, source_kind: str, enabled: int) -> None: ...
    def get(self, slug: str) -> dict | None: ...
    def list(self) -> list[dict]: ...
    def delete(self, slug: str) -> bool: ...
    def set_enabled(self, slug: str, enabled: int) -> bool: ...
```

### 4.2 SkillManager

```python
class SkillManager:
    def __init__(self, store: SkillStore, skills_dir: Path,
                 importer: SkillImporter | None = None): ...
    def install(self, source: str) -> str: ...      # 返回 slug
    def uninstall(self, slug: str) -> bool: ...
    def enable(self, slug: str) -> bool: ...
    def disable(self, slug: str) -> bool: ...
    def list(self) -> list[dict]: ...
```

**install(source) 流程**：

1. `importer.import_skill(source)`（缺省实例化 `SkillImporter()`；`SkillImporterError` 原样上抛）；
2. **校验**：取 `pkg.metadata["name"]`（注意：用 frontmatter 原值，**不用** `name_hint`——后者含来源回退，会绕过"SKILL.md 必须有合规 name"的契约）。缺失 → `SkillManagerError("SKILL.md 缺少 frontmatter name…")`；不匹配 `^[a-z0-9]+(?:-[a-z0-9]+)*$` → `SkillManagerError` 附规范说明；frontmatter 中的 `slug`/`id` 字段一律忽略（强不变式下无意义）；
3. **落盘**：`target = skills_dir / slug`；已存在则整目录 `rmtree`（避免旧版本残留文件）；逐文件写入（`mkdir parents`）。**路径防线**：每个 `(target / f.path).resolve()` 必须仍在 `target.resolve()` 之内，否则整包拒绝（基座已做 zip-slip 清洗，此为宿主双保险）；
4. **落库**：`store.upsert(slug, source, pkg.source_kind, 1)`；
5. 返回 `slug`。

**顺序与失败语义**：先文件后数据库（与 MCP「先注册后落库」同构）。文件阶段失败 → 无库记录，安装整体失败；落库失败 → 残留目录由下次同名重装或 uninstall 兜底。

**uninstall(slug)**：slug 形式校验 → `store.delete(slug)` + `rmtree(skills_dir/slug)`；两者宽容执行（任一存在即返回 True），不联网，与 MCP uninstall 一致。

**enable/disable(slug)**：行不存在返回 False；只翻 `enabled` 标志，文件不动（skill 是被动内容，无需 MCP 式重新 discover）。

**list()**：`store.list()` 每行附文件系统实况：`files_ok`（目录存在）、`file_count`（递归文件数）。返回键：`slug / source / source_kind / enabled / added_at / updated_at / files_ok / file_count`。

**错误类型**：`SkillManagerError(Exception)`，消息即面向调用方的说明（无 code 字段，与 `PluginError` 一致）。

**loader.py 改造**：签名变为 `list_skills(skills_dir: Path) -> list[str]`，删除 `from app.config import SKILLS_DIR`；逻辑（直接子目录、SKILL.md 门控、排序）不变。当前零生产调用方，现有测试同步改为传参式。

## 5. 宿主接线

### 5.1 app/db/skill_store.py（新增，~20 行）

```python
class SqliteSkillStore:
    """SkillStore 的 sqlite 实现:透传 app.db.store 的 CRUD。"""
    def upsert(self, slug, source, source_kind, enabled): store.upsert_skill(...)
    # get / list / delete / set_enabled 同理透传
```

### 5.2 slash.py

`/skill` → `ChannelCommand(kind="skill", query=…)`；kind 注释更新。**不加中文别名**（与 `/mcp` 保持一致）。

### 5.3 engine.py

- 装配：`skill_manager = SkillManager(SqliteSkillStore(), config.SKILLS_DIR)`（模块级；engine 是当前唯一消费方，无启动钩子需求）。
- `_handle_skill(conv_key, query)`：`is_tool_admin` 门控（非管理员拒绝，同 MCP）；子命令：

| 指令 | 行为 |
|---|---|
| `/skill` 或 `/skill list` | 列出：slug、启用标记、来源、files_ok |
| `/skill add <来源>` | 安装；成功回「已装入技能 <slug>」；`SkillImporterError`/`SkillManagerError` 转友好文案 |
| `/skill remove <slug>` | 卸载；无二次确认（与 MCP 一致） |
| `/skill enable\|disable <slug>` | 启停 |
| 其他 | `SKILL_HELP` |

- `HELP_TEXT` 增加 `/skill` 一行。
- 异常处理模式与 `_handle_mcp` 一致：管理层/协议层异常转文案，不漏到渠道。

## 6. 可抽取性保证

1. **架构守卫测试**：扫描 `app/agent/skills/` 下全部 `.py`（经 `pathlib` 遍历，排除 `__pycache__`），断言不含 `from app.` / `import app.` 字样的导入行。任何后续宿主耦合会直接红。
2. **本次不实际执行抽取打包**：`pyproject.toml` 的 `packages.find` 维持仅 `skill_importer*` 不动；将来抽取时扩为包含 `loader`、`manager` 并更名包身份，属后续任务。

## 7. README 交付（用户显式要求）

### 7.1 app/agent/skills/README.md 改写

顶部新增「skills 基座层」总述，原有 skill-importer 包文档**完整保留**（pyproject 的 `readme = "README.md"` 指向它）。新增内容必须包含：

1. **构成**：三块职责（skill_importer 导入协议 / loader 发现原语 / manager 生命周期 + SkillStore 协议），零业务耦合、依赖注入；
2. **如何赋能新项目**（逐步）：
   - `pip install <路径>`（依赖 httpx / pydantic，Python ≥ 3.11）；
   - 实现 `SkillStore` 协议（给出 sqlite 与内存版两个最小示例骨架）；
   - `SkillManager(store=…, skills_dir=Path(…))` 装配；
   - 把 `install/uninstall/enable/disable/list` 接上自己的 API / 指令面，权限门控由宿主负责；
3. **宿主清单（基座不提供、新项目需自备）**：
   - 持久化实现（`SkillStore`）；
   - skills 目录的路径配置与创建策略；
   - 用户交互面（API/渠道指令）与安全门控；
   - 明确说明基座**不含**：启动钩子（内容被动、落盘即生效）、agent 提示词注入、升级/批量指令、卸载二次确认。

### 7.2 backend/README.md 更新

- 目录结构条目措辞微调（skills 域现含基座 + 宿主接线）；
- 新增「Skill 管理（装/卸技能包）」小节（仿 MCP 小节写法）：`/skill` 指令族、管理员限定、落盘位置 `.feibot/skills/<slug>/`、元数据在 `installed_skills` 表。

## 8. 边界与非目标

- `skill_importer/` 源码与 `pyproject.toml` 零改动；`mcp_discovery/`、`app/agent/mcp/`、`tools/mcp_plugins.py` 不动。
- 不做 `/skill upgrade`（同名重装即覆盖）、不做批量导入（skill 无标准信封）、不做卸载确认。
- **不把 skill 内容注入 agent 提示词**——安装后只是"在架"；消费接入是后续任务。届时消费方以 `skill_manager.list()`（DB + enabled + files_ok）为权威，`loader` 仅作文件系统原语。
- 不实际执行基座抽取打包。

## 9. 错误处理汇总

| 场景 | 行为 | 渠道文案 |
|---|---|---|
| 来源解析/下载失败 | `SkillImporterError` 上抛 | 「安装失败：<code/message>」 |
| frontmatter name 缺失 | `SkillManagerError` | 说明需合规 name |
| name 不合 slug 形式 | `SkillManagerError` | 附规范：小写字母/数字/连字符 |
| 包内路径越界 | `SkillManagerError`，整包拒绝、不落库 | 「安装包路径非法」 |
| slug 不存在（remove/enable/disable） | 返回 False | 「没有名为「<slug>」的技能,/skill 查看」 |
| 非管理员调用 | engine 拦截 | 「Skill 管理仅限管理员使用。」 |
| 文件系统错误（OSError） | 上抛,engine 转文案 | 「安装失败：<exc>」（不落库） |

## 10. 测试（全离线）

| 文件 | 覆盖 |
|---|---|
| `tests/test_skill_manager.py`（新） | 纯注入式：内存 fake store + tmp `skills_dir` + 构造注入假 importer。安装落盘/校验拒绝（缺失、中文、大写）/同名覆盖/卸载/启停/列表 files_ok/路径防线/~20 例，**零 monkeypatch** |
| `tests/test_skill_store.py`（新） | sqlite CRUD（走 conftest 的 `DB_PATH` 临时夹具）+ `SqliteSkillStore` 透传 |
| `tests/test_skills.py`（改） | loader 用例改传参式；保留 config 不变量；**新增架构守卫测试**（基座零 `app.*` 导入） |
| `tests/test_slash.py`（增） | `/skill` 各形态解析 |
| `tests/test_engine.py`（增） | `_handle_skill` 门控与子命令（monkeypatch `engine.skill_manager` 为 fake） |
| 端到端串联例 | 假导入 → 落盘 tmp → `loader.list_skills(dir)` 发现（衔接迁移成果） |

收尾：全量 `pytest tests -q` 通过；Ruff 对**改动文件**零发现（全仓存量发现不属本任务，与 2026-08-20 迁移收尾口径一致）。

## 11. 验收清单

- [ ] `app/agent/skills/` 下无任何 `app.*` 导入（守卫测试绿）
- [ ] `loader.list_skills(skills_dir)` 传参式，不再引用 `app.config`
- [ ] `SkillManager` 全依赖注入，无模块级单例
- [ ] 安装：校验 → 清目录 → 落盘 → 落库；不合规 name 被拒；同名幂等覆盖
- [ ] 卸载：删库 + 删目录，宽容幂等；启停只翻标志
- [ ] `/skill` 指令族管理员门控，异常全转文案
- [ ] `installed_skills` 表 + CRUD + 适配器就位
- [ ] 两处 README 按 §7 更新（含赋能步骤与宿主清单）
- [ ] 全量测试通过；skill_importer 与 pyproject 零差异
