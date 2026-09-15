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
- [Docker 应用 + AppCenter 安装坑位（实机沉淀）](#docker-应用--appcenter-安装坑位实机沉淀)
- [Native Web 应用端口设置闭环（实机沉淀）](#native-web-应用端口设置闭环实机沉淀)
- [虚拟机承载型应用（实机沉淀）](#虚拟机承载型应用实机沉淀)

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

## Docker 应用 + AppCenter 安装坑位（实机沉淀）

以下为手动 Docker 部署型 FPK（无 gateway/CGI，仅 `cmd/main` 管理 docker compose）在 fnOS 备用实机上反复重装、改端口、验证应用设置页时遇到的问题和修复方法。适用于所有同类型应用。完整构建模板、lifecycle 合约和最终验证流程见 [Docker 应用 FPK 构建、问题处理与验证流程](docker-app-flow.md)。

### 1. `install-fpk` 对小 `app.tgz` 不落地 `/vol1/@appcenter/`

实机观察到 `appcenter-cli install-fpk` 安装后应用文件落在 `/var/apps/{app}/target`（稳定入口），`/vol1/@appcenter/{app}` 可能缺失或与 `/var` 不同步。

修复：

- 生命周期脚本（`cmd/main`、`cmd/config_callback`、`cmd/install_callback`）不得写死 `/vol1/@appcenter/{app}`，必须做路径归一化：

```bash
APP_DIR="${TRIM_PKGDIR:-${TRIM_APPDEST:-/vol1/@appcenter/$APP_NAME}}"
[ -f "$APP_DIR/target/docker/docker-compose.yml" ] && APP_DIR="$APP_DIR/target"
[ ! -f "$APP_DIR/docker/docker-compose.yml" ] && [ -f "/var/apps/$APP_NAME/target/docker/docker-compose.yml" ] && APP_DIR="/var/apps/$APP_NAME/target"
[ ! -f "$APP_DIR/docker/docker-compose.yml" ] && [ -f "/vol1/@appcenter/$APP_NAME/docker/docker-compose.yml" ] && APP_DIR="/vol1/@appcenter/$APP_NAME"
```

- 持久数据同理归一化：优先 `TRIM_PKGVAR`，缺省时按 `/var/apps/{app}/var` 与 `/volN/@appdata/{app}` 顺序探测。

### 2. 安装后 AppCenter 数据库缺行，导致应用设置页“无法连接到服务器”

手动/半手动安装（或 `install-fpk` 未完整触发）后，postgresql 库 `appcenter` 中可能出现：

- `app` 行缺失或 `status` 停在 `start`；
- `app_service` 缺失或 `url`/`default_url`/`service_url` 为空；
- `system_config` 缺 `('appAutoUpdate', '{appname}', 'false')` 行，后端 `ToConfigDetailVo` 取不到值直接 500。

修复（安装后或 `install_callback` 内幂等补齐）：

```sql
UPDATE app SET service_url='http://${host}:{port}/', status='running',
       is_stop=true, is_uninstall=true, updated_at=now()
 WHERE app_name='{appname}';
UPDATE app_service SET url='http://${host}:{port}/',
       default_url='http://${host}:{port}/', title='{Display}', updated_at=now()
 WHERE app_id=(SELECT id FROM app WHERE app_name='{appname}' LIMIT 1);
INSERT INTO system_config(type,k,v)
SELECT 'appAutoUpdate','{appname}','false'
WHERE NOT EXISTS (SELECT 1 FROM system_config WHERE type='appAutoUpdate' AND k='{appname}');
```

注意 shell 拼 SQL 时 `${host}` 会被 shell 展开，必须用 `chr(36)` 或 `'{'` 转义拼出字面量 `${host}`。

关键坑：**appcenter 在安装流程后半段才补写 `app`/`app_service` 行**。`install_callback` 同步执行一次 `UPDATE` 时目标行可能还不存在，随后被 appcenter 默认值（空 `service_url`）覆盖。因此 `install_callback` 需要“立即一次 + 后台延迟多次”同步：

```bash
( for delay in 3 8 15; do sleep "$delay"; sync_appcenter_db; done ) >/dev/null 2>&1 &
```

验证命令：

```bash
psql -h /var/run/postgresql -d appcenter -U postgres -P pager=off \
 -c "SELECT id,status,service_url FROM app WHERE app_name='{appname}';"
trim-cli app config detail {appname}   # 应返回 200 且 port 正确
```

### 3. 应用设置页端口显示旧值/文案错（改不动）

症状：设置页“修改端口”处 `initValue` 是旧端口，helper 文案是上游应用名（例如套模板时残留的 “New API”），且保存后端口不生效。

根因与修复：

- 设置页读取的是 `wizard/config`（以及 `wizard/install` 的默认值）。必须把 `initValue` 与 helper 文案同步为目标应用名和正确默认端口；改完要同时更新 **三处副本**：
  1. 构建源（`repo/{app}/wizard/*`）→ 重新 `fnpack build`；
  2. 已安装 `/vol1/@appcenter/{app}/wizard/*`；
  3. 已安装 `/var/apps/{app}/wizard/*` 及 `target/` 下若有副本。
- 保存端口走 `config_callback`。`config_callback` 必须做到：
  1. 校验端口 1–65535；
  2. 写 `docker/.env`（`wizard_access_port={port}`）与 `.env`；
  3. 用 python/json 改 `ui/config` 中 `url`/`.url` 条目的 `port` 字段（不要盲目 sed，ui/config 是 JSON）；
  4. 更新 AppCenter 数据库（见第 2 节 SQL，端口为变量）；
  5. 重启容器前清理旧容器（见第 4 节）再 `docker compose up -d`。
- 验证闭环：改 `{port1}`→`{port2}` 再改回 `{port1}`，两步都要确认容器 `Ports`、`/api/health` 与数据库 `service_url` 一致。

### 4. 旧 exited 容器导致 `docker compose up` 原地不动

compose 报无变更时，旧 `exited` 容器仍占用容器名，新端口绑定不生效。`cmd/main start` 与 `config_callback` 重启前必须：

```bash
docker compose down --remove-orphans 2>/dev/null || true
if docker ps -a --format '{{.Names}}' | grep -qx 'sample-web'; then docker rm -f sample-web; fi
docker compose up -d
```

（把 `sample-web` 换成实际容器名。）

### 5. `cmd/main start` 在 appcenter 环境无向导变量/无 `TRIM_PKGVAR`

appcenter 调 `cmd/main start` 时不一定注入 `wizard_*` 环境变量，也不一定有 `TRIM_PKGVAR`。修复：

- compose 端口写默认值：`"${wizard_access_port:-{port}}:{container_port}"`；
- 卷挂载写默认值：`${TRIM_PKGVAR:-/volN/@appdata/{app}}/data:/app/data`；
- `cmd/main` 自己生成/补齐 `docker/.env`（`TRIM_PKGVAR`、`wizard_access_port`、`ADMIN_EMAIL`、`ADMIN_PASSWORD`、`TZ`，`ENCRYPTION_KEY` 用 `openssl rand -hex 32`）后再 `docker compose up -d`。
- `start` 成功后 `exit 0`，不要阻塞等待 healthy（appcenter 有自己的轮询窗口，长阻塞会被判 start 失败并把状态写成 10500 一类）。

### 6. 容器被 137/OOM 杀掉而 appcenter 只报 `start` 失败

排查顺序：

```bash
docker inspect {ctr} --format 'exit={{.State.ExitCode}} oom={{.State.OOMKilled}}'
docker logs --tail 200 {ctr}
du -sh /var/apps/{app}/var
```

137 且 OOMKilled=true：加 `mem_limit`/`restart` 策略或降低数据量；同时检查数据目录是否把宿主机旧大目录挂进了容器。

### 7. 重装测试的完整清理顺序

在测试机做“删除后重装”时按此顺序，避免半残留：

1. `appcenter-cli stop {app}`；
2. `docker compose down`（`/vol1/@appcenter/{app}/docker` 与 `/var/apps/{app}/target/docker` 两处都试）+ `docker rm -f {ctr}`；
3. 把 `/vol1/@appcenter/{app}`、`/volN/@appdata/{app}`、`/vol1/@appconf/{app}`、`/var/apps/{app}` 改名移入备份目录（先备份）；
4. 清库：遍历 `information_schema` 找所有带 `app_id` 列的表逐一 `DELETE`，再删 `app_package`（按 `app_name`）、`system_config`（按 `k`）、`app_auto_upgrade_record`、`app`；
5. `systemctl restart trim_app_center.service`，确认 `status` 报 noinstall/不存在；
6. 上传新 FPK（远端重算 SHA-256 与本地一致）→ `install-fpk --volume N --env env` → 等待并检查 `app`、`app_service`、容器与 API。

### 8. 验证清单（交付前必做）

- `appcenter-cli status {app}` = running；
- `docker ps` 容器 healthy、宿主端口与 `wizard_access_port` 一致；
- `curl http://127.0.0.1:{port}/api/health`（或等价端点）返回 ok；
- `trim-cli app config detail {app}` 的 `port`/`serviceName`/`title` 正确，无上游应用名残留；
- 数据库 `app.service_url`、`app_service.url/default_url/title` 指向正确端口；
- HTTP 首页返回 200，HTTP 登录返回 200（不要记录 token）；
- 纯 HTTP 服务的 HTTPS 探测若返回 `wrong version number`，记录为协议预期，不当作应用错误；
- AppCenter `stop` 后 `status=stopped`，Docker 容器必须停止/Exited，HTTP 不可访问；
- AppCenter `start` 后 `status=running`，Docker 容器必须 `Up healthy`，HTTP health 返回 ok；
- 改端口→回改端口两步闭环成功，且每步都检查 Docker 端口、HTTP health、DB URL、`ui/config`；
- `grep -RIn '旧默认端口\|上游应用名' /vol1/@appcenter/{app} /var/apps/{app}` 无残留。

## Native Web 应用端口设置闭环（实机沉淀）

完整方案见 [Native Web FPK 端口设置闭环](native-web-port-flow.md)。这里记录排障时最容易误判的现象和修复方法。

### 现象 A：应用设置页改端口后，设置页回显已变，但“打开”仍进旧端口

证据模式：

- `GET /app-center/v1/config/wizard?appName={app}` 回显新端口。
- `GET /app-center/v1/config/detail?appName={app}` 的 `services[0].urls.port` 仍是旧端口。
- 新端口 Web 可能已经可访问，旧端口可能不可访问或仍可访问。

根因：

- `config_callback` 真实由 AppCenter 调用时按 package 用户运行，能写应用配置，但无权写 AppCenter PostgreSQL 的 `app.service_url` / `app_service.url/default_url`。

修复：

- `config/privilege` 设为 `run-as=root`，并在 `config_callback` 中幂等同步 AppCenter DB。
- 使用 `chr(36) || '{host}:...'` 拼字面量 `${host}`，避免 shell 展开。
- 保存后验证 `config/detail` 的 `urls.port` 与 `defaultUrls.port` 均变为新端口。

### 现象 B：AppCenter 打开入口已变新端口，但实际 Web 仍在旧端口

证据模式：

- `config/detail`、`ui/config`、`ports.conf` 都显示新端口。
- `ps` 中 `native-engine --adminAddr :旧端口 --localAddr :旧代理端口` 仍存在。
- 新端口连接拒绝，旧端口仍 HTTP 200。

根因：

- `cmd/main stop` 用新端口匹配进程：`pgrep -f "--adminAddr :${APP_PORT}"`。
- 端口修改后 `APP_PORT` 已是新端口，但旧进程仍绑定旧端口，因此 stop 找不到旧进程。

修复：

- 不要按 `--adminAddr` 匹配旧进程。改用本应用唯一特征，例如 `--cachePath ${TRIM_PKGETC}`。
- stop/start 后验证旧端口不响应、新端口 200。

### 现象 C：手工执行 config_callback 可切端口，飞牛页面真实保存不切端口

证据模式：

- SSH/root 手工执行 `wizard_access_port=... config_callback` 成功。
- AppCenter UI 或 `POST /app-center/v1/config/wizard` 保存后只改了配置文件/DB，实际进程未重启。

根因：

- 真实 AppCenter callback 环境中的 `TRIM_APPDEST` 不一定指向包含 `cmd/main` 的目录。
- 安装结构可能是 `cmd/` 在 `/var/apps/{app}/cmd/`，二进制在 `/volN/@appcenter/{app}/bin/`。

修复：

- `config_callback` 中解析 main 路径时优先探测 `/var/apps/{app}/cmd/main`，再尝试 `${TRIM_APPDEST}/cmd/main`、`${TRIM_APPDEST}/target/cmd/main`、`/vol1/@appcenter/{app}/cmd/main`。
- 必须通过真实 AppCenter UI/API 保存链路验证，不能只用手工 root 环境变量调用脚本验证。

### 五点一致性验证

每次端口修改后，必须同时检查：

1. wizard 回显端口。
2. `${TRIM_PKGETC}/ports.conf`。
3. `ui/config` 的 `.url.*.port`。
4. AppCenter `config/detail` 的 `urls.port` 和 `defaultUrls.port`。
5. 实际进程和端口：`--adminAddr`、`--localAddr`、新端口 HTTP/HTTPS 可用、旧端口关闭。

只有五点全部一致，才能说“飞牛应用设置页端口修改链路已修复”。

---

## 虚拟机承载型应用（实机沉淀）

适用：应用自己创建/管理 libvirt 虚拟机，或有一个 7×24 常驻的寻踪入口端口。完整设计见 [vm-app-flow.md](vm-app-flow.md)，验证方法见 [offline-and-live-testing.md](offline-and-live-testing.md)。主机地址一律写作 `<NAS_IP>` / `<VM_IP>`。

### A. 安装与回调

**A1 · `install-fpk` 卡住约 190 秒后段错误，报 `APP_INSTALL_FAILED_INSTALL_CALLBACK_EXCEPTION`**
安装回调有约 **190 秒**看门狗，超时会斩首回调并让 CLI 自身在 `install_fpk.go:66` 段错误。根因是把下载/预置这类重活同步跑在 `install_callback` 里。改"秒回模式"：回调只校验参数、写状态文件、拉起后台 worker，然后 `exit 0`；worker 用 `systemd-run` 挪出平台进程树，状态机文件化。

**A2 · 安装刚返回就被判 `APP_CRASH`，平台自动执行卸载清理（连 systemd 单元一起删）**
安装返回后、单元创建前存在空窗，此时 `main status` 返回非 0 即被判崩溃。入口/守护型应用的 `status` 必须恒 0。

**A3 · `[Error]Something wrong with environment variables.`（内部 `error while binding environment variable`）**
`appcenter-cli install-fpk --env` 传的是 **文件路径**（每行 `KEY=VALUE`），不是 `key=value` 字符串。

**A4 · 安装卡在解压：`gunzip` 报 `decompression OK, trailing garbage ignored`**
上游镜像 gz 尾部有杂散字节，内容等价且 SHA256 通过。实测退出码是 **2**（不是 1）。写成 `[ $? -eq 1 ] || exit 1` 的守卫会当场静默自杀，而且**直接 `exit` 不触发 `ERR` trap**，状态文件里连 `failed` 都不留，极难排查。正确守卫：容忍任意非零退出码，只校验解压产物非空（内容正确性由前置 SHA256 保证）。

**A5 · `systemd-run --unit=<同名>` 报单元已存在**
`--collect`/`--remain-after-exit` 的瞬时单元结束后**单元名会残留**。拉起前 `systemctl stop <unit>` + `systemctl reset-failed <unit>`。

**A6 · 已足够大的磁盘仍被 `qemu-img resize`，误报"磁盘扩容失败"**
用 `qemu-img info | grep -o "virtual size: [0-9]*" | awk '{print $3}'` 取到的是 **GiB 数字**（母盘 `4 GiB` → 4），拿它和 `目标字节` 比会永远判"不够大"，于是对不能缩容的 qcow2 调 resize。必须 `--output json` 读 `virtual-size`，解析失败按 0 处理。

**A7 · `fnpack build` 拒绝 `version = 18.2`**
新版 fnpack 要求 `x.y.z` 三段式（锁定的 1.2.3 接受两段）。发布一律用三段式版本号兼容两套工具链。

**A8 · 包体莫名变大，载荷里多了 `.pyc`**
`app/bin/` 下的 `__pycache__/` 会被原样打进 `app.tgz`。打包前清理，测试命令带 `PYTHONDONTWRITEBYTECODE=1`。

### B. 生命周期与记账

**B1 · 「启用」报 `error code 11000`，journal 里 `APP_START_FAILED_PORT_USAGE PORT_USAGE:<port>`，随后 `APP_CRASH`**
`manifest` 的 `checkport=true` + 入口端口常驻 = 平台启动前要求端口空闲，永远不满足。**入口常驻型应用必须 `checkport=false`**。

**B2 · 停用后点启用毫无反应，日志里只有 `status` 轮询**
平台点「启用」时**不回调 `main start`**。任何"靠 status 返回 3 换 start 回调"的设计都不成立；恢复入口要放在应用自己的服务或入口页里。

**B3 · 虚拟机明明在跑，点「停用」却没反应，必须先在应用中心「启用」再「停用」**
入口页一键开机/自动补开是直连 `virsh start`，AppCenter 仍挂「已停用」；而它一旦自认已停用，就不再回调 `cmd/main stop`。修法：入口开机后**后台**补敲一次 `appcenter-cli start <app>` 扳回记账（先证明该调用不会重启你的服务：前后对照 pid），并加冷却与"用户刚点停用就不抢"的护栏。

**B4 · 刚开好的虚拟机关掉了 / 停用后 60 秒内不该有的二次关机**
`main stop` 里阻塞等待优雅关机会被状态机咬（期间 start 被判 already started），而守望进程 12×5s 后**无条件**强制断电会把期间被重新开机的机器再关掉。修法：`stop` 发完 ACPI 立即返回；守望进程在强制断电前比对"开机请求令牌"（如 `/tmp/<app>-vm-start.req` 的 mtime），有更新的开机请求就放弃。

**B5 · 判断"平台到底调了哪个动作"毫无头绪**
在 `cmd/main` 首行写调用留痕：`echo "$(date -Iseconds) arg=$1 ppid=$(ps -o args= -p $PPID)" >> /tmp/<app>-main-calls.log`。`ppid` 会直接暴露 `trim_app_center`。

**B6 · `appcenter-cli start` 报 `error 10500`**
平台在 install 后已自动 `APP_STARTED`，应用已在运行。属预期，不要重试。

### C. 磁盘与网卡

**C1 · 重装把用户系统盘刷成出厂状态（最严重）**
装机完成后磁盘被移进 libvirt 存储池，而"是否已有磁盘"只判断了应用共享目录 → 重装走"重新下载 + `virsh vol-delete` + `cp` 覆盖"。判断必须**同时**看共享目录与存储池，复用路径绝不触碰池内卷。

**C2 · 向导里选了别的版本，装完还是原来那套系统**
磁盘复用逻辑优先命中，静默忽略了版本参数。要有版本标记并做换版本留档；**版本标记不能放在池目录里**（目录型池会把杂项文件当卷列出），写到 `/vol1/vm/<app>.disk-version`。

**C3 · 重装后"寻踪突然失效"、路由器绑定全废**
重装重新随机生成了网卡 MAC/UUID。要沿用上一次 domain 的 UUID 与 MAC，并把 MAC 落盘（卸载会 `undefine --nvram` 掉定义）。

**C4 · qemu 报 `Could not open '...qcow2': Permission denied`**
某些数据卷上 `umask 000` 会造出 000 权限文件。正确做法 `chown libvirt-qemu:libvirt-qemu` + 权限 `604`，关键在属主不是目录位。

### D. 入口与寻踪

**D1 · 端口在听、TCP 连得上，但首页与状态接口几十秒零响应，AppCenter 却显示「运行中」**
发现逻辑改成"后台线程单飞 + 请求侧只读缓存"后，发现链内部再次获取同一把不可重入锁 → 自我死锁。锁改 `threading.RLock()`，并补"持锁调用发现链不死锁"的用例（离线用例若把发现链整条打桩，就正好测不到这段）。

**D2 · 一键开机后跳不到管理界面（`ERR_ADDRESS_UNREACHABLE` 或 404）**
POST 后浏览器地址停在自家控制路径（如 `/power/start`），`meta refresh` 用同一 URL 重新 GET，被入口当普通路径透传给上游。修法：刷新显式带 `url=`；控制路径一律 302 回本站首页；302 前先探活。

**D3 · 页面挂着"已找到 `<VM_IP>`，服务还在起来"几十分钟，其实那地址是死的**
候选地址探活失败后没降级。要连续失败即降级回"还在获取地址"并清空候选；ARP 表只认已完成表项。

**D4 · 跳转后浏览器落空白错误页**
就绪判定只看 TCP 能连。核心服务重启期常见"socket 在听、一发请求就断"，必须要求**真拿到 HTTP 响应**，并按"要去的那扇门"分别把关（后台端口先起时不能算业务端口就绪）。

**D5 · 刚开机的 5~10 秒被报成"路由器 DHCP 没发地址"**
ARP 表还没建起来。要有开机宽限期（如 90 秒）且锚在**开机时刻**，期内一律显示"还在起来"。

**D6 · 关机后"启动虚拟机"按钮最长 20 秒才出现**
开关机态跟着地址发现缓存走，无人访问时发现节奏降到 20 秒。页面显示开关机态时做一次廉价实判（`virsh domstate` ≈0.1s，加 1 秒记忆），跳转仍读缓存。

**D7 · 抓包只见 DISCOVER 不见 OFFER，判定"这条 LAN 没有 DHCP 服务器"**
观测盲区：OFFER/ACK 是单播发给 vnet 口的，学习交换机不泛洪给 OVS 内层口；OVS 在 `ptype_all` 之前就把包收走，物理口也看不见 RX。要定性看路由器租约表；最便宜的反证是同一 MAC 过一会儿自己拿到地址。

**D8 · 在入口页设完静态地址，虚拟机当场失联**
在 netifd 托管接口上手跑 `udhcpc -n`，它退出时发 `deconfig` 把地址和路由一起冲掉（netifd 只补地址不补路由）。只能用 `ifup lan` + `ubus call network.interface.lan udhcpc renew`；把"源码里不许出现 `udhcpc -n`"写成用例断言。

**D9 · 无网络时进不去虚拟机**
唯一入口是串口：`script -qec "virsh -c qemu:///system console <domain>" /dev/null` 喂命令。

### E. 反代与网关

**E1 · 经 NAS 反代访问时，管理面板的按钮跳到 NAS 的别的端口（甚至飞牛登录页）或 `chrome-error`；直连虚拟机端口则正常**
前端按 `location.hostname` + 兄弟端口拼 URL。取证：playwright 包一层 `window.open` 记录实参，反代与直连各点一遍。修法：反代注入 `no-store` 的经典脚本（注在 `<head>` 开头，抢在 `type=module` 前），接管 `window.open` 并在捕获阶段改 `<a href>`，只改"同主机且端口 ≠ 反代端口"的链接。

**E2 · SPA 走统一网关前缀时 `Not Found` 或无限刷新**
前端路由没设 `basename`：网关路径命中 catch-all → auth guard 重定向回登录页 → document reload 循环。给路由注入 `basename`（结构化正则匹配压缩产物，alias 名会变），并给响应加 `no-store`。
