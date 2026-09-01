# Contributing

感谢你改进 Omarchy Hosts。该项目包含普通用户 QML/Python 代码和一个最小化的 root helper，因此权限边界相关改动需要比普通 UI 改动更严格的审阅。

## 开发流程

```bash
git clone https://github.com/laojianzi/omarchy-hosts-plugin.git
cd omarchy-hosts-plugin
make check
```

修改 `system/helper.py`、`src/omarchy_hosts/engine.py`、Polkit policy 或 LICENSE 后，先同步 Arch 包副本：

```bash
./scripts/sync-packaging.sh
./scripts/check.sh
```

不要手工只修改 `packaging/arch/` 中的生成副本；规范来源分别位于 `system/`、`src/omarchy_hosts/` 和仓库根目录。

## Pull request 要求

- 说明用户可见行为和安全影响；
- 对 bug 添加最小回归测试；
- 权限边界改动同时更新 `docs/THREAT-MODEL.md`；
- 数据模型或事务流程改动同时更新 `docs/ARCHITECTURE.md`；
- 确保 `make check` 通过；
- 在 Omarchy 4 主机上验证 manifest、panel 加载和相关 Apply/Undo 流程；
- 不提交真实内部 hostname、私有地址清单、`/etc/hosts` 副本、token 或密钥。

## 安全问题

可利用的安全问题不要提交公开 issue。请使用 GitHub Private Vulnerability Reporting；详见 [SECURITY.md](SECURITY.md)。

## 代码约定

- Python 使用类型标注，并保持 privileged 路径只依赖标准库；
- QML 通过参数数组调用进程，不拼接 shell 命令；
- 错误应 fail closed，并返回稳定的机器可读 code；
- 提交信息建议采用 Conventional Commits，例如 `fix: reject stale candidate after authorization`。
