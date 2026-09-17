# Codex Session Workspace v1.1.0

在 Mac 上为每个本地 Codex session 自动维护一个同名工作区：原始历史、会话说明、用户资料、Agent 工作文件和用户保留成果放在一起。

**最简单的使用方式：在另一台 Mac 上用 Codex 打开本仓库，让它执行 [INSTALL_PROMPT.md](INSTALL_PROMPT.md)。**这份提示词也可单独复制到另一台 Codex，作为完整重建规范；有源码时优先安装已提供的实现。

## 快速入口

- [安装 / 独立重建提示词](INSTALL_PROMPT.md)
- [完整运行说明](src/README.md)
- [需合并的文件管理规则](rules/AGENTS.fragment.md)
- [ZIP 归档、恢复与异常处理](docs/ARCHIVING.md)
- [版本说明与验证边界](docs/RELEASE.md)

```text
项目/
├─ 项目导航.md
├─ session A 名称/
│  ├─ 会话说明.md
│  ├─ 历史记录.jsonl
│  ├─ 资料/                  用户控制
│  ├─ 工作/                  Agent 生成，按工作项分组
│  │  └─ 索引.md
│  └─ 成果/                  用户选择保留
├─ session B 名称/
└─ 已归档/
   └─ 已归档 session 名称.zip
```

每 2 秒检查一次。活跃 session 改名时移动整个工作区；归档时校验打包为 `已归档/<session名称>.zip`，成功后移除原文件夹；取消归档时恢复完整工作区；归档后改名时更新 ZIP 名称及包内顶层目录。分叉独立保存历史。源会话确认删除 15 秒后移除历史副本，已有用户资料和产物仍保留。同步进程不调用模型。

聊天窗口的附件不会自动复制进 `资料/`；只有用户明确要求保存时才代为放入。处理后生成的内容放进 `工作/`。

归档保留普通文件、空目录、符号链接、权限和修改时间；不包含 macOS 扩展属性、资源叉或 ACL。归档前应停止其他程序向该目录写入；发现变化时保留副本并报告错误。

## 环境

- macOS，Python 3.9 或更新版本；仅使用标准库，不需要 pip 依赖或 API key。
- 本机 Codex 会话数据可读取。当前适配 `state_*.sqlite` 中的 threads 数据与原始 JSONL；必须在目标机先做只读检查。
- 默认 profile 是 `~/.codex`；支持 `CODEX_HOME`，安装时可显式传 `--codex-home`。
- 本版不提供 Windows 或 Linux 安装器。产品版本 `1.1.0` 与内部 `layout_version = 3` 属于不同编号，后者是既有数据布局标记。

## 手动执行

在仓库根目录运行：

```sh
python3 verify_package.py
python3 run_tests.py
python3 apply_rules.py
```

前两步分别校验文件和运行隔离测试。第三步只预览全局规则差异；根据安装提示词检查现有规则后，再传 `--apply --backup-dir <实际备份目录>`。已有同名文件管理章节时，显式使用 `--replace-file-rules`；不会覆盖其他顶层章节。

```sh
python3 src/install.py install --seed <当前电脑的真实session-ID> --backup-dir <实际备份目录>
python3 src/install.py status
python3 src/install.py stop
python3 src/install.py start
```

上面的尖括号必须替换为真实参数。安装器保存本机 Python 绝对路径、用户目录和 session ID，不使用发行包制作电脑的配置。

安装后的定位入口不受 session 改名影响：

```sh
python3 "$HOME/Library/Application Support/CodexSessionMirror/session_mirror.py" --locate
```

## 上传 GitHub

把**本发行包目录的内容**作为仓库内容上传，不要把包含个人会话与备份的整个工作项目一起上传。发行包内已经有 `.gitignore`，默认只允许版本清单中的发布文件，通过 Git 忽略运行时新增的 session 工作区、历史、数据库和日志。新增源码、许可证或 CI 文件时，应主动更新白名单并重新生成版本校验清单。

源码和提示词是重现方案所需的东西；真实聊天记录、个人 AGENTS.md 全文、系统状态和旧备份不属于发行内容。`rules/AGENTS.fragment.md` 仅包含可复用的目录管理规则。

`SHA256SUMS` 用于确认本地发行文件是否改变，不是数字签名。修改源码后原有校验会失败，这是预期行为，应在重新测试后制作新版本清单。
