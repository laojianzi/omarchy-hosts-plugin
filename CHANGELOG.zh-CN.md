[English](CHANGELOG.md) | **简体中文**

# 更新日志

Omarchy Hosts 的重要变更都会记录在这里。

本项目遵循[语义化版本](https://semver.org/lang/zh-CN/)。英文 changelog 是规范版本；简体中文文件是同步翻译。

## [1.0.1] - 2026-09-02

### 安全

- 用户状态的加锁、有界读取、私有临时文件创建、原子替换、权限修改、清理与目录同步均改为通过已校验且持续持有的目录描述符相对执行。
- 每个特权 candidate 的文件名绑定其精确 JSON 字节的 SHA-256；root helper 会在不跟随符号链接的情况下重新打开完整 runtime 目录链并校验摘要。
- root 事务状态由“先检查再重新打开”改为一次有界 no-follow 描述符读取。
- QML 增加有界流式输出、逐操作 deadline、终止升级和组件销毁清理。
- CLI 增加并发排空 pipe、独立进程会话、硬 deadline、输出上限和进程组清理。
- root helper 增加独立的授权后 watchdog。

### 变更

- 更新 Arch helper package，并同步加固后的特权边界 helper 源码。
- 扩展文件系统竞态、candidate 替换、不安全 root state、输出上限、超时与后代进程回归测试。
- 记录 Marketplace 安全整改，以及从 `v1.0.0` 升级时必须重装 helper 的要求。

[1.0.1]: https://github.com/laojianzi/omarchy-hosts-plugin/releases/tag/v1.0.1

## [1.0.0] - 2026-09-01

### 新增

- 原生 Omarchy 4 顶栏组件与键盘优先 hosts 管理面板；
- profile 新建、编辑、删除、启停及 staged 配置持久化；
- 确定性 hosts planning engine，支持 IPv4、IPv6、alias、IDN/IDNA 规范化、大小限制及受保护名称检查；
- managed block 渲染，完整保留 Omarchy Hosts marker 外的每个字节，并保持 LF 或 CRLF 换行风格；
- 检测已启用 profile 与 unmanaged `/etc/hosts` 条目之间的阻断性冲突，并对非阻断重复项给出 warning；
- 在任何特权操作前展示精确 unified diff；
- 独立于用户可写插件 checkout 安装的最小化 Polkit Apply/Undo helper；
- 短生命周期 candidate 校验、profile/baseline hash、文件所有权与链接检查、root-owned 事务锁、备份和元数据；
- 基于 `renameat2(RENAME_EXCHANGE)` 的原子提交路径，以及 exchange 前后并发写入恢复测试；
- 具备漂移检测、并绑定原 Apply 用户的单步 Undo；
- 仓库内 CLI、诊断命令和 Omarchy shell `hosts` IPC target；
- 用于特权 helper 的 Arch Linux `PKGBUILD` 及同步的打包源码副本；
- Python、manifest、XML、QML 结构、打包、文档、版本和事务/竞态自动检查；
- 规范英文文档、简体中文镜像，以及每份文档中的中英切换。

### 安全

- 用户可写的 QML 与 Python 永远不会以 root 执行；
- 特权 helper 在隔离 Python 模式下，只导入固定路径、root-owned 且不可写的打包代码；
- 若已审阅的 profile 或 `/etc/hosts` baseline 在提交前发生变化，Apply 会失败关闭；
- 如果上次成功 Apply 后存在外部修改，Undo 会拒绝覆盖这些变更。

[1.0.0]: https://github.com/laojianzi/omarchy-hosts-plugin/releases/tag/v1.0.0
