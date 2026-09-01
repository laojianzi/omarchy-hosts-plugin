# 架构说明

## 目标

Omarchy Hosts 的核心目标不是“用图形界面覆盖 `/etc/hosts`”，而是建立一个可审阅、可证明、权限最小化的提交链路：

```text
Omarchy Panel
    │
    │ JSON / stdin / Process
    ▼
Unprivileged CLI + StateStore
    │
    │ preview hashes + 0600 candidate
    ▼
pkexec + Polkit action
    │
    ▼
Root-owned helper + identical pure engine
    │
    ├── lock
    ├── revalidate
    ├── backup
    ├── atomic rename
    └── durable transaction metadata
```

## 组件

### `Panel.qml`

职责：

- 实现 Omarchy `bar-widget` 与 `Panel` 交互；
- 展示 profile、同步状态、阻断错误、warning 和 diff；
- 提供键盘游标、表单、删除确认、Apply 和 Undo；
- 注册 `hosts` IPC target；
- 不直接读取或写入系统文件。

面板依赖 Omarchy shell 中的 `qs.Commons` 和 `qs.Ui`，不创建第二个常驻 UI 进程。

### `Service.qml`

职责：

- 维护 panel 的长期状态；
- 以串行方式执行普通用户 CLI；
- 每 5 秒对账一次 state、helper 和 `/etc/hosts`；
- 使用 Quickshell `Process` 参数数组，不通过 shell 拼接命令；
- Profile JSON 通过 stdin 传递；
- Apply/Undo 携带 UI 已审阅的哈希。

Service 不持有 root 权限。调用 `pkexec` 的也是普通用户 CLI，Polkit 决定是否启动 helper。

### `src/omarchy_hosts/engine.py`

纯函数策略引擎，无文件写入和进程启动：

- normalization；
- profile/entry/hostname/IP validation；
- managed block 解析；
- unmanaged mapping 分析；
- conflict detection 与 same-mapping dedupe；
- deterministic rendering；
- exact block replacement；
- SHA-256 与 unified diff。

同一文件会复制到 root-owned Arch 包中。预览端与提交端使用同一算法，但 privileged helper 从不相信预览结果，会独立重新运行算法。

### `src/omarchy_hosts/store.py`

用户配置存储：

- Omarchy 固定配置路径 `~/.config/omarchy/hosts/`；
- `0700` 目录、`0600` state/lock；
- symlink、hardlink、owner、mode 检查；
- `flock`；
- 临时文件 + `fsync` + `os.replace`；
- schema normalization；
- profile CRUD。

同 UID 的恶意进程不属于 root 权限边界，但 store 仍避免误写链接和损坏文件。

### `src/omarchy_hosts/cli.py`

连接 UI、state、预览与 Polkit：

- `ui-state` 生成完整 panel model；
- `diff` 和 `status`；
- profile CRUD；
- Apply 前验证 preview hash；
- 在固定 runtime 目录 staging candidate；
- 调用固定 `/usr/bin/pkexec` 和固定 helper 路径；
- 解析稳定 JSON envelope；
- 把 root transaction 摘要同步到 user state。

CLI 本身没有任何直接写 `/etc/hosts` 的代码。

### `system/helper.py`

唯一 privileged 组件，命令面极小：

```text
apply ABSOLUTE_CANDIDATE_PATH
undo [EXPECTED_AFTER_SHA256]
```

职责：

- 从 `PKEXEC_UID` 识别原调用者；
- 只接受 `/run/user/$UID/omarchy-hosts/candidates/` 的直接子文件；
- 验证目录和候选 owner/type/link/mode/size/time；
- 重算 profile 配置哈希；
- 重新读取 `/etc/hosts` 并重建 plan；
- 事务锁、备份、原子替换与状态记录；
- Undo caller/transaction/current hash 约束；
- syslog 审计。

helper 使用 `/usr/bin/python -I -B`。由于 `-I` 不包含脚本目录，helper 通过 `importlib` 加载经过 owner、link 和 mode 检查的固定 `engine.py`，不会从 `PYTHONPATH`、用户 site-packages 或当前目录导入代码。

### Polkit policy

两个 action：

- `io.omarchy.hosts.apply`
- `io.omarchy.hosts.undo`

策略绑定固定 executable path 和 `argv1`，active session 要求管理员认证；inactive/remote subject 被拒绝；不使用认证缓存变体。

## 数据模型

用户 state：

```json
{
  "schemaVersion": 1,
  "profiles": [
    {
      "id": "development",
      "name": "Development",
      "description": "Local stack",
      "enabled": true,
      "entries": [
        {"address": "127.0.0.1", "names": ["app.test", "api.app.test"]}
      ],
      "createdAt": "...",
      "updatedAt": "..."
    }
  ],
  "lastApply": {
    "beforeSha256": "...",
    "afterSha256": "...",
    "managedSha256": "...",
    "configSha256": "...",
    "backup": "..."
  }
}
```

privileged state 不复制 profiles，只保留执行 Undo 所需的事务摘要。

## Preview binding

UI review 后，Apply 不是简单地“应用当前配置”。它携带：

- `expectedBaseSha256`：审阅时 `/etc/hosts` 的完整哈希；
- `expectedConfigSha256`：审阅时所有 enabled profiles 的 canonical hash。

普通用户 CLI 先比较一次；candidate 再携带两者；root helper 又比较一次。任一阶段不一致都要求重新审阅。

## 原子写入

`atomic_replace()`：

1. 在 `/etc` 同目录创建 `O_EXCL | O_NOFOLLOW` 临时文件；
2. 复制原文件 mode、uid、gid 与可读取 xattrs；
3. 写完整数据并 `fsync(temp_fd)`；
4. 再次读取目标，比较 inode 与预期 SHA-256；
5. 使用 Linux `renameat2(RENAME_EXCHANGE)` 原子交换目标与临时 inode；
6. 验证被交换出的旧版本仍等于预期基线，并确认目标仍指向已准备的新 inode；
7. 删除被交换出的旧版本并 `fsync(/etc)`。

原子交换同时保留两个版本，使 helper 能在提交边界检测并发写入；系统不支持 `RENAME_EXCHANGE` 时 fail closed，不退化为存在竞态窗口的普通替换。

## 失败原子性

系统文件与事务 metadata 无法跨两个目录形成单个文件系统事务，因此实现补偿事务：

- 先写 `/etc/hosts`；
- 再写 privileged state；
- state 写失败时，在持有全局锁的情况下检查目标哈希并把旧内容原子恢复；
- 回滚也失败时返回包含 recovery backup 名称的高优先级错误，不伪装成功。

Undo 使用同样的补偿方式。

## 确定性与保留策略

managed block 不包含时间戳或随机字段，因此同一配置和同一 unmanaged 内容始终得到相同目标字节和 SHA-256。

首次插入 managed block 时，原 `/etc/hosts` 整体作为前缀保留；替换时，只移除 marker 两端之间的 block bytes，`before` 与 `after` 原样拼接。

## 并发模型

- QML Service：单进程串行队列；
- user state：advisory `flock`；
- privileged target：全局 root lock；
- preview → CLI：base/config hash；
- candidate → helper：base/config hash + time window；
- helper write window：target inode + hash recheck。

这些层分别覆盖 UI 重入、多个 CLI 实例、多个桌面用户，以及非协作的系统文件修改器。
