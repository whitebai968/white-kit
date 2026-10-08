# Skills 共用安装指南

适用于本仓库所有采用独立目录和 SKILL.md 的技能。先选择你实际使用的 Agent，再按其安装入口操作。四种工具可以共用技能文件与本指南，但各自的目录、权限和刷新机制不同。

## 最少操作的安装入口

对能联网、能操作本地文件且支持 Skills 的 Agent，可以直接发送：

```text
请阅读 https://raw.githubusercontent.com/whitebai968/white-kit/main/white-skill/INSTALL_PROMPT.md ，将 writing-for-agents 安装到我当前 Agent 的个人技能目录，按指南检查并告诉我怎样调用。
```

把 writing-for-agents 换成需要的技能名即可。这个入口让 Agent 帮你执行安装，仍需遵循宿主的权限确认和原生导入要求；本指南不承诺任意 Agent 都能点一次链接直接安装，最少操作取决于宿主的实际入口。不会操作本机文件的聊天工具不能靠这段话获得本地 Skills 支持。

## 按工具选择推荐方法

| Agent | 推荐安装入口 | 默认个人目录 | 安装后确认 |
| --- | --- | --- | --- |
| WorkBuddy | 下载单技能纯 ZIP，在“技能 → 添加技能 → 上传技能”导入 | 普通版默认 ~/.workbuddy/skills；专享版或自定义配置可能不同 | 原生导入自动配置；在已安装技能中确认。手动复制后新建任务，仍不显示再重开应用 |
| Codex | 用下方 skills CLI，或复制完整技能目录 | ~/.agents/skills | 在技能选择器中确认；必要时重新打开会话或重启 |
| Claude Code | 用下方 skills CLI，或复制完整技能目录 | ~/.claude/skills | /skills 确认；目录首次创建或未被发现时 /reload-skills，必要时新开会话 |
| Hermes Agent | 首选 Hermes 原生安装命令 | 默认 ~/.hermes/skills；HERMES_HOME 和 profile 会改变位置 | hermes skills list；新会话或 /reset 后调用 |

“~”表示当前用户主目录，Windows 对应当前用户目录。表中列的是默认本地个人目录，实际以所用版本、profile 和自定义配置为准。你看到的其他兼容目录不能自动推广到所有版本。公司管理或云会话有自己的加载规则，按宿主要求处理。

### WorkBuddy

从本仓库 packages 目录下载对应的 `<技能名>-skill.zip`，在 WorkBuddy 技能页点击“添加技能 → 上传技能”，选择 ZIP，按原生检查完成导入。选择纯技能包，不必安装 Node.js，也不必手工寻找隐藏目录。

### Codex 和 Claude Code

已安装 Node.js（含 npx）的用户可运行：

```bash
npx skills add https://github.com/whitebai968/white-kit/tree/main/white-skill --skill writing-for-agents -g -a codex
npx skills add https://github.com/whitebai968/white-kit/tree/main/white-skill --skill writing-for-agents -g -a claude-code
```

把技能名换成需要的名称。也可以去掉 `-a` 参数，在安装器中选择它支持的工具。`-g` 表示个人级安装。已有同名技能时先备份，再选择是否替换。当前 Vercel 安装器没有公开的 `workbuddy` 目标，不能直接套用 `-a workbuddy`。

### Hermes Agent

已有 Hermes 的用户可直接运行，无需另外安装 Node.js：

```bash
hermes skills install whitebai968/white-kit/white-skill/writing-for-agents
hermes skills list
```

同样把末尾技能名替换成需要的技能。原生安装会走安全扫描和确认；按当前 profile 安装，避免把目录猜成固定的 ~/.hermes。Vercel CLI 的 Hermes 目标名是 `hermes-agent`，它可作为备选，原生命令更适合管理 Hermes profile。

## 共用的手动安装办法

1. 下载对应的 `<技能名>-skill.zip`，解压后找到完整技能文件夹。
2. 确认当前工具支持本地 Skills，确认实际的个人技能目录；已有同名目录先备份。
3. 把整个技能文件夹放进该目录，保留配套文件。安装后应是 `个人技能目录/技能名/SKILL.md`，不要多套一层分享包目录。
4. 按表格刷新或新开会话，确认工具能发现技能，再执行一个小任务。

安装包结构示例：

```text
writing-for-agents/
  SKILL.md
  SKILL-MECHANICS.md
  agents/openai.yaml
  LICENSE.txt
```

保留所有参考文件。`agents/openai.yaml` 是 Codex 的可选元数据，其他工具按自己的支持情况读取或忽略。技能正文如果含宿主专有命令，仍需按目标工具核对；目录格式通用不等于每个正文行为都完全通用。

项目级安装只影响对应项目：Codex 常用 `.agents/skills`，Claude Code 用 `.claude/skills`，WorkBuddy 普通版使用 `.workbuddy/skills`，Hermes 支持 `.hermes/skills` 或 `.agents/skills`，其中 Hermes 项目技能需按其原生 trust 流程授权。本指南默认个人安装，避免首次分享时混淆两个级别。

## 怎样算安装完成

- 文件完整：SKILL.md 与引用的配套文件存在，技能名与目录对应。
- 宿主发现：技能在当前工具的技能列表或选择器中可见；复制完成与成功加载分开确认。
- 能够调用：先选择技能或在请求中明确技能名，执行一个小样例。各工具的 `$`、`/` 等语法不同，按宿主选择器或文档使用。

如果原生导入或安全策略拒绝安装，报告原因并遵循宿主规则，不用目录复制绕过拒绝。若暂时只有文件复制完成，说明需要怎样刷新，不把它说成已经加载。

## 资料来源与核实范围

查证于 2026-10-08。Codex、Claude Code、Hermes 的目录和命令依据官方文档；WorkBuddy 原生上传入口依据官方用户指南，普通版默认目录另外由本机官方 5.5.1 发布程序只读核实。特殊配置可能改变默认路径。

- [Codex 官方 Skills 文档](https://learn.chatgpt.com/docs/build-skills)
- [Claude Code 官方 Skills 文档](https://code.claude.com/docs/en/skills)
- [WorkBuddy 官方技能管理指南](https://www.codebuddy.cn/docs/workbuddy/From-Beginner-to-Expert-Guide/Function-Description/Skills-Market)
- [Hermes 官方技能系统](https://hermes-agent.nousresearch.com/docs/user-guide/features/skills)
- [Vercel skills CLI](https://github.com/vercel-labs/skills)

仓库验收区分包结构和文件安装检查与真正的宿主加载。WorkBuddy/Hermes 原生导入、Windows 和自定义 profile 未在本轮实际运行，因此不把所有平台标记为实测通过。
