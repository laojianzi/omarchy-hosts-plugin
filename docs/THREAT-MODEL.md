# 威胁模型

## 受保护资产

- `/etc/hosts` 中 managed block 外的系统和用户内容；
- `/etc/hosts` 的完整性、owner、mode 与扩展属性；
- root helper 的执行路径与导入路径；
- root-only 历史备份；
- 用户 profile 配置的私密性；
- Undo 不覆盖后续合法更改的保证。

## 信任边界

### 不可信或低信任

- 第三方插件 QML 与普通用户 Python 后端；
- IPC 调用参数；
- 用户 state 内容；
- candidate 路径和 JSON；
- 环境变量、`PATH`、`PYTHONPATH`、当前目录；
- `/etc/hosts` 从 preview 到 commit 之间的状态；
- 同一用户会话中的其他普通进程。

### 可信

- 经过用户审阅并由包管理器安装的 root-owned wrapper/helper/engine/policy；
- Linux 文件权限、Polkit、rename 与 advisory lock 语义；
- root 管理员本身。

root 已被攻陷不在本项目可防护范围内。

## 主要威胁与控制

### 可写插件脚本获得 root

**威胁：** Polkit 直接执行 `~/.config/...` 下的 helper，普通用户随后替换脚本实现提权。

**控制：** Polkit 只允许 `/usr/lib/omarchy-hosts/omarchy-hosts-helper`；wrapper/helper/engine 由 pacman 安装、root-owned、不可被 group/other 写。用户插件从不作为 root 执行。

### PATH/PYTHONPATH/import 劫持

**威胁：** `pkexec` 环境或当前目录让 helper 导入用户控制的 `engine`。

**控制：** 固定 `/usr/bin/python -I -B`；按验证后的绝对文件路径加载 engine；检查 regular file、owner、link count 和 write bits；不调用 shell，不查找 PATH。

### 任意文件写入

**威胁：** candidate 指定另一个目标或 backup 路径，实现 root 任意文件覆盖。

**控制：** helper 中目标常量固定为 `/etc/hosts`；candidate 不包含目标路径；backup 名称由 helper 生成，Undo 使用严格正则；candidate 只接受固定 runtime 目录的直接子文件。

### Symlink/hardlink 攻击

**威胁：** 将 state、candidate、lock、target 或 backup 替换成链接。

**控制：** `lstat`、`O_NOFOLLOW`、regular-file 检查、owner 检查、`st_nlink == 1`、open 前后 inode 比较。Privileged 目录本身也检查 owner/type/mode。

### Preview/Apply TOCTOU

**威胁：** 用户审阅后，另一个进程改变 profiles 或 `/etc/hosts`，Apply 提交未审阅内容。

**控制：** UI → CLI 携带 base/config SHA-256；CLI 重算；candidate 再携带；helper 重算并比较；写入前再次比较 target hash，rename 前再检查 target inode+hash。

### Candidate 篡改或重放

**威胁：** candidate 在授权前被修改，或旧 candidate 被重复提交。

**控制：** candidate `0600`、caller-owned、单链接、固定目录；helper 重算配置哈希；`requestUid` 必须匹配 `PKEXEC_UID`；15 分钟有效期；base hash 使成功 Apply 后的重放失效。

### Hostname 冲突造成错误路由

**威胁：** 多个 profile 或 unmanaged 行把名称指向不同地址。

**控制：** root/helper 和 preview 都执行同一冲突检测；异址冲突阻断，同址重复去重并提示。

### 覆盖外部更新

**威胁：** DHCP、配置管理、管理员或其他 hosts 工具在 preview/Apply/Undo 期间修改文件。

**控制：** 完整文件哈希、managed block 哈希、事务锁、write-window inode/hash 检查；Undo 要求当前内容仍等于 Apply 后 hash。

### 备份泄漏

**威胁：** hosts 中包含内网名称，普通用户读取历史备份。

**控制：** backup directory `0700`，backup files `0600`，均 root-owned。Unprivileged state 仅包含哈希和 backup basename，不含备份内容。

### 部分提交

**威胁：** 写入中断、磁盘满、metadata 写失败造成截断或无法 Undo。

**控制：** 同目录临时文件、完整写循环、file/dir fsync、原子 rename；metadata 失败执行补偿回滚；回滚失败返回 recovery backup 细节并记录错误，而不是返回成功。

### Polkit 授权过宽

**威胁：** 任意参数或远程/inactive session 利用授权执行未预期动作。

**控制：** action 绑定 executable path 与第一参数 `apply`/`undo`；helper 自身检查完整 argc；`allow_any=no`、`allow_inactive=no`、active 要求 `auth_admin`；不使用 `*_keep`。

## 有意不解决的问题

- 对抗已获得 root 的攻击者；
- 对抗同一用户读取其自己的普通 user state；
- 为 `/etc/hosts` 提供通配、按进程路由或 DNS server 功能；
- 同步多台机器；
- 替代企业配置管理系统；
- 修复第三方应用自身的 DNS 缓存行为。

## 安全不变量

1. 普通用户可修改的文件永远不作为 root Python 程序入口或导入模块。
2. Helper 永远只写固定 `/etc/hosts`。
3. 没有成功 Polkit 授权，不发生 privileged 写入。
4. 没有通过 root 侧重新验证，不发生 privileged 写入。
5. target 与审阅基线不同，不发生写入。
6. managed block 外内容由 plan 原样保留。
7. Undo 不覆盖 Apply 之后的外部修改。
8. 返回“成功”意味着 target 与 root transaction metadata 都已持久提交。
