[English](THREAT-MODEL.md) | **简体中文**

# 威胁模型

本文是 Omarchy Hosts 威胁模型的简体中文翻译；规范版本为英文 [THREAT-MODEL.md](THREAT-MODEL.md)。

## 1. 范围

Omarchy Hosts 允许已登录的 Omarchy 用户暂存命名 hosts profile，并在明确完成 Polkit 认证后更新 `/etc/hosts` 内的 managed block。范围包括：

- QML 插件与 Omarchy shell IPC surface；
- 仓库内用户权限 Python 代码与 profile state；
- preview 生成与 runtime candidate；
- Polkit action policy 与 `pkexec` 权限切换；
- 打包后的特权 helper 与 planning engine；
- `/etc/hosts`、root-owned backup、lock 与 transaction metadata；
- 并发 writer 和文件系统故障下的 Apply/Undo 行为；
- Arch package 边界。

DNS server、应用级 DNS cache、远程名称服务、Omarchy 自身安全及任意 root 管理不属于直接范围，除非它们与已记录的项目边界发生交互。

## 2. 安全目标

系统目标：

1. **Unmanaged hosts 数据完整性。** managed marker 外内容不会被有意重写或静默替换；
2. **审阅完整性。** 以特权 Apply 的状态必须就是 reviewed diff 表示的状态；
3. **特权约束。** 用户可写插件代码不能使 root helper 导入或执行任意用户代码或 command；
4. **授权。** Apply 与 Undo 必须经过预期的本地 active-session Polkit 决策；
5. **冲突安全。** 歧义 marker 和不兼容 hostname mapping 失败关闭；
6. **并发安全。** 检测外部 writer，且不静默覆盖其较新版本；
7. **恢复能力。** 报告成功的 Apply 必须具有有效 backup 和一致 transaction metadata；metadata 阶段失败时执行补偿；
8. **Undo 安全。** Undo 只恢复其获授权的事务，绝不覆盖后续漂移；
9. **可用性边界。** 输入、文件、diff 与 journal 处理都有限制，避免失控资源消耗。

## 3. 资产

受保护资产包括：

- `/etc/hosts` 当前及先前有效内容；
- Omarchy Hosts managed block 外的 mapping；
- 用户 reviewed diff 的真实含义；
- root execution control flow 与 imported code；
- Polkit authorization intent；
- root-owned backup 与 transaction metadata；
- 执行 Apply 的用户 identity；
- 无关本地配置与日志的机密性；
- 桌面 shell 可用性。

Profile confidentiality 有价值，但不是主要 secrecy boundary：profile 由登录用户拥有，任何已经以该用户身份运行的 process 通常都能读取用户配置。

## 4. 行为者与能力

### 普通桌面用户

可以编辑自己的 profile、插件 checkout、environment、working directory、runtime file 和 QML configuration。可以请求 Polkit 认证，但不能直接写 root-owned protected file。

### 已攻陷的桌面用户进程

拥有与登录用户相同的权限，可能 race candidate creation、替换用户文件、控制环境变量、发送 shell IPC call，或在本插件之外展示误导性 UI。它不能假定管理员一定认证，也不能直接写 root-owned package/system state。

### 本地管理员/root

可以修改全部系统和包状态。抵御恶意 root 管理员不是项目目标。

### 并发合法 writer

package、configuration manager、editor、VPN client、container tool 或管理员可能不使用 Omarchy Hosts lock 而更新 `/etc/hosts`。

### 远程攻击者

可能影响用户复制的数据、远程服务或 Web 内容，但除非存在其他入侵，否则无法直接本地执行。插件的 Apply/Undo 不需要网络访问。

## 5. 信任边界

```text
不可信/用户控制
  QML 插件 checkout
  profile file 与 IPC request
  environment 与 current directory
  runtime candidate
                │
                │ 明确 Polkit action + 固定 executable
                ▼
特权信任基
  root-owned wrapper/helper/engine/policy
  Python runtime
  kernel 与 filesystem primitive
  root-owned transaction state
                │
                ▼
受保护 target
  /etc/hosts
```

跨越 Polkit 边界并不会让 candidate 自动可信。helper 会独立验证每项安全相关属性。

## 6. 攻击面与控制

### 6.1 恶意 profile 内容

**威胁**

- 通过 hostname、label、error 或 option field 进行 command injection；
- 使用 newline/comment injection 逃逸 managed representation；
- 病态输入导致 CPU、memory 或 diff 过大；
- wildcard 或 malformed hostname 改变解析语义；
- IDN 歧义；
- 将冲突 mapping 隐藏在多个 profile 中。

**控制**

- 结构化解析，不从 profile value 构造 command；
- canonical IP parsing 与 hostname normalization；
- IDNA 转换与小写 canonical form；
- 拒绝不支持的 wildcard、scoped 地址和受保护名称；
- 限制 profile、entry、alias、field length 与 rendered output；
- 确定性渲染；
- 显式 conflict/duplicate analysis；
- UI 对不可信输出使用纯文本渲染。

### 6.2 Candidate 替换与文件系统攻击

**威胁**

- preview 后替换 candidate；
- symlink 或 hard-link attack；
- path traversal 越出 runtime directory；
- 长时间后重用 candidate；
- 修改 file ownership 或 permission；
- 提供 device、FIFO、directory 或其他 special file。

**控制**

- candidate 限制在 caller 预期 `/run/user/$UID` 子树；
- 不可预测名称和严格 parent directory；
- regular-file、owner、mode、size、age 与 single-link 校验；
- 不跟随不安全 link；
- helper 重新计算 canonical profile hash；
- 短有效期与一次性 operation 消费；
- helper 从 normalized source 重新渲染，而不是信任 proposed byte。

### 6.3 Python import 与 executable 替换

**威胁**

- 通过 `PYTHONPATH`、current directory 或插件 checkout 注入 import；
- 通过 environment variable 替换 interpreter/helper path；
- 编辑用户插件 checkout 中的 helper source，并诱导 root 执行；
- bytecode cache 替换。

**控制**

- 固定 root-owned executable wrapper 与 interpreter path；
- isolated Python mode，禁用 bytecode write；
- 校验打包 code 与 directory 的 root ownership 和不可写性；
- 只从 packaged engine path 导入；
- 特权流程不执行 repository-local install hook 或 plugin script；
- package checksum 与 source synchronization check。

### 6.4 Polkit 滥用

**威胁**

- inactive 或 remote session 获得授权；
- 宽泛 passwordless rule；
- Apply 与 Undo action confusion；
- 通过 `pkexec` 执行任意 executable 或 argument；
- 授权被复用于非预期 operation。

**控制**

- Apply 与 Undo 使用独立固定 action ID；
- active local administrator authentication policy；
- 固定 helper executable 与明确 operation argument；
- 不提供通用 root command interface；
- helper 校验 operation、effective UID、caller UID 以及 candidate/transaction ownership。

本地管理员可以替换 Polkit rule；抵御恶意 root 不在范围内。

### 6.5 Managed marker 攻击

**威胁**

- 重复、嵌套、重排或残缺 marker；
- 构造类似 marker 的 comment；
- 扩张超出预期 block 的所有权；
- 换行转换导致整文件重写。

**控制**

- exact marker matching；
- 只允许零个或一个顺序正确的 block；
- malformed layout 失败关闭；
- 确定性首次插入；
- block 外 byte preservation；
- 保留现有 LF/CRLF 风格。

### 6.6 过期审阅 / TOCTOU

**威胁**

- 用户审阅 diff 后 profile state 改变；
- 管理员认证完成前 `/etc/hosts` 改变；
- candidate result 与 source profile 不匹配；
- filesystem identity 在校验与提交之间变化。

**控制**

- review binding 中包含 canonical profile/configuration hash；
- `/etc/hosts` baseline hash 与 identity binding；
- 用户后端在调用 Polkit 前进行 preflight；
- 提权后由 helper 重新计算；
- transaction lock 和 commit 前第二次 baseline check；
- proposed-result hash 比对。

### 6.7 并发外部 writer

**威胁**

- 其他 process 在 baseline 校验后、replacement 前写入；
- 其他 process 在 replacement 后立即写入；
- 简单 rename 虽原子成功，却仍覆盖语义上较新的版本；
- cleanup 删除并发版本的唯一副本。

**控制**

- 同目录 temporary file；
- 使用 `renameat2(RENAME_EXCHANGE)`，而非 unchecked replace；
- exchange 前后 hash/identity verification；
- finalization 前发现并发时 rollback 到并发版本；
- 必须保留较新 post-exchange target 时保存 recovery file；
- 两类 race 均有专门自动测试；
- directory fsync。

### 6.8 Backup、metadata 与 Undo 攻击

**威胁**

- backup path traversal 或 symlink substitution；
- `/etc/hosts` 已改变但 metadata 未持久化的不完整成功；
- 其他用户调用 Undo；
- Undo 覆盖 Apply 后合法修改；
- stale transaction metadata 恢复无关 backup。

**控制**

- root-only backup/metadata directory；
- 受限的自动生成 backup name；
- regular-file 与 ownership check；
- transaction metadata 记录 before/after hash 与 caller UID；
- metadata persistence 失败时 compensation rollback；
- Undo caller binding；
- restore 前校验当前 after-hash；
- 串行 Apply/Undo lock。

### 6.9 QML 与 shell 可用性

**威胁**

- malformed JSON 或 process output 导致 shell 崩溃；
- 重复 polling 或 oversized output 降低桌面性能；
- error message markup injection；
- focus trap 阻止安全取消。

**控制**

- bounded backend response 与 defensive JSON parsing；
- 受控 polling 和 single in-flight operation；
- 外部文本按纯文本渲染；
- 原生 Omarchy focus/keyboard component；
- shell 保持无特权，因此 UI crash 不会授予 root 权限。

由于 Omarchy plugin 在用户 session 内未沙箱化，恶意插件仍可影响用户 shell。启用第三方插件前必须审阅代码。

## 7. 假设

设计假设：

- kernel 与本地文件系统正确实现所需 open、fsync、locking 和 `renameat2` 语义；
- root-owned package file 与 Polkit policy 尚未被攻击者修改；
- `pkexec` 提供可信原 caller identity；
- `/etc/hosts` 是本地普通文件，而非刻意异常的 bind mount、symlink 或 network filesystem object；
- 管理员认证代表对展示 operation 的有意批准；
- Python 标准库和 interpreter 是可信操作系统组件；
- 用户安装前审阅 plugin/helper 变更。

这些假设不成立且 helper 能检测时，应失败关闭。

## 8. 剩余风险

- 已攻陷的桌面用户可以修改 staged profile，并尝试通过社会工程诱导管理员批准；
- 恶意 root 管理员可以替换特权信任基中的任何控制；
- 某些异常文件系统或 hardened environment 可能不支持原子 exchange，此时 Apply 不可用，而不是降级到弱写入；
- 即使执行 file/directory fsync，在最窄 hardware/filesystem durability boundary 上发生系统 crash 仍可能需要管理员检查；
- 应用可能 cache name-resolution result，无法立即观察 `/etc/hosts` 变化；
- 其他工具可能使用语义不兼容的 managed section，conflict detector 无法推断所有外部意图。

项目选择记录这些风险，而不是使用不安全 fallback 隐藏它们。

## 9. 安全测试要求

涉及 parsing、candidate、filesystem operation、Polkit、helper import、transaction 或 packaging 的变更，必须同时测试正常路径与 adversarial variant。至少应保持以下覆盖：

- malformed 与 oversized input；
- symlink/hard-link 与 permission violation；
- stale profile 和 hosts baseline；
- candidate tampering 与 expiration；
- marker corruption；
- pre/post-exchange race；
- metadata-write compensation；
- caller-bound、drift-aware Undo；
- packaged source divergence。

提交安全敏感变更前运行：

```bash
./scripts/check.sh
```

## 10. 报告

遵循规范英文 [SECURITY.md](../SECURITY.md) 中的私密报告说明。尚未修复问题的 exploit detail 不应出现在公开 issue 或 pull request 中。

## 11. 相关文档

规范英文入口：

- [Architecture](ARCHITECTURE.md)
- [Security policy](../SECURITY.md)
- [README](../README.md)
- [Contributing](../CONTRIBUTING.md)
- [Changelog](../CHANGELOG.md)

## 可变路径与无界进程审阅

### 威胁

不受信任的同用户进程可能重命名已经检查过的目录、替换已经检查过的文件名，或在管理员授权提示打开期间替换 candidate。畸形或卡死的后端也可能产生无限输出、让 QML 操作永久占用，或者在直接父进程退出后留下后代进程。

### 控制

所有用户状态与 candidate 操作都根植于已持有的 no-follow 目录描述符；子项打开、私有创建、替换、清理、chmod、加锁和 fsync 均相对于描述符执行。candidate 内容还会绑定到文件名摘要，并由特权 helper 再次校验。root state 使用一次有界描述符读取。QML、CLI 和 helper 各层分别执行明确的输出或时间上限；CLI 创建独立进程会话，使后代进程可以整体终止。

### 剩余风险

具有同一 Unix identity 的进程可以读取或修改用户拥有的 profile 与插件源码、竞争可用性并阻止桌面功能工作。该 identity 本来就位于 Omarchy 插件信任边界内。这些控制用于阻止路径重定向到无关对象、阻止被修改的 candidate 字节跨越特权边界，并限制预期实现的资源占用；它们不会尝试在同一用户账号下沙箱隔离彼此不信任的进程。
