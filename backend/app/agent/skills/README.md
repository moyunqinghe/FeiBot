# skill-importer

技能包导入的通用纯协议层，零业务/数据库/Web 框架耦合。给定一个来源字符串，
解析并归一化出标准技能包（文件集 + SKILL.md + frontmatter 元数据）。

支持：开源平台 slug/URL、GitHub repo/tree/blob/raw/archive、raw SKILL.md、
zip URL、owner/repo 简写。

- 不依赖任何业务代码，不含数据库、不含 fastapi/sqlmodel——所有参数显式传入。
- 依赖：`httpx`、`pydantic`。要求 Python >= 3.11。

## 安装

```bash
pip install /Users/moyunqinghe/个人/学习/feibot/backend/app/agent/skills
pip install -e /Users/moyunqinghe/个人/学习/feibot/backend/app/agent/skills   # 调试
pip install "/Users/moyunqinghe/个人/学习/feibot/backend/app/agent/skills[test]"  # 含测试依赖
```

## 快速上手

```python
from skill_importer import SkillImporter, SkillImporterError

importer = SkillImporter()          # 全默认
pkg = importer.import_skill("weather-pack")                          # 开源平台 slug
pkg = importer.import_skill("owner/repo/tree/main/skills/weather")   # GitHub tree
pkg = importer.import_skill("https://raw.githubusercontent.com/owner/repo/main/SKILL.md")
pkg = importer.import_skill("owner/repo")                            # 自动探测 main/master

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
PACKAGE_INVALID / SKILL_MD_MISSING / HTML_NOT_SKILL / REDIRECT_LOOP / GITHUB_API_ERROR`

## 运行测试

```bash
cd /Users/moyunqinghe/个人/学习/feibot/backend/app/agent/skills
/Users/moyunqinghe/个人/学习/feibot/backend/.venv/bin/python -m pytest tests/
```
全部离线运行：纯函数 + httpx.MockTransport，不发真实网络请求。
