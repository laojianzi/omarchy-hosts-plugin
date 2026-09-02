**English** | [简体中文](ARCHITECTURE.zh-CN.md)

# Architecture

This document is the canonical architecture description for Omarchy Hosts. The Simplified Chinese document is a synchronized translation.

Omarchy Hosts is a native Omarchy 4 shell plugin that manages a narrowly delimited block inside `/etc/hosts`. The design separates user interaction and planning from the privileged system-file transaction.

## 1. Goals and invariants

The architecture is organized around five invariants:

1. The Omarchy plugin checkout is user-writable and must never become part of the root trust base.
2. A user can stage and review changes without acquiring administrator privileges.
3. The exact reviewed profile state and `/etc/hosts` baseline are cryptographically bound to Apply.
4. Bytes outside the managed block are preserved and concurrent external writers are not silently overwritten.
5. A successful Apply is either fully recorded with a recoverable backup or compensated before reporting failure.

These invariants take priority over convenience features.

## 2. Component map

```text
┌─────────────────────────────────────────────────────────────┐
│ Omarchy shell (desktop user)                                │
│                                                             │
│  Panel.qml ───────────────┐                                 │
│  - bar widget             │                                 │
│  - keyboard panel         │                                 │
│  - profile forms          │                                 │
│  - diff review            │                                 │
│                           ▼                                 │
│  Service.qml ── Process argument arrays / stdin ─────────┐  │
└──────────────────────────────────────────────────────────│──┘
                                                           ▼
┌─────────────────────────────────────────────────────────────┐
│ User Python backend                                         │
│                                                             │
│  cli.py        command protocol, preview, candidate creation│
│  store.py      secure user-state persistence                │
│  engine.py     normalization, conflicts, render, diff       │
└─────────────────────────────┬───────────────────────────────┘
                              │ pkexec + fixed action
                              ▼
┌─────────────────────────────────────────────────────────────┐
│ Polkit authorization                                        │
│  io.omarchy.hosts.apply / io.omarchy.hosts.undo             │
└─────────────────────────────┬───────────────────────────────┘
                              ▼
┌─────────────────────────────────────────────────────────────┐
│ Packaged privileged helper                                  │
│                                                             │
│  fixed wrapper → isolated Python → root-owned helper.py     │
│  root-owned packaged engine.py                              │
│  transaction lock, backup, metadata, atomic exchange        │
└─────────────────────────────┬───────────────────────────────┘
                              ▼
                       /etc/hosts
```

## 3. Omarchy plugin layer

### `manifest.json`

The manifest declares `io.omarchy.hosts` as a single-instance `bar-widget`, with `Panel.qml` as its entry point and Network as its category. Omarchy discovers the repository as a regular third-party plugin.

### `Panel.qml`

The panel owns presentation and interaction only:

- bar glyph and attention state;
- profile list and keyboard cursor;
- add/edit/delete forms;
- staged enable/disable controls;
- preview, warning, conflict, and unified-diff display;
- Apply/Undo confirmation and status feedback;
- Omarchy `hosts` IPC handler.

The UI uses Omarchy shell components, theme tokens, focus helpers, and panel sizing. Data originating outside QML is rendered as plain text where markup interpretation would be unsafe.

### `Service.qml`

The service bridges QML to the repository-local CLI. It launches processes with argument arrays rather than interpolated shell command strings. Structured profile payloads are sent through standard input. It tracks operation state, parses JSON responses, refreshes status, and exposes stable data to the panel.

The service is not privileged. It can create a candidate request, but it cannot write `/etc/hosts` directly.

## 4. User backend

### `store.py`

The state store maintains profile definitions and last-apply information below the user's Omarchy configuration directory. It creates directories and files with restrictive permissions and rejects unsafe state objects such as symlinks, hard-linked files, unexpected types, and overly permissive modes.

Profile identifiers are generated deterministically enough for stable references while remaining unique. Updates preserve creation metadata and use atomic user-space writes.

### `engine.py`

The planning engine is a pure, deterministic layer shared conceptually by preview and privileged apply. Its responsibilities include:

- parsing profile entry text;
- normalizing profile structures;
- canonicalizing IP addresses;
- validating hostnames and aliases;
- converting IDNs to lowercase IDNA ASCII;
- enforcing input and rendered-output limits;
- detecting conflicts and duplicate mappings;
- locating and validating managed markers;
- preserving the existing newline style;
- rendering the desired managed block;
- constructing the proposed full file and unified diff;
- computing canonical configuration hashes.

It does not perform privileged I/O.

### `cli.py`

The CLI is both a human diagnostic interface and the process protocol used by QML. It loads user state, invokes the engine, reads the current `/etc/hosts`, creates a preview, and writes a short-lived candidate under the invoking user's runtime directory.

A candidate contains normalized source data and review bindings, not authority. The privileged helper treats it as untrusted input and recomputes all derived values.

## 5. Profile and rendering model

A normalized profile contains:

```text
id
name
description
enabled
entries[]
```

A normalized entry contains a canonical IP address and one or more canonical hostnames. Enabled profiles are ordered deterministically for rendering. Duplicate identical mappings are collapsed; incompatible mappings for the same hostname block the plan.

The engine owns only this block:

```text
# BEGIN OMARCHY HOSTS — managed by io.omarchy.hosts
# profile: Example
127.0.0.1 app.test api.app.test
# END OMARCHY HOSTS
```

The exact comment format may evolve, but the begin and end markers form the ownership boundary. All bytes before the begin marker and after the end marker are copied unchanged. If no block exists, the engine inserts one without rewriting unrelated lines. Multiple, nested, reversed, or incomplete markers are rejected.

## 6. Preview and candidate lifecycle

Preview is deliberately unprivileged:

1. Load and normalize the staged profiles.
2. Read `/etc/hosts` and capture its bytes and safe filesystem identity information.
3. Produce warnings and blocking conflicts.
4. Render the proposed content.
5. Compute:
   - normalized profile/configuration hash;
   - current hosts baseline hash;
   - proposed result hash;
   - exact unified diff.
6. Return the result to QML for review.

When Apply is requested, the user backend repeats the relevant checks and creates a candidate in a private runtime directory below `/run/user/$UID`. The candidate is:

- a regular file;
- owned by the invoking UID;
- mode `0600` or stricter;
- limited to one hard link;
- bounded in size;
- short-lived;
- named with a non-predictable component.

The candidate includes the reviewed hashes and normalized source state. It is consumed through the fixed helper operation after Polkit authorization.

## 7. Polkit boundary

The policy exposes separate Apply and Undo actions. The intended policy is:

- inactive session: deny;
- non-local or remote session: deny;
- active local session: require administrator authentication;
- no broad passwordless rule;
- no user-selected executable;
- no arbitrary helper arguments beyond the defined operation contract.

`pkexec` supplies the original caller identity. The helper uses it to validate candidate ownership and to bind Undo to the user who created the transaction.

## 8. Privileged helper

The helper is installed by the Arch package into fixed root-owned locations. The executable wrapper selects a fixed Python interpreter and starts Python in isolated mode with bytecode generation disabled. Before importing the packaged engine, the helper verifies that the code path and files are root-owned and not writable by group or other.

The helper never imports from:

- the user's plugin checkout;
- the current working directory;
- `PYTHONPATH`;
- a candidate-selected path.

### Apply transaction

The privileged Apply flow is:

1. Verify effective UID and caller UID context.
2. Open and validate the candidate without following unsafe links.
3. Check owner, mode, link count, type, age, size, and allowed directory ancestry.
4. Parse normalized source state.
5. Recompute the canonical profile/configuration hash.
6. Acquire the global Omarchy Hosts transaction lock.
7. Open and validate `/etc/hosts` as the expected root-owned regular file.
8. Recheck the reviewed baseline hash and filesystem identity.
9. Re-run the planning engine and compare the proposed result hash.
10. Create and fsync a root-owned backup.
11. Create a same-directory temporary replacement with appropriate mode/ownership.
12. Atomically exchange it with `/etc/hosts` using `renameat2(RENAME_EXCHANGE)`.
13. Detect whether a concurrent writer changed the target immediately before or after the exchange.
14. Restore the concurrent version or preserve a recovery file when required; never silently overwrite the newer writer.
15. Fsync the directory.
16. Persist root-owned transaction metadata containing before/after hashes, backup reference, caller UID, and timestamps.
17. If metadata persistence fails, compensate by restoring the prior version before returning an error.
18. Remove the consumed candidate when safe.

The transaction reports success only after the system file and recovery metadata are coherent.

### Undo transaction

Undo is not an unconditional restore:

1. Acquire the same transaction lock.
2. Load and validate the last transaction metadata and backup name.
3. Confirm the current caller matches the original Apply caller.
4. Confirm current `/etc/hosts` still matches the recorded after-hash.
5. Validate the backup as a safe root-owned regular file.
6. Restore it through the same atomic replacement discipline.
7. Record or clear transaction state consistently.

External changes after Apply therefore cause Undo to fail instead of being overwritten.

## 9. Concurrency and recovery

A process lock serializes Omarchy Hosts transactions, but it cannot force unrelated tools to use that lock. The helper therefore combines:

- baseline hashing;
- inode/type/link validation;
- same-directory temporary files;
- atomic exchange;
- post-exchange verification;
- explicit recovery preservation.

Two race classes are tested:

- **Pre-exchange race:** another writer changes the target after baseline validation but before exchange. The helper detects the mismatch and restores the concurrent version.
- **Post-exchange race:** another writer replaces the target immediately after exchange. The helper does not overwrite that newer version and retains the recoverable file for administrator inspection.

This is compare-and-swap behavior at the filesystem boundary, not merely an atomic rename.

## 10. Packaging

`packaging/arch/PKGBUILD` installs only the privileged unit:

- fixed helper wrapper;
- privileged helper implementation;
- packaged planning engine;
- Polkit policy;
- license.

The copies in `packaging/arch/` are synchronized from canonical source files by `scripts/sync-packaging.sh`. The check suite verifies byte equality and the PKGBUILD SHA-256 values. The regular Omarchy plugin remains a Git checkout in the user's configuration directory.

## 11. Validation and CI

`scripts/check.sh` is the main repository gate. It performs available checks for:

- Python syntax and imports;
- manifest schema and entry points;
- Polkit XML;
- QML structural/security invariants;
- packaged source synchronization and hashes;
- documentation pairs, language switches, canonical references, and local links;
- version consistency;
- unit and race tests;
- native Omarchy validation and `makepkg` verification when available.

GitHub Actions runs the portable subset on every push and pull request.

## 12. Data ownership summary

| Data | Owner | Trust level |
| --- | --- | --- |
| Plugin checkout | desktop user | untrusted by root helper |
| Profiles/state | desktop user | untrusted input |
| Runtime candidate | desktop user, private mode | untrusted transport |
| Packaged helper/engine/policy | root/package manager | privileged trust base |
| `/etc/hosts` | root | protected system state |
| Backups/transaction metadata | root | protected recovery state |

The privileged trust base is intentionally limited to the packaged helper, packaged engine, policy, Python runtime, kernel/filesystem primitives, and root-owned state.

## 13. Related documents

- [README](../README.md)
- [Threat model](THREAT-MODEL.md)
- [Security policy](../SECURITY.md)
- [Contributing](../CONTRIBUTING.md)
- [Changelog](../CHANGELOG.md)

## Descriptor and process-lifetime model

### State transaction descriptors

`StateStore` obtains descriptors for the configured home, `omarchy`, and `hosts` directories without following symlinks. It validates the managed directories and locks both the held state-directory inode and a compatibility lock file opened relative to it. Reads open `state.json` once through that directory descriptor with size, type, owner, link-count, and mode checks. Writes create a mode-0600 unpredictable child, synchronize it, replace `state.json` with `renameat`-style directory-relative arguments, and synchronize the directory before releasing the lock.

### Candidate authorization handoff

The CLI creates a candidate through a held `/run/user/$UID/omarchy-hosts/candidates` descriptor and names it `request-<content-sha256>-<nonce>.json`. The pathname is only a rendezvous value for Polkit. The privileged helper independently opens and validates every directory component, opens the basename with `O_NOFOLLOW`, bounds the read, and compares the actual bytes with the digest in the basename before parsing. A replacement containing different requested state therefore fails even if it occurs while the authorization dialog is open.

### Process and output ownership

The QML service owns the outer user-visible deadline and output ceilings. It receives streamed records, terminates the CLI on deadline or overflow, escalates if needed, and performs teardown when the component is destroyed. The CLI owns the nested `pkexec` process session: it concurrently drains both pipes, imposes tighter protocol limits, and terminates the session on timeout, overflow, or a signal from QML. The privileged helper owns the final post-authentication deadline through an in-process watchdog. Each layer therefore cleans up the processes and resources it created.
