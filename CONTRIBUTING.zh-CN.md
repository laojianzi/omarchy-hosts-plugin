[English](CONTRIBUTING.md) | **简体中文**

# 为 Omarchy Hosts 贡献代码

感谢你改进 Omarchy Hosts。本项目同时包含 Omarchy/QML 用户界面、普通用户权限的 Python planning 层、最小化特权 helper、Arch 包以及安全敏感的文件系统事务。所有修改都应保持这些边界。

英文是规范文档语言。简体中文文件作为同步翻译维护，并使用 `.zh-CN.md` 后缀。

## 开发环境

在 Omarchy 或 Arch Linux 系统中克隆仓库：

```bash
git clone https://github.com/laojianzi/omarchy-hosts-plugin.git
cd omarchy-hosts-plugin
```

运行全部可用检查：

```bash
./scripts/check.sh
```

也可以使用 Make target：

```bash
make check
make test
make sync-packaging
```

测试套件只依赖 Python 标准库。环境存在相应工具时，还会运行 Omarchy 原生校验与 `makepkg` 源校验。

## 架构约束

修改实现前请阅读规范英文文档：

- [Architecture](docs/ARCHITECTURE.md)
- [Threat model](docs/THREAT-MODEL.md)
- [Security policy](SECURITY.md)

以下约束是刻意设计的：

- QML 和仓库内 Python 只以桌面用户身份运行，绝不以 root 运行；
- 特权 helper 由 `packaging/arch/` 安装到固定 root-owned 路径；
- helper 不得从用户可写插件 checkout 导入代码；
- Preview 与 Apply 必须使用同一个规范化 profile hash 和 `/etc/hosts` baseline hash；
- helper 必须自行重新校验并重新渲染请求状态；
- 写入必须保留 unmanaged 字节、元数据预期和并发写入安全；
- 当前文件与上次 Apply 结果发生漂移时，Undo 必须失败。

任何以削弱这些不变量为代价的便利性捷径都不可接受。

## 修改代码

创建范围明确的分支：

```bash
git switch -c type/short-description
```

建议使用清晰的 conventional commit：

```text
feat: add profile import preview
fix: reject stale candidate metadata
security: harden recovery file validation
docs: document profile conflict semantics
test: cover post-exchange writer race
```

安全敏感 patch 中不要混入无关重构。reviewer 应能将每项行为变化对应到测试及相关信任边界。

## 测试

每项行为变更都应新增或更新测试。现有覆盖包括：

- entry 与 profile 规范化；
- IPv4、IPv6、hostname、alias、IDN 与受保护名称校验；
- 确定性渲染与换行风格保留；
- managed marker 损坏；
- profile 与 unmanaged mapping 冲突；
- state 文件权限、symlink 与 hard link 检查；
- candidate 时效、owner、mode 与 hash 校验；
- 原子 exchange 和并发写入恢复；
- Apply 元数据、回滚、漂移及绑定调用者的 Undo；
- CLI smoke 行为；
- 打包源码同步。

开发时可运行目标测试，提交前必须运行完整套件：

```bash
python -m unittest tests.test_engine -v
./scripts/check.sh
```

## QML 修改

面板应继续使用 Omarchy shell 组件与主题 token，不要引入第二套视觉系统。必须保持键盘操作、横向/纵向 bar 行为、dialog/form 关闭后的焦点恢复，以及对不可信错误输出的纯文本渲染。

不要使用用户可控值拼接 shell command。应使用 `Process.command` 参数数组，并通过标准输入传递结构化 payload。

## 特权 helper 修改

应将 `system/helper.py`、`system/omarchy-hosts-helper`、打包后的 engine 副本、Polkit policy 和 `packaging/arch/PKGBUILD` 视为一个安全单元。

修改 helper 或 engine 后同步打包副本：

```bash
./scripts/sync-packaging.sh
```

检查生成的 diff，然后运行：

```bash
./scripts/check.sh
```

不要向特权进程添加网络访问、任意命令执行、插件 hook、用户自选输出路径，或从用户控制目录导入代码的能力。

## 文档语言规则

每份面向用户的 Markdown 文档都必须成对存在：

```text
DOCUMENT.md          规范英文文档
DOCUMENT.zh-CN.md    简体中文翻译
```

该规则适用于根 README、changelog、贡献指南、安全策略、架构文档和威胁模型。

每个文件都必须以语言切换开头，例如：

```markdown
**English** | [简体中文](DOCUMENT.zh-CN.md)
```

以及：

```markdown
[English](DOCUMENT.md) | **简体中文**
```

维护规则：

1. 先更新规范英文文档；
2. 在同一个 pull request 中更新中文翻译；
3. 保持 heading 与实质章节结构一致；
4. 仓库默认入口与跨文档引用应指向规范英文路径；
5. 使用相对链接，确保 branch、tag、fork 和下载后的源码归档中均可访问；
6. 提交前运行文档检查器。

```bash
python scripts/check-docs.py
```

## Pull request

Pull request 应说明：

- 面向用户或安全相关的问题；
- 采用的设计，以及必要时说明被放弃的方案；
- 受影响的信任边界；
- 新增或修改的测试；
- 已执行的 Omarchy、Polkit 或打包人工验证；
- 文档与翻译更新。

测试、截图、issue 和 pull request 中不得包含真实内部 hostname、私有 IP 清单、凭据、SSH key、token 或生产 `/etc/hosts` 副本。

## 发布流程

以下位置的版本必须一致：

- `manifest.json`；
- `src/omarchy_hosts/__init__.py`；
- `CHANGELOG.md` 与 `CHANGELOG.zh-CN.md` 的最新 heading；
- 使用 `vMAJOR.MINOR.PATCH` 的 Git tag。

发布步骤：

1. 更新中英文 changelog 以及所有受影响文档翻译；
2. 本地运行 `./scripts/check.sh`；
3. 只有 CI 通过后才合并 release pull request；
4. 创建并推送 annotated tag，例如：

   ```bash
   git tag -a vX.Y.Z -m "Omarchy Hosts vX.Y.Z"
   git push origin vX.Y.Z
   ```

5. 永久 release workflow 会校验 tag 与版本关系，并从规范英文 changelog 创建 GitHub Release。中文发布摘要应链接到 `CHANGELOG.zh-CN.md`，而不是替换规范 release notes；
6. 在 Omarchy 4 上验证源码归档、Release 页面、安装命令及全新安装后的 Apply/Undo 流程。

如果已知 Omarchy 原生验证或特权 Apply/Undo 验收未完成，不应发布生产版本。

## 安全报告

对于尚未修复的漏洞或可帮助绕过 helper 检查的 exploit，不要创建公开 issue。请遵循规范英文 [SECURITY.md](SECURITY.md)。

## 许可证

提交贡献即表示你同意按仓库 MIT license 授权该贡献。
