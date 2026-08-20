# Skill 运行时目录调整设计

## 背景

`skill-importer` 是独立仓库维护的纯协议基座，职责是把外部来源解析、校验并归一化为 `SkillPackage`。feibot 是它的宿主之一，负责决定导入结果如何安装和加载。

当前 `backend/skills/` 位于源码树中，只包含一个未接入业务的 `echo` 示例；`app.config.SKILLS_DIR` 和 `app.agent.skills.loader` 将其视作已安装 Skill 的发现目录。这混淆了源码和可变运行时数据。

## 决策

已安装 Skill 归入现有运行时数据根目录：

```text
backend/.feibot/
├── feibot.db
└── skills/
    └── <skill-id>/
        ├── SKILL.md
        ├── scripts/
        └── assets/
```

`SKILLS_DIR` 从 `BASE_DIR / "skills"` 改为 `DATA_DIR / "skills"`。`backend/skills/` 及其 `echo` 示例删除。

## 边界

- `skill-importer` 保持不变，仍只返回标准化的 `SkillPackage`。
- `app.agent.skills.loader` 保持发现器职责，只扫描 `SKILLS_DIR` 下含 `SKILL.md` 的直接子目录。
- 本次不实现安装服务、卸载、升级、数据库元数据或管理 API。
- 三个独立协议基座的源码布局和依赖方式本次不调整。
- `.feibot/skills/` 是运行时目录，继续由现有 `.gitignore` 中的 `.feibot/` 规则排除。

## 文档调整

删除 README 中将 `backend/skills/` 描述为项目根级 Skill 内容目录的旧说明，并在 `.feibot/` 说明中明确已安装 Skill 位于 `.feibot/skills/`。这样文档不会引导开发者重新创建已经废弃的源码级目录。

## 验证

新增 loader 单元测试，使用临时目录和配置替换验证：

1. Skill 数据目录不存在时返回空列表。
2. 仅发现直接子目录中包含 `SKILL.md` 的目录。
3. 发现结果按名称排序。

运行 backend 测试集和 Ruff 检查，确认路径迁移没有引入回归。
