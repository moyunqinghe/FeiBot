# skills 基座层(容器目录)

feibot 的 skill 导入适配与运行时发现层。整个目录零业务/数据库/Web 框架耦合
（有架构守卫测试保证：任何 `app.*` 导入都会让 `tests/test_skills.py` 失败），
可整体抽取为独立基座包供新项目复用。

## 构成

| 模块 | 职责 |
|---|---|
| `skill_importer/` | 导入协议：`import_skill(source)` 把来源解析为 `SkillPackage`（包文档见下文） |
| `loader.py` | 发现原语：`list_skills(skills_dir)` 列出含 `SKILL.md` 的子目录（不执行内容） |
| `manager.py` | 生命周期：`SkillManager(store, skills_dir)` 安装/卸载/启停/列表；持久化经 `SkillStore` 协议注入 |

## SKILL.md 合规要求

frontmatter 必须含 `name`，且 slug 与 name 完全一致：仅允许小写字母/数字/连字符
（`^[a-z0-9]+(?:-[a-z0-9]+)*$`，如 `daily-ai-news`）。缺失或不合规的包会被
`SkillManager.install` 拒绝；同名重装为幂等覆盖（升级路径）。

## 赋能新项目（步骤）

1. 安装（依赖 `httpx`、`pydantic`，Python >= 3.11；抽取打包后改为基座包名）：

   ```bash
   pip install <repo>/backend/app/agent/skills
   ```

2. 实现 `SkillStore` 协议（`upsert/get/list/delete/set_enabled` 五个方法）。
   内存版最小示例：

   ```python
   class MemorySkillStore:
       def __init__(self):
           self.rows = {}

       def upsert(self, slug, source, source_kind, enabled):
           self.rows[slug] = {"slug": slug, "source": source,
                              "source_kind": source_kind, "enabled": enabled}

       def get(self, slug):
           return self.rows.get(slug)

       def list(self):
           return [self.rows[s] for s in sorted(self.rows)]

       def delete(self, slug):
           return self.rows.pop(slug, None) is not None

       def set_enabled(self, slug, enabled):
           row = self.rows.get(slug)
           if row is None:
               return False
           row["enabled"] = enabled
           return True
   ```

   sqlite 宿主可参照 feibot 的 `app/db/skill_store.py`（透传到 store 函数）。

3. 装配管理器并调用：

   ```python
   from pathlib import Path
   from skill_importer import SkillImporter          # 导入协议(可选自定义)
   from <基座包>.manager import SkillManager          # 抽取打包后以实际包名导入

   manager = SkillManager(store=MemorySkillStore(), skills_dir=Path("/data/skills"))
   slug = manager.install("owner/repo/tree/main/skills/daily-ai-news")
   manager.list(); manager.disable(slug); manager.enable(slug); manager.uninstall(slug)
   ```

4. 把 `install/uninstall/enable/disable/list` 接上自己的 API / 渠道指令面；
   权限门控由宿主自行决定。

## 宿主清单（基座不提供，新项目需自备）

- `SkillStore` 持久化实现（表结构、slug 冲突策略归宿主）；
- skills 目录的路径配置（安装会按需创建子目录）；
- 用户交互面（API / 渠道指令）与安全门控；
- 基座**不含**：启动钩子（内容被动，落盘即生效）、agent 提示词注入、
  升级/批量指令、卸载二次确认。

---

# skill-importer

技能包导入的通用纯协议层，零业务/数据库/Web 框架耦合。给定一个来源字符串，
解析并归一化出标准技能包（文件集 + SKILL.md + frontmatter 元数据）。

支持：开源平台 slug/URL、GitHub repo/tree/blob/raw/archive、raw SKILL.md、
zip URL、owner/repo 简写。

- 不依赖任何业务代码，不含数据库、不含 fastapi/sqlmodel——所有参数显式传入。
- 依赖：`httpx`、`pydantic`。要求 Python >= 3.11。

## 安装

以下命令在仓库根目录执行（`<repo>` 为本仓库 checkout 路径）：

```bash
pip install <repo>/backend/app/agent/skills
pip install -e <repo>/backend/app/agent/skills   # 调试
pip install "<repo>/backend/app/agent/skills[test]"  # 含测试依赖
```

## 快速上手

```python
from skill_importer import SkillImporter, SkillImporterError

importer = SkillImporter()          # 全默认
importer = SkillImporter(github_token="ghp_...")  # 可选:认证提升 GitHub API 配额(匿名 60 次/小时)
pkg = importer.import_skill("weather-pack")                          # 开源平台 slug
pkg = importer.import_skill("owner/repo/tree/main/skills/weather")   # GitHub tree
pkg = importer.import_skill("https://raw.githubusercontent.com/owner/repo/main/SKILL.md")
pkg = importer.import_skill("owner/repo")                            # 默认分支自动探测

for file in pkg.files:
    print(file.path, len(file.content))
print(pkg.skill_markdown, pkg.slug_hint, pkg.name_hint)
```

## 包边界（使用方自己决定的事）

- 技能包存哪、表结构、权限模型 —— 宿主的存储设计。
- slug 冲突策略（`-2` 后缀等）、最终 name/slug/homepage —— 由宿主从 hints 决定。
- 下载结果如何落库 / 如何接入 agent 工具系统。

## 安全行为

- 下载目标 SSRF 守卫（best-effort）：仅 http(s)，DNS 解析出任一非公网地址
  （内网/回环/链路本地等）即拒绝；重定向逐跳校验，最多 10 跳。
  注意：守卫与实际连接是两次独立 DNS 解析，无法完全防御 DNS rebinding
  （低 TTL 应答在两次解析间切换公网/内网 IP）。若宿主运行环境有更严格的
  网络边界（VPC egress 策略等），以宿主防护为准。
- zip-slip 防御（路径含 `..` 整包拒绝）、解压总字节闸（默认 `max_file_bytes × max_files`）、
  下载前 Content-Length 预检。

## 错误

统一 `SkillImporterError(message, *, code, cause)`，code 取值：
`SOURCE_INVALID / HTTP_ERROR / CONNECT_FAILED / TIMEOUT / TOO_LARGE /
PACKAGE_INVALID / SKILL_MD_MISSING / HTML_NOT_SKILL / REDIRECT_LOOP / GITHUB_API_ERROR /
RATE_LIMITED`（GitHub API 限流：重试或配置 `github_token` 提升配额）

## 运行测试

在 `<repo>/backend/app/agent/skills` 目录执行（`python` 指向装有依赖的环境，如仓库 venv）：

```bash
python -m pytest tests/ -q
```
全部离线运行：纯函数 + httpx.MockTransport，不发真实网络请求。
