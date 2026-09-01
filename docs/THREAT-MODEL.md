**English** | [简体中文](THREAT-MODEL.zh-CN.md)

# Threat Model

This is the canonical threat model for Omarchy Hosts. The Simplified Chinese document is a synchronized translation.

## 1. Scope

Omarchy Hosts lets a logged-in Omarchy user stage named hosts profiles and, after explicit Polkit authentication, update a managed block inside `/etc/hosts`. The scope includes:

- the QML plugin and Omarchy shell IPC surface;
- repository-local user Python code and profile state;
- preview generation and runtime candidates;
- Polkit action policy and `pkexec` transition;
- the packaged privileged helper and packaged planning engine;
- `/etc/hosts`, root-owned backups, locks, and transaction metadata;
- Apply and Undo behavior under concurrent writers and filesystem failures;
- the Arch package boundary.

DNS servers, application-specific DNS caching, remote name services, the security of Omarchy itself, and arbitrary root administration are outside the direct scope except where they interact with a documented project boundary.

## 2. Security objectives

The system aims to guarantee:

1. **Integrity of unmanaged hosts data.** Content outside the managed markers is not intentionally rewritten or silently replaced.
2. **Review integrity.** The state applied with privilege is the state represented by the reviewed diff.
3. **Privilege containment.** User-writable plugin code cannot cause the root helper to import or execute arbitrary user code or commands.
4. **Authorization.** Apply and Undo require the intended local active-session Polkit decision.
5. **Conflict safety.** Ambiguous markers and incompatible hostname mappings fail closed.
6. **Concurrency safety.** External writers are detected and their newer version is not silently overwritten.
7. **Recovery.** A reported successful Apply has a validated backup and coherent transaction metadata; a failed metadata phase is compensated.
8. **Undo safety.** Undo restores only the transaction it is authorized for and never overwrites later drift.
9. **Availability bounds.** Input, file, diff, and journal processing are bounded to avoid uncontrolled resource use.

## 3. Assets

Protected assets include:

- the current and prior valid contents of `/etc/hosts`;
- mappings outside the Omarchy Hosts managed block;
- the meaning of the user's reviewed diff;
- root execution control flow and imported code;
- Polkit authorization intent;
- root-owned backups and transaction metadata;
- the identity of the user who performed Apply;
- the confidentiality of unrelated local configuration and logs;
- desktop shell availability.

Profile confidentiality is useful but not a primary secrecy boundary: profiles are owned by the logged-in user and any process already running as that user can generally read user configuration.

## 4. Actors and capabilities

### Normal desktop user

Can edit their own profiles, plugin checkout, environment, working directory, runtime files, and QML configuration. Can request Polkit authentication but cannot directly write root-owned protected files.

### Compromised desktop-user process

Has the same access as the logged-in user and may race candidate creation, replace user-owned files, control environment variables, send shell IPC calls, or display misleading UI outside this plugin. It cannot assume administrator authentication or write root-owned package/system state directly.

### Local administrator/root

Can modify all system and package state. Protection against a malicious root administrator is not a project objective.

### Concurrent legitimate writer

A package, configuration manager, editor, VPN client, container tool, or administrator may update `/etc/hosts` without using the Omarchy Hosts lock.

### Remote attacker

May influence data copied by the user, remote services, or web content, but has no direct local execution unless another compromise exists. The plugin itself does not require network access for Apply/Undo.

## 5. Trust boundaries

```text
Untrusted/user-controlled
  QML plugin checkout
  profile files and IPC requests
  environment and current directory
  runtime candidate
                │
                │ explicit Polkit action + fixed executable
                ▼
Privileged trust base
  root-owned wrapper/helper/engine/policy
  Python runtime
  kernel and filesystem primitives
  root-owned transaction state
                │
                ▼
Protected target
  /etc/hosts
```

Crossing the Polkit boundary does not make the candidate trusted. The helper independently validates every security-relevant property.

## 6. Attack surfaces and controls

### 6.1 Malicious profile content

**Threats**

- command injection through hostname, label, error, or option fields;
- newline/comment injection that escapes the managed representation;
- pathological input causing excessive CPU, memory, or diff size;
- wildcard or malformed hostnames changing resolution semantics;
- IDN ambiguity;
- conflicting mappings hidden across profiles.

**Controls**

- structured parsing, no command construction from profile values;
- canonical IP parsing and hostname normalization;
- IDNA conversion and lowercase canonical form;
- rejection of unsupported wildcard/scoped/controlled names;
- bounded profiles, entries, aliases, field lengths, and rendered output;
- deterministic rendering;
- explicit conflict and duplicate analysis;
- plain-text UI rendering for untrusted output.

### 6.2 Candidate substitution and filesystem attacks

**Threats**

- replacing a candidate after preview;
- symlink or hard-link attacks;
- path traversal outside the runtime directory;
- candidate reuse after a long delay;
- changing file ownership or permissions;
- supplying a device, FIFO, directory, or other special file.

**Controls**

- candidate constrained to the caller's expected `/run/user/$UID` subtree;
- non-predictable name and restrictive parent directories;
- regular-file, owner, mode, size, age, and single-link validation;
- no unsafe link following;
- canonical profile hash recomputed by the helper;
- short validity window and one-operation consumption;
- helper re-renders from normalized source rather than trusting proposed bytes.

### 6.3 Python import and executable substitution

**Threats**

- `PYTHONPATH`, current-directory, or plugin-checkout import injection;
- replacing the interpreter or helper path through environment variables;
- editing helper source in the user-owned plugin checkout and persuading root to execute it;
- bytecode cache substitution.

**Controls**

- fixed root-owned executable wrapper and interpreter path;
- isolated Python mode and disabled bytecode writes;
- root ownership and non-writability checks for packaged code and directories;
- imports only from the packaged engine path;
- no privileged execution of repository-local install hooks or plugin scripts;
- package checksums and source synchronization checks.

### 6.4 Polkit misuse

**Threats**

- authorization from an inactive or remote session;
- broad passwordless rules;
- action confusion between Apply and Undo;
- arbitrary executable or argument execution through `pkexec`;
- reuse of authorization beyond the intended operation.

**Controls**

- separate fixed action IDs for Apply and Undo;
- active local administrator authentication policy;
- fixed helper executable and defined operation argument;
- no generic root command interface;
- helper verifies its operation, effective UID, caller UID, and candidate/transaction ownership.

Local administrators can replace Polkit rules; malicious-root resistance is out of scope.

### 6.5 Managed-marker attacks

**Threats**

- duplicate, nested, reordered, or partial markers;
- crafted comments resembling markers;
- expanding ownership beyond the intended block;
- line-ending transformations that rewrite the file.

**Controls**

- exact marker matching;
- exactly zero or one well-ordered block;
- malformed layouts fail closed;
- deterministic first insertion;
- byte preservation outside the block;
- existing LF/CRLF style retained.

### 6.6 Stale review / TOCTOU

**Threats**

- profile state changes after the user reviews the diff;
- `/etc/hosts` changes before administrator authentication completes;
- the candidate contains a result that does not match its source profiles;
- filesystem identity changes between validation and commit.

**Controls**

- canonical profile/configuration hash in the review binding;
- `/etc/hosts` baseline hash and identity binding;
- user backend preflight before invoking Polkit;
- helper recomputation after privilege escalation;
- transaction lock and second baseline check immediately before commit;
- proposed-result hash comparison.

### 6.7 Concurrent external writers

**Threats**

- another process writes after baseline validation but before replacement;
- another process writes immediately after replacement;
- a simple rename succeeds atomically but still overwrites a newer semantic version;
- cleanup deletes the only surviving concurrent version.

**Controls**

- same-directory temporary file;
- `renameat2(RENAME_EXCHANGE)` rather than an unchecked replace;
- pre- and post-exchange hash/identity verification;
- rollback to the concurrent version when detected before finalization;
- recovery-file preservation when a newer post-exchange target must remain in place;
- dedicated automated tests for both race classes;
- directory fsync.

### 6.8 Backup, metadata, and Undo attacks

**Threats**

- backup path traversal or symlink substitution;
- incomplete success where `/etc/hosts` changed but metadata did not persist;
- a different user invoking Undo;
- Undo overwriting legitimate changes made after Apply;
- stale transaction metadata restoring an unrelated backup.

**Controls**

- root-only backup and metadata directories;
- constrained generated backup names;
- regular-file and ownership checks;
- before/after hashes and caller UID in transaction metadata;
- compensation rollback when metadata persistence fails;
- Undo caller binding;
- current after-hash verification before restore;
- serialized Apply/Undo lock.

### 6.9 QML and shell availability

**Threats**

- malformed JSON or process output crashing the shell;
- repeated polling or oversized output degrading the desktop;
- markup injection in error messages;
- focus traps that prevent safe cancellation.

**Controls**

- bounded backend responses and defensive JSON parsing;
- controlled polling and single in-flight operations;
- plain-text rendering for external text;
- native Omarchy focus and keyboard components;
- the shell remains unprivileged, so a UI crash does not grant root access.

Because Omarchy plugins are unsandboxed within the user session, a malicious plugin can still affect the user's shell. Users must review third-party plugin code before enabling it.

## 7. Assumptions

The design assumes:

- the kernel and local filesystem correctly implement required open, fsync, locking, and `renameat2` semantics;
- root-owned package files and Polkit policy have not already been modified by an attacker;
- `pkexec` supplies a trustworthy original caller identity;
- `/etc/hosts` is a local regular file rather than an intentionally unusual bind mount, symlink, or network filesystem object;
- administrator authentication represents deliberate approval of the displayed operation;
- the Python standard library and interpreter are trusted components of the operating system;
- the user reviews plugin and helper changes before installation.

When these assumptions are false, the helper should fail closed where it can detect the condition.

## 8. Residual risks

- A compromised desktop user can alter staged profiles and attempt to socially engineer an administrator into approving them.
- A hostile root administrator can replace any control in the privileged trust base.
- Some unusual filesystems or hardened environments may not support the atomic exchange operation, causing Apply to be unavailable rather than falling back to a weaker write.
- A system crash at the narrowest hardware/filesystem durability boundary may still require administrator inspection, despite file and directory fsync.
- Applications may cache name-resolution results and not observe `/etc/hosts` changes immediately.
- Other tools may implement their own managed sections with incompatible semantics; the conflict detector cannot infer every external tool's intent.

These risks are documented rather than hidden behind an unsafe fallback.

## 9. Security testing expectations

Changes affecting parsing, candidates, filesystem operations, Polkit, helper imports, transactions, or packaging require tests for both the intended path and adversarial variants. At minimum, preserve coverage for:

- malformed and oversized input;
- symlink/hard-link and permission violations;
- stale profile and hosts baselines;
- candidate tampering and expiration;
- marker corruption;
- pre- and post-exchange races;
- metadata-write compensation;
- caller-bound, drift-aware Undo;
- packaged source divergence.

Run:

```bash
./scripts/check.sh
```

before submitting a security-sensitive change.

## 10. Reporting

Follow the private reporting instructions in [SECURITY.md](../SECURITY.md). Do not place exploit details for an unpatched issue in a public issue or pull request.

## 11. Related documents

- [Architecture](ARCHITECTURE.md)
- [Security policy](../SECURITY.md)
- [README](../README.md)
- [Contributing](../CONTRIBUTING.md)
- [Changelog](../CHANGELOG.md)
