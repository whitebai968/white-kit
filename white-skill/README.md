# white-skill

这次分享的两个 Agent 技能及各自的安装使用材料。技能文件来自本次分享包，保留参考文件和调用元数据。

| 技能 | 适合的场景 | 使用指南 | 下载分享包 |
| --- | --- | --- | --- |
| [Writing for Agents](writing-for-agents/) | 把工作流程和质量要求整理成可复用的 AI 工作说明 | [Word 文档](docs/writing-for-agents-guide.docx) | [ZIP](packages/writing-for-agents.zip) |
| [Domain Modeling](domain-modeling/) | 澄清业务概念、关系和边界，维护项目术语与关键取舍 | [Word 文档](docs/domain-modeling-guide.docx) | [ZIP](packages/domain-modeling.zip) |

## 安装与使用

先安装 [Node.js](https://nodejs.org/en/download)，然后在终端运行需要的命令。以下安装到 Codex 个人技能目录，Mac 和 Windows 使用同一条命令。

Writing for Agents：

```bash
npx skills add https://github.com/whitebai968/white-kit/tree/main/white-skill --skill writing-for-agents -g -a codex
```

Domain Modeling：

```bash
npx skills add https://github.com/whitebai968/white-kit/tree/main/white-skill --skill domain-modeling -g -a codex
```

已有同名技能时先备份并按提示选择版本。安装后打开新的 Codex 会话，输入 `$writing-for-agents` 或 `$domain-modeling`，再写你的具体任务。未识别时重启 Codex。

也可以下载表格中的 ZIP，按包内安装说明手动复制完整技能文件夹。每份 Word 文档都包含三分钟产品介绍稿、GitHub 安装命令、手动安装步骤与可复制的使用示例。

安装器与命令参数说明见 [skills CLI](https://github.com/vercel-labs/skills)。

## 来源与版本

两个技能均源自 [Matt Pocock 的技能仓库](https://github.com/mattpocock/skills)，按 [MIT 许可证](LICENSE) 分发。

- **writing-for-agents** 是分享者修改过的版本：增加专业工作方法的人类可读性要求，并调整技能调用机制说明。
- **domain-modeling** 保留本次分享的版本，使用 CONTEXT.md 保存术语表，上游较新版本改用 GLOSSARY.md。

目录中的源码是可维护版本，分享包提供完整离线副本。版本更新时同步使用指南与包内文件。
