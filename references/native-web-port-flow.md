# Native Web FPK 端口设置闭环

本记录沉淀自带常驻引擎进程的这一类 **native Web + 端口直连 + 应用设置 wizard** FPK 的最终有效方案。适用场景：应用不是 Docker Compose，但需要在 fnOS AppCenter 中支持打开、启动/停用、应用设置页修改端口、同步桌面/应用中心入口。

> 只记录通用工程经验，不记录具体设备 IP、账号、测试日志或私有发布凭据。

## 最终有效结构

### manifest

关键字段：

```ini
appname               = native-web-demo
version               = 0.7.17
platform              = x86
ctl_stop              = true
service_port          = 18080
checkport             = true
desktop_uidir         = ui
desktop_applaunchname = native-web-demo.app
```

经验：

- `desktop_applaunchname` 必须与 `ui/config` 中 `.url` 的入口 key 一致。
- Web 管理入口走端口直连：`protocol=http + port=管理端口`。
- `platform=x86`，不要把含 ELF 的 native 包声明为 `all`。

### ui/config

使用 `.url` 而不是 `url`：

```json
{
  ".url": {
    "native-web-demo.app": {
      "title": "Native Web 样例应用",
      "icon": "images/icon_{0}.png",
      "type": "iframe",
      "protocol": "http",
      "port": "18080",
      "allUsers": true
    }
  }
}
```

经验：

- AppCenter 打开按钮读取的是 AppCenter service/config 数据，不一定实时读取磁盘 `ui/config`。
- 仍必须同步 `ui/config`，因为安装、图标、桌面入口、config detail 初始化都可能读取它。

### wizard/install 与 wizard/config

安装向导与应用设置页都应包含相同字段名：

- `wizard_access_port`：Web 管理端口，默认 `18080`
- `wizard_proxy_port`：Docker Registry Mirror 代理端口，默认 `18443`

关键经验：

- `wizard/config` 中固定 `initValue` 只能作为首次默认值；保存后回显必须由 `cmd/config_init` 动态输出覆盖。
- `config_init` 需要读取当前真实状态并输出：

```sh
echo "wizard_access_port=${ADMIN_PORT}"
echo "wizard_proxy_port=${PROXY_PORT}"
```

推荐读取优先级：

1. `${TRIM_PKGETC}/ports.conf`
2. 已安装 `ui/config`
3. 默认 `18080/18443`

## 权限模型

最终有效 `config/privilege`：

```json
{"defaults":{"run-as":"root"},"username":"native-web-demo","groupname":"native-web-demo"}
```

原因：

- AppCenter 页面真实保存 wizard 时会执行 `cmd/config_callback`。
- `config_callback` 不只要写应用自身配置，还要同步 AppCenter DB 的 `app.service_url` 与 `app_service.url/default_url`。
- 若 `run-as=package`，脚本可写自己的 `ports.conf`，服务也可能变更，但通常无权写 PostgreSQL，导致 Web 服务已换端口而 AppCenter “打开”仍指旧端口。

注意：

- 如果让 `config_callback` 以 root 运行，主服务是否降权要按实际 AppCenter 生命周期一致性验证。
- 该应用最终选择让 `main start` 保持 AppCenter 同一权限上下文，避免 root 启动的旧进程与降权新进程出现文件/日志/进程匹配不一致。
- 使用 root 权限必须在报告中说明理由：需要同步 AppCenter DB 与管理系统级代理端口。

## config_callback 必做同步

真实 AppCenter 保存接口形态：

```json
POST /app-center/v1/config/wizard
{
  "appName": "native-web-demo",
  "customParameters": [
    {"key": "wizard_access_port", "value": "18081"},
    {"key": "wizard_proxy_port", "value": "18444"}
  ]
}
```

`config_callback` 必须完成：

1. 校验 `wizard_access_port` 与 `wizard_proxy_port` 为 1–65535 且不同。
2. 写 `${TRIM_PKGETC}/ports.conf`：

   ```sh
   ADMIN_PORT=18081
   PROXY_PORT=18444
   ```

3. 同步已安装 `ui/config` 的 `.url.*.port`。
4. 同步 AppCenter 数据库：

   ```sql
   UPDATE app
      SET status='running',
          service_url='http://${host}:18081/',
          updated_at=now()
    WHERE app_name='native-web-demo';
   UPDATE app_service
      SET url='http://${host}:18081/',
          default_url='http://${host}:18081/',
          updated_at=now()
    WHERE app_id=(SELECT id FROM app WHERE app_name='native-web-demo' LIMIT 1);
   INSERT INTO system_config(type,k,v)
   SELECT 'appAutoUpdate','native-web-demo','false'
   WHERE NOT EXISTS (
     SELECT 1 FROM system_config WHERE type='appAutoUpdate' AND k='native-web-demo'
   );
   ```

   shell 拼 SQL 时 `${host}` 必须保持字面量，推荐用 `chr(36) || '{host}:...'`。

5. 重启服务。
6. 重启后立即同步一次 DB，并后台延迟多次同步，避免被 AppCenter 后续默认写入覆盖：

   ```sh
   sync_appcenter_db running
   ( for delay in 3 8 15; do sleep "$delay"; sync_appcenter_db running; done ) >/dev/null 2>&1 &
   ```

## 路径解析坑位

真实安装结构可能拆分为：

- `cmd/main` / `cmd/config_callback` 在 `/var/apps/{app}/cmd/`
- 二进制在 `/volN/@appcenter/{app}/bin/`
- `TRIM_APPDEST` 在不同 callback 环境中不一定指向能找到 `cmd/main` 的目录

因此 `config_callback` 不要只写：

```sh
MAIN_SCRIPT="${TRIM_APPDEST}/cmd/main"
```

推荐解析：

```sh
resolve_main_script() {
  if [ -x "/var/apps/${APP_NAME}/cmd/main" ]; then
    echo "/var/apps/${APP_NAME}/cmd/main"
  elif [ -x "${APP_ROOT}/cmd/main" ]; then
    echo "${APP_ROOT}/cmd/main"
  elif [ -x "${APP_ROOT}/target/cmd/main" ]; then
    echo "${APP_ROOT}/target/cmd/main"
  elif [ -x "/vol1/@appcenter/${APP_NAME}/cmd/main" ]; then
    echo "/vol1/@appcenter/${APP_NAME}/cmd/main"
  else
    echo ""
  fi
}
```

`cmd/main` 解析二进制目录也要同时支持：

1. `${APP_ROOT}/bin/native-engine`
2. `${APP_ROOT}/target/bin/native-engine`
3. `/vol1/@appcenter/${APP_NAME}/bin/native-engine`

## 端口切换时的进程匹配

不要用“新端口”查旧进程：

```sh
# 错误：端口从 18080 改到 18081 后，旧进程仍是 18080，找不到
pgrep -f "native-engine .*--adminAddr :${APP_PORT}"
```

推荐按应用身份匹配，例如 `cachePath`：

```sh
find_running_pid() {
  pgrep -f "native-engine .*--cachePath ${TRIM_PKGETC}" 2>/dev/null | head -n 1 && return 0
  pgrep -f "${KS_BIN} .*--adminAddr" 2>/dev/null | head -n 1
}
```

这样端口从 `18080 -> 18081` 时，`main stop` 能找到并停止旧 `18080` 进程，再启动新 `18081` 进程。

## 必须用真实 AppCenter 保存链路验证

手工用环境变量直接执行：

```sh
wizard_access_port=18081 wizard_proxy_port=18444 /var/apps/native-web-demo/cmd/config_callback
```

只能证明脚本逻辑可运行，不能证明飞牛页面真实链路可用。必须通过 AppCenter UI 或等价 API 验证：

```text
POST /app-center/v1/config/wizard
```

### 五点一致性检查

保存 `18081/18444` 后必须同时满足：

1. 设置页 wizard 回显：`18081/18444`
2. `ports.conf`：`ADMIN_PORT=18081`、`PROXY_PORT=18444`
3. `ui/config`：`.url.native-web-demo.app.port = 18081`
4. AppCenter `config/detail`：`urls.port = 18081` 且 `defaultUrls.port = 18081`
5. 实际进程与端口：
   - `native-engine --adminAddr :18081 --localAddr :18444`
   - `http://127.0.0.1:18081/` 返回 200
   - `https://127.0.0.1:18444/v2/` 返回 200
   - 旧 `18080/18443` 不再响应

再保存回默认 `18080/18443` 做反向验证。最后验证 AppCenter stop/start 后仍能按当前配置恢复。

## 最终交付 checklist

- `fpk.py build --arch amd64` 生成最终 FPK，并记录 SHA-256。
- 完整卸载旧版，确认 `status=noinstall` 后安装最终 FPK。
- 安装默认端口 `18080/18443`，验证 Web 与代理均可用。
- 通过真实 AppCenter 设置保存到 `18081/18444`，执行五点一致性检查。
- 保存回默认 `18080/18443`，再次执行五点一致性检查。
- AppCenter stop/start 后当前端口可用。
- 发布到公开仓库时只上传必要交付物：FPK、`.sha256`、源码包、README/metadata；不要上传远程验证日志、设备路径、账号、IP 或测试过程报告。
