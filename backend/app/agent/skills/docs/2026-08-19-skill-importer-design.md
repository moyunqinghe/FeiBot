# Skill Importer 纯协议层基座 — 设计文档

> **日期**：2026-08-19
> **状态**：已评审（边界/API/回流/测试/交付物 均已确认）
> **涉及仓库**：
> - 承载：`/Users/moyunqinghe/个人/学习/feibot/backend/app/agent/skills/`（复用已有预留展位目录，与 `app/agent/mcp/` 同构）
> - 回流：`/Users/moyunqinghe/工作/项目/StaffDeck/backend/app/api/general_skills.py`（dogfood）
> - 消费：新项目（FastAPI + SQLModel，路径待定，`pip install <路径>`）

---

## 0. 背景与目标

StaffDeck 的"配置 Skill 工具"能力（`/enterprise/general-skills`）中，**从远程源导入技能**是一块纯协议逻辑：给定一个来源字符串（开源平台 slug/URL、GitHub repo/tree/blob/raw/archive、raw SKILL.md、zip URL、owner/repo 简写），拉取并归一化成标准技能包（文件集 + SKILL.md + frontmatter 元数据）。该逻辑目前深嵌在 `backend/app/api/general_skills.py`（约 1580 行）里，与租户/权限/资源绑定/运行时等宿主概念耦合。

**目标**：把这块逻辑萃取成独立的**纯协议层基座包** `skill-importer`（零业务/DB/Web 框架耦合），供任何干净项目 `pip install <路径>` 复用；同时**回流 StaffDeck** 让 `general_skills.py` 改用它，以现有测试证明萃取零丢失。

**参考范式**：`feibot/backend/app/agent/mcp/`（mcp-discovery）、`feibot/backend/app/channels/wechat/`（wechat-ilink）、StaffDeck `backend/app/llm/`——均为"独立目录 + pyproject.toml + 包 + README + 离线测试 + 统一错误码"的纯协议层基座。

---

## 1. 范围与边界

### 1.1 包内（skill-importer 负责）

- 来源解析：`import_skill(source)` 接受任意支持的来源字符串，返回标准技能包
- 全部源类型：开源平台（clawhub/skillhub，域名可配）slug 与 URL、GitHub repo/tree/blob/raw/archive、raw SKILL.md、zip URL、owner/repo 简写、HTML 页面→真实技能包跳转
- 下载（httpx）、zip 解包 + 子树 + 路径清洗、SKILL.md 定位与校验、frontmatter 解析、slug 化、名称/主页提示抽取
- 约束：包大小 / 单文件大小 / 文件数 / 超时 / 重定向跳数上限
- 统一错误：`SkillImporterError(code, cause, message)`
- 可配置：平台域名、平台下载端点、GitHub 域名、user-agent、超时、各上限

### 1.2 包外（宿主负责，不进包）

- 持久化模型（GeneralSkill 表）、租户/权限/AgentResourceBinding 分支
- API 路由（import / CRUD / publish / archive / run）与错误文案翻译
- 运行器（runner / RuntimeEnv / capabilities）
- base64 上传接口的**解码与大小校验**（仅复用包的 `normalize_skill_files()` 做归一化）
- 认证、多租户、前端

---

## 2. 目录与交付物

```
feibot/backend/app/agent/skills/            # 容器目录（已存在，作为独立包容器，占位 loader 保留不动）
├── pyproject.toml                          # name = "skill-importer"
├── README.md                               # 安装 / 快速上手 / 边界 / 错误码表
├── skill_importer/
│   ├── __init__.py                         # 公开 API
│   ├── model.py                            # SkillFile / SkillPackage
│   ├── resolver.py                         # import_skill / normalize_skill_files / 源类型分发
│   ├── github.py                           # GitHub 各形态处理 + main/master 回退
│   ├── ziputil.py                          # zip 解包 / 子树 / 路径清洗 / SKILL.md 定位
│   ├── metadata.py                         # frontmatter 解析 / slug 化 / 名称·主页提示
│   └── errors.py                           # SkillImporterError + 错误码常量
└── tests/                                  # 全部离线（httpx.MockTransport）
```

- `app/agent/skills/` 已有的 `__init__.py` / `loader.py`（feibot 预留展位，全仓零调用）：**保留不动**。pyproject 的 `packages.find` 仅 `include=["skill_importer*"]`，两者互不干扰。
- 布局与 `app/agent/mcp/`（容器 `mcp` → 包 `mcp_discovery`）完全同构：容器目录短名 `skills` → 包 `skill_importer`。

---

## 3. 包模块职责

| 模块 | 职责 |
|---|---|
| `model.py` | `SkillFile`(pydantic)：`path/content/size/mime_type`（与宿主 `GeneralSkillFile` 字段同构，宿主可直连 `model_validate`）；`SkillPackage`(frozen dataclass)：`files` / `skill_markdown` / `metadata` / `name_hint` / `slug_hint` / `homepage_hint` / `source_kind` |
| `resolver.py` | 唯一入口 `SkillImporter.import_skill(source)`；`normalize_skill_files(files, markdown=None)`；源类型分发（见 §6）；`_load_remote` / 重定向环守卫 |
| `github.py` | raw URL / blob / tree / archive / repo 根目录（API 目录遍历 → 主分支回退 → zip 兜底） |
| `ziputil.py` | `_files_from_zip(data, subtree)`、路径清洗（去 `__MACOSX/.git/node_modules/.venv/dist/build`、拒绝 `..`、根目录剥离）、SKILL.md 定位 |
| `metadata.py` | frontmatter 解析（`---` 块、`key: value`、`[a, b]` 列表）、`_metadata_text` 候选键提取、`_slugify`、平台 slug/主页提示 |
| `errors.py` | `SkillImporterError(code, cause, message)` 与错误码常量（见 §7） |

---

## 4. 数据模型

```python
# model.py
class SkillFile(BaseModel):            # pydantic，字段与宿主 GeneralSkillFile 同构
    path: str
    content: str
    size: int | None = None
    mime_type: str | None = None

@dataclass(frozen=True)
class SkillPackage:
    files: tuple[SkillFile, ...]       # 已归一化：SKILL.md + 其余文件，路径相对根
    skill_markdown: str                # SKILL.md 正文（非空）
    metadata: dict[str, object]        # frontmatter 解析结果（无 frontmatter 时为空 dict）
    name_hint: str | None
    slug_hint: str | None
    homepage_hint: str | None
    source_kind: str                   # "platform" | "github" | "url" | "shorthand"
```

---

## 5. 入口与配置

```python
from skill_importer import SkillImporter, SkillImporterError, SkillPackage

importer = SkillImporter(
    timeout_seconds=120.0,
    user_agent="skill-importer/1.0",
    github_hosts={"github.com", "www.github.com", "raw.githubusercontent.com"},
    platform_hosts={"clawhub.ai", "www.clawhub.ai", "skillhub.ai", "www.skillhub.ai"},
    platform_download_endpoint="https://wry-manatee-359.convex.site/api/v1/download",
    max_package_bytes=96 * 1024 * 1024,
    max_file_bytes=2 * 1024 * 1024,
    max_files=240,
    max_indirections=5,
    transport=None,                    # httpx.MockTransport 注入点 → 离线测试
)
pkg: SkillPackage = importer.import_skill(source)
```

- 全部参数可选、有默认（默认值 = StaffDeck 现行为，见 §8）。
- `transport=None` 时内部构造 `httpx.Client`（重定向跟随行为与现 urllib 一致），`transport` 供离线测试注入 `httpx.MockTransport`。
- 另暴露无状态辅助 `normalize_skill_files(files, markdown=None) -> list[SkillFile]`，供宿主上传路径复用归一化逻辑。

---

## 6. 源类型分发决策树

（逐条翻译自 `general_skills.py` 现逻辑，行为等价）

```
import_skill(source)
├─ 判定平台 slug：http(s) 且 host ∈ platform_hosts → path 取 slug（parts[1] 或 parts[0]）
│   或 无 "/" 的裸串 → 视为 slug（校验 [A-Za-z0-9][A-Za-z0-9_.-]{1,127}，去 .zip/.md 后缀）
│   ├─ 命中 → 下载 {platform_download_endpoint}?slug=… → 解析（失败时若原输入是 URL 则直接尝试该 URL）
│   └─ 未命中 → 继续
├─ http(s) URL → _load_remote(url)
├─ owner/repo 简写（正则 [A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:/.+)?）→ _load_remote(https://github.com/{简写})
└─ 其它 → SOURCE_INVALID

_load_remote(url, visited)
├─ 无 scheme/netloc → SOURCE_INVALID
├─ visited 已含该 URL → REDIRECT_LOOP
├─ visited 长度 ≥ max_indirections → REDIRECT_LOOP
├─ host ∈ github_hosts → _load_github(parsed)
├─ 下载：
│   ├─ .zip 后缀 或 content-type 含 zip → zip 解包
│   ├─ HTML（content-type 或 <!doctype html/<html 开头）→ 提取候选技能包链接（raw GitHub / 平台下载端点 /
│   │   github tree/blob/zip）→ 跟随（≤max_indirections）；无候选 → HTML_NOT_SKILL
│   ├─ 类 markdown（.md 后缀 / /skill 结尾 / text/markdown·text/plain）→ 单 SKILL.md 文件
│   └─ 其它 → SOURCE_INVALID

_load_github(parsed)
├─ raw.githubusercontent.com：需 owner/repo/branch/path（否则 SOURCE_INVALID）→ 下载 → 单文件
├─ github.com：
│   ├─ owner/repo（去 .git）
│   │   ├─ /archive/… → zip
│   │   ├─ /blob/ 或 /raw/ + branch + path → raw.githubusercontent.com → 单文件
│   │   ├─ /tree/ + branch + subtree → API 目录遍历
│   │   └─ 其它（仓库根 / owner/repo/path）→ 对 ["main", "master"] 逐个 API 目录遍历 → 全失败 zip 兜底
```

GitHub API 目录遍历（含递归）与 zip 兜底的具体约束同现逻辑：每文件 ≤ `max_file_bytes`、总数 ≤ `max_files`、跳过 `__MACOSX/.git/node_modules/.venv/dist/build`；子树命中时仅取该子树并剥离前缀；结果必须含 SKILL.md 否则 `SKILL_MD_MISSING`。

---

## 7. 错误码全表 + StaffDeck 文案映射

统一异常 `SkillImporterError(code: str, cause: BaseException | None, message: str)`（对齐 `McpDiscoveryError`）。

| code | 含义 | StaffDeck 现状文案（回流时逐字保留） |
|---|---|---|
| `SOURCE_INVALID` | 来源无法识别 / 非 URL / 非技能内容 | "开源平台来源必须是开源平台 slug、GitHub URL、raw SKILL.md URL、zip URL 或 owner/repo 路径"；"Remote skill source must be a valid URL"；"GitHub source must include owner and repository"；"Raw GitHub source must include owner, repo, branch and path"；"Remote source must be a zip package, GitHub skill directory, or raw Markdown skill file" |
| `HTTP_ERROR` | 下载 HTTP 状态异常 | "Download failed with HTTP {code}" |
| `CONNECT_FAILED` | 连接失败 / 无法下载 | "Download failed: {reason}"；"Unable to download GitHub skill package: …" |
| `TIMEOUT` | 下载/读超时 | "Download timed out" |
| `TOO_LARGE` | 包或单文件超限 | "General skill package is too large" |
| `PACKAGE_INVALID` | zip 损坏 / 路径非法 / 空 SKILL.md | "General skill file path: {path}"（非法路径）；"General skill SKILL.md cannot be empty" |
| `SKILL_MD_MISSING` | 未找到 SKILL.md | "Package does not contain SKILL.md"；"GitHub directory does not contain SKILL.md"；"General skill folder must contain SKILL.md" |
| `HTML_NOT_SKILL` | 平台页面无可下载技能包 | "开源平台页面没有暴露可下载的技能包或 GitHub 目录。HTML 页面不会被当作 SKILL.md 导入。" |
| `REDIRECT_LOOP` | 自引用 / 跳数超限 | "Remote skill source redirects to itself"；"Remote skill source contains too many indirections" |
| `GITHUB_API_ERROR` | GitHub API 返回非法 JSON | "Remote GitHub API returned invalid JSON" |

> 宿主映射方式：`general_skills.py` 的 `_load_skill_package(source)` 捕获 `SkillImporterError`，按 `code` 返回对应的 HTTP 400 中文 detail（表内文案）；未知 code 回退通用 "导入技能失败"。base64 上传侧错误（"content is not valid base64" / 空包 / 超限等）保留在宿主 API 层。

---

## 8. 约束常量（默认值）

| 常量 | 值 | 现出处 |
|---|---|---|
| `max_package_bytes` | 96 MiB | MAX_CLAWHUB_PACKAGE_BYTES |
| `max_file_bytes` | 2 MiB | MAX_CLAWHUB_FILE_BYTES |
| `max_files` | 240 | MAX_CLAWHUB_FILES |
| `timeout_seconds` | 120 | REMOTE_SKILL_DOWNLOAD_TIMEOUT_SECONDS |
| `max_indirections` | 5 | `len(visited) >= 5` |
| github_hosts | `{github.com, www.github.com, raw.githubusercontent.com}` | GITHUB_HOSTS + RAW_GITHUB_HOST |
| platform_hosts | `{clawhub.ai, www.clawhub.ai, skillhub.ai, www.skillhub.ai}` | CLAWHUB_HOSTS ∪ SKILLHUB_HOSTS |
| platform_download_endpoint | `https://wry-manatee-359.convex.site/api/v1/download` | CLAWHUB_DOWNLOAD_ENDPOINT |

---

## 9. StaffDeck 回流改造清单（dogfood）

1. `backend/pyproject.toml` 增加依赖：`skill-importer @ file:///Users/moyunqinghe/个人/学习/feibot/backend/app/agent/skills`（开发期 `pip install -e` 该路径）。
2. `backend/app/api/general_skills.py`：
   - 新增 `_load_skill_package(source) -> SkillPackage` 适配函数（捕获 `SkillImporterError` → 按 §7 表映射中文 detail）。
   - 删除 `_load_clawhub_source` / `_load_github_skill_source` / `_download_*` / `_files_from_zip` / `_parse_skill_metadata` / `_parse_metadata_value` / `_metadata_text` / `_slugify` / `_source_name` / `_clawhub_slug_from_source` / `_valid_clawhub_slug` / `_clawhub_homepage_from_source` / `_looks_like_*` / `_skip_package_path` / `_decode_text` / `_guess_mime_type` / `_clean_package_path` / `_find_skill_file` / `_skill_markdown_from_files` 等由包取代的函数。
   - `import-skillhub` / `import-clawhub` 改用 `_load_skill_package(request.source)`。
   - `import-package`（base64）保留宿主解码与大小校验，但 `_normalize_skill_files` 改调包的 `normalize_skill_files()`；`_skill_files_or_markdown` / `_skill_directories*` / `_unique_slug` / CRUD 与权限逻辑**不动**。
   - 保留 `GeneralSkillFile` 导出结构（包 `SkillFile` 字段同构，宿主 `model_validate` 直通）。
3. 回归验证：跑 `backend/tests/test_general_skills.py` 及全量后端测试，行为与文案不变。

## 10. 测试策略

### 10.1 包自带测试（`skills/tests/`，全离线，httpx.MockTransport）
直接迁移 StaffDeck 现有用例行为 + 补边界：
- zip 根目录剥离、单 md 视为 SKILL.md、缺 SKILL.md、超大包/超多文件
- GitHub tree（API 目录递归）、blob/raw 单文件、archive zip 兜底、owner/repo 简写、main/master 回退
- raw SKILL.md、zip URL、HTML 页面→真实包跳转、纯 HTML 拒绝（HTML_NOT_SKILL）
- 平台 slug（裸串 / URL）、平台下载端点命中与回退原 URL、slug 校验非法
- 重定向环（自引用 / 超跳数）、非 URL 源（SOURCE_INVALID）、超时 / HTTP 错误 / 非法 JSON

### 10.2 StaffDeck 测试迁移
- 现 `test_general_skills.py` 远程导入用例（monkeypatch `_download_url` / `_download_json`）改为注入 `httpx.MockTransport`：fixture 构造 `SkillImporter(transport=...)` 注入 `general_skills.py` 使用的 resolver。
- 所有断言保留（slug、文件路径列表、frontmatter 元数据、`-2` 去重后缀、published/archived 状态）。

---

## 11. 新项目接入示例（写入 README）

```python
from skill_importer import SkillImporter, SkillImporterError

importer = SkillImporter()          # 全默认

# 开源平台 slug
pkg = importer.import_skill("weather-pack")
# GitHub tree 目录
pkg = importer.import_skill("owner/repo/tree/main/skills/weather")
# raw SKILL.md
pkg = importer.import_skill("https://raw.githubusercontent.com/owner/repo/main/SKILL.md")
# owner/repo 简写（自动探测 main/master）
pkg = importer.import_skill("owner/repo")

for f in pkg.files:
    print(f.path, len(f.content))
print(pkg.skill_markdown)
```

---

## 12. 开放问题与后续

- **新项目路径/仓库名**：待用户提供；接入即 `pip install <feibot skills 路径>`，无需其它改动。
- **feibot 自身接 skill**：后续可按需删除占位 loader，或让 `app.agent.skills` 直接调用 `skill_importer`（与 mcp 的"宿主即首个消费者"一致）。
- **platform 域名/端点为默认值**：若新项目对接不同平台，仅传 `platform_hosts` / `platform_download_endpoint` 即可，无需改包。
- **错误文案语言**：包内 `message` 用英文（纯协议层），中文文案只存在于 StaffDeck 宿主映射层。
