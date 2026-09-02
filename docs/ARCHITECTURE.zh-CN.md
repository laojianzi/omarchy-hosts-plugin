[English](ARCHITECTURE.md) | **简体中文**

# 架构

本文是 Omarchy Hosts 架构文档的简体中文翻译；规范版本为英文 [ARCHITECTURE.md](ARCHITECTURE.md)。

Omarchy Hosts 是一款原生 Omarchy 4 shell 插件，只管理 `/etc/hosts` 中边界明确的一段内容。整体设计将用户交互和规划过程与特权系统文件事务分离。

## 1. 目标与不变量

架构围绕五项不变量组织：

1. Omarchy 插件 checkout 由用户写入，绝不能成为 root 信任基的一部分；
2. 用户无需管理员权限即可暂存和审阅变更；
3. 实际 Apply 必须与用户审阅的 profile 状态及 `/etc/hosts` baseline 通过 hash 精确绑定；
4. managed block 外的字节必须保留，不能静默覆盖并发外部写入者；
5. 成功 Apply 必须具有完整可恢复备份和元数据，否则在报告失败前执行补偿恢复。

这些不变量优先于便利性功能。

## 2. 组件图

```text
┌─────────────────────────────────────────────────────────────┐
│ Omarchy shell（桌面用户）                                   │
│                                                             │
│  Panel.qml ───────────────┐                                 │
│  - 顶栏组件               │                                 │
│  - 键盘面板               │                                 │
│  - profile 表单           │                                 │
│  - diff 审阅              │                                 │
│                           ▼                                 │
│  Service.qml ── Process 参数数组 / stdin ────────────────┐  │
└──────────────────────────────────────────────────────────│──┘
                                                           ▼
┌─────────────────────────────────────────────────────────────┐
│ 用户权限 Python 后端                                        │
│                                                             │
│  cli.py        command protocol、preview、candidate 创建    │
│  store.py      安全用户状态持久化                           │
│  engine.py     规范化、冲突、渲染、diff                    │
└─────────────────────────────┬───────────────────────────────┘
                              │ pkexec + 固定 action
                              ▼
┌─────────────────────────────────────────────────────────────┐
│ Polkit 授权                                                  │
│  io.omarchy.hosts.apply / io.omarchy.hosts.undo             │
└─────────────────────────────┬───────────────────────────────┘
                              ▼
┌─────────────────────────────────────────────────────────────┐
│ 打包后的特权 helper                                          │
│                                                             │
│  固定 wrapper → 隔离 Python → root-owned helper.py          │
│  root-owned 打包 engine.py                                  │
│  事务锁、备份、元数据、原子 exchange                        │
└─────────────────────────────┬───────────────────────────────┘
                              ▼
                       /etc/hosts
```

## 3. Omarchy 插件层

### `manifest.json`

manifest 将 `io.omarchy.hosts` 声明为单实例 `bar-widget`，入口为 `Panel.qml`，分类为 Network。Omarchy 将该仓库作为普通第三方插件发现。

### `Panel.qml`

面板只负责展示和交互：

- 顶栏 glyph 与 attention 状态；
- profile 列表和键盘 cursor；
- 新建、编辑、删除表单；
- staged 启用/禁用控制；
- preview、warning、conflict 和 unified diff 展示；
- Apply/Undo 确认与状态反馈；
- Omarchy `hosts` IPC handler。

UI 使用 Omarchy shell 组件、主题 token、焦点 helper 与面板尺寸约定。来自 QML 外部的数据在可能发生 markup 解释风险的位置按纯文本渲染。

### `Service.qml`

service 将 QML 连接到仓库内 CLI。它使用参数数组启动 process，而不是拼接 shell command string；结构化 profile payload 通过标准输入传递。它跟踪 operation 状态、解析 JSON response、刷新 status，并向面板暴露稳定数据。

service 没有特权。它可以创建 candidate request，但不能直接写 `/etc/hosts`。

## 4. 用户后端

### `store.py`

state store 在用户 Omarchy 配置目录下维护 profile 定义与 last-apply 信息。它使用严格权限创建目录和文件，并拒绝 symlink、hard-linked file、异常类型和权限过宽等不安全 state object。

profile identifier 在保持稳定引用的同时确保唯一。更新会保留 creation metadata，并使用用户空间原子写入。

### `engine.py`

planning engine 是 preview 与特权 apply 在逻辑上共享的纯确定性层。职责包括：

- 解析 profile entry text；
- 规范化 profile structure；
- canonicalize IP address；
- 校验 hostname 与 alias；
- 将 IDN 转换为小写 IDNA ASCII；
- 强制输入和渲染输出大小限制；
- 检测冲突与重复 mapping；
- 定位并验证 managed marker；
- 保留现有换行风格；
- 渲染目标 managed block；
- 构造 proposed full file 与 unified diff；
- 计算 canonical configuration hash。

它不执行特权 I/O。

### `cli.py`

CLI 同时是人工诊断接口和 QML 使用的 process protocol。它加载用户 state、调用 engine、读取当前 `/etc/hosts`、生成 preview，并在调用用户的 runtime 目录下写入短生命周期 candidate。

candidate 只包含规范化 source data 与 review binding，不代表授权。特权 helper 将其视为不可信输入，并重新计算所有派生值。

## 5. Profile 与渲染模型

规范化 profile 包含：

```text
id
name
description
enabled
entries[]
```

规范化 entry 包含 canonical IP address 与一个或多个 canonical hostname。已启用 profile 按确定顺序渲染。完全相同的重复 mapping 会被合并；同一 hostname 的不兼容 mapping 会阻断 plan。

engine 只拥有以下 block：

```text
# BEGIN OMARCHY HOSTS — managed by io.omarchy.hosts
# profile: Example
127.0.0.1 app.test api.app.test
# END OMARCHY HOSTS
```

具体 comment 格式可以演进，但 begin/end marker 构成所有权边界。begin marker 前和 end marker 后的所有字节都会原样复制。若 block 不存在，engine 会在不重写无关行的情况下插入；多个、嵌套、反向或不完整 marker 会被拒绝。

## 6. Preview 与 candidate 生命周期

Preview 刻意保持无特权：

1. 加载并规范化 staged profile；
2. 读取 `/etc/hosts`，记录其字节与安全 filesystem identity 信息；
3. 生成 warning 和 blocking conflict；
4. 渲染 proposed content；
5. 计算：
   - normalized profile/configuration hash；
   - 当前 hosts baseline hash；
   - proposed result hash；
   - 精确 unified diff；
6. 将结果返回 QML 供审阅。

请求 Apply 时，用户后端会重复相关检查，并在 `/run/user/$UID` 下的 private runtime 目录中创建 candidate。candidate 必须：

- 是普通文件；
- 由调用 UID 拥有；
- mode 为 `0600` 或更严格；
- 只有一个 hard link；
- 大小受限；
- 生命周期短；
- 文件名包含不可预测部分。

candidate 包含已审阅 hash 和规范化 source state。完成 Polkit 授权后，固定 helper operation 会消费它。

## 7. Polkit 边界

policy 暴露独立 Apply 与 Undo action。预期策略：

- inactive session：拒绝；
- 非本地或 remote session：拒绝；
- active local session：要求管理员认证；
- 不提供宽泛 passwordless rule；
- 不允许用户选择 executable；
- 除明确 operation contract 外不接受任意 helper argument。

`pkexec` 提供原始 caller identity。helper 使用它校验 candidate owner，并将 Undo 绑定到创建事务的用户。

## 8. 特权 helper

helper 由 Arch 包安装到固定 root-owned 路径。executable wrapper 选择固定 Python interpreter，以 isolated mode 且禁用 bytecode generation 启动 Python。在导入打包 engine 前，helper 会验证 code path 和文件均由 root 拥有且 group/other 不可写。

helper 永远不会从以下位置导入：

- 用户插件 checkout；
- current working directory；
- `PYTHONPATH`；
- candidate 指定路径。

### Apply 事务

特权 Apply 流程：

1. 验证 effective UID 与 caller UID context；
2. 在不跟随不安全 link 的情况下打开并校验 candidate；
3. 检查 owner、mode、link count、type、age、size 与允许的 directory ancestry；
4. 解析规范化 source state；
5. 重新计算 canonical profile/configuration hash；
6. 获取全局 Omarchy Hosts transaction lock；
7. 将 `/etc/hosts` 打开并校验为预期 root-owned 普通文件；
8. 重新检查已审阅 baseline hash 与 filesystem identity；
9. 重新运行 planning engine，并比较 proposed result hash；
10. 创建并 fsync root-owned backup；
11. 在同一目录创建具有正确 mode/ownership 的临时 replacement；
12. 使用 `renameat2(RENAME_EXCHANGE)` 与 `/etc/hosts` 原子交换；
13. 检测 exchange 之前或之后是否存在并发 writer；
14. 必要时恢复并发版本或保留 recovery file，绝不静默覆盖较新 writer；
15. fsync directory；
16. 持久化 root-owned transaction metadata，包括 before/after hash、backup reference、caller UID 与 timestamp；
17. metadata 持久化失败时，在返回 error 前补偿恢复旧版本；
18. 安全时删除已消费 candidate。

只有系统文件与 recovery metadata 一致后，事务才报告成功。

### Undo 事务

Undo 不是无条件 restore：

1. 获取同一 transaction lock；
2. 加载并校验最后一次 transaction metadata 和 backup name；
3. 确认当前 caller 与原 Apply caller 一致；
4. 确认当前 `/etc/hosts` 仍匹配记录的 after-hash；
5. 将 backup 校验为安全 root-owned 普通文件；
6. 使用相同原子 replacement discipline 恢复；
7. 一致地记录或清理 transaction state。

因此，Apply 后的外部修改会使 Undo 失败，而不会被覆盖。

## 9. 并发与恢复

process lock 会串行化 Omarchy Hosts 事务，但无法强制无关工具使用该锁。因此 helper 组合使用：

- baseline hash；
- inode/type/link 校验；
- 同目录临时文件；
- 原子 exchange；
- post-exchange verification；
- 明确 recovery preservation。

测试覆盖两类 race：

- **Pre-exchange race：** 其他 writer 在 baseline 校验后、exchange 前修改 target。helper 检测 mismatch 并恢复并发版本；
- **Post-exchange race：** 其他 writer 在 exchange 后立即替换 target。helper 不覆盖较新版本，并保留可恢复文件供管理员检查。

这是文件系统边界上的 compare-and-swap，而不只是 atomic rename。

## 10. 打包

`packaging/arch/PKGBUILD` 只安装特权单元：

- 固定 helper wrapper；
- 特权 helper 实现；
- 打包 planning engine；
- Polkit policy；
- license。

`packaging/arch/` 内副本由 `scripts/sync-packaging.sh` 从规范 source 同步。检查套件验证 byte equality 和 PKGBUILD SHA-256。普通 Omarchy 插件仍是用户配置目录中的 Git checkout。

## 11. 校验与 CI

`scripts/check.sh` 是仓库主 gate，执行可用检查：

- Python syntax 与 import；
- manifest schema 与 entry point；
- Polkit XML；
- QML structure/security invariant；
- 打包源码同步与 hash；
- 文档配对、语言切换、规范引用和本地链接；
- 版本一致性；
- unit 与 race test；
- 工具可用时的 Omarchy 原生校验和 `makepkg` 校验。

GitHub Actions 会在每次 push 与 pull request 上执行可移植子集。

## 12. 数据所有权汇总

| 数据 | Owner | 信任级别 |
| --- | --- | --- |
| 插件 checkout | 桌面用户 | root helper 不信任 |
| Profile/state | 桌面用户 | 不可信输入 |
| Runtime candidate | 桌面用户，private mode | 不可信传输 |
| 打包 helper/engine/policy | root/package manager | 特权信任基 |
| `/etc/hosts` | root | 受保护系统状态 |
| Backup/transaction metadata | root | 受保护恢复状态 |

特权信任基被刻意限制为打包 helper、打包 engine、policy、Python runtime、kernel/filesystem primitive 和 root-owned state。

## 13. 相关文档

规范英文入口：

- [README](../README.md)
- [Threat model](THREAT-MODEL.md)
- [Security policy](../SECURITY.md)
- [Contributing](../CONTRIBUTING.md)
- [Changelog](../CHANGELOG.md)

## 描述符与进程生命周期模型

### 状态事务描述符

`StateStore` 在不跟随符号链接的情况下获取配置 home、`omarchy` 和 `hosts` 目录的描述符。它校验受管理目录，并同时锁定已持有的状态目录 inode 与通过该目录相对打开的兼容 lock file。读取时通过目录描述符只打开一次 `state.json`，并校验大小、类型、owner、link count 和 mode。写入时创建 mode-0600 的不可预测子文件，完成同步后使用类似 `renameat` 的目录相对参数替换 `state.json`，再同步目录并释放锁。

### Candidate 授权交接

CLI 通过已持有的 `/run/user/$UID/omarchy-hosts/candidates` 描述符创建 candidate，并命名为 `request-<content-sha256>-<nonce>.json`。路径名只作为 Polkit 交接点。特权 helper 会独立打开并校验每个目录组件，使用 `O_NOFOLLOW` 打开 basename，执行有界读取，并在解析前将实际字节与 basename 中的摘要比较。因此，即使替换发生在授权对话框打开期间，只要请求状态不同就会失败。

### 进程与输出所有权

QML service 负责最外层用户可见 deadline 与输出上限：接收流式记录，在超时或溢出时终止 CLI，必要时升级强杀，并在组件销毁时清理。CLI 负责内层 `pkexec` 进程会话：并发排空两个 pipe，施加更严格的协议上限，并在超时、溢出或收到 QML 信号时终止会话。特权 helper 通过进程内 watchdog 负责授权完成后的最终 deadline。每一层都只清理自己创建的进程和资源。
