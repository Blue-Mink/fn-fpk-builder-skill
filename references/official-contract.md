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
- `checkport=true|false`：启动前是否检查端口。
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

`cmd/main` 接收 `start`、`stop`、`status`：

- `start`/`stop` 成功返回 `0`，失败返回 `1`。
- `status` 返回 `0` 表示运行中，返回 `3` 表示未运行。
- 不认识的动作返回 `1`。

状态检查应验证真正代表可用性的进程、PID 或容器，而不是无条件返回成功。PID 文件需要防止陈旧 PID 和 PID 复用误判。

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

## 实测的 FPK 容器结构

以下是对 fnpack 1.2.3 产物的观察，不是文档承诺：

- `.fpk` 是 gzip 压缩 tar。
- 外层包含 `manifest`、`app.tgz`、`cmd/`、`config/`、`wizard/` 和图标。
- `manifest.checksum` 等于 `app.tgz` 字节内容的 MD5。
- fnpack 会把 staging 中的 `.DS_Store` 带入产物，因此构建前必须主动清理或拒绝。

审计器应验证这些实测不变量，但报错应说明这是当前工具链格式验证，而非永不变化的公开格式。外层发布制品另外生成 SHA-256。
