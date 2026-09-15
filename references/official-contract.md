# fnOS FPK 官方契约

本页是实现和审查 fnOS 应用包时的规范基线。除特别标注为“实测”外，内容均来自飞牛开发者文档。官方文档与本页冲突时，以官方最新文档为准；运行设备的偶然目录、旧版工具帮助或某个项目的做法不能覆盖官方契约。

## 目录

- [来源与适用范围](#来源与适用范围)
- [包目录和安装目录](#包目录和安装目录)
- [Manifest](#manifest)
- [生命周期和状态码](#生命周期和状态码)
- [环境变量、权限与资源](#环境变量权限与资源)
- [用户入口](#用户入口)
- [向导、依赖和运行时](#向导依赖和运行时)
- [图标与发布检查](#图标与发布检查)
- [CLI 契约](#cli-契约)
- [实测的 FPK 容器结构](#实测的-fpk-容器结构)

## 来源与适用范围

官方机器可读版本：

- `https://developer.fnnas.com/llms-full.txt`
- `https://developer.fnnas.com/llms.txt`

需要同时核对官方文档源码的开发者，可以通过 `FNNAS_DOCS_ROOT` 指向自己的本地文档仓库；Skill 只记录仓库内相对路径，不记录个人文件系统位置。

重点原文：

- `docs/core-concepts/framework.md`
- `docs/core-concepts/manifest.md`
- `docs/core-concepts/environment-variables.md`
- `docs/core-concepts/privilege.md`
- `docs/core-concepts/resource.md`
- `docs/core-concepts/app-entry.md`
- `docs/core-concepts/index-cgi.md`
- `docs/core-concepts/gateway-registration.md`
- `docs/core-concepts/wizard.md`
- `docs/core-concepts/dependency.md`
- `docs/core-concepts/runtime.md`
- `docs/core-concepts/icon.md`
- `docs/cli/fnpack.md`
- `docs/cli/appcentercli.md`

## 包目录和安装目录

fnpack 项目至少包含：

```text
app/
cmd/
config/
  privilege
  resource
wizard/
manifest
ICON.PNG
ICON_256.PNG
```

声明 `desktop_uidir` 时，`app/{desktop_uidir}/` 必须存在。UI 目录通常是 `app/ui/`，其中可包含 `config`、`index.cgi` 和 `images/`。

安装后使用 `/var/apps/{appname}` 作为稳定入口：

```text
/var/apps/{appname}/target  -> 实际应用文件
/var/apps/{appname}/etc     -> 持久配置
/var/apps/{appname}/var     -> 持久运行数据
/var/apps/{appname}/tmp     -> 临时数据
/var/apps/{appname}/home    -> 应用用户数据
/var/apps/{appname}/...     -> 系统为已声明共享目录建立访问链接
```

官方页面分别出现过 `share/` 与 `shares/` 表述；应用不要依赖该拼写差异。共享数据优先使用 `TRIM_DATA_SHARE_PATHS`，普通应用路径优先使用其他 `TRIM_*` 变量。不要把 `/volN/@appcenter`、`/usr/local/apps/@appcenter` 等某台设备上的物理位置写入应用逻辑。

## Manifest

基础字段：

- `appname`：稳定且唯一的应用 ID。当前实机 appcenter 约束长度为 3–32；官方文档未明确写出该限制，因此将它标记为设备观测约束并在目标设备复核。
- `version`：应用版本。
- `display_name`：用户可见名称。
- `desc`：应用描述。
- `source`：第三方应用使用 `thirdparty`。
- `maintainer`、`maintainer_url`：维护者信息。
- `distributor`、`distributor_url`：发布者与维护者不同时填写。

兼容和平台字段：

- `platform=x86`：x86 设备。
- `platform=arm`：ARM 设备。
- `platform=all`：仅用于不包含特定架构二进制的包。
- `os_min_version`、`os_max_version`：只声明真实测试过的范围。
- 旧 `arch` 字段已经废弃，不应再生成。

运行和入口字段：

- `ctl_stop=true|false`：是否显示启动、停止及状态控制。
- `service_port`：固定服务端口。
- `checkport=true|false`：启动前是否检查端口。**入口/守护常驻型应用必须 `false`**：这类应用的端口按设计 7×24 在线，`true` 会让「停用后再启用」必然失败——`appcenter-cli start` 报 `error code 11000`，journal 里是 `APP_START_FAILED_PORT_USAGE PORT_USAGE:<port>`，随后被判 `APP_CRASH`（虚拟机应用样例实测，改 `false` 后停用/启用彻底可用）。
- `version`：应用版本。**新版 fnpack 要求 `x.y.z` 三段式**，两段（如 `18.2`）会被拒；技能锁定的 1.2.3 接受两段。为兼容两套工具链，发布用三段式版本号。
- `desktop_uidir`：相对于 `app/` 的 UI 目录，默认 `ui`。
- `desktop_applaunchname`：应用卡片应打开的入口 ID。
- `disable_authorization_path`：是否隐藏授权目录设置。
- `install_type=root`：安装到系统分区；空值由用户选择存储位置。
- `install_dep_apps`：冒号分隔的直接依赖，可用 `name>version` 声明最低版本。
- `changelog`：面向用户的更新说明。

解析 manifest 时不要把它直接 `source` 到 shell。值可能有引号、空格或多行内容；解析器应保存原值边界，并对重复字段、缺失字段和非法平台给出明确错误。还要校验系统版本格式与上下限顺序、`name>version` 依赖表达式、1–65535 端口、布尔字段、`install_type` 以及不能逃出 `app/` 的 `desktop_uidir`。

## 生命周期和状态码

`cmd/` 可包含：

- `install_init`、`install_callback`
- `upgrade_init`、`upgrade_callback`（这些是官方包结构名称；本 Skill 的实机更新仍固定执行卸载后安装，不调用原位升级路径）
- `uninstall_init`、`uninstall_callback`
- `config_init`、`config_callback`
- `main`

生命周期脚本需要尽量幂等。失败前把简短、可执行的用户提示写入 `TRIM_TEMP_LOGFILE`，同时返回非零状态。

### 卸载到底删了什么（实测按应用类型分裂）

不要在 README 里写"卸载不丢数据"或"卸载会清干净"这类绝对结论——它取决于应用形态与数据落点，且都曾错过：

| 应用形态 | 实测卸载行为 | 应对 |
| --- | --- | --- |
| 虚拟机承载型 | 只 `destroy` + `undefine --nvram`；qcow2 在 libvirt 存储池里，**不会被删**；被删的只有 UEFI 变量文件（缺了从 `/usr/share/OVMF/OVMF_VARS.fd` 复制） | README 写"磁盘保留、重装复用"，并给出彻底清除命令 |
| Docker 承载型 | 平台卸载会连**镜像**一起删 | 需要保留数据卷要在 `resource` 声明共享目录，并提示备份 |
| 某些 native 应用 | 卸载会**清空 `@appconf`**（引擎真正的配置目录，`--cachePath` 指这里），重装后配置全回内置默认 | 升级/换包流程里强制先备份配置目录 |

另外：卸载向导若没有传"删除数据"字段，生命周期脚本里那段删除分支就永远不会执行——写脚本时不要把希望寄托在一个包里根本不存在的向导字段上。

`cmd/main` 接收 `start`、`stop`、`status`：

- `start`/`stop` 成功返回 `0`，失败返回 `1`。
- `status` 返回 `0` 表示运行中，返回 `3` 表示未运行。
- 不认识的动作返回 `1`。

状态检查应验证真正代表可用性的进程、PID 或容器，而不是无条件返回成功。PID 文件需要防止陈旧 PID 和 PID 复用误判。

### 实测修正：平台实际只回调 `stop`

真机留痕（在 `cmd/main` 里记录 `arg` 与 `ppid`）证明：**点「启用」时平台只轮询 `status`，不会回调 `main start`**。因此两条常见设计都不成立，必须避开：

- 「停用期间让 `status` 返回 3，好让平台回调 `start`」——`start` 永远不会来。
- 停用窗口内 `status` 返回 3 会被判 `APP_CRASH`，之后平台不再补开机，应用卡在异常态。

对**常驻入口型 / 守护型应用**（自己起 systemd 服务、端口长期在线、真正的重活在虚拟机或容器里），实测结论是 `status` 恒返回 `0`，开关机语义交给应用自己的入口页处理；这是对官方契约的**有意偏离**，必须在 README 与发布报告里写明理由与取证依据。完整时序与护栏见 [vm-app-flow.md](vm-app-flow.md#平台生命周期真机语义)。

同类实测：`appcenter-cli start` 对已在运行的应用报 `error 10500`（平台在 install 后已自动 `APP_STARTED`），不是故障，不要重试。

## 环境变量、权限与资源

常用路径变量：

- `TRIM_APPDEST`、`TRIM_PKGETC`、`TRIM_PKGVAR`
- `TRIM_PKGTMP`、`TRIM_PKGHOME`、`TRIM_PKGMETA`
- `TRIM_APPDEST_VOL`

应用与系统上下文：

- `TRIM_APPNAME`、`TRIM_APPVER`、`TRIM_OLD_APPVER`、`TRIM_APP_STATUS`
- `TRIM_SYS_VERSION`、`TRIM_SYS_ARCH`、`TRIM_KERNEL_VERSION`
- `TRIM_SERVICE_PORT`
- `TRIM_DATA_SHARE_PATHS`、`TRIM_DATA_ACCESSIBLE_PATHS`
- `TRIM_TEMP_LOGFILE`、`TRIM_TEMP_UPGRADE_FOLDER`、`TRIM_PKGINST_TEMP_DIR`

权限默认使用专用包用户：

```json
{
  "defaults": {"run-as": "package"},
  "username": "myapp",
  "groupname": "myapp"
}
```

仅在明确需要时加入 `join-groups`。`run-as=root` 应触发高风险提示；若特权仅用于初始化，应在启动长期服务前降权。

`config/resource` 可声明：

- `data-share`：由系统创建并通过 Windows ACL 授权的共享目录。
- `usr-local-linker`：把稳定的 bin、lib、etc 接口链接到 `/usr/local`。
- `docker-project`：指向 `app/` 下含 `docker-compose.yaml` 的项目目录。

只声明应用实际需要的资源，资源名应跨版本稳定。

## 用户入口

端口入口用于独立服务；它不继承 NAS 登录态。入口的 `port` 应与 `manifest.service_port` 及服务监听保持一致。

CGI 入口：

- 常用路径为 `/cgi/ThirdParty/{appname}/index.cgi/`。
- 系统在调用 CGI 前检查 NAS 登录态。
- `protocol` 和 `port` 对 CGI 路由无效。
- 不支持 WebSocket，不适合长请求、高流量 API 或常驻服务。

统一网关：

- `gatewayPrefix` 使用 `/app/{appname}` 或其稳定子路径。
- `gatewaySocket` 只写 socket 文件名；服务在 `${TRIM_APPDEST}` 创建它。
- 系统验证会话后转发到 Unix Socket。
- 身份 Header 为 `X-Trim-Userid`、`X-Trim-Isadmin`、`X-Trim-Username`。
- 网关只提供已登录身份，业务权限仍由应用验证。
- HTTP 和 WebSocket 路由都应保持在声明的前缀下。

`app/ui/config` 是 JSON。入口 ID 使用 appname 前缀，并与 `desktop_applaunchname` 对齐。文件打开入口收到的 `path` 查询参数仍是不可信输入。

### 多桌面图标入口（`ui/config` 的 `.url` 表）

一个应用可以在飞牛桌面注册**多个图标入口**：`ui/config` 形如 `{".url": {"<appname>.<EntryID>": {...}, ...}}`，每个键对应桌面一个图标。平台在**安装/升级时自动导入** appcenter 库 `app_service` 表（实测：某三入口应用与某双入口应用均自导入，无需 psql INSERT）；卸载自动删行，重装自动 upsert。

条目字段：`title`、`desc`、`icon`（`images/ICON.PNG` 或 `images/icon_{0}.png`，后者要求 64/256 文件都在）、`type`（`iframe` | `url`）、`protocol` + `port` + `url`、`noDisplay`（`false` 才显示）、`allUsers`、`fileTypes`、`control.accessPerm`。

`type=url` + `port` 拼出 `http://${host}:{port}{path}` **顶层打开**（新窗口）；`type=iframe` 在飞牛窗口内嵌。选型要点（2026-09-13 门户类应用实测教训）：

- 目标是**明文 http 服务端口**时**必须用 `type=url` 直连**——用户经 https 访问飞牛桌面时，iframe 内嵌 http 会被按混合内容拦截（症状：入口“局域网打不开”/空白）；顶层导航不受此限制。
- `type=iframe` 适合同源 CGI 路径（`/cgi/ThirdParty/<app>/index.cgi/...`），窗口标题栏跟随页面 `document.title`（同源可读）；跨源 iframe 读不到 title，保持入口 `title`。
- `url` 支持 query（如 `/cgi/.../index.cgi/?apps=1`），参数原样到达应用自带 web 服务，可用它做“同一服务、不同参数=不同入口”的定制页（例：`?apps=1` 全屏应用图标页）；多入口也**不必各开端口**，同端口不同路径即可（某虚拟机应用的双入口共用同一入口端口的 `/` 与 `/ha` 两条路径）。

安装后核对导入结果：

```bash
psql -h /var/run/postgresql -d appcenter -U postgres -tAc \
  "SELECT s.service_name, s.title, s.url FROM app_service s, app a WHERE a.id=s.app_id AND a.app_name='<appname>'"
```

## 向导、依赖和运行时

四种向导文件为 `install`、`upgrade`、`uninstall`、`config`，内容都是步骤数组。字段值会作为同名环境变量交给生命周期脚本。

- 自定义字段建议使用 `wizard_` 前缀。
- 不要使用系统保留的 `TRIM_` 前缀。
- 密钥使用 `password` 类型，但脚本仍不得记录该值。
- UI 校验不是安全边界，生命周期脚本必须再次校验。

`install_dep_apps` 的多个依赖按从右到左的顺序安装和启用，且不会递归补齐嵌套依赖。当前应用直接需要的依赖应全部显式声明。

官方运行时示例包括 `python312`、`nodejs_v22`、`java-21-openjdk`。调用前将对应 `/var/apps/{runtime}/target/bin` 加入 `PATH`，并在干净设备验证依赖确实可安装。

## 图标与发布检查

- `ICON.PNG`：64×64。
- `ICON_256.PNG`：256×256。
- PNG 或 JPG、sRGB、单文件不超过 1024 KB。
- 入口使用 `images/icon_{0}.png` 时，相应 64 和 256 图标必须存在。
- 对深色满底、方形背景明显的应用图标，圆角**必须**用官方 squircle 曲线方式：`icon_fit.py --source <art> --out-root <pkg> --style fnos-squircle`。飞牛官方桌面图标的角是**连续曲率曲线（squircle），不是纯圆弧**——同等半径（约画布宽 24.8%）下纯圆弧会显得“更圆”。2026-09-06 一个深色满底图标先按纯圆弧交付，被以“圆角太圆了”退回，改为 1:1 复刻官方曲线后确认通过（“就这个图标的圆角风格”）。旧的纯圆弧参数（64 `r=20` / 256 `r=80`）仅保留为 legacy `fnos-rounded-dark` 风格，不作为默认。
- 官方角剖面已内置于 `scripts/icon_fit.py`（`OFFICIAL_LEFT_PROFILE_224`：224×224 画布左缘剖面，索引=行 y、值=该行最左不透明 x，覆盖左上与左下角弧；2026-09-06 实测 trim.file-manager / trim.app-center / trim.setting / trim.docker / trim.download-center / trim.resource-manager 六个官方图标剖面完全一致，水平对称性已验证）。预渲染 mask 在 `assets/icon/official-squircle-mask-512.png`，可直接用作 alpha 或对照基准。
- 需要从实机重新提取时（防官方换风格）：官方桌面图标是前端静态资源、不走 serviceicon，地址 `http://<nas>:5666/static/app/icons/trim.file-manager/icon.png?size=256`（实际返回 224×224 满幅 RGBA）。逐行取 `alpha>128` 的最左 x 得左缘剖面，替换 `OFFICIAL_LEFT_PROFILE_224` 即可。
- **源图必须满幅（full-bleed）**：`icon_fit.py` 的 `contain_square()` 用 `thumbnail()` 等比放进画布、**不裁剪**，源图自带的白边或透明边距会原样带进结果，表现为"圆角是对的、整块图案却内缩"。这曾被误判成脚本的内缩 bug。判别与验证法（2026-09-15 复测：满幅 512 纯色方图 + 设备真官方图标 `trim.file-manager` 对照）——`--style fnos-squircle` 的输出与官方左缘剖面**中段行差 0、全行差 ≤1**（LANCZOS 抗锯齿边缘属预期），`x=0` 列不透明行数 `125/256` 与官方 `125/256` 相同，四角 20×20 平均 alpha ≈0。**若你的输出左边距 ≥2px，先量源图的内容包围盒，不要改脚本。**
- 需要一条已实机验收的现成曲线基准时，可直接取验收通过图标的 alpha 通道（`Image.open(x).getchannel("A")`）贴到满幅素材上；技能自带 `assets/icon/official-squircle-mask-512.png`（mode `L`，四角为 0），是同一条官方曲线的 512 版本，可作对照与剖面参照。
- 圆角验证不能只看 alpha 数据，至少做三项：① 左缘剖面与官方剖面逐行对比（LANCZOS 抗锯齿会让首/末行的阈值边缘外扩几像素，属预期；中间行最大差应 ≤2px）；② 轮廓叠加：两图标 alpha 边界分用红/绿描边叠到同一画布，四角应基本重合；③ 从同一张飞牛桌面截图裁出两个磁贴（实际 48px 尺寸放大 ×6）并排目检。
- 圆角处理后同步覆盖根级 `ICON.PNG`、`ICON_256.PNG`，以及 UI 入口图标 `ui/images/icon_64.png`、`ui/images/icon_256.png`、`ui/images/icon_0.png`、`ui/images/icon_0_256.png`（有 GIF 动图版本时同步）；若项目同时维护 `app/ui/images/`，也要同步覆盖，避免源码、payload 和 AppCenter 桌面入口不一致。
- 换图标后必须提醒用户：飞牛桌面图标 URL 带 `Cache-Control: max-age=604800 immutable`（7 天不可变缓存），已缓存设备会继续显示旧图标，需浏览器强刷（Ctrl+Shift+R）或 App 重新登录才可见新版。验证部署时以服务端 `md5sum /vol1/@appcenter/<app>/ui/images/icon_0_256.png` 为准，不要以用户端截图为准。

发布前至少覆盖首次安装、卸载后安装新版本、启动、停止、重启、卸载保留/删除数据、权限拒绝、依赖不可用、资源不足及所有声明架构。

## CLI 契约

官方 fnpack 1.2.3 文档入口：

```bash
fnpack create <appname>
fnpack create <appname> --template docker
fnpack create <appname> --without-ui true
fnpack build
fnpack build --directory <path>
```

1.2.3 二进制实测还接受 `build -d <path>`，但自动化应优先使用帮助中确认的参数，不能把旧 fnOS 设备上的 fnpack 1.0.0 当成发布构建器。

官方 appcenter-cli 文档入口：

```bash
appcenter-cli install-fpk myapp.fpk
appcenter-cli install-fpk myapp.fpk --env config.env
appcenter-cli install-local
appcenter-cli default-volume
appcenter-cli list
appcenter-cli start myapp
appcenter-cli stop myapp
```

不同设备版本的帮助文本可能不完整。远程自动化应先执行只读探测，再调用已验证的子命令，并保留原始输出。

实测补充：

- `--env` 接收的是**环境变量文件路径**（每行 `KEY=VALUE`），不是 `key=value` 字符串。传字符串会得到 `[Error]Something wrong with environment variables.`（内部报 `error while binding environment variable`）。重装时这份 env 必须照抄首次装机那份，否则会静默改掉向导值（内存、磁盘、端口）。
- 还可用（设备观测）：`appcenter-cli status <app>`、`appcenter-cli uninstall <app>`、`appcenter-cli install-fpk <fpk> --volume <N> --env <file>`。
- `appcenter-cli` 在设备上位于 `/usr/local/bin/`（PATH 可直接调用），且**必须以 root 运行**：非 root 会 `panic: ApplyPermission … dial unix /run/trim_cgi/rpcbroker: permission denied`。
- `start` 对已在运行的应用报 `error 10500`（平台在 install 后已自动 `APP_STARTED`），属预期，不要重试。
- 安装回调有约 **190 秒**看门狗，超时会把回调斩首并让 `install-fpk` 自身段错误。重活必须走"秒回 + 后台 worker"，见 [vm-app-flow.md](vm-app-flow.md#190-秒安装回调看门狗与秒回模式)。

## 实测的 FPK 容器结构

以下是对 fnpack 1.2.3 产物的观察，不是文档承诺：

- `.fpk` 是 gzip 压缩 tar。
- 外层包含 `manifest`、`app.tgz`、`cmd/`、`config/`、`wizard/` 和图标。
- `manifest.checksum` 等于 `app.tgz` 字节内容的 MD5。
- **`fnpack build` 每次都会把 `manifest.checksum` 重写成当前 `app.tgz` 的真实 MD5**（实测：源树 manifest 里留着上一个版本的旧值，构建后包内自动变成新值且与 `app.tgz` 的 MD5 相等）。所以改过 `app/` 下的文件不必手工重算校验和；但**平台并不校验它**（checksum 陈旧的包照样装得上），发布前仍应确认包内该项是真值。
- **整包 SHA-256 不可字节复现**：`fnpack` 打 tar 会带条目 mtime，gzip 头带压缩时间戳，连"重打但内容没变"都会得到不同哈希。判断两次构建是否等价要解包 `diff -r` 比载荷文件（或比载荷 MD5），不要拿整包哈希比对。
- fnpack 会把 staging 中的 `.DS_Store` 带入产物，因此构建前必须主动清理或拒绝。同理，`app/bin/` 下的 `__pycache__/`、`*.pyc` 也会被原样打进去（实测让包体多出几十 KB 并污染载荷），跑过测试的目录打包前要清理，测试命令本身带 `PYTHONDONTWRITEBYTECODE=1`。

审计器应验证这些实测不变量，但报错应说明这是当前工具链格式验证，而非永不变化的公开格式。外层发布制品另外生成 SHA-256。
