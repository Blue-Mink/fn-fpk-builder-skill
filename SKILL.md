---
name: fn-fpk-builder-skill
description: Build, validate, inspect, release, install, replace, troubleshoot, and safely remove fnOS/飞牛 OS FPK application packages. Use when an agent works with fnpack, appcenter-cli, .fpk archives, fnOS manifests, native or Docker app templates, x86_64/ARM64 packaging, GitHub Actions release pipelines, or SSH-based deployment and smoke testing on a fnOS device.
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
3. Read [references/docker-app-flow.md](references/docker-app-flow.md) before building or debugging a Docker Web FPK that must support AppCenter open/start/stop, port configuration, HTTP access, or lifecycle-managed Docker Compose.
4. Read [references/native-web-port-flow.md](references/native-web-port-flow.md) before building or debugging a native Web FPK whose AppCenter settings wizard changes management/proxy ports and must keep `config_init`、`config_callback`、`ui/config`、AppCenter `config/detail` and runtime listeners in sync.
5. Read [references/vm-app-flow.md](references/vm-app-flow.md) before building or debugging a **libvirt/KVM-backed FPK** (the app creates or manages a virtual machine) or any app with a 7×24 resident entry port that must track a changing VM address. It covers the ~190-second install-callback watchdog and the fast-return pattern, the platform's real lifecycle semantics, VM disk/NIC identity, address discovery gates, serial-console recovery, and reverse-proxy link rewriting.
6. Read [references/offline-and-live-testing.md](references/offline-and-live-testing.md) before writing regression tests for lifecycle/entry logic, before concluding a root cause from device behavior, or when asked to prove a fix with evidence (including screen recordings).
7. Read [references/ci-release.md](references/ci-release.md) before authoring or changing release automation. Start from `assets/github-actions/fpk.yml` when it fits the project.
8. Read [references/public-release-hygiene.md](references/public-release-hygiene.md) before publishing to a public GitHub/FnDepot repository or uploading Release assets.
9. Read [references/remote-testing.md](references/remote-testing.md) before connecting to a device, installing, replacing, rolling back, collecting logs, or running smoke tests.
10. Read [references/install-runbook.md](references/install-runbook.md) when repeatedly test-installing an app on one device — the concrete `stop → uninstall → install → start` cycle, what each step really prints, backup duties per app type, five-dimension post-install verification, rollback, and the error-code table. Its placeholders are `<NAS_IP>` / `<app>` / `<port>`; keep it desensitized. For the executable version use `assets/install-runbook/reinstall.sh` (parameterized, wizard/env preflight, uninstall postcondition gate, `DRY_RUN`).
11. Read [references/appcenter-state-db-runbook.md](references/appcenter-state-db-runbook.md) when AppCenter status, DB URL, `ui/config`, wizard回显, Docker/进程状态, or the Open button disagree.
12. Read [references/temporary-remote-patches.md](references/temporary-remote-patches.md) before applying runtime/debug patches on an installed device, and record rollback instructions.
13. Read [references/security.md](references/security.md) before accepting root privilege, CGI/gateway exposure, secrets, symlinks, or unusual archive content.
14. For final release polish of desktop icons, follow [references/official-contract.md#图标与发布检查](references/official-contract.md) and use `scripts/icon_fit.py --style fnos-squircle`: the official fnOS corner is a continuous-curvature squircle curve (profile embedded in the script, user-verified on a real package on 2026-09-06), NOT a plain circular arc — the legacy `fnos-rounded-dark` (64 `r=20` / 256 `r=80`) looks "rounder" than official and was rejected. **Feed a full-bleed source**: `contain_square()` fits without cropping, so artwork margins show up as an inset tile (re-verified 2026-09-15: output profile matches the official icon exactly — mid-row delta 0, `x=0` opaque rows 125/256 on both). Four corner alphas must be `0`; sync root-level and `ui/images/` (plus `app/ui/images/` if present); validate via profile diff + outline overlay + real desktop-scale comparison; warn about the 7-day `immutable` icon cache on user devices.
15. Read [references/entry-icon-design.md](references/entry-icon-design.md) when designing or redesigning the **artwork of an entry icon itself** — especially thin-line, letter-derived, or transparent-background marks that must stay readable at ~52px — before touching pixels: sketch-to-spec verification, stroke hierarchy, per-slot optical redraw, dual light/dark inks, and the 64/32/16 gates.
16. Read [references/troubleshooting.md](references/troubleshooting.md) only when a command, build, install, start, or validation step fails.

17. For a worked end-to-end example in one of the three app classes (Docker Web, native Web port loop, VM-backed), read `references/case-studies.md` — the delivered chain, the dead ends that were ruled out, and the acceptance checklist.

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
- Never run long work inside the install callback. The platform kills callbacks after roughly 190 seconds (and the CLI itself segfaults); use the fast-return pattern with a detached worker and a file-based state machine — see [references/vm-app-flow.md](references/vm-app-flow.md).
- For resident-entry or daemon-style apps: set `checkport=false`, keep `main status` at `0`, and treat that as a **documented deviation** from the official status contract with the evidence written into the README and the release report. Never block inside `main stop`; hand the shutdown follow-up to a detached watcher that re-checks a power-on token before forcing power-off.
- Never assume "uninstall keeps data" or "uninstall wipes everything": the outcome differs by app type (VM disk lives in the libvirt pool and survives; Docker images get removed; some native apps lose `@appconf`). Check every location when deciding "does a disk/config already exist", and archive the old disk before switching VM image versions.
- When testing lifecycle or entry-redirect logic, never stub out the function under test. A test suite that replaces the discovery chain wholesale passed 49/49 while shipping a self-deadlock. Every regression test for a fixed bug must fail against the old bytes.
- Do not report a root cause from vibes. Back it with call-trace logging in `cmd/main` (argument + parent PID), named platform journal events, and state-file timestamps; measure responsiveness with before/after latency probes.
- Keep this Skill's own references desensitized: no device addresses, SSH accounts, credentials, tokens, private workspace paths, or artifact hashes of unpublished builds. Write placeholders as `<NAS_IP>` / `<app>` / `<port>`, and refer to sample apps by category ("a router-image FPK"), never by their real names.
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
python3 "$SKILL_DIR/scripts/fnos.py" verify-web-app  Collect AppCenter/DB/runtime/HTTP evidence for one Web app in a single pass
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
