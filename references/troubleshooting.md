# 故障排查

先执行对应 CLI 的 `--help`，再运行 `fpk.py toolchain`、`fpk.py doctor` 或 `fnos.py doctor`。使用 `--json` 保存完整证据；不要凭一条人类可读错误猜测根因。

依据：`https://developer.fnnas.com/llms-full.txt`、独立多架构实现验证，以及 2026-07-25 实机只读探测与隔离烟测。

## 目录

- [工具链](#工具链)
- [打包与归档](#打包与归档)
- [架构](#架构)
- [安装与状态](#安装与状态)
- [入口和网络](#入口和网络)
- [Docker 与运行时](#docker-与运行时)
- [路径、权限和数据](#路径权限和数据)
- [最小诊断包](#最小诊断包)

## 工具链

### 找不到 fnpack

检查操作系统与主机架构。默认下载 1.2.3 对应二进制并核对 `provenance.json` 的 SHA-256。不要直接使用目标 NAS 上的旧 fnpack 1.0.0。

### Linux arm64 下载返回 404

这是 2026-07-25 对官方 1.2.3 URL 的实测结果。可在 Linux amd64 CI 组包 ARM 目标；或者由用户显式提供可信 Linux arm64 fnpack 及其哈希。不要自动使用第三方镜像。

### fnpack 哈希不一致

删除本次下载，报告 URL、期望哈希、实际哈希和字节数。检查代理/CDN/下载页面是否返回了 HTML。不得继续执行。

### `fnpack build` 参数不识别

输出 fnpack 二进制 SHA-256、`fnpack --help` 与 `fnpack build --help`。官方 1.2.3 实测没有可用的 `--version` 子命令，因此版本身份以固定下载地址和内置哈希共同确认；其 `build` 支持 `-d/--directory`。设备内 1.0.0 可能不同，构建自动化只支持已固定的 1.2.3。

## 打包与归档

### 缺少 manifest、config 或图标

确认传给 build 的是 FPK 项目根目录，而非源码仓库根目录。必需结构包括 `app/`、`cmd/`、`config/privilege`、`config/resource`、`wizard/`、`manifest`、两个根图标。

### JSON 合法但 fnpack 失败

检查文件编码、权限、声明路径和入口目录。`desktop_uidir` 存在时，其相对目录必须位于 `app/`。Docker resource 的 `path` 相对于 `app/`。

### `manifest.checksum` 不匹配

从外层 FPK 原样读取 `app.tgz`，对这些字节计算 MD5；不要对解压后的目录计算。若仍不同，产物可能损坏或不符合 fnpack 1.2.3 实测格式。

### 发现 `.DS_Store` 或旧制品

清理 staging 的元数据与旧 `.fpk`，重新构建。不要只在产物生成后修改 tar，否则 checksum 与工具生成证据会失效。

### 路径穿越或危险链接

停止处理并隔离该 FPK。不要尝试“修复后安装”；回到可信源码与 staging 重建。

## 架构

### amd64 包含 arm64，或反之

检查 overlay 输入是否写入相同公共路径、是否复用了上一次 staging，以及 glob 是否同时复制两套二进制。为每个架构重新创建空 staging，并检查所有 ELF，不只检查主程序。

### 检测到 Mach-O

这是 macOS 构建产物。重新为 `GOOS=linux`、Rust Linux target 或 Linux 容器构建；不能通过改名解决。

### 检测到 PE

这是 Windows 二进制。检查发布目录选择和跨平台构建输出路径。

### `platform=all` 但发现 ELF

生成 x86/arm 独立 FPK。若 ELF 来自依赖、native addon 或内嵌工具，同样不能使用 `all`。

### Docker 包在某个架构启动失败

FPK 的 `platform=all` 不保证镜像多架构。检查镜像 manifest/digest 是否包含设备 `uname -m` 对应平台。

## 安装与状态

### `appcenter-cli install-fpk` 失败

保留命令退出码和原始输出，随后查看：

```bash
tail -n 200 /var/log/trim_app_center/error.log
```

实机 appcenter-cli 可能在输出 `[Error]...` 时仍返回退出码 0。始终同时判断输出语义；`status` 返回 `noinstall` 也必须视为未安装，而不是成功。

`uninstall` 即使返回 0 也要再次查询状态。若仍是 running/stopped，保留现场并报告“卸载后置条件失败”，不要继续删除物理目录。

错误码 10111 可能包含具体 manifest 约束。例如已观测到 appname 长度必须为 3–32。以同一时间点的 appcenter error.log 为证据，不要只保留数字错误码。

检查平台、系统最低版本、安装卷、端口、依赖应用和向导 env。部署前先完成本地审计、上传与哈希核对；目标已安装时必须卸载并确认 `noinstall`，不得用直接覆盖安装来诊断或更新。

安装失败后先查状态。若失败过程留下 running、stopped 或其他已安装状态，重试新包或安装回滚包前都必须再次卸载并确认 `noinstall`。

### 一直是 `starting`

在超时内轮询状态，同时检查应用日志、PID、socket/端口和 `cmd/main status`。`starting` 不能当成功。

### status 始终 running

检查 `cmd/main status` 是否无条件 `exit 0`。状态应验证代表服务的进程或容器。

### status 始终 stopped

`status` 未运行应返回 3。检查 PID 文件是否写在持久可写目录、容器名是否与 Compose 一致、进程是否立即退出。

### appcenter 帮助没有 appname 参数

实机 1.0.1 的部分帮助文本不完整。仍应给 `start/stop/status/uninstall` 传精确 appname，并通过隔离测试应用验证；不要对生产应用试错。

### 卸载成功但隔离烟测报告残留

检查 `/var/apps/{app}`、appcenter/data/conf/home/temp、`/volN/@appmeta/{app}` 以及专用包用户和组。实机观察到 appcenter-cli 1.0.1 可能留下 `@appdata/{app}/smoke.log`、空 conf/home/meta 目录与 nologin 包账号。通用卸载命令不擅自删除这些对象；只有 `smoke` 会对唯一 `fpk-skill-smoke-*` 命名空间执行受限清理：center/conf/home/temp/meta 必须为空；data 必须恰好包含一个非符号链接普通文件 `smoke.log`；账号的 home 必须为 `/home/{user}` 且 shell 必须是 `/usr/sbin/nologin`。任何属性不符都停止并报告。

## 入口和网络

### 桌面图标不存在

检查 manifest 的 `desktop_uidir`、`desktop_applaunchname`，入口 ID，以及 `icon_{0}.png` 对应的 64/256 文件。UI config 必须是合法 JSON。

### CGI 返回 404

确认 URL 为 `/cgi/ThirdParty/{appname}/index.cgi/`，CGI 可执行，映射根位于 `/var/apps/{appname}/target`。记录 `REQUEST_URI` 时不要包含秘密。

### CGI 能访问不应暴露的文件

立即停止发布。路径必须规范化并限制在静态根；仅查找字符串 `..` 不足以覆盖编码和符号链接绕过。

### 统一网关 502 或入口打不开

检查 `gatewayPrefix`、`gatewaySocket`、服务实际监听的 Unix Socket、socket 所在目录与权限。`gatewaySocket` 只写文件名，socket 位于 `${TRIM_APPDEST}`。

### 网关用户身份缺失

确认请求确实通过 `/app/{appname}` 到达，而不是直接端口访问。只有网关路径提供受验证的 `X-Trim-*` 上下文；业务服务不得信任直连客户端伪造的同名 Header。

### WebSocket 失败

CGI 不支持 WebSocket。使用统一网关或独立端口，并确保 WebSocket 路由位于稳定 gateway prefix 下。

## Docker 与运行时

### Docker 容器名不匹配

显式 `container_name` 或使用 Compose 项目标签检查状态。不要假定 Compose 自动生成名称永远不变。

### 端口冲突

对齐 `manifest.service_port`、UI config `port` 和 Compose 宿主端口。若通过统一网关访问，可改用 Unix Socket 并去掉不必要的端口入口。

### 找不到 node/python/java

确认 manifest 声明运行时依赖，并在生命周期脚本中把 `/var/apps/{runtime}/target/bin` 加入 PATH。不要依赖交互 shell 的 PATH。

### native addon 加载失败

检查 addon 的 ELF 架构、libc 和动态库依赖。即使主应用是 JavaScript/Python，原生扩展仍需按目标构建。

## 路径、权限和数据

### `/usr/local/apps/@appcenter/...` 不存在

改用 `/var/apps/{appname}` 稳定入口或 `TRIM_*` 环境变量。物理安装路径和卷号随设备与安装位置变化。

### Permission denied

检查 `run-as`、文件所有者、执行位、用户授权路径、data-share ACL 和必要的 `join-groups`。不要把切换到 root 当成首选修复。

### 卸载重装后数据丢失

持久数据应位于 `TRIM_PKGETC`、`TRIM_PKGVAR`、`TRIM_PKGHOME` 或声明的 share，而不是 `TRIM_APPDEST`。实机更新固定走卸载重装，因此应检查 uninstall 生命周期的数据保留行为，不得依赖原位升级脚本保留状态。

### 生命周期错误在 UI 中不可读

失败前向 `TRIM_TEMP_LOGFILE` 写入一条短且可操作的信息，同时把详细诊断写入应用日志；两处都不要记录秘密。

## 最小诊断包

可共享的诊断证据：

- `fpk.py doctor/inspect --json` 输出。
- fnpack 版本、主机 OS/架构和工具文件 SHA。
- FPK 外层 SHA-256、manifest 和安全归档清单。
- ELF 类型摘要，不必上传完整专有二进制。
- 远端架构、appcenter-cli 版本、应用状态。
- 已脱敏的应用中心错误与应用日志尾部。

必须删除或遮蔽：

- SSH 地址中的敏感用户名或公网地址。
- 密码、Token、Cookie、私钥。
- 向导 env 的值。
- 用户文件路径和内容。
- 设备 machine ID。
