# 虚拟机承载型 FPK 实机沉淀（libvirt / KVM）

来源：两个 fnOS 虚拟机承载型应用（一个路由器系统、一个智能家居系统）的多轮装机实测。判定适用：应用需要自己 `virsh define/start` 虚拟机、依赖飞牛「虚拟机」应用提供 libvirt/OVMF/virsh，或需要一个常驻端口替虚拟机做地址寻踪。Docker Web 应用看 [docker-app-flow.md](docker-app-flow.md)，不要套用本页。

本页所有主机地址一律写作 `<NAS_IP>` / `<VM_IP>`，示例不含任何设备凭据。

## 目录

- [架构](#架构)
- [190 秒安装回调看门狗与秒回模式](#190-秒安装回调看门狗与秒回模式)
- [平台生命周期真机语义](#平台生命周期真机语义)
- [磁盘、网卡身份与卸载语义](#磁盘网卡身份与卸载语义)
- [常驻寻踪入口](#常驻寻踪入口)
- [拿不到 IPv4 的排障与串口自救](#拿不到-ipv4-的排障与串口自救)
- [反代与统一网关下的前端链接](#反代与统一网关下的前端链接)
- [装机与重装操作序列](#装机与重装操作序列)
- [交付 checklist](#交付-checklist)

## 架构

```text
cmd/install_callback        只做参数校验 + 写状态文件 + 拉起后台 worker，立即 exit 0
app/bin/<app>-install-worker.sh   状态机：下载 → 校验 → 预置 → 扩容 → define → 起入口服务 → ready
libvirt domain + qcow2      磁盘注册在 libvirt 存储池（不要放应用目录）
app/bin/<app>-web-redirect.py     systemd 常驻单元：固定入口端口 + 地址寻踪 + 可选反代
AppCenter 数据行             平台在安装后自写，应用只在自己的服务真起来后校正
```

- 入口服务用应用自带的 systemd 单元（如 `<app>-web.service`），并在卸载回调里删掉；平台不会替你管这个进程。
- 单元里 `StartLimitIntervalSec` 属于 `[Unit]` 段，放进 `[Service]` 会被老 systemd 告警忽略。
- 虚拟机应用需要 `run-as=root`（`virsh`/libvirt 与网络预置都要特权），并在发布报告里写明理由；能降权启动长期服务的场景应显式降权。
- 依赖飞牛「虚拟机」应用时，`manifest.install_dep_apps` 里声明它，并在 `doctor` 阶段确认 `virsh -c qemu:///system list --all` 可用。

## 190 秒安装回调看门狗与秒回模式

**现象**：下载或预置耗时较长的应用，`appcenter-cli install-fpk` 会同步卡住约 **190 秒**后被斩首，报 `APP_INSTALL_FAILED_INSTALL_CALLBACK_EXCEPTION`，CLI 自身在 `install_fpk.go:66` 段错误。**任何可能超过三分钟的安装在回调里同步跑都必死。**

**修法（秒回模式）**：

1. `cmd/install_callback` 只做三件事：参数校验、写 `/tmp/<app>.install.state`、拉起后台 worker，然后 `exit 0`。
2. worker 放 `app/bin/`，用 `systemd-run --unit=<app>-install --collect` 拉起，把它挪出平台进程树（普通 `setsid nohup` 实测也能存活，但 `systemd-run` 便于 `journalctl -u` 取证）。
3. 状态机文件化，前端入口页与排查都读它：
   `starting → queued → downloading → verifying → extracting → provisioning → resizing → defining-vm → entry-service → ready`，失败写 `failed:<原因>`。
4. worker 加锁文件防并发；`cmd/main start` 要能按原参数续跑未完成的安装。
5. 环境变量经 `/tmp/<app>.install.env` 传递（`printf '%q'` 序列化），不要在回调里假设 worker 继承了什么。

**秒回模式配套三坑（都曾造成静默失败）**：

- **状态空窗被判崩溃**：安装返回后、systemd 单元创建前，`main status` 若返回非 0，平台判 `APP_CRASH` 并**自动执行卸载清理**（连单元一起删）。入口/守护型应用 `status` 必须恒 0，理由见下节。
- **快速路径蹭变量**：worker 里"磁盘已存在就跳过下载"这类快速路径会绕过前段赋值。后段用到的每个变量（`APP_DIR`、`QCOW2_FILE` 等）必须在自己的段内独立解析，否则拼出 `/bin/xxx` 这种空路径静默失败。
- **`systemd-run` 撞名**：`--collect`/`--remain-after-exit` 的瞬时单元结束后单元名会残留，再 `--unit=同名` 必失败。拉起前先 `systemctl stop <unit>` + `systemctl reset-failed <unit>`。
- 回调里 `bash -c '...'` 字符串内的语法错误，外层 `bash -n` 查不出来——发布前把字符串提出来单独 `bash -n`。

## 平台生命周期真机语义

这几条只能靠实机留痕取证得到，且与直觉相反。**取证手法**：在 `cmd/main` 首行加一行调用留痕，`ppid` 会直接暴露调用方是 `trim_app_center`：

```bash
echo "$(date -Iseconds) arg=$1 ppid=$(ps -o args= -p $PPID)" >> /tmp/<app>-main-calls.log
```

| 实测结论 | 影响 |
| --- | --- |
| 平台点「启用」时**只回调 `stop`，从不回调 `start`**（调用留痕里只有 `status` 轮询） | 不要设计"停用期让 `status` 返回 3，好等平台回调 `start`"——这条路径不存在 |
| 停用窗口内 `status` 返回 3 会被判 `APP_CRASH`，之后平台连补开机都不做，应用卡在异常态 | 常驻入口型 / 守护型应用 `status` **恒返回 0**；这与官方契约"3 表示未运行"是有意的偏离，必须在 README 与发布报告里写明取舍 |
| `manifest` `checkport=true` + 入口端口 7×24 常驻 → 「启用」报 `error code 11000`，journal 里 `APP_START_FAILED_PORT_USAGE PORT_USAGE:<port>` | **入口常驻型应用必须 `checkport=false`**（端口由应用自己管） |
| `main stop` 里阻塞等待优雅关机（最长 60s）会被状态机咬：期间的「启动」被判 already started 直接跳过；更晚到达的回滚 stop 还会把刚开好的机器关掉 | `stop` 发完 ACPI 立即返回，收尾交给 setsid 守望进程；`stop` 只在虚拟机真在跑时才写"用户主动停用"标记 |
| 守望进程 12×5s 后无条件强制断电，会把期间被重新开机的虚拟机再关掉（竞态实锤） | 停机/开机各写请求代号令牌（如 `/tmp/<app>-vm-start.req` 的 mtime），守望在强制断电前比对，开机代号更新即放弃 |
| 入口页一键开机/自动补开是直连 `virsh start`，AppCenter 不知情仍挂「已停用」；而它一旦自认为已停用，点「停用」就**不再回调 `cmd/main stop`**，虚拟机在跑却关不掉 | 入口开机后后台补敲一次 `appcenter-cli start <app>` 扳回记账。三条护栏：后台线程执行（该调用约 2 秒）、加冷却（如 60s）、执行前再查停用标记（用户刚点停用就不抢方向盘） |
| 补敲 `appcenter-cli start` 前必须证明它**不会重启你的服务进程**（真机 pid 前后对照不变），否则会自杀式循环 | 已验证：入口服务 pid 不变 |
| `appcenter-cli start` 报 `error 10500` = 平台在 install 后已自动 `APP_STARTED`（已在运行），不是故障 | 别把它当失败去重试 |

停用/启用的正确分工：`stop` 真关机 + 写用户停用标记；`start`（若被调）先等守望落定再开机并在 10 秒内确认落到 `running`，否则重试一次；入口页在虚拟机非用户停机时自动补开，用户停机时显示"已关机 + 启动虚拟机"按钮。

## 磁盘、网卡身份与卸载语义

- **磁盘位置**：装机完成后把 qcow2 注册进 libvirt 存储池（`/vol1/vm/pool/<app>.qcow2`），并在 worker 里删掉共享目录副本。
- **重装保盘**：判断"是否已有磁盘"必须**同时**看共享目录与存储池。只看共享目录会导致重装走"重新下载 + `virsh vol-delete` + `cp` 覆盖"，把用户系统盘刷成出厂状态——这是本项目历史上最严重的数据丢失缺陷。
- **版本标记不要放在池目录里**：目录型 libvirt 池会把池内任何杂项文件当卷列出来。版本标记写 `/vol1/vm/<app>.disk-version`。
- **换版本**：磁盘内系统版本与向导所选不符时，先把旧盘整体留档到 `/vol1/vm/backup/<app>.qcow2.swap-<ver>-<ts>` 再装所选版本；版本判不出来时**保守复用，绝不覆盖**。老装机没有标记就退回从 domain XML 的 `<osVersion>` 推断并补写标记。
- **网卡身份**：重装/升级要沿用上一次 domain 的 UUID 与 MAC，并把 MAC 落盘（`/vol1/vm/<app>.vm-mac`）——卸载会 `undefine --nvram` 掉定义。换 MAC 会让路由器按 MAC 的绑定与租约全废、按 MAC 认网口的系统认错口，表现为"寻踪突然失效"。
- **卸载语义按应用类型分裂，不要在 README 写绝对结论**：
  - 虚拟机类：磁盘在 libvirt 池里，卸载回调只 `destroy` + `undefine --nvram`，**磁盘和配置不会被删**（除非卸载向导显式传删除动作）。被删的只有 UEFI 变量文件，缺了从 `/usr/share/OVMF/OVMF_VARS.fd` 复制即可。
  - Docker 类：平台卸载会连镜像一起删。
  - 某些 native 应用：卸载会清空 `@appconf`（即引擎真正的配置目录，`--cachePath` 指这里），重装后配置全回内置默认 → **重装前必须备份**。
- **`qemu-img` 扩容判断**：必须 `qemu-img info --output json` 读 `virtual-size`。用 `grep -o "virtual size: [0-9]*" | awk '{print $3}'` 拿到的是 **GiB 数字**（母盘 `4 GiB` → 4），拿它和 `目标GB*1073741824` 比会永远判"盘不够大"，于是对已经足够大的盘调 `qemu-img resize`（qcow2 不能缩）→ 误报"磁盘扩容失败"。解析失败按 0 处理，老实走扩容分支。
- **大文件校验**：不要对 GB 级 qcow2 做 `md5sum`（会把 ssh 会话拖到超时）。验证"盘没被动"用 `stat -c '%s %y'`。
- **数据卷权限**：某些卷上 `umask 000` 会造出 000 权限文件（连目录都 `d--------`），qemu 报 `Could open ... Permission denied`。正确做法是 `chown libvirt-qemu:libvirt-qemu` + 权限 `604`，关键在属主而不是目录位。

## 常驻寻踪入口

固定端口 + 动态虚拟机地址是这个应用形态的核心。多级发现链与实测耗时（局域网规模基线，越快越先用）：

| 顺序 | 手段 | 量级 | 备注 |
| --- | --- | --- | --- |
| 1 | 记录的 MAC 查 ARP 表 | ~16 ms | 最快，但要求表项 `REACHABLE`/`FAILED` 之外的有效态 |
| 2 | 虚拟机控制台画面直读（VNC 帧 + 自嵌字形 OCR / serial 探测） | ~0.1 s | 随网络就绪即出现，不依赖 VM 与 NAS 互通；读到的地址必须再探活才采信 |
| 3 | mDNS | 秒级 | 取决于 guest 是否发 |
| 4 | `virsh domifaddr` | ~45 ms | 无 guest agent 时常常返回空 |
| 5 | 局域网 ping 扫描 | 整段秒级 | 必须跳过 `docker*`/`br-*`/`virbr*`/`veth*`/`vnet*`/`tun*`/`tap*` 接口，否则会把桥网段一起扫 |
| 6 | 端口/HTTP 指纹 | 百 ms 级 | 最终确认"哪扇门开了" |

**闸门与状态口径（都是实机踩出来的）**：

- 就绪判定必须是 **HTTP 响应级**，不能只看 TCP 能连。核心服务重启期常见"socket 在听、一发请求就断"，浏览器会落到空白错误页。
- **按"要去的那扇门"分别把关**：管理后台端口常常先于业务端口就绪。若状态是"任一门开就算就绪"，入口会把人 302 到还没起来的业务端口。
- 候选地址被探活否掉后必须**降级**并清空候选，否则页面能挂着"已找到 `<VM_IP>`，服务还在起来"几十分钟，把排障方向带偏；ARP 表只认已完成表项。
- 刚开机的宽限期（如 90 秒）内一律显示"还在起来"，不许断言"路由器 DHCP 没发地址"——那时只是 ARP 还没建起来。宽限期锚在**开机时刻**，不要锚"上次成功"（重启后那次成功早过期）。
- 入口页/状态接口**绝不把自家控制路径透传给上游**（`/power/*` 等），且 `meta refresh` 必须显式带 `url=`，否则浏览器停在控制路径上重新 GET，被透传成上游 404。
- 发现逻辑改成"后台线程单飞 + 请求侧只读缓存"时，**发现锁必须是可重入锁（`RLock`）**：若发现链内部还要再拿同一把 `threading.Lock`，第一发就自我死锁——表现为端口在听、TCP 连得上、HTTP 永不响应，而 AppCenter 显示"运行中"。
- 开关机态与地址缓存**不要混用同一个结论**：页面每次显示开关机态时应做一次廉价实判（`virsh domstate` 约 0.1 秒，加 1 秒记忆），否则关机后按钮要等下一个发现周期（无人访问时可能 20 秒）才出现。
- 不要在请求路径里调平台 CLI（约 2 秒级），放后台线程。

## 拿不到 IPv4 的排障与串口自救

**先纠正一个极易犯的观测错误**：在 OVS 桥的内层口或物理口上用 `AF_PACKET` 抓 DHCP，会"只见 DISCOVER、不见 OFFER"——OFFER/ACK 是**单播**发给 vnet 口的，学习交换机不泛洪给内层口；而 OVS 用 `netdev_rx_handler_register` 在 `ptype_all` 之前就把包收走，物理口上也看不见 RX。所以"零 OFFER"**不是**"这条 LAN 没有 DHCP 服务器"的证据。要定性就去路由器看租约列表与地址池（同一 MAC 过一会儿自己拿到地址，就是最便宜的反证）。

虚拟机没有网络时的唯一入口是串口，经 libvirt 直接喂命令：

```bash
script -qec "virsh -c qemu:///system console <domain>" /dev/null
```

入口页可以据此做一个"网络修复"页（全部经串口执行，不需要虚拟机有网络）：重试 DHCP、给 LAN 设静态地址（参数必须严格校验为合法 IPv4/CIDR）、重启 guest 网络、手动指定跳转地址（存到应用共享目录，跨重装生效，探活通过才优先跳）。要点：

- **绝不在 netifd 托管的接口上手跑 `udhcpc -n`**：它退出时给脚本发 `deconfig`，把地址和路由一起冲掉，静态口当场 `Network unreachable` 整机失联，只能 `/etc/init.d/network restart` 救回（netifd 只补地址不补路由）。正确做法是 `ifup lan` + `ubus call network.interface.lan udhcpc renew`。把这条约束写成离线用例断言（源码里不许出现 `udhcpc -n`）。
- 串口动作耗时稳定在数十秒量级；页面用后台线程 + 前端轮询，并加并发挡；修复页本身不要自动刷新。
- 首启慢属预期，要在向导、入口页和 README 三处做一致的预期管理（例如容器化智能家居系统 首次拉镜像 15~40 分钟，之后每次开机分钟级）。

## 反代与统一网关下的前端链接

**症状**：经 NAS 端反代/网关访问时，管理面板里的按钮跳到 NAS 的别的端口（甚至跳到飞牛登录页）或直接 `chrome-error`；**直连虚拟机端口时同一个按钮是好的**。

**根因**：前端按 `location.hostname` + 兄弟端口拼 URL（端口取自它自己后端接口的字段，或干脆写死）。经反代后 `location.hostname` 变成 NAS，于是端口落在 NAS 上。

**取证**：用 playwright `add_init_script` 包一层 `window.open` 记录实参，走反代与直连各点一遍，两条 URL 一比就露馅。

**修法**：反代往 HTML 注入一段**由本反代自供、`no-store`、每次现算虚拟机 IP** 的经典脚本，接管 `window.open` 并在捕获阶段改写 `<a href>`，规则是"只改写与本页同主机、且有效端口 ≠ 本反代端口的链接"，端口原样保留；寻不到 IP 时退化为空操作（不要拼出 `http://:80` 这种废地址）。脚本要注在 `<head>` 开头——面板主脚本多为 `type=module`（默认延迟），经典脚本先执行才赶得上点按钮。代价要在 README 写清：这些按钮是浏览器直连虚拟机 IP，非局域网环境照样打不开。

**SPA 走统一网关前缀**（`/app/<app>/`）时，前端路由必须注入 `basename`，否则路由表只认 `/login`，网关路径命中 catch-all → 被 auth guard 重定向回 `/login` → document reload 无限刷新。注入用结构化正则匹配压缩产物（alias 名会变），改 JS 响应时剥离 `Accept-Encoding` 以便改写，并给所有响应加 `no-store`。

## 装机与重装操作序列

```bash
appcenter-cli status <appname>
appcenter-cli stop <appname>
appcenter-cli uninstall <appname>
appcenter-cli install-fpk /path/<app>-<ver>-fnos-amd64.fpk --volume <N> --env /tmp/<app>.env
appcenter-cli start <appname>
```

- `--env` 接收的是**环境变量文件路径**（每行 `KEY=VALUE`），不是 `key=value` 字符串。传字符串会报 `[Error]Something wrong with environment variables.`（内部是 `error while binding environment variable`）。
- 重装的 env 必须**照抄首次装机那份**，否则会把向导值（内存、磁盘、端口）改掉。
- 载荷等价性验证走"源码 / 包内 / 设备"三方 `md5sum` 比对；不要拿整包 SHA 比对重建产物——`fnpack` 打 tar 带条目 mtime、gzip 头带压缩时间戳，**整包哈希不可字节复现**。要判等价就解包 `diff -r` 比载荷文件。
- 打包前确认没有 `__pycache__`/`*.pyc` 混进 `app.tgz`（`py_compile`、临时 import 都会生成，实测让包体多出几十 KB 并污染载荷）；跑测试脚本时带 `PYTHONDONTWRITEBYTECODE=1`。
- 图标类改动别忘了服务端缓存 7 天 `immutable`，验证以设备侧文件 `md5sum` 为准，不要以用户端截图为准。

## 交付 checklist

1. `install-fpk` 秒级返回（秒回模式生效），状态文件按序推进到 `ready`；`journalctl -u <app>-install` 无未捕获错误。
2. 复用磁盘路径下，存储池内 qcow2 的**大小与 mtime 不变**（别整盘哈希）；换版本路径下 `backup/` 里确有 `swap-*` 留档。
3. `appcenter-cli status` 与虚拟机实际状态自洽；连续"停用 → 打开入口 → 开机"来回不出现 `APP_CRASH`、`PORT_USAGE`、自动卸载。
4. 入口开机后 AppCenter 记账在数秒内自动同步为运行中（后台补敲生效）。
5. 三条链路分别验证：默认入口、业务子入口（如 `/ha`）、网络修复页；再加一次直连虚拟机端口对照。
6. 无网络场景跑一次串口自救路径，确认不会把 guest 网络搞失联。
7. 重装后 MAC/UUID 不变、磁盘不变；`journalctl -u trim_app_center` 无异常事件。
8. 包内无 `__pycache__`、无设备地址/凭据/令牌；README 与向导的预期管理口径一致。
