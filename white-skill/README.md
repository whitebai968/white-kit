# white-skill

这次分享的两个 Agent 技能。各技能说明自己的用途与示例，共用 [安装指南](INSTALL.md) 和 [给 Agent 的安装提示词](INSTALL_PROMPT.md)。

| 技能 | 用途 | 使用指南 | 纯技能安装包 | 完整分享包 |
| --- | --- | --- | --- | --- |
| [Writing for Agents](writing-for-agents/) | 把知识、经验和规则写成 Agent 能清楚理解与使用的文档 | [Word](docs/writing-for-agents-guide.docx) | [纯 ZIP](packages/writing-for-agents-skill.zip) | [分享 ZIP](packages/writing-for-agents.zip) |
| [Domain Modeling](domain-modeling/) | 澄清业务概念、关系和边界，维护项目术语与关键取舍 | [Word](docs/domain-modeling-guide.docx) | [纯 ZIP](packages/domain-modeling-skill.zip) | [分享 ZIP](packages/domain-modeling.zip) |

## 最少操作的开始方式

在支持本地 Skills、能联网并操作文件的 Agent 中发送：

```text
请阅读 https://raw.githubusercontent.com/whitebai968/white-kit/main/white-skill/INSTALL_PROMPT.md ，将 writing-for-agents 安装到我当前 Agent 的个人技能目录，按指南检查并告诉我怎样调用。
```

将技能名换成 domain-modeling 即可安装另一个。安装需要遵循工具自己的权限确认、原生导入与刷新要求。WorkBuddy 首选上传纯技能 ZIP，Hermes 首选原生 CLI，Codex/Claude Code 可用通用安装器；具体入口见 [共用指南](INSTALL.md)。

## 使用示例

Writing for Agents：

```text
请使用 writing-for-agents 优化这份项目说明，保留事实与约束，整理信息层级、适用条件和参考资料入口，让 Agent 清楚怎样执行以及何时算完成。
```

Domain Modeling：

```text
请使用 domain-modeling 梳理这个项目的业务概念，和我澄清含义、关系及边界，用具体场景检查定义，并把确认结果更新到术语表。
```

## 来源与版本

两个技能源自 [Matt Pocock 的技能仓库](https://github.com/mattpocock/skills)，按 [MIT 许可证](LICENSE) 分发。

- writing-for-agents 保留分享者修改版：专业方法同时照顾人类可读性，调整了调用机制说明；核心仍是为 Agent 编写和优化文档。
- domain-modeling 保留此次分享版，使用 CONTEXT.md 保存术语，上游较新版本改用 GLOSSARY.md。

纯技能包可用于单技能导入；完整分享包还含 Word 与共用说明的当次副本。未来新技能沿用本指南，不必各自维护目录与安装步骤。
