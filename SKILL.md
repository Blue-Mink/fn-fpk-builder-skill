---
name: fn-fpk-builder-skill
description: Build, validate, inspect, release, install, replace, troubleshoot, and safely remove fnOS/飞牛 OS FPK application packages. Use when Codex works with fnpack, appcenter-cli, .fpk archives, fnOS manifests, native or Docker app templates, x86_64/ARM64 packaging, GitHub Actions release pipelines, or SSH-based deployment and smoke testing on a fnOS device.
---

# fnOS FPK Builder

Create evidence-backed fnOS packages without modifying the source project in place. Prefer the bundled Python tools for fragile archive, architecture, checksum, and remote lifecycle operations.

Resolve the directory containing this `SKILL.md` as `SKILL_DIR` before running bundled tools. The user's working directory is normally an application repository, so never assume `scripts/` resolves to this Skill:

```bash
SKILL_DIR=/absolute/path/to/fn-fpk-builder-skill
python3 "$SKILL_DIR/scripts/fpk.py" --help
```

## Route the request

1. Read [references/official-contract.md](references/official-contract.md) before creating or changing an fnOS package structure, manifest, lifecycle script, wizard, privilege, resource, UI entry, CGI, or gateway configuration.
2. Read [references/build-and-architecture.md](references/build-and-architecture.md) before cross-building, applying architecture overlays, choosing `platform`, or packaging Go, Rust, Node, Python, Docker, or other native dependencies.
3. Read [references/ci-release.md](references/ci-release.md) before authoring or changing release automation. Start from `assets/github-actions/fpk.yml` when it fits the project.
4. Read [references/remote-testing.md](references/remote-testing.md) before connecting to a device, installing, replacing, rolling back, collecting logs, or running smoke tests.
5. Read [references/security.md](references/security.md) before accepting root privilege, CGI/gateway exposure, secrets, symlinks, or unusual archive content.
6. Read [references/troubleshooting.md](references/troubleshooting.md) only when a command, build, install, start, or validation step fails.

## Follow the core workflow

1. Inspect the repository and find the package root, existing build commands, prepared artifacts, target architectures, and release conventions. Do not assume a language or monorepo layout.
2. Run environment and project diagnostics:

   ```bash
   python3 "$SKILL_DIR/scripts/fpk.py" doctor --project /absolute/path/to/package
   ```

3. Build application binaries with the project's own locked build commands. Do not invent or silently execute an arbitrary prebuild shell command.
4. Put architecture-specific files in explicit overlay directories when the common package tree cannot already be built per architecture.
5. Build in isolated staging:

   ```bash
   python3 "$SKILL_DIR/scripts/fpk.py" build \
     --project /absolute/path/to/package \
     --out /absolute/path/to/dist \
     --arch both \
     --overlay-amd64 /absolute/path/to/amd64-overlay \
     --overlay-arm64 /absolute/path/to/arm64-overlay
   ```

6. Inspect every final artifact independently:

   ```bash
   python3 "$SKILL_DIR/scripts/fpk.py" inspect /absolute/path/to/app.fpk
   ```

7. Deploy only when the user authorized the target device and application. Diagnose the remote first, then use the lifecycle wrapper:

   ```bash
   python3 "$SKILL_DIR/scripts/fnos.py" doctor --host root@fnos-host
   python3 "$SKILL_DIR/scripts/fnos.py" deploy --host root@fnos-host /absolute/path/to/app.fpk
   ```

8. Report exact artifacts, SHA-256 values, target architecture, commands run, validations performed, remote status, and any skipped evidence.

Use `--json` on every command when results need to be consumed by an agent or CI.

## Enforce safety invariants

- Treat official fnOS documentation as the behavioral authority. Treat repository scripts and device observations as implementation evidence, not universal API guarantees.
- Pin and verify `fnpack`. Never replace a failed checksum with an observed value.
- Use `platform=all` only when the payload contains no architecture-specific native executable or library. Build separate `x86` and `arm` packages otherwise.
- Reject path traversal, absolute paths, unsafe links, duplicate archive members, Mach-O/PE binaries, mixed ELF architectures, checksum mismatch, and native binaries that contradict the target.
- Never package `.DS_Store`, VCS data, private keys, credential files, or local environment files.
- Stage a copy and rewrite only the staged manifest. Never mutate source manifests or prepared artifacts during packaging.
- Use fnOS runtime variables such as `TRIM_APPDEST`, `TRIM_PKGETC`, and `TRIM_PKGVAR`. Use `/var/apps/{appname}` only as the stable installed entry point when a variable is unavailable.
- Warn on root privilege and broad network/file exposure. Do not silently downgrade declared privileges.
- Never install an FPK over an installed app. For every redeploy or update, stop the target, uninstall it, verify `status=noinstall`, and only then call `install-fpk`; abort if the uninstall postcondition fails. Treat `--clean` only as a deprecated compatibility flag. Require `--yes` for standalone uninstall.
- Never disable SSH host-key verification or print environment-file contents.
- Use a uniquely named `fpk-skill-smoke-*` package for smoke tests. Never repurpose an existing application as the test fixture.

## Command map

Local FPK operations:

```text
python3 "$SKILL_DIR/scripts/fpk.py" toolchain   Inspect or install the verified fnpack tool
python3 "$SKILL_DIR/scripts/fpk.py" init        Create an official native or Docker project
python3 "$SKILL_DIR/scripts/fpk.py" doctor      Validate host and source package readiness
python3 "$SKILL_DIR/scripts/fpk.py" build       Stage, package, inspect, name, and hash FPKs
python3 "$SKILL_DIR/scripts/fpk.py" inspect     Audit a project directory or final FPK
python3 "$SKILL_DIR/scripts/fpk.py" sources     Show provenance or verify source content hashes
```

Remote fnOS operations:

```text
python3 "$SKILL_DIR/scripts/fnos.py" doctor     Inspect device architecture and CLI versions
python3 "$SKILL_DIR/scripts/fnos.py" deploy     Select, upload, verify, uninstall/reinstall, and verify
python3 "$SKILL_DIR/scripts/fnos.py" status     Query application status
python3 "$SKILL_DIR/scripts/fnos.py" logs       Discover or tail application-owned logs
python3 "$SKILL_DIR/scripts/fnos.py" start      Start an installed application
python3 "$SKILL_DIR/scripts/fnos.py" stop       Stop an installed application
python3 "$SKILL_DIR/scripts/fnos.py" uninstall  Explicitly remove an application
python3 "$SKILL_DIR/scripts/fnos.py" smoke      Build and exercise an isolated disposable package
```

Run a command with `--help` instead of guessing an option. Exit codes are `0` for success, `1` for an operation or validation failure, and `2` for invalid arguments or an unsupported environment.

## Validate changes to this skill

Run the complete local suite:

```bash
python3 -m unittest discover -s tests -v
VALIDATOR="${CODEX_SKILL_CREATOR_DIR:-$HOME/.codex/skills/.system/skill-creator}/scripts/quick_validate.py"
python3 "$VALIDATOR" .
```

When `FNPACK_BIN` points to verified fnpack 1.2.3, the integration suite must build real native and Docker fixtures. For substantial revisions, run the clean-room prompts and rubric in `evals/` with fresh agents and compare them with a no-skill baseline.
