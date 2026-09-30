# 实机案例：三类应用的完整交付链路

这三段是真实项目从构建到验收的记录，用来对照「做到什么程度算交付完成」。
规范条文在各自的流程文档里，本文件只讲**当时怎么走的、哪些路被排除了**；
写新应用时按类别找对应案例，再回流程文档核对细节。

- [Docker Web FPK 最终链路](#case-study-docker-web-fpk-最终链路)
- [Native Web FPK 端口设置闭环](#case-study-native-web-fpk-端口设置闭环)
- [虚拟机承载型 FPK（两个样例）](#case-study-虚拟机承载型-fpk两个样例)

## Case Study: Docker Web FPK 最终链路

> 完整流程见 [references/docker-app-flow.md](docker-app-flow.md)，历史排障见 [references/remote-testing.md#docker-web-应用专项调试记录](remote-testing.md)。

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

> 完整流程见 [references/native-web-port-flow.md](native-web-port-flow.md)。

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

- 只手工执行 `wizard_access_port=<port> ... config_callback` 不等于真实飞牛页面链路；必须用 AppCenter UI 或 `POST /app-center/v1/config/wizard` 验证。
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

> 完整流程见 [references/vm-app-flow.md](vm-app-flow.md)，症状索引见 [references/troubleshooting.md#虚拟机承载型应用实机沉淀](troubleshooting.md)，验证与取证方法见 [references/offline-and-live-testing.md](offline-and-live-testing.md)。

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
