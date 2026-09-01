**English** | [简体中文](CONTRIBUTING.zh-CN.md)

# Contributing to Omarchy Hosts

Thank you for improving Omarchy Hosts. The project combines an Omarchy/QML user interface, an unprivileged Python planning layer, a small privileged helper, an Arch package, and security-sensitive filesystem transactions. Changes should preserve those boundaries.

English is the canonical documentation language. Simplified Chinese files are maintained as synchronized translations and use the `.zh-CN.md` suffix.

## Development setup

Clone the repository on an Omarchy or Arch Linux system:

```bash
git clone https://github.com/laojianzi/omarchy-hosts-plugin.git
cd omarchy-hosts-plugin
```

Run the full available check suite:

```bash
./scripts/check.sh
```

Or use the Make targets:

```bash
make check
make test
make sync-packaging
```

The test suite uses only Python's standard library. Native Omarchy validation and `makepkg` source verification are performed when the corresponding tools are available.

## Architecture constraints

Before changing the implementation, read:

- [Architecture](docs/ARCHITECTURE.md)
- [Threat model](docs/THREAT-MODEL.md)
- [Security policy](SECURITY.md)

The following constraints are intentional:

- QML and repository-local Python run as the desktop user, never as root.
- The privileged helper is installed from `packaging/arch/` into fixed root-owned paths.
- The helper must not import code from the user-writable plugin checkout.
- Preview and Apply must use the same normalized profile hash and `/etc/hosts` baseline hash.
- The helper must re-validate and re-render the requested state itself.
- Writes must preserve unmanaged bytes, metadata expectations, and concurrent-writer safety.
- Undo must fail when the current file has drifted from the last applied result.

A convenience shortcut that weakens one of these invariants is not an acceptable trade-off.

## Making changes

Create a focused branch:

```bash
git switch -c type/short-description
```

Use conventional, descriptive commits where practical:

```text
feat: add profile import preview
fix: reject stale candidate metadata
security: harden recovery file validation
docs: document profile conflict semantics
test: cover post-exchange writer race
```

Keep unrelated refactors out of security-sensitive patches. A reviewer should be able to connect each behavioral change to a test and to the relevant trust boundary.

## Tests

Add or update tests for every behavior change. Existing coverage includes:

- entry and profile normalization;
- IPv4, IPv6, hostname, alias, IDN, and protected-name validation;
- deterministic rendering and line-ending preservation;
- managed-marker corruption;
- profile and unmanaged mapping conflicts;
- state-file permission, symlink, and hard-link checks;
- candidate age, owner, mode, and hash validation;
- atomic exchange and concurrent-writer recovery;
- Apply metadata, rollback, drift, and caller-bound Undo;
- CLI smoke behavior;
- packaging source synchronization.

Run a targeted test during development and the full suite before submission:

```bash
python -m unittest tests.test_engine -v
./scripts/check.sh
```

## QML changes

The panel should continue to use Omarchy shell components and theme tokens rather than introducing a second visual system. Preserve keyboard operation, vertical and horizontal bar behavior, focus recovery after dialogs/forms, and plain-text rendering for untrusted error output.

Do not build shell command strings from user-controlled values. Use `Process.command` argument arrays and standard input for structured payloads.

## Privileged helper changes

Treat `system/helper.py`, `system/omarchy-hosts-helper`, the packaged engine copy, the Polkit policy, and `packaging/arch/PKGBUILD` as one security unit.

After changing helper or engine source, synchronize the package copies:

```bash
./scripts/sync-packaging.sh
```

Then inspect the generated diff and run:

```bash
./scripts/check.sh
```

Do not add network access, arbitrary command execution, plugin hooks, user-selected output paths, or imports from user-controlled directories to the privileged process.

## Documentation language policy

Every user-facing Markdown document must exist as a pair:

```text
DOCUMENT.md          canonical English
DOCUMENT.zh-CN.md    Simplified Chinese translation
```

This applies to the root README, changelog, contribution guide, security policy, architecture document, and threat model.

Each file must begin with a language switch. For example:

```markdown
**English** | [简体中文](DOCUMENT.zh-CN.md)
```

and:

```markdown
[English](DOCUMENT.md) | **简体中文**
```

Rules:

1. Update the English canonical document first.
2. Update the Chinese translation in the same pull request.
3. Keep headings and substantive sections aligned.
4. Default repository and cross-document references should point to the English canonical path.
5. Use relative links so links work on branches, tags, forks, and downloaded source archives.
6. Run the documentation checker before committing.

```bash
python scripts/check-docs.py
```

## Pull requests

A pull request should explain:

- the user-visible or security-relevant problem;
- the selected design and rejected alternatives when important;
- the affected trust boundaries;
- the tests added or changed;
- manual Omarchy, Polkit, or package validation performed;
- documentation and translation updates.

Do not include real internal hostnames, private IP inventories, credentials, SSH keys, tokens, or a copy of a production `/etc/hosts` file in tests, screenshots, issues, or pull requests.

## Release process

The release version must agree in:

- `manifest.json`;
- `src/omarchy_hosts/__init__.py`;
- the newest heading in `CHANGELOG.md` and `CHANGELOG.zh-CN.md`;
- the Git tag, using `vMAJOR.MINOR.PATCH`.

For a release:

1. Update both changelogs and all affected documentation translations.
2. Run `./scripts/check.sh` locally.
3. Merge the release pull request only after CI passes.
4. Create and push an annotated tag, for example:

   ```bash
   git tag -a v1.0.0 -m "Omarchy Hosts v1.0.0"
   git push origin v1.0.0
   ```

5. The permanent release workflow validates the tag/version relationship and creates the GitHub Release from the canonical English changelog. Add any Chinese release summary as a link to `CHANGELOG.zh-CN.md` rather than replacing the canonical notes.
6. Verify the source archives, release page, installation commands, and fresh-install Apply/Undo flow on Omarchy 4.

Do not publish a production release when native Omarchy or privileged Apply/Undo validation is known to be incomplete.

## Security reports

Do not open a public issue for an unpatched vulnerability or an exploit that would help bypass the helper's checks. Follow [SECURITY.md](SECURITY.md).

## License

By contributing, you agree that your contribution is licensed under the repository's MIT license.
