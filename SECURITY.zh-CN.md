[English](SECURITY.md) | **简体中文**

# 安全策略

Omarchy Hosts 需要从未沙箱化的桌面插件环境修改 root-owned 的本地域名解析文件，因此安全报告是本项目的一等工程工作。

英文安全策略是规范版本；简体中文文件是同步翻译。

## 支持版本

| 版本 | 是否支持 |
| --- | --- |
| `1.0.x` | 是 |
| `< 1.0.0` | 否 |

安全修复会从最新受支持版本线发布。报告可能导致 patch release、文档更新、打包/策略调整，或暂时建议禁用 Apply/Undo。

## 报告漏洞

对于尚未修复的漏洞、特权边界绕过，或可意外覆盖 `/etc/hosts` 的可复现方法，**不要**创建公开 issue。

推荐渠道：

1. 仓库启用时，使用 GitHub 私密的 **Security → Report a vulnerability** 流程；
2. 否则发送邮件至 `laojianzi1994@gmail.com`，主题填写 `Omarchy Hosts security report`。

请尽量提供：

- 受影响版本或 commit SHA；
- Omarchy、kernel、Python、Polkit 与文件系统信息；
- 问题位于 QML、用户 CLI/service、candidate 处理、Polkit policy、helper 安装、Apply、Undo 还是打包；
- 使用非敏感测试 hostname 的完整前置条件和复现步骤；
- 预期行为与实际行为；
- 影响范围，以及是否涉及 root-owned 数据或并发外部写入；
- 日志；必要时请移除用户名、hostname、IP 清单、token 和敏感路径；
- 可用时提供建议修复或测试用例。

项目会在实际可行的情况下尽快确认报告。请为复现、设计不破坏事务安全的修复、准备协调 patch 以及在 Omarchy 4 上验证结果预留时间，再进行公开披露。

## 安全边界

项目划分以下信任域：

- **Omarchy shell 插件：** 用户可写 QML，以当前桌面用户身份运行；
- **用户后端：** 仓库内 Python，负责 profile、preview、持久化和 candidate 创建；
- **Polkit：** 仅授权固定 helper executable 以及明确的 Apply 或 Undo action；
- **特权 helper：** 安装到 root-owned、不可写路径，并使用隔离 Python 设置执行；
- **系统状态：** `/etc/hosts`、root-owned 备份、事务锁和事务元数据。

特权 helper 不得从插件 checkout 导入代码，也不得执行用户提供的 shell command。它只接受范围明确的 operation 和经过校验的 candidate path 或事务引用。

完整模型以规范英文 [Architecture](docs/ARCHITECTURE.md) 和 [Threat model](docs/THREAT-MODEL.md) 为准。

## 关键控制

实现必须保持以下控制：

- candidate 是短生命周期、单 hard link、由调用用户拥有且 group/other 不可访问的普通文件；
- candidate path 被限制在预期的 `/run/user/$UID` 子树；
- profile 数据在提权前完成规范化、大小限制和 hash；
- helper 会重新解析、重新校验并重新渲染请求状态；
- `/etc/hosts` 必须是符合预期的 root-owned 普通文件，并具备安全链接属性；
- managed marker 严格解析，损坏布局失败关闭；
- 持有事务锁时重新检查已审阅 baseline hash；
- 最终提交使用原子 exchange 语义，并检测 exchange 前后的并发写入者；
- 只有 root-owned 备份和元数据完成写入后，事务才报告成功；
- Undo 绑定原 Apply 用户，并拒绝覆盖后续漂移；
- 不可信 process/journal 输出在 QML 中按纯文本渲染；
- 用户控制值通过参数数组或标准输入传递，而不是插值进 shell command string。

任何有意放宽上述控制的 patch 都必须附带书面 threat analysis 和等效补偿控制。

## 范围之外与剩余风险

以下情况本身不构成项目漏洞：

- 已经以同一桌面用户身份运行的恶意插件读取或修改该用户的 Omarchy Hosts profile；
- root 管理员直接编辑 `/etc/hosts`、包文件、Polkit policy、备份或 helper 代码；
- 本地 hosts 文件语义之外的 DNS 或应用行为；
- 磁盘空间耗尽或文件系统不支持所需原子操作导致的可用性损失；
- 本地用户账号已被攻陷后，通过社会工程诱导管理员认证一个明确展示的恶意 diff。

当这些条件与项目缺陷结合并跨越已记录信任边界时，仍欢迎提交报告。

## 披露与致谢

请在修复或缓解措施可用前协调公开披露。经报告者许可，release notes 会使用其偏好的姓名或 handle 致谢；匿名报告同样会被尊重。

## 运维建议

启用插件或安装更新前：

```bash
omarchy plugin validate "$HOME/.config/omarchy/plugins/io.omarchy.hosts"
cd "$HOME/.config/omarchy/plugins/io.omarchy.hosts"
./scripts/check.sh
```

请特别审阅 `system/`、`packaging/arch/`、planning engine 和 Polkit policy 的变更。不要在未审阅 diff 与包 checksum 的情况下，从不可信或本地已修改的 checkout 安装 helper 文件。
