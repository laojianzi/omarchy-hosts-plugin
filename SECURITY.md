# Security Policy

## 支持版本

| 版本 | 安全更新 |
|---|---|
| `1.0.x` | 支持 |
| `< 1.0` | 不支持 |

## 报告安全问题

发布到代码托管平台后，请使用该仓库的 **Private vulnerability reporting / Security Advisory** 通道，不要在公开 issue 中披露可利用细节、候选 payload、提权步骤或真实内部 hostname。

报告应尽量包含：

- Omarchy、Quickshell、Python、polkit 与内核版本；
- 插件 commit/version；
- 受影响的边界：QML、user backend、candidate、Polkit、helper、backup 或 Undo；
- 最小复现步骤；
- 预期与实际结果；
- 已脱敏的 journal/helper JSON envelope；
- 是否需要本地用户、active session 或管理员认证。

## 安全响应原则

- 首先确认是否破坏 [威胁模型](docs/THREAT-MODEL.md) 中的安全不变量；
- privileged helper、policy 或 engine 的修复必须同步更新 Arch package 副本与 checksum；
- 安全修复必须带回归测试；
- 不通过“隐藏错误”解决问题：遇到不确定状态应 fail closed；
- 不向 sudoers 添加 wildcard 命令；
- 不把 helper 移入用户可写插件目录；
- 不放宽 candidate 路径、link、owner、mode、time 或 hash 检查来改善便利性。

## 本地安全审计

```bash
./scripts/check.sh
./scripts/sync-packaging.sh --check

stat -c '%U %G %a %n' \
  /usr/lib/omarchy-hosts/omarchy-hosts-helper \
  /usr/lib/omarchy-hosts/helper.py \
  /usr/lib/omarchy-hosts/engine.py \
  /usr/share/polkit-1/actions/io.omarchy.hosts.policy

~/.config/omarchy/plugins/io.omarchy.hosts/bin/omarchy-hosts doctor
```

预期 privileged files 所有者为 root，且 group/other 不可写。
