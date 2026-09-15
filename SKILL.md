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
3. Read [references/docker-app-flow.md](references/docker-app-flow.md) before building or debugging a Docker Web FPK that must support AppCenter open/start/stop, port configuration, HTTP access, or lifecycle-managed Docker Compose.
4. Read [references/native-web-port-flow.md](references/native-web-port-flow.md) before building or debugging a native Web FPK whose AppCenter settings wizard changes management/proxy ports and must keep `config_init`、`config_callback`、`ui/config`、AppCenter `config/detail` and runtime listeners in sync.
5. Read [references/vm-app-flow.md](references/vm-app-flow.md) before building or debugging a **libvirt/KVM-backed FPK** (the app creates or manages a virtual machine) or any app with a 7×24 resident entry port that must track a changing VM address. It covers the ~190-second install-callback watchdog and the fast-return pattern, the platform's real lifecycle semantics, VM disk/NIC identity, address discovery gates, serial-console recovery, and reverse-proxy link rewriting.
6. Read [references/offline-and-live-testing.md](references/offline-and-live-testing.md) before writing regression tests for lifecycle/entry logic, before concluding a root cause from device behavior, or when asked to prove a fix with evidence (including screen recordings).
7. Read [references/ci-release.md](references/ci-release.md) before authoring or changing release automation. Start from `assets/github-actions/fpk.yml` when it fits the project.
8. Read [references/public-release-hygiene.md](references/public-release-hygiene.md) before publishing to a public GitHub/FnDepot repository or uploading Release assets.
9. Read [references/remote-testing.md](references/remote-testing.md) before connecting to a device, installing, replacing, rolling back, collecting logs, or running smoke tests.
10. Read [references/appcenter-state-db-runbook.md](references/appcenter-state-db-runbook.md) when AppCenter status, DB URL, `ui/config`, wizard回显, Docker/进程状态, or the Open button disagree.
11. Read [references/temporary-remote-patches.md](references/temporary-remote-patches.md) before applying runtime/debug patches on an installed device, and record rollback instructions.
12. Read [references/security.md](references/security.md) before accepting root privilege, CGI/gateway exposure, secrets, symlinks, or unusual archive content.
13. For final release polish of desktop icons, follow [references/official-contract.md#图标与发布检查](references/official-contract.md) and use `scripts/icon_fit.py --style fnos-squircle`: the official fnOS corner is a continuous-curvature squircle curve (profile embedded in the script, user-verified on a real package on 2026-09-06), NOT a plain circular arc — the legacy `fnos-rounded-dark` (64 `r=20` / 256 `r=80`) looks "rounder" than official and was rejected. **Feed a full-bleed source**: `contain_square()` fits without cropping, so artwork margins show up as an inset tile (re-verified 2026-09-15: output profile matches the official icon exactly — mid-row delta 0, `x=0` opaque rows 125/256 on both). Four corner alphas must be `0`; sync root-level and `ui/images/` (plus `app/ui/images/` if present); validate via profile diff + outline overlay + real desktop-scale comparison; warn about the 7-day `immutable` icon cache on user devices.
14. Read [references/troubleshooting.md](references/troubleshooting.md) only when a command, build, install, start, or validation step fails.

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

---

## Case Study: Docker Web FPK 最终链路

> 完整流程见 [references/docker-app-flow.md](references/docker-app-flow.md)，历史排障见 [references/remote-testing.md#docker-web-应用专项调试记录](references/remote-testing.md)。

### 背景
样例应用是一个 Docker Web 应用 FPK。最终目标不是“能打包”或“容器 healthy”而已，而是 AppCenter 的打开、停止、启动、修改端口、卸载重装全部能传递到 Docker、DB 和 UI 入口。

### 最终有效组合
1. `manifest`: 使用 `platform=x86`、`service_port={port}`、`ctl_stop=true`；不要再生成 deprecated `arch=x86_64`。
2. `config/resource`: 不声明 `docker-project`，只保留必要 data-share；由 `cmd/main` lifecycle 统一管理 Docker Compose。
3. `cmd/main`: `start`/`stop` 管理 Docker；`status` 运行返回 0，停止返回 3。
4. `install_callback`: 调用自身同目录 `main`，payload 目录通过 `TRIM_PKGDIR` 传入，并做立即 + 延迟 DB 同步。
5. `config_callback`: 改端口后同步 `.env`、`docker/.env`、`ui/config`、`app.service_url`、`app_service.url/default_url`，重建容器，并延迟回写 `status=running`。

### 已排除的坑
- 写死 `/vol1/@appcenter/{app}` 会漏掉 `/var/apps/{app}/target` payload。
- `docker-project` resource 在某些实机组合下会让 AppCenter 内置 stop 走错 `/docker/docker-compose.yaml`，导致 AppCenter `stopped` 但容器仍 `Up healthy`。
- `install_callback` 只从 payload 找 `cmd/main` 会造成安装后 AppCenter `running` 但 Docker 仅 `Created`。
- `config_callback` 只同步一次 DB 可能被 AppCenter 后续默认写入覆盖成 `status=start`。

### 交付验证 checklist
1. `fpk.py inspect` 对最终 FPK 返回 `ok=true`，无重复归档成员。
2. 安装后 `appcenter-cli status {app}=running`，Docker `Up healthy`。
3. `http://{host}:{port}/` 返回 200，登录 API 返回 200（不记录 token）。
4. 纯 HTTP 服务的 HTTPS 探测返回 `wrong version number` 时记录为协议预期。
5. AppCenter `stop` 后 Docker 必须停止且 HTTP 不可访问；`start` 后 Docker 必须恢复 healthy。
6. 端口 `{port} → 临时端口 → {port}` 两步闭环：Docker 端口、HTTP health、DB URL、`ui/config` 全部一致。
7. 最终报告记录 FPK SHA-256、源码快照、远端状态、warning 及 root 权限理由。

---

## Case Study: Native Web FPK 端口设置闭环

> 完整流程见 [references/native-web-port-flow.md](references/native-web-port-flow.md)。

样例应用是 native Web 应用，不是 Docker Compose 应用。最终目标是应用设置页修改管理端口/代理端口后，AppCenter 打开入口、`ui/config`、`ports.conf`、AppCenter DB 与实际 `native-engine` 监听端口全部一致。

### 最终有效组合

1. `manifest`: `platform=x86`、`ctl_stop=true`、`service_port=18080`、`desktop_uidir=ui`、`desktop_applaunchname=native-web-demo.app`。
2. `ui/config`: 使用 `.url` + `type=iframe` + `protocol=http` + 管理端口；入口 key 必须等于 `desktop_applaunchname`。
3. `wizard/install` 与 `wizard/config`: 字段统一为 `wizard_access_port`、`wizard_proxy_port`。
4. `cmd/config_init`: 从 `ports.conf` / `ui/config` 读取真实当前端口，输出 wizard 字段，不能依赖固定 `initValue`。
5. `config/privilege`: `run-as=root`，否则真实 AppCenter `config_callback` 无权同步 PostgreSQL 中的 `app.service_url` 与 `app_service.url/default_url`。
6. `cmd/config_callback`: 同步 `ports.conf`、`ui/config`、AppCenter DB，解析真实 `cmd/main` 路径，重启服务，并延迟多次回写 DB `status=running`。
7. `cmd/main`: 端口切换时不要按新 `--adminAddr` 找旧进程；用本应用 `--cachePath ${TRIM_PKGETC}` 匹配 `native-engine` 进程。

### 已排除的坑

- 只手工执行 `wizard_access_port=18081 ... config_callback` 不等于真实飞牛页面链路；必须用 AppCenter UI 或 `POST /app-center/v1/config/wizard` 验证。
- `run-as=package` 会导致服务端口可能变了，但 AppCenter 打开入口仍停旧端口。
- `main stop` 按新端口找进程会杀不掉旧端口进程，表现为 `ports.conf/ui/config/config/detail` 都是新端口，但实际 Web 仍在旧端口。
- `config_callback` 只用 `${TRIM_APPDEST}/cmd/main` 在真实安装结构中可能找不到 main；优先探测 `/var/apps/{app}/cmd/main`。

### 交付验证 checklist

1. 默认安装后：`18080` Web 200，`18443` HTTPS `/v2/` 200，AppCenter `config/detail` 端口 18080。
2. 真实应用设置保存到 `18081/18444` 后：wizard 回显、`ports.conf`、`ui/config`、AppCenter `config/detail`、实际进程监听全部一致。
3. 旧端口 `18080/18443` 必须不再响应，新端口 `18081/18444` 必须响应。
4. 再保存回 `18080/18443` 做反向验证。
5. 最终包完整卸载重装后再跑一次真实 AppCenter 设置链路，不能只依赖覆盖脚本验证。

---

## Case Study: 虚拟机承载型 FPK（两个样例）

> 完整流程见 [references/vm-app-flow.md](references/vm-app-flow.md)，症状索引见 [references/troubleshooting.md#虚拟机承载型应用实机沉淀](references/troubleshooting.md)，验证与取证方法见 [references/offline-and-live-testing.md](references/offline-and-live-testing.md)。

### 背景

两者都是"在飞牛 NAS 上跑一台虚拟机"的 FPK。真正的难点不在打包，而在四件事：安装回调的 190 秒看门狗、应用中心启停记账与虚拟机真实状态背离、虚拟机 DHCP 地址漂移后的可达性、以及拿不到 IP 时用户还能自救。

### 最终有效组合

1. `manifest`：`platform=x86`、`service_port=<入口端口>`、**`checkport=false`**、`ctl_stop=true`、三段式 `version`。
2. `cmd/install_callback`：秒回（校验 + 写状态文件 + 拉起 worker 后 `exit 0`）；worker 放 `app/bin/`，用 `systemd-run` 拉起、加锁文件、`main start` 可按原参数续跑；状态机文件化到 `/tmp/<app>.install.state`。
3. `cmd/main`：`status` 恒 0（作为有意偏离写明理由与取证）；`stop` 发完 ACPI 立即返回，收尾交给 setsid 守望进程并带"开机请求令牌"防竞态；只有虚拟机真在跑时才写用户停用标记；入口侧开机后**后台**补敲 `appcenter-cli start` 扳回记账（60 秒冷却 + 停用标记护栏 + 先证明该调用不重启自身服务）。
4. 磁盘与网卡：qcow2 注册进 libvirt 存储池；"是否已有磁盘"同时看共享目录与池；磁盘版本标记与 MAC 记录写在 `/vol1/vm/<app>.*`（不放池目录）；换版本先把旧盘整体留档到 `backup/`；`qemu-img` 一律 `--output json` 取 `virtual-size`。
5. 入口服务：自带 systemd 单元的常驻进程，多级发现链跑在后台线程且**锁可重入**；就绪判定要求 HTTP 真响应并按"要去的那扇门"分别把关；开机宽限期内不下 DHCP 结论；无 IPv4 时提供经 libvirt 串口的网络修复页。
6. 反代/网关：注入 `no-store` 的经典脚本改写 `window.open` 与 `<a href>`（只改同主机异端口链接）；SPA 走网关前缀必须注入 `basename`。

### 已排除的坑

- `checkport=true` + 常驻入口端口 → 停用后再也启不来（`11000` / `APP_START_FAILED_PORT_USAGE`）。
- 停用窗口让 `status` 返回 3 → 被判 `APP_CRASH` 并自动清理；而平台点「启用」时根本不会回调 `main start`。
- 复用判断只看共享目录 → 重装把用户系统盘刷成出厂（真实数据丢失）。
- 磁盘版本标记放进池目录 → 被目录型池当卷列出；版本选择被复用逻辑静默忽略。
- 重装重新随机生成网卡 MAC → 路由器绑定与租约全废，看起来像"寻踪突然失效"。
- `gunzip` 尾杂散字节实测退出码为 2；写成"非 1 即失败"的守卫会当场静默自杀，且直接 `exit` 不触发 `ERR` trap，状态文件里连 `failed` 都不留。
- 发现锁不可重入 → 端口在听、连接能建、HTTP 永不响应，而 AppCenter 显示"运行中"；离线用例把发现链整条打桩，所以 49/49 全绿仍漏。
- 入口把自家控制路径透传给上游、`meta refresh` 不带 `url=` → 开机后 404 / 不可达。
- 只看 TCP 可连、不按门把关 → 浏览器落到空白错误页；探活失败不降级 → 页面挂着死地址几十分钟。
- 在 OVS 内层口/物理口抓 DHCP 只见请求不见应答 → 观测盲区，不是"网络没有 DHCP"；在 netifd 托管接口跑 `udhcpc -n` → 退出时 `deconfig` 把地址和路由一起冲掉，整机失联。

### 交付验证 checklist

1. `install-fpk` 秒级返回，状态按序推进到 `ready`；`journalctl -u trim_app_center.service` 无 `APP_CRASH` / `PORT_USAGE`。
2. 复用磁盘路径下池内 qcow2 的大小与 mtime 不变（不要整盘哈希）；换版本路径确有 `backup/*swap-*` 留档。
3. 连续"停用 → 打开入口 → 开机"来回不失稳；入口开机后数秒内应用中心记账自动同步为运行中。
4. 默认入口、业务子入口（如 `/ha`）、网络修复页三条链路 + 直连虚拟机端口对照全部通过；无网络场景串口自救可用且不失联。
5. 离线回归全绿，且每条 bug 修复用例能在旧字节上失败；结构不变量（锁可重入、控制路径不透传、禁用命令串）有用例守住。
6. 包内无 `__pycache__`、无设备地址/凭据/token；首启耗时口径在向导、入口页、README 三处一致。
