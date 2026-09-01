**English** | [简体中文](README.zh-CN.md)

# Omarchy Hosts

A native, keyboard-first `/etc/hosts` profile manager for **Omarchy 4**.

Omarchy Hosts is implemented as an Omarchy shell bar widget and panel rather than a separate GTK, Electron, or web application. It stages profile changes in the user session, presents the exact unified diff for review, and delegates only the final system-file transaction to a minimal Polkit-authorized helper.

> Version: `1.0.0`
>
> Plugin ID: `io.omarchy.hosts`
>
> Documentation policy: English files without a language suffix are canonical. Simplified Chinese translations use the `.zh-CN.md` suffix and are kept structurally aligned.

## Highlights

- Native Omarchy bar widget and keyboard-driven panel.
- Named profiles with independent enable/disable state.
- IPv4, IPv6, aliases, IDN/IDNA normalization, and deterministic rendering.
- Preview-before-apply workflow with an exact unified diff.
- Managed-block updates that preserve all bytes outside the plugin markers.
- Conflict detection against enabled profiles and unmanaged `/etc/hosts` entries.
- Polkit authentication only for Apply and Undo.
- Root-owned backups, drift detection, and one-step transactional Undo.
- Compare-and-swap checks around `/etc/hosts`, including concurrent-writer protection.
- CLI and Omarchy shell IPC surfaces for automation and diagnostics.

## Design goals

Omarchy Hosts follows the Omarchy experience instead of reproducing a desktop hosts-switching application:

1. **Shell-native interaction.** The UI lives inside the long-running Omarchy shell and uses its panel, typography, colors, keyboard navigation, and IPC conventions.
2. **Reviewable system changes.** Enabling a profile only changes staged state. `/etc/hosts` is not modified until the user reviews the generated diff and explicitly applies it.
3. **Small privileged surface.** User-writable QML and Python never run as root. A separately installed helper validates every candidate again before changing the system file.
4. **Fail-closed transactions.** Malformed markers, conflicting hostnames, stale previews, unsafe filesystem objects, and unexpected concurrent writes block the operation.
5. **Reversible operation.** Each successful Apply records a root-owned backup and metadata so a safe Undo can restore the prior version.

## Repository layout

```text
.
├── manifest.json                 Omarchy plugin manifest
├── Panel.qml                     Native bar widget and management panel
├── Service.qml                   User-session controller and process bridge
├── bin/omarchy-hosts             Repository-local CLI launcher
├── src/omarchy_hosts/            Validation, planning, persistence, and CLI
├── system/                       Privileged helper and Polkit policy
├── packaging/arch/               Arch Linux package for the root helper
├── scripts/                      Validation, installation, and sync scripts
├── tests/                        Unit and transaction/race tests
└── docs/                         Architecture and threat model
```

See [Architecture](docs/ARCHITECTURE.md) for the full component and transaction model, and [Threat model](docs/THREAT-MODEL.md) for the security analysis.

## Requirements

- Omarchy 4 with the plugin-capable `omarchy-shell`.
- Arch Linux userspace as shipped by Omarchy.
- Python 3.12 or newer.
- `polkit` and `pkexec` for privileged Apply/Undo.
- `makepkg` for installing the privileged helper package.

The unprivileged panel and CLI can be inspected and tested without installing the helper. Applying changes to `/etc/hosts` requires the packaged helper and policy.

## Install the Omarchy plugin

```bash
omarchy plugin add \
  https://github.com/laojianzi/omarchy-hosts-plugin.git \
  --enable
```

Validate and reload it:

```bash
plugin_dir="$HOME/.config/omarchy/plugins/io.omarchy.hosts"

omarchy plugin validate "$plugin_dir"
omarchy-shell shell rescanPlugins
omarchy plugin enable io.omarchy.hosts
omarchy restart shell
```

The plugin installer deliberately does not run install hooks or request `sudo`. The privileged helper is installed separately so its source and package recipe can be reviewed first.

## Install the privileged helper

```bash
cd "$HOME/.config/omarchy/plugins/io.omarchy.hosts/packaging/arch"

less PKGBUILD
less helper.py
less engine.py
less io.omarchy.hosts.policy

makepkg -si
```

The package installs a fixed interpreter wrapper, a root-only helper implementation, a validated copy of the planning engine, and the Polkit action policy. It does not grant passwordless writes to `/etc/hosts`.

Run the diagnostic after installation:

```bash
"$HOME/.config/omarchy/plugins/io.omarchy.hosts/bin/omarchy-hosts" doctor
```

## Basic workflow

1. Open the Hosts widget from the Omarchy bar.
2. Add a profile and enter standard hosts lines, for example:

   ```text
   127.0.0.1 app.test api.app.test
   10.20.0.15 grafana.lab prometheus.lab
   ::1 v6-app.test
   ```

3. Enable one or more profiles. This only updates staged user state.
4. Open the preview and inspect warnings, blocking conflicts, and the unified diff.
5. Select **Apply** and complete the Polkit administrator authentication.
6. Use **Undo** only while `/etc/hosts` still matches the version produced by the last Apply.

## Managed block

Omarchy Hosts owns only the text between its markers:

```text
# BEGIN OMARCHY HOSTS — managed by io.omarchy.hosts
# ... enabled profile entries ...
# END OMARCHY HOSTS
```

Everything outside the markers is preserved byte-for-byte, including comments, ordering, unrelated mappings, and the existing LF or CRLF line-ending style. Missing markers are inserted deterministically; duplicate, reversed, or malformed markers fail closed.

## Validation and conflict rules

Before preview and again inside the privileged helper, the engine validates:

- profile, entry, hostname, alias, and rendered-output size limits;
- canonical IPv4 and IPv6 addresses;
- IDN conversion to lowercase IDNA ASCII;
- valid hostname shapes, with narrowly supported local-development underscores;
- rejection of wildcards, scoped IPv6 literals, and protected system names such as `localhost`;
- conflicts where the same hostname maps to different addresses;
- duplicate mappings that can be safely collapsed or reported as warnings.

A candidate is rejected when it no longer matches the reviewed profile hash or the reviewed `/etc/hosts` baseline.

## CLI and IPC

Use the repository-local launcher for diagnostics and automation:

```bash
./bin/omarchy-hosts --help
./bin/omarchy-hosts --version
./bin/omarchy-hosts doctor
```

When the widget is loaded, the Omarchy shell exposes the `hosts` IPC target. For example:

```bash
omarchy-shell hosts status
```

Run `./bin/omarchy-hosts --help` and the IPC status command on the installed version for the authoritative command surface.

## Data and system paths

User-owned state is stored below the Omarchy configuration directory. Runtime candidates are created with restrictive permissions below `/run/user/$UID`. Privileged backups and transaction metadata are stored in root-owned system directories by the packaged helper.

The helper accepts only short-lived candidate files from the expected runtime directory, owned by the invoking user, with a single hard link and no group/other permissions. It re-parses and re-renders the candidate rather than trusting derived output from the UI process.

## Security

Plugins execute as unsandboxed user code inside `omarchy-shell`; review a plugin before enabling it. Omarchy Hosts minimizes the additional risk of modifying a root-owned file by keeping privileged code outside the plugin checkout and validating trust boundaries on both sides of Polkit.

Read:

- [Security policy](SECURITY.md)
- [Threat model](docs/THREAT-MODEL.md)
- [Architecture](docs/ARCHITECTURE.md)

Security-sensitive reports should follow the private reporting guidance in [SECURITY.md](SECURITY.md), not a public issue containing exploit details.

## Development

Clone the repository and run all available checks:

```bash
git clone https://github.com/laojianzi/omarchy-hosts-plugin.git
cd omarchy-hosts-plugin
./scripts/check.sh
```

Useful targets:

```bash
make check
make test
make sync-packaging
```

The check suite validates Python, the plugin manifest, Polkit XML, QML structure, Arch package source synchronization, documentation language pairs and links, version consistency, and the automated tests. Native Omarchy manifest validation and `makepkg` source verification run when those tools are available.

See [Contributing](CONTRIBUTING.md) before submitting changes.

## Documentation

English is the canonical/default documentation language. Every canonical document has a Simplified Chinese translation and every document has a language switch at the top.

- [README](README.md) · [简体中文](README.zh-CN.md)
- [Changelog](CHANGELOG.md) · [简体中文](CHANGELOG.zh-CN.md)
- [Contributing](CONTRIBUTING.md) · [简体中文](CONTRIBUTING.zh-CN.md)
- [Security policy](SECURITY.md) · [简体中文](SECURITY.zh-CN.md)
- [Architecture](docs/ARCHITECTURE.md) · [简体中文](docs/ARCHITECTURE.zh-CN.md)
- [Threat model](docs/THREAT-MODEL.md) · [简体中文](docs/THREAT-MODEL.zh-CN.md)

## Release status

`v1.0.0` is the first public release. It establishes the native plugin UI, validation and planning engine, hardened Apply/Undo helper, Arch package, automated tests, and bilingual documentation baseline.

See the canonical [Changelog](CHANGELOG.md) for release details.

## License

MIT. See [LICENSE](LICENSE).
