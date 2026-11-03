# 入口、统一网关与 CGI：选型、契约与验收

飞牛的桌面入口不只是一个端口：安装/升级时平台把 `ui/config` 的 `.url` 表导入 `appcenter` 库的
`app_service` 表（一条 = 一个桌面图标），运行时按 `type` / `port` / `gatewaySocket` 走不同通道。
选错通道的典型症状是入口空白、502、混合内容被拦、点了没反应——而 `fnpack build` 一个错都不会报。

## 1. 选型表

| 后端形态 | 入口写法 | 关键字段 | 注意 |
|---|---|---|---|
| 自带 HTTP 端口，要求顶层新窗口 | `type=url` + `protocol` + `port` | `url` 可带路径与 query | **明文 http 服务必须走这条**，iframe 会被混合内容拦截 |
| 自带 HTTP 端口，想嵌在飞牛窗口内 | `type=iframe` + `protocol` + `port` | 同上 | 只有 https/同源才安全；跨源读不到 `document.title` |
| 只监听 Unix Socket、要登录身份、要 WebSocket | `type=iframe` + `gatewaySocket` + `gatewayPrefix` | §2 | 推荐给常驻服务；端口可以完全不暴露 |
| 已有 CGI 脚本产出 HTML | `type=iframe` + `url=/cgi/ThirdParty/<appname>/index.cgi` | §3 | 平台先校验登录态；不支持 WebSocket |
| 文件关联打开 | `type=url` + `fileTypes` | §4.1 | 设备上见过非空样例（如 `["txt","sml",…]`）；空数组=不注册 |

## 2. 统一网关

### 2.1 声明

```json
{".url": {"demo.Application": {
  "title": "Demo", "type": "iframe", "icon": "images/ICON.PNG",
  "gatewaySocket": "app.sock", "gatewayPrefix": "/app/demo", "url": "/app/demo/"
}}}
```

- `gatewaySocket` **只写文件名**，不要写路径；平台解析成 `${TRIM_APPDEST}/<文件名>`。
- `TRIM_APPDEST` = `/var/apps/<appname>/target`，它是指向 `/vol1/@appcenter/<appname>` 的软链，
  所以 socket 的真实位置是 `/vol1/@appcenter/<appname>/app.sock`（设备实测）。
- `gatewayPrefix` 写 `/app/<appname>`，**不要以 `/` 结尾**；设备实测全部是这个一段式前缀，更深的子路径（如 `/app/demo/v2`）**未验证**，不要默认依赖。
- `url` 用 `/app/<appname>/`（带尾斜杠，桌面点开即该路径）。

### 2.2 前缀不会被改写（实测）

网关**不剥前缀**：应用必须先自己处理 `/app/<appname>` 再路由。实测 Moo：

```bash
# 同一个 socket，带前缀 200、不带前缀 404 ⇒ 前缀是应用自己的责任
curl -s -o /dev/null -w '%{http_code}\n' --unix-socket /vol1/@appcenter/moo/app.sock \
  http://127.0.0.1/app/moo/api/about      # 200
curl -s -o /dev/null -w '%{http_code}\n' --unix-socket /vol1/@appcenter/moo/app.sock \
  http://127.0.0.1/api/about              # 404
```

SPA 的 `base`、静态资源 URL、WebSocket 升级路径都要挂在前缀下，否则表现为白屏 / 资源 404 / WS 连不上。

### 2.3 socket 权限

- 平台代理以 root 连接，`srwx------ root root`（0700）可用（Moo 实测）。
- 应用以包用户运行时，需要放宽到 `0666`/`0777` 或同组可写；设备上三种形态都见过
  （moo `0700 root`、dog-dev `0666`、magpie `0777` + 自有属主）。
- 创建后立刻 `chmod`，不要依赖 umask；重装后目录属主可能变化。

### 2.4 身份头

平台校验会话后转发，注入 `X-Trim-Userid`、`X-Trim-Isadmin`、`X-Trim-Username`：

- 只信任来自网关的请求；直连端口必须拒绝或**剥离**同名头，否则本机任何进程都能伪造管理员。
- 身份头只说明「是谁」，业务权限仍由应用自己判（Moo 另要求 `X-Moo-Admin: 1` 才开管理 API）。
- 再套一层反代时要透传并防注入。

### 2.5 WebSocket / 长连接

网关支持 WebSocket，CGI 不支持；WS 路由必须和 HTTP 一样落在 `gatewayPrefix` 下。
大文件下载、SSE、长轮询建议额外暴露端口直连——网关的缓冲/超时策略未逐版本验证，不要假设等同直连。

## 3. CGI 入口

- `url` 形如 `/cgi/ThirdParty/<appname>/index.cgi`，带尾斜杠 `.../index.cgi/` 也存在于线上（两种都见过）。
- 平台**先校验 NAS 登录态**再调用；不需要自己实现登录，但**授权仍要自己做**。
- 脚本必须可执行；请求路径要规范化，禁止 `..`、编码绕过、符号链接逃逸出静态根。
- 映射根为 `/var/apps/<appname>/target`（= `TRIM_APPDEST`）。
- 不支持 WebSocket，不适合长请求 / 高流量 / 常驻服务。
- 404 排查：路径拼写、`desktop_applaunchname` 指向、可执行位、映射根下文件是否存在。

## 4. 多入口与可见性

- 入口 ID 约定 `<appname>.<EntryID>`，`manifest.desktop_applaunchname` 必须指向其中一个。
- 同端口不同路径可拆多个入口（如 `/` 与 `/ha`），不必各开端口。
- `noDisplay`（`false` 才显示）、`allUsers`、`control.accessPerm`、`fileTypes`（数组）。
- 安装/升级自动 upsert 到 `app_service`，卸载删行；**停用不回收**——点开只会撞失败页/502。

### 4.1 其它实测字段（设备上真实出现过的写法）

| 字段 | 观察到的用法 | 建议 |
|---|---|---|
| `width` / `height` | 数字，控制 iframe 窗口初始尺寸（如 1280×820） | 桌面弹窗偏小时再加；不写用平台默认 |
| `fileTypes` | 扩展名数组，**不带点**（`["fpk"]`、`["txt","md",...]`） | 与 `noDisplay: true` 搭配注册「打开方式」；空数组=不注册 |
| `${entry.title}` / `${entry.desc}` | 条目标题/描述占位符，平台按实际文件类型替换 | 文件关联类入口用它，避免写死一个类型名 |
| `control` 细项 | `accessPerm` / `fullUrlPerm` / `portPerm` / `pathPerm`（`readonly`/`hidden`/`editable`）、`showRoute`、`auth` | 第三方包通常只写 `control.accessPerm`；其余细项多为平台对系统应用的托管字段，别照抄 |
| `microApp` | 布尔，设备上见过（浏览器类应用） | **语义未验证**，不要照抄；需要窗口化先按 `width/height` 试 |

系统应用还会用平台相对路径（`/p`、`/fn-sync/`）做入口；第三方包不要依赖这些内置路由，用自己的
端口 / 网关 / CGI 前缀。

## 5. 验收

```bash
# 1) 安装后数据库里的真实入口
psql -h /var/run/postgresql -d appcenter -U postgres -tAc \
  "SELECT service_name,type,url,gateway_socket,gateway_prefix FROM app_service s
     JOIN app a ON a.id=s.app_id WHERE a.app_name='<appname>'"
# 2) socket 是否存在、权限是否够
ss -xl | grep '<appname>'; ls -l /var/apps/<appname>/target/*.sock
# 3) root 直连 socket：带前缀 200、无前缀 404（证明应用自己处理前缀）
curl -s -o /dev/null -w '%{http_code}\n' --unix-socket /var/apps/<appname>/target/app.sock \
  http://127.0.0.1/app/<appname>/health
# 4) 走面板网关（带会话）：桌面点开路径 == ui/config 的 url
```

浏览器再确认三件事：标题（同源 iframe 才读得到）、静态资源无 404、WebSocket 能升级。

## 6. 坑对照

| 症状 | 常见根因 | 处理 |
|---|---|---|
| 入口空白 / 被拦 | 明文 http 用了 iframe（混合内容） | 改 `type=url`，或服务加 https |
| 502 / 打不开 | socket 不存在、权限不足、`gatewaySocket` 写了路径 | §2.2 / §2.3 |
| 打开是 404 | 应用没处理 `/app/<name>` 前缀 | 路由加前缀，SPA base 同步 |
| 静态资源 404 | 前端按 `/` 绝对路径打包 | 统一前缀或运行时 base |
| 点开跳登录页 | CGI 未登录 / 会话过期 | 正常行为，先排除路径错 |
| 停用后入口还在 | 入口是静态 DB 行 | 预期行为，见 `appcenter-state-db-runbook.md` |
| `fpk.py inspect` 报 gatewayPrefix | 前缀不是 `/app/...`、带尾斜杠、或 socket 带路径 | 按 §2.1 改 |

相关：`official-contract.md`「用户入口」、`troubleshooting.md` 的 CGI/网关条目、`appcenter-state-db-runbook.md`。
