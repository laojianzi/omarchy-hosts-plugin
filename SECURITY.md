**English** | [简体中文](SECURITY.zh-CN.md)

# Security Policy

Omarchy Hosts changes a root-owned name-resolution file from an unsandboxed desktop plugin environment. Security reports are therefore treated as first-class engineering work.

English is the canonical security policy. The Simplified Chinese file is a synchronized translation.

## Supported versions

| Version | Supported |
| --- | --- |
| `1.0.x` | Yes |
| `< 1.0.0` | No |

Security fixes are released from the latest supported line. A report may result in a patch release, documentation update, package-policy change, or a temporary recommendation to disable Apply/Undo.

## Reporting a vulnerability

Do **not** open a public issue for an unpatched vulnerability, a privilege-boundary bypass, or reproducible instructions that could overwrite `/etc/hosts` unexpectedly.

Preferred channels:

1. Use GitHub's private **Security → Report a vulnerability** flow for this repository when it is available.
2. Otherwise email `laojianzi1994@gmail.com` with the subject `Omarchy Hosts security report`.

Include as much of the following as possible:

- affected version or commit SHA;
- Omarchy, kernel, Python, Polkit, and filesystem details;
- whether the issue is in QML, the user CLI/service, candidate handling, Polkit policy, helper installation, Apply, Undo, or packaging;
- exact preconditions and reproduction steps using non-sensitive test hostnames;
- expected and actual behavior;
- impact and whether root-owned data or concurrent external writes are affected;
- logs with usernames, hostnames, IP inventories, tokens, and paths redacted where appropriate;
- a proposed fix or test case, when available.

You should receive an acknowledgement as soon as practical. Please allow time to reproduce the issue, design a fix that preserves transaction safety, prepare coordinated patches, and validate the result on Omarchy 4 before public disclosure.

## Security boundaries

The project separates the following trust domains:

- **Omarchy shell plugin:** user-writable QML running as the logged-in desktop user.
- **User backend:** repository-local Python for profiles, preview, persistence, and candidate creation.
- **Polkit:** authorizes only the fixed helper executable and explicit Apply or Undo action.
- **Privileged helper:** packaged into root-owned, non-writable paths and executed with isolated Python settings.
- **System state:** `/etc/hosts`, root-owned backups, transaction locks, and transaction metadata.

The privileged helper must not import code from the plugin checkout or execute user-supplied shell commands. It accepts only a narrow operation and a validated candidate path or transaction reference.

See the canonical [Architecture](docs/ARCHITECTURE.md) and [Threat model](docs/THREAT-MODEL.md) for the full model.

## Important controls

The implementation is expected to preserve all of these controls:

- candidate files are short-lived, single-link regular files owned by the invoking user and inaccessible to group/other;
- candidate paths are constrained to the expected `/run/user/$UID` subtree;
- profile data is normalized, bounded, and hashed before privilege escalation;
- the helper re-parses, re-validates, and re-renders the requested state;
- `/etc/hosts` is required to be an expected root-owned regular file with safe link properties;
- managed markers are parsed strictly and malformed layouts fail closed;
- the reviewed baseline hash is rechecked while holding the transaction lock;
- the final commit uses atomic exchange semantics and detects pre- and post-exchange writers;
- backups and metadata are root-owned and written before a successful transaction is reported;
- Undo is bound to the original Apply user and refuses to overwrite later drift;
- untrusted process and journal output is rendered as plain text in QML;
- user-controlled values are passed as argument arrays or standard input, not interpolated into shell command strings.

A patch that intentionally relaxes one of these controls must include a written threat analysis and an equivalent compensating control.

## Out of scope and residual risks

The following are not vulnerabilities by themselves:

- a malicious plugin already running as the same desktop user reading or modifying that user's Omarchy Hosts profiles;
- a root administrator directly editing `/etc/hosts`, package files, Polkit policy, backups, or helper code;
- DNS or application behavior outside the semantics of the local hosts file;
- availability loss when the system is out of disk space or the filesystem does not support the required atomic operation;
- social engineering that convinces an administrator to authenticate a clearly displayed malicious diff after the local user account has already been compromised.

Reports are still welcome when one of these conditions can be combined with a project defect to cross a documented trust boundary.

## Disclosure and credits

Please coordinate public disclosure until a fix or mitigation is available. With the reporter's permission, release notes will credit the reporter by the preferred name or handle. Anonymous reporting is also respected.

## Operational guidance

Before enabling the plugin or installing an update:

```bash
omarchy plugin validate "$HOME/.config/omarchy/plugins/io.omarchy.hosts"
cd "$HOME/.config/omarchy/plugins/io.omarchy.hosts"
./scripts/check.sh
```

Review changes to `system/`, `packaging/arch/`, the planning engine, and Polkit policy with particular care. Never install helper files copied from an untrusted or locally modified checkout without reviewing the diff and package checksums.

## I/O and process-boundary hardening

### Descriptor-relative user state

Version 1.0.1 opens the user configuration chain one component at a time with `O_DIRECTORY | O_NOFOLLOW`, validates each managed directory through its held descriptor, and keeps that descriptor open for the complete lock/read/write transaction. The state lock, state read, private temporary creation, atomic replacement, mode changes, and directory synchronization are performed relative to held descriptors. Replacing an ancestor pathname after validation cannot redirect an in-flight operation.

### Candidate and root-state binding

Privileged candidates are created below the caller's private runtime directory through held descriptors. The filename embeds the SHA-256 of the exact JSON bytes, and the root helper opens the complete `/run/user/$UID/omarchy-hosts/candidates` chain without following symlinks before checking owner, mode, link count, size, and content digest. Root transaction state is likewise read once through a bounded `O_NOFOLLOW` descriptor rather than through a check-then-reopen pathname sequence.

### Bounded process lifetime

The QML panel streams backend output under explicit ceilings and applies a deadline to every operation. Timeout, overflow, panel destruction, and failed startup trigger termination and escalation. The CLI launches `pkexec` in a separate process session, drains stdout and stderr concurrently under independent byte limits, tears down the process group on failure, and handles termination signals so candidate cleanup still runs. The root helper also has an independent hard watchdog after authorization.

These controls limit accidental or adversarial denial of service from stuck processes and malformed output. They do not turn the unsandboxed user plugin into a security boundary against its own Unix account; a process already acting as that user can still deny service or replace user-owned plugin code.
