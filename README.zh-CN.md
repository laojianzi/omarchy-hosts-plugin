[English](README.md) | **简体中文**

# Omarchy Hosts

一款面向 **Omarchy 4** 的原生、键盘优先 `/etc/hosts` profile 管理插件。

Omarchy Hosts 不是独立的 GTK、Electron 或 Web 应用，而是直接实现为 Omarchy shell 顶栏组件与面板。插件在用户会话中暂存 profile 变更，展示精确的 unified diff 供审阅，仅在最终系统文件事务阶段调用一个经过 Polkit 授权的最小化 helper。

> 版本：`1.0.0`
>
> 插件 ID：`io.omarchy.hosts`
>
> 文档规则：不带语言后缀的英文文档是规范版本；简体中文翻译使用 `.zh-CN.md` 后缀，并与英文文档保持结构同步。

## 主要能力

- 原生 Omarchy 顶栏组件与键盘驱动面板；
- 支持独立启停的命名 profile；
- 支持 IPv4、IPv6、alias、IDN/IDNA 规范化与确定性渲染；
- 先预览、后应用，并展示精确 unified diff；
- 仅更新 managed block，完整保留 marker 外的原始字节；
- 检测已启用 profile 之间以及 unmanaged `/etc/hosts` 条目之间的冲突；
- 只有 Apply 与 Undo 需要 Polkit 认证；
- root-owned 备份、漂移检测与单步事务 Undo；
- 对 `/etc/hosts` 执行 compare-and-swap 检查，防止并发写入被覆盖；
- 提供 CLI 与 Omarchy shell IPC，便于自动化和诊断。

## 设计目标

Omarchy Hosts 追求 Omarchy 原生体验，而不是复刻传统 hosts 切换桌面应用：

1. **Shell 原生交互。** UI 运行在长期驻留的 Omarchy shell 内，使用它的面板、字体、配色、键盘导航与 IPC 约定。
2. **系统变更可审阅。** 启用 profile 只改变 staged 状态；只有在用户审阅 diff 并明确 Apply 后才修改 `/etc/hosts`。
3. **最小化特权面。** 用户可写的 QML 与 Python 永远不以 root 运行。独立安装的 helper 会在写入系统文件前重新验证 candidate。
4. **失败即关闭。** marker 损坏、hostname 冲突、预览过期、不安全的文件对象和意外并发写入都会阻止事务。
5. **操作可撤销。** 每次成功 Apply 都会记录 root-owned 备份与元数据，以便在安全条件满足时执行 Undo。

## 仓库结构

```text
.
├── manifest.json                 Omarchy 插件清单
├── Panel.qml                     原生顶栏组件与管理面板
├── Service.qml                   用户会话控制器与进程桥接
├── bin/omarchy-hosts             仓库内 CLI 启动器
├── src/omarchy_hosts/            校验、规划、持久化与 CLI
├── system/                       特权 helper 与 Polkit 策略
├── packaging/arch/               root helper 的 Arch Linux 包
├── scripts/                      检查、安装与同步脚本
├── tests/                        单元测试及事务/竞态测试
└── docs/                         架构与威胁模型
```

完整组件和事务模型以英文 [Architecture](docs/ARCHITECTURE.md) 为准；安全分析以英文 [Threat model](docs/THREAT-MODEL.md) 为准。中文译本可通过各文档顶部的语言切换进入。

## 运行要求

- 支持插件的 Omarchy 4 `omarchy-shell`；
- Omarchy 默认提供的 Arch Linux 用户空间；
- Python 3.12 或更高版本；
- 用于 Apply/Undo 的 `polkit` 与 `pkexec`；
- 用于安装特权 helper 包的 `makepkg`。

不安装 helper 也可以审阅、测试普通用户面板和 CLI；真正写入 `/etc/hosts` 需要安装打包后的 helper 与策略。

## 安装 Omarchy 插件

```bash
omarchy plugin add \
  https://github.com/laojianzi/omarchy-hosts-plugin.git \
  --enable
```

校验并重载：

```bash
plugin_dir="$HOME/.config/omarchy/plugins/io.omarchy.hosts"

omarchy plugin validate "$plugin_dir"
omarchy-shell shell rescanPlugins
omarchy plugin enable io.omarchy.hosts
omarchy restart shell
```

Omarchy 插件安装器不会执行 install hook，也不会主动请求 `sudo`。特权 helper 单独安装，便于先审阅源码和打包配方。

## 安装特权 helper

```bash
cd "$HOME/.config/omarchy/plugins/io.omarchy.hosts/packaging/arch"

less PKGBUILD
less helper.py
less engine.py
less io.omarchy.hosts.policy

makepkg -si
```

该包会安装固定解释器 wrapper、root-only helper 实现、经过校验的 planning engine 副本和 Polkit action policy。它不会授予免密写入 `/etc/hosts` 的权限。

安装后执行诊断：

```bash
"$HOME/.config/omarchy/plugins/io.omarchy.hosts/bin/omarchy-hosts" doctor
```

## 基本使用流程

1. 从 Omarchy 顶栏打开 Hosts 组件。
2. 新建 profile，并输入标准 hosts 行，例如：

   ```text
   127.0.0.1 app.test api.app.test
   10.20.0.15 grafana.lab prometheus.lab
   ::1 v6-app.test
   ```

3. 启用一个或多个 profile。此时只更新用户 staged 状态。
4. 打开预览，检查 warning、阻断性冲突和 unified diff。
5. 选择 **Apply**，完成 Polkit 管理员认证。
6. 仅当 `/etc/hosts` 仍与上次 Apply 生成的版本一致时使用 **Undo**。

## Managed block

Omarchy Hosts 只管理 marker 之间的内容：

```text
# BEGIN OMARCHY HOSTS — managed by io.omarchy.hosts
# ... 已启用 profile 的条目 ...
# END OMARCHY HOSTS
```

marker 外的所有内容都会按字节保留，包括注释、顺序、无关映射及已有的 LF/CRLF 换行风格。marker 缺失时会确定性插入；重复、反向或损坏的 marker 会失败关闭。

## 校验与冲突规则

在预览阶段以及特权 helper 内部，engine 都会校验：

- profile、entry、hostname、alias 与渲染输出的大小上限；
- 规范化 IPv4 与 IPv6 地址；
- IDN 转换为小写 IDNA ASCII；
- 合法 hostname 结构，并仅为本地开发场景有限支持下划线；
- 拒绝 wildcard、scoped IPv6 literal 以及 `localhost` 等受保护系统名称；
- 同一 hostname 指向不同地址的冲突；
- 可安全合并或仅需 warning 的重复映射。

如果 candidate 不再匹配用户审阅时的 profile hash 或 `/etc/hosts` baseline，Apply 会被拒绝。

## CLI 与 IPC

使用仓库内 launcher 进行诊断和自动化：

```bash
./bin/omarchy-hosts --help
./bin/omarchy-hosts --version
./bin/omarchy-hosts doctor
```

组件加载后，Omarchy shell 会提供 `hosts` IPC target，例如：

```bash
omarchy-shell hosts status
```

实际可用命令以已安装版本的 `./bin/omarchy-hosts --help` 与 IPC status 输出为准。

## 数据与系统路径

用户状态存放在 Omarchy 配置目录下。运行期 candidate 以严格权限创建在 `/run/user/$UID` 下。特权备份和事务元数据由打包后的 helper 写入 root-owned 系统目录。

helper 只接受预期 runtime 目录内、由调用用户拥有、只有一个 hard link、且未向 group/other 开放的短生命周期 candidate。它会重新解析并渲染 candidate，而不是信任 UI 进程提供的派生输出。

## 安全

插件会作为未沙箱化的用户代码运行在 `omarchy-shell` 中，因此启用前应审阅源码。Omarchy Hosts 通过把特权代码放在插件 checkout 之外，并在 Polkit 两侧都校验信任边界，降低修改 root-owned 文件带来的额外风险。

规范英文文档：

- [Security policy](SECURITY.md)
- [Threat model](docs/THREAT-MODEL.md)
- [Architecture](docs/ARCHITECTURE.md)

安全敏感问题请按照 [SECURITY.md](SECURITY.md) 的私密报告说明提交，不要在公开 issue 中披露可利用细节。

## 开发

克隆仓库并运行所有可用检查：

```bash
git clone https://github.com/laojianzi/omarchy-hosts-plugin.git
cd omarchy-hosts-plugin
./scripts/check.sh
```

常用目标：

```bash
make check
make test
make sync-packaging
```

检查套件覆盖 Python、插件 manifest、Polkit XML、QML 结构、Arch 包源同步、文档语言配对与本地链接、版本一致性和自动化测试。环境具备相关工具时，还会执行 Omarchy 原生 manifest 校验和 `makepkg` 源校验。

提交变更前请阅读规范英文 [Contributing](CONTRIBUTING.md)。

## 文档

英文是默认且规范的文档语言。每份规范文档都有简体中文译本，每份文档顶部都提供语言切换。

- [README](README.md) · [简体中文](README.zh-CN.md)
- [Changelog](CHANGELOG.md) · [简体中文](CHANGELOG.zh-CN.md)
- [Contributing](CONTRIBUTING.md) · [简体中文](CONTRIBUTING.zh-CN.md)
- [Security policy](SECURITY.md) · [简体中文](SECURITY.zh-CN.md)
- [Architecture](docs/ARCHITECTURE.md) · [简体中文](docs/ARCHITECTURE.zh-CN.md)
- [Threat model](docs/THREAT-MODEL.md) · [简体中文](docs/THREAT-MODEL.zh-CN.md)

## 发布状态

`v1.0.0` 是第一个公开版本，建立了原生插件 UI、校验与 planning engine、加固的 Apply/Undo helper、Arch 包、自动化测试和双语文档基线。

发布详情以规范英文 [Changelog](CHANGELOG.md) 为准。

## 许可证

MIT，参见 [LICENSE](LICENSE)。
