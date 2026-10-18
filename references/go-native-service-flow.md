# Go 原生服务 FPK 构建与高频迭代发版

适用对象：后端为 Go 二进制、前端为 SPA（Vite/Next 等构建产物）、前端嵌入二进制（`go:embed` 或包内静态目录）的 fnOS 原生应用。此类应用发版频率高（数十至数百个版本），构建-部署-验证必须做成分钟级可重复的流水线，否则迭代成本失控。

本页沉淀自多个真实 Go 原生应用的完整迭代史（含数百个版本的高频迭代应用），全部结论经实机验证。最后更新：2026-09-30。

## 目录

- [标准 build.sh 流水线](#标准-buildsh-流水线)
- [go:embed 前端的两个硬约束](#goembed-前端的两个硬约束)
- [单架构交付（x86 only）](#单架构交付x86-only)
- [部署三态：热替换 / 就地升级 / 卸载重装](#部署三态热替换--就地升级--卸载重装)
- [版本判定：以运行进程为准](#版本判定以运行进程为准)
- [构建环境坑：/tmp 回收与 fnpack 兜底](#构建环境坑tmp-回收与-fnpack-兜底)
- [发版约定：Release 资产与完整性核对](#发版约定release-资产与完整性核对)
- [交付验证清单](#交付验证清单)

## 标准 build.sh 流水线

单一事实源原则：**版本号只写在 `manifest` 里**，build.sh、前端、ldflags 全部从它读取，禁止多处手写版本。

```bash
#!/bin/bash
# 仅 x86_64 交付（按项目约定）
set -e
export COPYFILE_DISABLE=1

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BUILD_DIR="$SCRIPT_DIR/build"
FNOS_DIR="$SCRIPT_DIR/fnos"
FNPACK="$BUILD_DIR/fnpack"

# ── Step 0: fnpack（下载失败时用已验证的本地副本兜底，见下文）──
if [ ! -x "$FNPACK" ]; then
    curl -fsSL "<官方 fnpack 地址>" -o "$FNPACK" && chmod +x "$FNPACK" \
      || { cp <已验证的本地 fnpack 副本路径> "$FNPACK" && chmod +x "$FNPACK"; }
fi

# 版本号从 manifest 读取（单一事实源）
VERSION=$(awk -F'= *' '/^version[ \t]*=/ {gsub(/ /,"",$2); print $2; exit}' "$FNOS_DIR/manifest")
[ -n "$VERSION" ] || { echo "manifest 中未找到 version" >&2; exit 1; }

# ── Step 1: 前端（产物目录必须与 go:embed 或包内 web/ 一致）──
cd "$SCRIPT_DIR/frontend"
npm ci --silent
npm run build          # outDir 指向 ../web/dist 之类
cd "$SCRIPT_DIR"

# ── Step 2: Go 交叉编译（版本注入 + 去符号）──
export PATH=<Go 工具链持久路径>/bin:$PATH
CGO_ENABLED=0 GOOS=linux GOARCH=amd64 \
  go build -ldflags "-s -w -X main.Version=$VERSION" \
  -o "$BUILD_DIR/bin/app-server" ./cmd/server

# ── Step 3: 组装 staging（app.tgz 内平铺：二进制 + web/，无外层前缀）──
STAGE="$BUILD_DIR/stage-app"
rm -rf "$STAGE"; mkdir -p "$STAGE"
cp "$BUILD_DIR/bin/app-server" "$STAGE/"
cp -r "$SCRIPT_DIR/web/dist" "$STAGE/web"

cd "$STAGE" && tar -czf "$BUILD_DIR/app.tgz" . && cd -
# 用构建器（fpk.py build 或 fnpack pack）按 manifest checksum 打包

# ── Step 4: 产物命名 + sha256 ──
cp <final.fpk> "$SCRIPT_DIR/app_${VERSION}_x86.fpk"
sha256sum "$SCRIPT_DIR/app_${VERSION}_x86.fpk"
```

要点：

- `CGO_ENABLED=0` 静态链接，目标机无需 Go 运行时。
- `-ldflags -X main.Version` 注入版本号，运行时经 HTTP 端点（如 `/api/version`）回显——这是部署后验证版本的唯一可靠通道（见[版本判定](#版本判定以运行进程为准)）。
- `app.tgz` 必须在**内容目录内部**执行 `tar -czf`（成员为 `./app-server`、`./web/…`）。带外层前缀（成员为 `app/app-server`）会导致安装后文件落错位置（平台解压会去掉一层前缀），典型症状是服务找不到二进制/入口页 404。
- 前端产物目录名（`web/`、`dist/`）必须与生命周期脚本和入口配置引用的一致，三处同步。

## go:embed 前端的两个硬约束

1. **前端改动必须整包重建**：`go:embed` 把前端产物编译进二进制，部署后修改磁盘上的 `web/` 目录对已嵌入内容无效。判断「这次改动要不要重跑前端构建」的规则：`frontend/` 有 diff → 全量 build.sh；只有 `*.go` 有 diff → 可走快速路径（只交叉编译 + 重打 FPK，跳过 `npm ci`，省 1-3 分钟）。
2. **前端嵌入与包内 `web/` 目录二选一**：若前端是独立目录由服务器读取（非 embed），热替换时必须**同时**替换二进制和 `web/` 目录，只换二进制会让新后端读旧前端（或反之），产生「版本对了一半」的诡异状态。

## 单架构交付（x86 only）

用户/项目约定只出 x86 版时：

- `manifest` 写 `platform=x86`。
- 构建只跑 `GOARCH=amd64`。
- 发布侧（Release）只挂 `*_x86.fpk` 资产；商店索引侧同样只声明 x86 平台（协议细节见 fpk-build-to-fndepot 技能），arm 设备端不显示该应用（这是声明生效，不是 bug）。
- **严禁**顺手多打一个 arm 包「以防万一」——未验证的 arm 产物（尤其依赖架构特定二进制时）上线是事故源；约定变更必须显式决策。

## 部署三态：热替换 / 就地升级 / 卸载重装

三种部署路径语义完全不同，选错会丢数据或假成功：

### ① 二进制热替换（开发迭代，分钟级）

适用：同一应用名内的高频迭代，数据在 `@appdata`，不需要平台 DB 登记变更。

```text
1. 从进程表找真实 pid（ss -tlnp 按应用端口），SIGTERM → 等待真正退出
   （pid 文件不可信：平台 status 可能失准，旧进程 PPID=1 无监管还活着）
2. 替换部署目录中的二进制（运行中的二进制不能 cp 覆盖：text file busy；
   先停进程再 copy，或用 mv 原子替换）+ 同步 web/ 目录
3. 用应用自己的生命周期脚本拉起（必须带完整 TRIM_* 环境：
   TRIM_APPNAME / TRIM_APPDEST / TRIM_PKGVAR / TRIM_SERVICE_PORT）
4. 验证：/api/version 返回目标版本 + 关键 API 冒烟
```

关键坑：

- **`install-fpk` 对已安装应用是 no-op**：走「已安装」跳过分支，rc=0 但二进制没换、版本不升、DB 元数据不更新——「覆盖安装报成功」是假成功。热替换永远走 停→换→拉起。
- 手动 kill 后平台**不会自动 respawn**；残留进程占端口导致平台 start 报 `APP_AUTO_START_FAILED_PORT_USAGE`（用户看到「点启动没反应」）。杀进程后先确认端口释放再拉起。
- 平台 `stop` 可能假成功（报停止但进程还在）——判活一律看真实进程（`/proc/<pid>`、端口持有者），不信任 status 输出。

### ② daemon RPC 就地升级（正式 FPK 换版，保数据）

适用：正式版本交付，需要平台 DB 登记新版本、保持用户数据（`@appdata`）完整。

```text
1. 升级前记数据指纹：config 文件 sha256 + 数据目录项数
2. 新 FPK 暂存（放持久目录，勿放 /tmp）→ 升级前检查（canUpgrade）
   （同版本升级会被拒：目标版本与已装相同时必须先 bump 版本）
3. 向 appcenter daemon 的私有 unix socket 提交升级任务
   （/var/run/com.trim.app.center.sock 的 /rpc/v1/update/task，
   即官方 Web UI「升级」走的同一条路）
4. daemon 执行 停应用→换包→自动恢复
5. 全量验证：DB 登记版本 + 数据指纹前后一致 + 进程 RUNNING + /api/version
```

关键坑：

- **CLI 的「升级」= 卸载 + 重装，会毁掉 `@appdata`**——绝对不要用 `appcenter-cli` 做正式升级。
- **daemon 换包后不一定重启进程**：升级任务报完成、DB 已登记新版，但运行进程可能仍是旧版（磁盘二进制已换、进程未加载）。`/api/version` 仍旧版 → 精确 pid 直杀旧进程 + 平台 start 拉起。
- `/rpc/v1` 是非契约接口，fnOS 大版本可能变，用前探测。

### ③ 完整卸载重装（改注册信息才用）

改端口、改桌面入口、改 manifest 注册字段时，热替换和就地升级都不更新平台 DB 注册——必须完整卸载重装。注意卸载可能清数据（按应用类型而异），先备份。

## 版本判定：以运行进程为准

- 平台面板显示的版本可能**滞后**于实际二进制（元数据缓存/换包未重启），出现「面板显示旧版、实际跑新版」是已知现象。
- 唯一判据：`curl -s <入口>/api/version`（或等价端点）返回的版本 = 构建注入的版本。
- 磁盘二进制 sha256 与 FPK 产物 sha256 交叉核对，可确认「磁盘是否已是新版」——但磁盘新版 + 进程旧版 = 仍需重启。

## 构建环境坑：/tmp 回收与 fnpack 兜底

- **fnOS 的 `/tmp` 是 tmpfs 且被持续回收**（内存压力下整目录消失）：Go 工具链、fnpack、构建产物、部署暂存一律放持久卷路径（如数据卷下的专用目录）。典型事故：工具链从 `/tmp` 消失 → 构建中断 → 重装。
- **官方 fnpack 下载 URL 间歇 404**（尤其非 x86 主机构建机）：流水线里保留「下载失败 → 用本地已验证副本」兜底分支；本地副本必须与 provenance 登记的版本+哈希一致，不得静默降级到未知版本。
- 构建-部署整链尽量合成**单条原子命令**执行（一次 SSH 会话内完成 解包→换二进制→拉起→验证），减少中间状态暴露在 tmpfs 回收窗口。
- scp 可能静默失败（无输出 rc=0）：传输后必须比对两端 sha256。
- **源树必须进版本管理（git）**：高频迭代项目的真实事故链——源树非 git 管理 + 数据卷磁盘损坏/构建目录被回收 → 当天改动只存在于工作区，无法找回，只能凭二进制/产物反推。「源码改动当天就提交」是硬纪律，哪怕仓库 private。
- **前端构建环境要可复现**：node/npm 版本钉住（`package.json` engines 或 `.nvmrc`），`node_modules` 用 `npm ci`（lockfile 精确安装）而非 `npm install`；构建机访问 npm registry 不稳时配置内网/公共镜像 registry 兜底（`npm ci --registry=…` 或 `.npmrc`）。前端产物哈希是「前端是否变了」的判据，环境漂移会导致同源码不同产物。

## 发版约定：Release 资产与完整性核对

高频迭代项目的稳定发版形态（GitHub 托管时）：

- 轻量 tag（`vX.Y.Z`）触发 CI 自动建 Release，挂裸二进制资产；**FPK 手动补挂**（CI 不打包 FPK 时）。
- FPK 资产上传注意：
  - 必须走 release 的 `upload_url`（指向 uploads.github.com）；旧的 `api.github.com/.../assets` POST 路径已迁移，恒 404。
  - 资产名带 `?name=` 参数指定文件名（`app_0.x.y_x86.fpk` 风格，架构后缀显式）。
  - 新资产约 60s CDN 传播延迟，期间 404 属正常；持续 404 才查仓库可见性/token 权限。
- **完整性核对免下载**：GitHub 侧 asset digest 与本地 sha256 比对一致即通过，不需要把 FPK 再下回来验。
- 交付给用户时附 sha256 尾行（用户侧校验用）；changelog 用「单行加粗亮点 + 一句说明」的紧凑格式。
- 推送必须等用户明确指令；多版本积压时一次指令下一批推齐，tag 对齐各版本 FPK 实际构建时的代码树（构建晚于 tag 的 FPK 不补挂，避免张冠李戴）。

## 交付验证清单

每个版本交付前：

1. 后端测试全绿（含 `-race`）+ 前端类型检查（tsc）通过。
2. `build.sh` 产物存在，manifest 版本 = 二进制 `/api/version` = 产物文件名版本（三处一致）。
3. 部署后：运行进程 `/api/version` 返回目标版本（不信面板显示）。
4. 关键 API 冒烟（列表/核心写操作）通过；UI 入口页 200。
5. FPK sha256 已报告；正式换版路径（就地升级）数据指纹前后一致。
6. 仅改 Go 的版本确认跳过了前端重建且前端产物哈希未变（防止误带旧前端）。
