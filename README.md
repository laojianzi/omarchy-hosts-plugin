# Omarchy Hosts

面向 **Omarchy 4 / Quattro** 的原生 hosts 管理插件。它不是一个塞进桌面的独立 GTK、Electron 或 Web 应用，而是直接运行在 `omarchy-shell` 中的 QML 顶栏面板，复用 Omarchy 的主题、键盘导航、Panel、IPC 与插件生命周期。

> 版本：`1.0.0`  
> 插件 ID：`io.omarchy.hosts`  
> 管理目标：本机 `/etc/hosts`

## 为什么这样设计

普通 hosts 切换器往往把“配置”和“系统状态”混成一个按钮：点击后立即覆盖整个 `/etc/hosts`，权限范围大，也很难确认究竟改了什么。Omarchy Hosts 把流程拆成三个明确阶段：

1. **编辑配置**：配置保存在用户目录，完全不需要 root。
2. **审阅计划**：插件展示 `/etc/hosts` 的统一 diff，并检测冲突与外部漂移。
3. **授权提交**：仅在 Apply 时调用一个 root-owned、Polkit 约束的最小 helper，进行再次验证、备份和原子写入。

启用或禁用 profile 只会形成待应用状态，不会偷偷修改系统文件。

## 功能

- 原生 Omarchy 顶栏 `Panel`，自动跟随主题、字体、间距与横/竖栏布局。
- Profile 分组：开发环境、VPN、实验室、客户项目等映射可独立启停。
- IPv4、IPv6、别名、IDN 域名与本地域名下划线支持。
- 对 enabled profiles 之间的同名异址冲突进行阻断。
- 对 managed block 外已有映射进行冲突检测；同名同址只提示并去重。
- Apply 前显示统一 diff；预览与提交通过配置哈希和 `/etc/hosts` 哈希绑定。
- 只维护一段带明确 marker 的 managed block，managed block 外的每个字节原样保留。
- Polkit 管理员认证，不使用宽泛 sudoers 规则，也不把可写插件脚本直接提权。
- `/etc/hosts` 原子替换、事务锁、写前/写中竞态检查、扩展属性复制和目录 `fsync`。
- 每次提交前创建 root-only 备份，最多保留 20 份。
- 单步 Undo；仅允许原提交用户恢复，且 `/etc/hosts` 在 Apply 后未被其他程序更改时才执行。
- 元数据落盘失败时自动回滚目标文件，避免“文件已改但事务记录丢失”。
- 原生 IPC 与终端 CLI，便于键位、自定义脚本和自动化调用。
- 依赖外部应用为零；插件后端仅使用 Python 标准库。

## 安装

### 1. 先运行仓库检查

```bash
cd omarchy-hosts-plugin
./scripts/check.sh
```

在非 Omarchy 环境中，检查脚本会跳过 Omarchy 运行时 manifest 校验；其余 Python、事务、安全、打包和 QML 结构检查仍会运行。

### 2. 安装普通用户插件

```bash
./scripts/install-local.sh
```

脚本会：

- 校验插件 manifest；
- 原子复制到 `~/.config/omarchy/plugins/io.omarchy.hosts/`；
- 重新扫描并启用插件；
- **不会运行 sudo，也不会安装 privileged helper**。

目标目录已存在时，默认拒绝覆盖。确认替换并保留时间戳备份：

```bash
./scripts/install-local.sh --replace
```

### 3. 审阅并安装系统 helper

不要以 root 身份运行 `makepkg`。先阅读这四个文件：

```bash
cd ~/.config/omarchy/plugins/io.omarchy.hosts/packaging/arch
less PKGBUILD helper.py engine.py io.omarchy.hosts.policy
makepkg -si
```

本地 PKGBUILD 使用固定文件清单和 SHA-256 校验，将以下 root-owned 文件安装到系统：

```text
/usr/lib/omarchy-hosts/omarchy-hosts-helper
/usr/lib/omarchy-hosts/helper.py
/usr/lib/omarchy-hosts/engine.py
/usr/share/polkit-1/actions/io.omarchy.hosts.policy
/usr/share/licenses/omarchy-hosts-helper/LICENSE
```

Arch 构建环境需要 `base-devel`；运行时依赖为 `python>=3.11` 与 `polkit`。

### 4. 重新加载界面

插件目录通常会自动热重载。顶栏仍未出现时执行：

```bash
omarchy restart shell
```

也可以确认状态：

```bash
omarchy plugin validate ~/.config/omarchy/plugins/io.omarchy.hosts
omarchy plugin enable io.omarchy.hosts
omarchy-shell hosts status
```

### 从 Git 仓库安装

项目发布为 Git 仓库后，普通用户插件可使用 Omarchy 自带命令安装：

```bash
omarchy plugin add <git-repository-url> --enable
```

Omarchy 插件安装器不会替第三方插件执行 sudo 或安装 hook，所以 privileged helper 仍需进入 `packaging/arch` 审阅并单独安装。

## 使用

### 顶栏

- **左键**：打开或关闭 Hosts 面板。
- **中键**：刷新配置、helper 与 `/etc/hosts` 状态。
- 顶栏图标颜色/状态：
  - `○`：没有 managed hosts；
  - `●`：已应用且同步；
  - `◐`：有待应用更改；
  - `!`：managed block 出现外部漂移；
  - `✕`：存在阻断错误。

### 键盘

面板列表模式：

| 按键 | 行为 |
|---|---|
| `↑` / `↓` | 移动游标 |
| `Enter` | 启用/禁用当前 profile，或进入 Add profile |
| `A` | 新建 profile |
| `E` | 编辑当前 profile |
| `Delete` | 删除当前 profile |
| `P` | 打开 diff 审阅页 |
| `R` | 刷新 |
| `U` | Undo（可用时） |
| `Esc` | 返回上一层或关闭面板 |
| `Tab` | 切换相邻 Omarchy panel |

在 diff 审阅页按 `A` 或 `Enter` 可发起授权 Apply。

### Entry 格式

每行格式与 `/etc/hosts` 一致：

```text
IP hostname [alias ...]
```

示例：

```text
# 本地开发
127.0.0.1 app.test api.app.test
10.20.0.15 grafana.lab prometheus.lab
::1 v6-app.test
```

规则：

- 空行和 `#` 注释被忽略；
- 一个地址后可写多个 alias；
- 域名会转成小写，非 ASCII 域名会转换为 IDNA ASCII；
- `localhost` 等系统保留名不能被 profile 接管；
- 不支持 `*.test` 这类通配符，因为 `/etc/hosts` 本身不提供通配匹配；
- 不接受带 `%eth0` scope 的 IPv6 地址；
- 单个 profile 可保存为空，便于先建立结构再填写映射。

## 提交模型

插件只修改以下 marker 之间的内容：

```text
# >>> omarchy-hosts managed block >>>
# Managed by io.omarchy.hosts. Edit profiles through Omarchy Hosts, not this block.
...
# <<< omarchy-hosts managed block <<<
```

### Apply 的完整流程

1. 普通用户后端读取 state 和 `/etc/hosts`。
2. 纯逻辑引擎验证 profile、检测冲突并生成 diff。
3. 用户在面板审阅 diff。
4. Apply 命令携带已审阅的 profile 配置哈希和 `/etc/hosts` 基线哈希。
5. 后端在调用 Polkit 前再次确认预览没有过期。
6. 候选请求以 `0600` 文件写入 `/run/user/$UID/omarchy-hosts/candidates/`，有效期 15 分钟。
7. root helper 重新读取并验证候选、重新计算配置哈希、重新生成目标内容。
8. helper 获取全局事务锁，并确认 `/etc/hosts` 未发生变化。
9. 创建 `0600` 备份，同目录写临时文件，复制 owner/mode/xattrs，`fsync` 后原子 rename。
10. 写入 root-owned 事务元数据。若元数据失败，helper 自动把 `/etc/hosts` 回滚到提交前内容。

### Undo 的限制

Undo 是安全恢复，不是强制覆盖：

- 只能撤销最后一次成功 Apply；
- 只能由发起该 Apply 的用户操作；
- 当前 `/etc/hosts` 必须与 Apply 后哈希完全一致；
- UI 中看到的事务哈希也必须仍是最新；
- Undo 前会再创建一份 safety backup；
- 任一条件不满足都会拒绝覆盖现有文件。

## 冲突与漂移

### 阻断冲突

以下情况不会允许 Apply：

- 两个 enabled profiles 把同一 hostname 指向不同地址；
- managed block 外已经把该 hostname 指向另一个地址；
- managed block marker 缺失、重复或顺序错误；
- profile/state/候选文件无效或超过安全大小限制；
- `/etc/hosts` 不是单链接普通文件；
- 从预览到提交期间配置或 `/etc/hosts` 被修改。

### 非阻断提示

- 多个 enabled profiles 重复声明同名同址：只渲染第一次声明；
- managed block 外已有同名同址映射：保留外部行，不在 managed block 中重复。

### Drift

若最后一次 Apply 使用的 profile 配置未变，但 managed block 的实际哈希不同，面板会显示 `drift`。插件不会静默覆盖；请先刷新并阅读新的 diff。

## IPC

插件注册 `hosts` IPC target：

```bash
omarchy-shell hosts open
omarchy-shell hosts close
omarchy-shell hosts toggle
omarchy-shell hosts refresh
omarchy-shell hosts status
omarchy-shell hosts list
omarchy-shell hosts enable development
omarchy-shell hosts disable development
omarchy-shell hosts apply
omarchy-shell hosts undo
```

`enable` / `disable` 可以使用 profile id 或精确 profile 名称。它们只改变 staged 配置；`apply` 才会请求系统授权。

IPC 的修改类方法返回 `started`，表示请求已进入插件的串行执行队列；最终结果反映在随后一次 `status` 中。

## CLI

普通用户 CLI 位于插件目录：

```bash
PLUGIN=~/.config/omarchy/plugins/io.omarchy.hosts

"$PLUGIN/bin/omarchy-hosts" status
"$PLUGIN/bin/omarchy-hosts" list
"$PLUGIN/bin/omarchy-hosts" diff
"$PLUGIN/bin/omarchy-hosts" doctor
"$PLUGIN/bin/omarchy-hosts" apply
"$PLUGIN/bin/omarchy-hosts" undo
```

稳定 JSON envelope：

```bash
"$PLUGIN/bin/omarchy-hosts" --json status
```

Profile 自动化示例：

```bash
printf '%s\n' '{"name":"Development","description":"Local stack","enabled":true,"entriesText":"127.0.0.1 app.test api.app.test\\n"}' \
  | "$PLUGIN/bin/omarchy-hosts" --json profile-save -
```

大 payload 通过标准输入传递，不会塞进一个超长命令行参数。

## 文件与权限

| 路径 | 所有者/模式 | 用途 |
|---|---|---|
| `~/.config/omarchy/plugins/io.omarchy.hosts/` | 用户 | 插件源码与普通用户后端 |
| `~/.config/omarchy/hosts/` | 用户 / `0700` | 用户配置目录 |
| `~/.config/omarchy/hosts/state.json` | 用户 / `0600` | profiles 与最后提交摘要 |
| `/run/user/$UID/omarchy-hosts/candidates/` | 用户 / `0700` | 短生命周期授权请求 |
| `/usr/lib/omarchy-hosts/` | root，不可被 group/other 写 | privileged helper 与策略引擎 |
| `/var/lib/omarchy-hosts/state.json` | root / `0644` | 可撤销事务摘要；不保存 profile 内容 |
| `/var/lib/omarchy-hosts/backups/` | root / `0700` | `/etc/hosts` 事务备份 |
| `/run/lock/omarchy-hosts.lock` | root / `0600` | 跨进程提交锁 |

## 卸载

为了同时移除 managed block，先在 UI 中禁用全部 profiles，审阅并 Apply。然后执行：

```bash
~/.config/omarchy/plugins/io.omarchy.hosts/scripts/uninstall-local.sh
sudo pacman -Rns omarchy-hosts-helper
```

保留 user state 是默认行为。一起删除：

```bash
~/.config/omarchy/plugins/io.omarchy.hosts/scripts/uninstall-local.sh --purge-state
```

root-owned backups 不会被插件脚本自动删除。确认不再需要恢复历史后，由管理员显式处理 `/var/lib/omarchy-hosts/`。

## 开发与验证

```bash
make check
make test
make package-check
```

当前测试覆盖：

- profile、IP、hostname、IDNA 和 entry 文本验证；
- enabled profile 与 unmanaged hosts 冲突；
- managed block 精确保留、CRLF、marker 异常和确定性渲染；
- state 权限、符号链接、硬链接、锁和原子保存；
- 预览哈希绑定、候选 staging、helper 协议；
- 候选内容哈希、时效、调用者 UID；
- Apply/Undo、竞态、外部漂移、caller 隔离；
- 元数据提交失败后的自动回滚；
- Polkit、isolated Python 导入、PKGBUILD 同步与 checksum；
- QML 结构和 Omarchy 原生组件契约。

通用 CI 无法替代真实 Omarchy 图形会话中的 QML 加载测试。发布前仍应在目标 Omarchy 4 主机上执行 `omarchy plugin validate .`、打开 panel、完成一次临时 profile 的 Apply/Undo，并查看 shell journal。

## 进一步阅读

- [架构说明](docs/ARCHITECTURE.md)
- [威胁模型](docs/THREAT-MODEL.md)
- [安全策略](SECURITY.md)
- [贡献指南](CONTRIBUTING.md)
- [变更记录](CHANGELOG.md)

## License

MIT，见 [LICENSE](LICENSE)。
