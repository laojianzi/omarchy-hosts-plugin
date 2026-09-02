**English** | [简体中文](CHANGELOG.zh-CN.md)

# Changelog

All notable changes to Omarchy Hosts are documented here.

This project follows [Semantic Versioning](https://semver.org/). English is the canonical changelog; the Simplified Chinese file is a synchronized translation.

## [1.0.1] - 2026-09-02

### Security

- Made user-state locking, bounded reads, private temporary creation, atomic replacement, permission changes, cleanup, and directory synchronization descriptor-relative through validated held directories.
- Bound each privileged candidate filename to the SHA-256 of its exact JSON bytes and made the root helper reopen the complete runtime-directory chain without following symbolic links before verifying the digest.
- Replaced the root transaction-state check-then-reopen flow with one bounded no-follow descriptor read.
- Added bounded QML streaming, per-operation deadlines, termination escalation, and component-destruction cleanup.
- Added a bounded CLI process runner with concurrent pipe draining, a dedicated process session, hard deadlines, output limits, and process-group teardown.
- Added an independent post-authorization watchdog to the root helper.

### Changed

- Updated the Arch helper package and synchronized packaged helper source for the hardened privilege boundary.
- Expanded filesystem race, candidate substitution, unsafe root-state, output-limit, timeout, and descendant-process regression coverage.
- Documented the Marketplace security remediation and the required helper reinstall for upgrades from `v1.0.0`.

[1.0.1]: https://github.com/laojianzi/omarchy-hosts-plugin/releases/tag/v1.0.1

## [1.0.0] - 2026-09-01

### Added

- Native Omarchy 4 bar widget and keyboard-first hosts management panel.
- Profile creation, editing, deletion, enable/disable, and staged configuration persistence.
- Deterministic hosts planning engine with IPv4, IPv6, aliases, IDN/IDNA normalization, validation limits, and protected-name checks.
- Managed-block rendering that preserves every byte outside the Omarchy Hosts markers and retains LF or CRLF line endings.
- Blocking conflict detection across enabled profiles and unmanaged `/etc/hosts` entries, plus non-blocking duplicate warnings.
- Exact unified-diff preview before any privileged operation.
- Minimal Polkit-authorized Apply and Undo helper installed separately from the user-writable plugin checkout.
- Short-lived candidate validation, profile and baseline hashing, filesystem ownership/link checks, root-owned transaction locks, backups, and metadata.
- Atomic `renameat2(RENAME_EXCHANGE)` commit path with pre-exchange and post-exchange concurrent-writer recovery tests.
- Drift-aware single-step Undo bound to the user who performed the original Apply.
- Repository-local CLI, diagnostics, and Omarchy shell `hosts` IPC target.
- Arch Linux `PKGBUILD` for the privileged helper and synchronized packaged source copies.
- Automated Python, manifest, XML, QML structure, packaging, documentation, version, and transaction/race checks.
- Canonical English documentation with Simplified Chinese mirrors and language switching in every document.

### Security

- User-writable QML and Python are never executed as root.
- The privileged helper imports only fixed, root-owned, non-writable packaged code under isolated Python execution.
- Apply fails closed when the reviewed profile or `/etc/hosts` baseline changes before commit.
- Undo refuses to overwrite external changes made after the last successful Apply.

[1.0.0]: https://github.com/laojianzi/omarchy-hosts-plugin/releases/tag/v1.0.0
