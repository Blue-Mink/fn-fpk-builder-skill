# AppCenter 状态、数据库与真实运行态联合校验

fnOS AppCenter 的 UI 状态、数据库字段和应用真实运行态可能短暂或长期不一致。不要只凭 `appcenter-cli status`、容器 healthy、或者端口 200 中任意单点判断应用可交付。

## 核心模型

至少区分五层状态：

1. **真实运行态**：进程、容器、端口监听、HTTP health。
2. **AppCenter CLI 状态**：`appcenter-cli status {appname}`。
3. **AppCenter 数据库 `app` 表**：`status`、`service_url`、`is_stop`、`is_uninstall`。
4. **AppCenter 数据库 `app_service` 表**：`url`、`default_url`、`type`、`gatewaySocket`、`gatewayPrefix`。
5. **UI/设置来源**：`ui/config`、`wizard/config`、`cmd/config_init` 输出、`config/detail` / `config/wizard` 接口。

这五层可以互相不一致。交付判断必须以“五点一致”为准。

## 最小只读诊断

```bash
appcenter-cli status {appname}
ss -lntp | grep ':{port}' || true
curl -sS -D - --max-time 5 http://127.0.0.1:{port}/health-or-api -o /tmp/body
psql -h /var/run/postgresql -d appcenter -U postgres -P pager=off <<'SQL'
SELECT id, app_name, name, status, service_url, is_stop, is_uninstall, updated_at
FROM app WHERE app_name='{appname}';
SELECT s.id, s.service_name, s.type, s.url, s.default_url, s.full_url, s.gateway_socket, s.gateway_prefix, s.control, s.updated_at
FROM app_service s JOIN app a ON a.id=s.app_id WHERE a.app_name='{appname}';
SELECT type,k,v FROM system_config WHERE k='{appname}' OR (type='appAutoUpdate' AND k='{appname}');
SQL
cat /var/apps/{appname}/target/ui/config 2>/dev/null || cat /vol1/@appcenter/{appname}/ui/config 2>/dev/null
```

不要在公开日志里保留真实 cookie、token、密码或私有域名。

同样的查询连同一遍 HTTP 探活，可以用技能自带 CLI 一次采齐（`--json` 便于比对前后差异）：

```bash
python3 "$SKILL_DIR/scripts/fnos.py" verify-web-app {appname} --host root@<NAS_IP> --json
```

手工敲适合单次定位；反复回归、或要在交付报告里附证据时用 CLI，避免漏项。

## 常见不一致与修复方向

### AppCenter running，但服务没起来

- `cmd/main status` 可能无条件返回 0。
- Docker 容器可能只是 `Created` 或 unhealthy。
- 修复：`status` 必须检查真实进程/容器；`install_callback` 调用同目录 `main start`；必要时缩短 start 阻塞，避免 AppCenter 超时改写状态。

### 服务健康，但打开按钮指向旧端口

- `ui/config`、`app.service_url`、`app_service.url/default_url` 没同步。
- 修复：`config_callback` 同步全部入口并延迟多次回写，避免 AppCenter 后续默认写入覆盖。

### 应用设置页无法连接或 panic

- `app_service` 缺 URL、`system_config` 缺 `appAutoUpdate` 等记录时，后端配置详情可能 500。
- 修复：安装或启动后幂等补齐必要 DB 行；重启 `trim_app_center.service` 后再读 `config/detail` 验证。

### 设置页回显旧值

- `wizard/config initValue` 只能作为默认值；真实回显应由 `cmd/config_init` 输出当前状态覆盖。
- 修复：`config_init` 从持久配置、已安装 `ui/config` 或 DB 读取真实端口并输出 `wizard_*={value}`。

## 应用停用后桌面入口还在

**症状**：应用中心里已“停用”，桌面图标仍在；点开只剩反代失败页或空白，用户以为应用崩了。

**根因**：桌面入口是平台在**安装/升级时**从 `ui/config` 的 `.url` 表导入 `app_service` 的
**静态条目**（`no_display='f'` 即可见）。它与进程无关——停用只停服务，**不回收、不隐藏**入口行；
只有卸载删行，重装再按新的 `ui/config` 重建。

**可选修法（应用侧，要求生命周期脚本是 root）**：

| 时机 | 动作 |
| :--- | :--- |
| `stop` 成功 | `UPDATE app_service SET no_display='t' WHERE service_name LIKE '<appname>.%'`，同时写隐藏标记（放 `@appdata`，跨重装保留） |
| `start` | 置回 `'f'` 并清标记 |
| `status` 健康且标记在位 | **自愈**补 `'f'`：平台“启用”可能只轮询 `status`、不回调 `start`（实测过两次） |

护栏（缺一不可）：

- **SQL 失败一律静默跳过**。`psql`/`sudo` 不存在、连不上库、权限不足都不能让生命周期脚本非零退出——
  入口显隐是锦上添花，不值得起用启停失败去换。
- `LIKE` 模式必须带应用前缀 `<appname>.%`（入口 ID 按约定就是 appname 前缀），**绝不做全表更新**。
- 写库前先按 `app_id` 或入口 ID 查一次并打印命中行数，确认只覆盖自己那几行。
- 入口自身的失败页仍要能看懂（正确状态码 + 人话说明 + 自动刷新）。隐藏入口只减少误点，不能替代错误页。
- `run-as=package` 的应用**做不到**这件事（连不上库、也没权限）。此时应在交付说明里写明
  “停用后桌面入口仍在”属平台行为，别在文档里承诺联动。

## 写库注意事项

- shell 中要保留字面量 `${host}`，不要被 shell 展开。推荐 SQL 里用 `chr(36) || '{host}:...'` 拼接。
- 写库前确认 appname 精确匹配，不要使用宽泛 `LIKE` 更新多个应用。
- 写库后至少读回 `app` 与 `app_service` 两张表。
- AppCenter 可能在 callback 返回后继续写默认值；关键字段应立即同步一次，并在后台延迟 3/8/15 秒再同步。

## 交付判定

只有同时满足以下条件，才说 AppCenter 链路闭环：

- `appcenter-cli status {appname}=running`。
- 真实进程/容器运行且 health 200。
- `app.service_url` 与 `app_service.url/default_url` 指向当前入口。
- `ui/config` 与入口端口/类型一致。
- `config/detail` / `config/wizard` 显示当前值。
- stop/start 会真正传递到进程/容器。
- 修改端口后新端口可用、旧端口不可用，再改回默认也成立。
