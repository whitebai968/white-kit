# white-kit

按个人习惯打造的 Codex 工作环境扩展与协作工具。

Personal extensions and tools for working with Codex.

这里存放围绕日常 Agent 工作设计的小项目：让上下文更容易接续、文件更容易管理、重复操作更少。每个项目独立提供源码、使用说明和安装入口。

## 项目

| 项目 | 用途 | 当前版本 | 平台 |
| --- | --- | --- | --- |
| [Codex Session Workspace](projects/codex-session-workspace/) | 按 session 同步历史与文件，完整分叉复制、ZIP 归档恢复和异常导航 | v1.2.0 | macOS · Python 3.9+ |

v1.2.0 新增：新分叉一次性复制父工作区，之后独立维护；同步失败原因直接显示在项目导航，供 Agent 接手时检查。详见 [分叉说明](projects/codex-session-workspace/docs/FORKING.md)。

## 在 Codex 中使用

用 Codex 打开本仓库，发送：

> 请进入 projects/codex-session-workspace，读取 INSTALL_PROMPT.md，按要求完成安装和验收。

也可以只把该项目的 [完整安装提示词](projects/codex-session-workspace/INSTALL_PROMPT.md) 交给另一台 Mac 的 Codex。已有源码时优先使用仓库提供的版本。

## 仓库组织

```text
white-kit/
├─ README.md
└─ projects/
   └─ codex-session-workspace/
      ├─ README.md
      ├─ INSTALL_PROMPT.md
      ├─ src/
      ├─ rules/
      ├─ tests/
      └─ docs/
```

每个小项目保留自己的版本、依赖和验证方法。新增项目放入 projects 下，并补充本页入口及 Git 文件白名单。

## 会话同步项目的边界

- 会话历史保存为原始文件副本，不代表第三方 Agent 能原生导入 Codex 会话或模型内部状态。
- 聊天附件按任务读取使用，不自动复制进资料目录。
- ZIP 归档保留普通文件、目录、符号链接、权限及修改时间；其他元数据和异常处理见 [归档说明](projects/codex-session-workspace/docs/ARCHIVING.md)。
- 项目安装与真实使用验收遵循各自说明；源码发布不等于已经升级本机后台服务。

本仓库只存放可复用源码、规则、说明和测试。个人会话历史、用户配置、凭证、运行日志和私人备份不属于仓库内容。
