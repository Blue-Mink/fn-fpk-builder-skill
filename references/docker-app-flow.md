# Docker 应用 FPK 构建、问题处理与验证流程

本页沉淀一个 Docker Web 应用最终版在 fnOS 备用机上的实测经验，抽象成适用于 Docker Web 应用的 FPK 构建流程。示例中的 `{appname}`、`{display}`、`{container}`、`{port}`、`{image}` 均为占位符，替换为目标应用实际值。

## 适用场景

适用于：

- FPK payload 内主要是 `docker/docker-compose.yml`、UI 入口、wizard 配置与 lifecycle 脚本；
- 容器对外暴露一个 HTTP Web 端口；
- 需要在 AppCenter 中支持打开、停止、启动、修改端口和卸载；
- 不依赖 gateway/CGI 代理 NAS 登录态；
- 需要用 `cmd/main` 自己管理 `docker compose`。

不适用于：

- 原生二进制服务型 FPK；
- 官方 `docker-project` 资源能完整管理 stop/start/config 的已验证模板；
- 需要 HTTPS 终止、NAS 反代或登录态桥接的应用。

## 推荐目录结构

```text
{app-root}/
  manifest
  config/
    privilege
    resource
  cmd/
    install_init
    install_callback
    config_init
    config_callback
    uninstall_init
    uninstall_callback
    upgrade_init
    upgrade_callback
    main
  wizard/
    install
    config
  app/
    docker/
      docker-compose.yml
    ui/
      config
      images/
        icon_64.png
        icon_256.png
        icon_0.png
        icon_0_256.png
    README.md
```

注意：`app/` 里只放 payload。不要把外层 `manifest`、`cmd/`、`config/`、`wizard/` 再复制进 `app/`，否则 `app.tgz` 可能出现重复 `config/`、`config/resource` 等成员，审计应直接拒绝。

## Manifest 要点

```text
appname               = {appname}
display_name          = {display}
version               = 1.0.0
platform              = x86
source                = thirdparty
desktop_uidir         = ui
desktop_applaunchname = {appname}.Application
service_port          = {port}
ctl_stop              = true
```

规则：

- 新包不要再生成 deprecated `arch=x86_64`。
- `platform=x86|arm|all` 按真实 payload 与镜像支持情况选择。
- `ctl_stop=true` 是 AppCenter 显示并调用启停控制的关键字段。
- `service_port`、wizard 默认端口、`ui/config` 端口、compose 端口必须一致。

## Resource 选择：优先 lifecycle 管理 Docker

实测结论：在当前 fnOS/AppCenter 组合中，声明 `docker-project` 后，AppCenter 内置 stop 可能走错误路径并报：

```text
open /docker/docker-compose.yaml: no such file or directory
```

现象是 AppCenter 显示 stop 成功，`status=stopped`，但 Docker 容器仍 `Up healthy`。因此对于手写 lifecycle 的 Docker Web 应用，推荐不要声明 `docker-project`，统一让 `cmd/main` 管理 Docker。

推荐 `config/resource` 只声明实际需要的数据共享：

```json
{
  "data-share": {
    "shares": [
      {
        "name": "{appname}",
        "permission": {"rw": ["{appname}"]}
      },
      {
        "name": "{appname}/data",
        "permission": {"rw": ["{appname}"]}
      }
    ]
  }
}
```

如果确实要使用 `docker-project`，必须在目标 fnOS 实机验证 AppCenter stop/start/config 会真正作用到容器，不能只看 AppCenter 状态。

## 权限

默认仍应优先 `run-as=package`。但若 lifecycle 脚本需要：

- 执行 `docker compose up/down/stop`；
- 删除残留容器；
- 写 `/var/apps/{appname}/target/docker/.env`；
- 同步 AppCenter PostgreSQL 数据库；

则通常需要 root：

```json
{"defaults":{"run-as":"root"}}
```

这会触发 `application requests root execution` warning。必须在报告中解释 root 的最小必要性。

## Compose 模板

```yaml
services:
  app:
    image: {image}
    container_name: {container}
    restart: unless-stopped
    ports:
      - "${wizard_access_port:-{port}}:3000"
    environment:
      TZ: ${TZ:-Asia/Shanghai}
    volumes:
      - ${TRIM_PKGVAR:-/vol1/@appdata/{appname}}/data:/app/data
```

要求：

- 容器名、`cmd/main status`、清理逻辑中的名字必须一致。
- 端口使用 `${wizard_access_port:-默认端口}`，不能写死。
- 数据目录使用 `${TRIM_PKGVAR:-...}`，允许 AppCenter 注入真实持久目录。
- 发布前检查镜像是否支持目标架构；浮动 tag 应在构建记录中说明。

## 路径归一化

实机观察：FPK 安装后稳定入口常是 `/var/apps/{appname}`，payload 可能位于 `/var/apps/{appname}/target`，而 `/vol1/@appcenter/{appname}` 不一定包含完整 payload。

所有 lifecycle 脚本都必须归一化路径：

```bash
APP_NAME="${TRIM_APPNAME:-{appname}}"
APP_DIR="${TRIM_PKGDIR:-${TRIM_APPDEST:-/vol1/@appcenter/$APP_NAME}}"

if [ ! -f "$APP_DIR/docker/docker-compose.yml" ] && [ -f "$APP_DIR/target/docker/docker-compose.yml" ]; then
  APP_DIR="$APP_DIR/target"
fi
if [ ! -f "$APP_DIR/docker/docker-compose.yml" ] && [ -f "/var/apps/$APP_NAME/target/docker/docker-compose.yml" ]; then
  APP_DIR="/var/apps/$APP_NAME/target"
fi
if [ ! -f "$APP_DIR/docker/docker-compose.yml" ] && [ -f "/vol1/@appcenter/$APP_NAME/docker/docker-compose.yml" ]; then
  APP_DIR="/vol1/@appcenter/$APP_NAME"
fi

PKG_VAR="${TRIM_PKGVAR:-}"
if [ -z "$PKG_VAR" ]; then
  if [ -e "/var/apps/$APP_NAME/var" ]; then
    PKG_VAR="/var/apps/$APP_NAME/var"
  else
    PKG_VAR="/vol1/@appdata/$APP_NAME"
  fi
fi
```

`install_callback` 比较特殊：它应调用自身同目录的 `cmd/main`，而不是从 payload 目录找 `cmd/main`：

```bash
SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
MAIN_SCRIPT="$SCRIPT_DIR/main"
TRIM_APPNAME="$APP_NAME" TRIM_PKGDIR="$APP_DIR" TRIM_PKGVAR="$PKG_VAR" "$MAIN_SCRIPT" start
```

否则会出现 AppCenter `running`，但 Docker 仅 `Created` 或未启动的假成功。

## `cmd/main` 合约

`cmd/main` 必须支持：

```text
start
stop
status
restart  # 可选但建议提供
```

状态码：

- `start`/`stop` 成功返回 0；
- `status` 运行中返回 0；
- `status` 未运行返回 3；
- 未知动作返回 1。

`status` 必须检查真实容器状态，不能无条件成功：

```bash
status() {
  if docker ps --filter "name={container}" --filter "status=running" 2>/dev/null | grep -q {container}; then
    echo "running"
    exit 0
  else
    echo "stopped"
    exit 3
  fi
}
```

`start` 和 `config_callback` 在 `docker compose up -d` 前必须清理旧容器，避免端口不更新：

```bash
cd "$APP_DIR/docker"
docker compose down --remove-orphans 2>/dev/null || true
if docker ps -a --format '{{.Names}}' | grep -qx '{container}'; then
  docker rm -f {container} >/dev/null 2>&1 || true
fi
docker compose up -d
```

## AppCenter 数据库同步

Docker 应用常需要在 `install_callback`、`start`、`config_callback` 后同步 AppCenter URL 和状态。通用函数：

```bash
sync_appcenter_db() {
  command -v psql >/dev/null 2>&1 || return 0
  psql -h /var/run/postgresql -d appcenter -U postgres -v ON_ERROR_STOP=0 >/dev/null 2>&1 <<SQL || true
UPDATE app
   SET service_url='http://' || chr(36) || '{host}:${PORT}/',
       status='running', is_stop=true, is_uninstall=true, updated_at=now()
 WHERE app_name='${APP_NAME}';
UPDATE app_service
   SET url='http://' || chr(36) || '{host}:${PORT}/',
       default_url='http://' || chr(36) || '{host}:${PORT}/',
       title='${DISPLAY_NAME}',
       updated_at=now()
 WHERE app_id=(SELECT id FROM app WHERE app_name='${APP_NAME}' LIMIT 1);
INSERT INTO system_config(type,k,v)
SELECT 'appAutoUpdate','${APP_NAME}','false'
WHERE NOT EXISTS (SELECT 1 FROM system_config WHERE type='appAutoUpdate' AND k='${APP_NAME}');
SQL
}
```

必须使用 `chr(36) || '{host}'` 拼出字面量 `${host}`，避免 shell 展开。

安装和改配置时不要只同步一次。AppCenter 可能在脚本返回后继续写 `app`/`app_service` 默认值，覆盖 `status=running` 或 URL，因此使用立即同步 + 延迟同步：

```bash
sync_appcenter_db
(
  for delay in 3 8 15; do
    sleep "$delay"
    sync_appcenter_db
  done
) >/dev/null 2>&1 &
```

## `config_callback` 改端口闭环

保存端口时必须完成五件事：

1. 校验端口 1–65535；
2. 更新 `$APP_DIR/.env` 和 `$APP_DIR/docker/.env`；
3. 用 JSON 解析更新 `$APP_DIR/ui/config` 中 `url` 或 `.url` 的 `port` 字段；
4. 同步 `app.service_url`、`app_service.url/default_url`，并保持 `status=running`；
5. 清理旧容器并 `docker compose up -d`。

验证必须做双向闭环：

```text
默认端口 → 临时端口 → 默认端口
```

每一步都检查：

- `appcenter-cli status {appname}` = running；
- `docker ps` 端口映射等于目标端口；
- `curl http://127.0.0.1:{port}/api/health` 返回 ok；
- 旧端口不再可访问；
- `app.service_url` 与 `app_service.url/default_url` 更新；
- `ui/config` 更新。

## HTTP/HTTPS 验证

若应用只提供 HTTP，`ui/config` 应明确：

```json
"protocol": "http"
```

验证：

```bash
curl -i http://{host}:{port}/
curl -i http://{host}:{port}/api/health
curl -k -i https://{host}:{port}/
```

HTTPS 对纯 HTTP 端口返回 `OpenSSL wrong version number` 是预期现象，不应当作应用错误。报告中应明确“该端口为纯 HTTP，允许 HTTP 访问”。

## 远程安装测试流程

1. 上传 FPK 到临时目录并在远端重算 SHA-256，必须与本地一致。
2. 为 wizard 准备 env 文件，例如：

   ```bash
   wizard_access_port={port}
   ```

3. CLI 需要有 `OfficialAppUsers` 组权限时，用：

   ```bash
   setpriv --groups 902 appcenter-cli ...
   ```

4. 不要覆盖安装。先 stop，再 uninstall，确认 `status=noinstall`。
5. 清理只限目标应用：容器、目标应用目录、目标应用 DB 行。业务数据优先改名备份，不直接删除。
6. 安装：

   ```bash
   setpriv --groups 902 appcenter-cli install-fpk package.fpk --env install.env --volume 1
   ```

7. 等待后验证安装、HTTP、登录、启停和端口配置。

## 最终交付证据

每个 Docker FPK 交付前至少记录：

- FPK 路径、SHA-256、大小；
- `fpk.py inspect` 的 `ok=true`、warning、成员计数、manifest 摘要；
- 远端安装命令和目标 volume；
- `appcenter-cli status`；
- `docker ps` 容器状态和端口；
- HTTP health 和登录结果（不记录 token）；
- HTTPS 结果（若纯 HTTP，应说明 wrong version number 是预期）；
- AppCenter stop/start 后 Docker 和 HTTP 的变化；
- 端口改动前后 DB、UI config、Docker 和 HTTP 的一致性；
- 源码快照或源码 SHA256 清单。

## 最终实测结论

最终版验证通过的关键组合：

- `manifest`: `platform=x86`、`service_port={port}`、`ctl_stop=true`，无 deprecated `arch`。
- `config/resource`: 不声明 `docker-project`，只保留 data-share。
- `cmd/main`: 负责 Docker lifecycle；`status` stopped 返回 3。
- `install_callback`: 调同目录 `main` 启动容器，并做延迟 DB 同步。
- `config_callback`: 改端口后同步 env、UI config、DB、Docker，并做延迟 DB 同步。

备用机验证结果：安装成功，入口首页 200，登录 200，HTTPS wrong version number（纯 HTTP 预期），AppCenter stop/start 能传递到 Docker，端口 `{port1} → {port2} → {port1}` 全链路通过。
