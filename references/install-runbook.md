# FPK 测试装机操作单（stop → uninstall → install → start）

本页是**照着敲**的操作单：在已装过同一应用的设备上反复装卸以验证新版本、验证载荷、复现问题。方法论与平台语义见 [remote-testing.md](remote-testing.md)，虚拟机承载型的额外序列见 [vm-app-flow.md](vm-app-flow.md)，症状索引见 [troubleshooting.md](troubleshooting.md)。

> **脱敏约定**：主机一律写作 `<NAS_IP>`，应用写作 `<app>`，端口写作 `<port>`，版本写作 `<ver>`；本页不含设备地址、账号口令、构件哈希、私有工作区路径。把本页内容搬到公开仓库前，按 [public-release-hygiene.md](public-release-hygiene.md) 再跑一遍 grep 清单。

## 目录

- [三条铁律](#三条铁律)
- [前置检查](#前置检查)
- [env 文件与安装卷](#env-文件与安装卷)
- [备份](#备份)
- [四步序列](#四步序列)
- [装后验证五个维度](#装后验证五个维度)
- [装机落点速查](#装机落点速查)
- [回滚](#回滚)
- [错误码与判读](#错误码与判读)
- [冷启动等待参考](#冷启动等待参考)
- [可直接抄的脚本](#可直接抄的脚本)

## 三条铁律

| 铁律 | 依据 |
| :--- | :--- |
| **换版本必须四步全走**。`install-fpk` 打在已安装应用上是**空操作**：打印 `[Info]Application [<app>] is installed.`、退出码 0，包内一个字节都没换 | 平台不支持覆盖安装/原位升级；`scripts/fnos.py deploy` 内部执行的也正是"停→卸→验→装" |
| **`start` 报 `code 10500` 不是失败**：`install-fpk` 完成后平台已自动拉起，10500 = 已在运行。判成败只看 `status`、端口与 HTTP | 把它当失败去重试会掩盖真实状态 |
| **卸载前先备份**。删不删数据按应用类型分裂，不能假设 | 见[备份](#备份)三分类 |

`appcenter-cli` 必须 root 运行，否则 `panic: ApplyPermission … dial unix /run/trim_cgi/rpcbroker: permission denied`（它在 `/usr/local/bin/appcenter-cli`）。子命令全集：`install / uninstall / start / stop / check / status / list / install-fpk / install-local / manual-install / default-volume`。

**不要只依赖退出码**：实测某些失败仍返回 0。同时解析输出里的 `[Error]`、`noinstall`、状态文本。

## 前置检查

```bash
APP=<app>; FPK=/absolute/path/<app>-<ver>-fnos-amd64.fpk

sha256sum "$FPK"                                   # ① 本地算哈希并留档
tar -tzf <(tar -xOf "$FPK" app.tgz) | head         # 顺带确认载荷布局与预期一致

appcenter-cli check    $APP                        # ② 装没装
grep -E '^version' /var/apps/$APP/manifest         #    设备上真实版本（别凭记忆）
appcenter-cli status   $APP
tar -tf "$FPK" | grep -c '^wizard/'                # ③ >0 才需要 --env
```

架构规范化：`x86_64|amd64 → *-fnos-amd64.fpk`，`aarch64|arm64 → *-fnos-arm64.fpk`，其他值拒绝，不猜。

## env 文件与安装卷

```bash
# /tmp/<app>.env —— 每行一个 KEY=VALUE；权限 600，绝不回显内容
wizard_access_port=<port>
```

- `--env` 接收的是**文件路径**，不是 `key=value` 字符串。传字符串报 `[Error]Something wrong with environment variables.`（内部是 `error while binding environment variable`）。
- **重装必须照抄首次装机那份 env**，否则会把向导值（端口、内存、磁盘、所选镜像版本）静默改掉——实测有把用户虚拟机内存从 2048 改成 4096 的风险。
- `--volume` 是**卷序号**；默认卷为 0 时可从唯一的 `/volN/@appcenter` 推导，有多个候选时必须让用户显式选。`-v` 在升级场景被忽略（但本流程本来就不走升级）。
- 系统盘应用例外：`TRIM_PKGVAR` 可能落在 `/usr/local/apps/@appdata/<app>`，主配置在 `/usr/local/etc/<app>`。写脚本一律用运行时变量而不是拼 `/vol1/...`。

## 备份

```bash
TS=$(date +%Y%m%d%H%M%S)
tar czf /vol1/${APP}-appdata-bak-$TS.tgz -C /vol1/@appdata $APP   # 数据/日志/pid
tar czf /vol1/${APP}-appconf-bak-$TS.tgz -C /vol1/@appconf $APP   # 配置（目录不存在就跳过）
sha256sum /vol1/${APP}-*-bak-$TS.tgz
```

卸载语义三分类（**逐应用实测，不要外推**）：

| 类型 | 卸载行为 | 应对 |
| :--- | :--- | :--- |
| Docker 承载 | 镜像/卷可能被一并删除 | 事前 `docker commit` 或导出卷数据 |
| 虚拟机承载 | **磁盘不删**（qcow2 在 libvirt 存储池，不在 `@appdata`）；只 `destroy` + `undefine --nvram`（会删 UEFI 变量） | 版本标记与网卡 MAC 单独落盘记录；换镜像版本前把旧盘整体留档 |
| 原生应用 | 有的保留 `@appconf`/`@appdata`，**有的会整目录清空** | 一律先 `tar` 备份再卸 |

判「磁盘/配置是否已存在」要**同时**查共享目录与 libvirt 池，只查一处会导致重装时把用户盘覆盖掉。验「磁盘没被动」用 `stat -c '%s %y' <qcow2>`，别对 GB 级镜像整盘哈希（会把 ssh 会话拖超时）。

## 四步序列

```bash
appcenter-cli stop       $APP
appcenter-cli uninstall  $APP
sleep 2
appcenter-cli check      $APP            # 必须真到 noinstall，别跳过
appcenter-cli install-fpk "$FPK" --volume <N>        # 有向导再加 --env /tmp/$APP.env
appcenter-cli start      $APP            # 10500 忽略
```

| 步骤 | 正常表现 | 坑 |
| :--- | :--- | :--- |
| `stop` | `stop success`，数十秒内返回 | 应用侧 `cmd/main stop` **不许阻塞等待**（阻塞会被状态机咬：期间的 start 被判 already started，留下回滚任务）。若应用是被入口页/`virsh start`/CGI 自行拉起的，平台记账仍是「已停用」，此时 `stop` 是**空操作**——先 `start` 再 `stop` 才真停 |
| `uninstall` | `uninstall success` | 返回 0 也可能压根没卸（随后 `check` 仍显示已装）→ **必须中止**，不要继续装。`code 10150` = 本地应用禁止卸载 |
| `check` | 未装 → 非 `Installed` | 装在一个"半死不活的登记"上是最难复现的一类故障 |
| `install-fpk` | `Installation complete. Use appcenter-cli start <app> to launch.` | 含下载/预置的重活**不能在 `install_callback` 里同步跑**：约 **190 秒**看门狗会斩首并报 `APP_INSTALL_FAILED_INSTALL_CALLBACK_EXCEPTION`，CLI 自身还会 SIGSEGV。解法见[秒回模式](vm-app-flow.md#190-秒安装回调看门狗与秒回模式) |
| `start` | 无输出即成功，或 `code 10500`（正常） | 装完先等冷启动再判，见[冷启动等待参考](#冷启动等待参考) |

## 装后验证五个维度

```bash
echo "== 1 记账";  appcenter-cli status $APP                     # 期望 running
echo "== 2 版本";  grep -E '^version' /var/apps/$APP/manifest    # 平台认这一份
echo "== 3 载荷";  md5sum /var/apps/$APP/target/<关键载荷文件>
echo "== 4 服务";  ss -lntp | grep <port>; \
                  curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:<port>/
echo "== 5 事件";  journalctl -u trim_app_center.service --since "-3 min" --no-pager \
                    | grep -oE 'APP_[A-Z_]+' | sort | uniq -c
```

- **载荷三方一致**才算装对：构建树 == 包内 == 设备。

  ```bash
  md5sum <构建树>/app/<payload 相对路径>
  tar -xzOf $FPK app.tgz | tar -xOf - <payload 相对路径> | md5sum
  ssh root@<NAS_IP> md5sum /var/apps/$APP/target/<payload 相对路径>
  ```

  别拿**整包 SHA** 判"重建是否等价"——`fnpack` 打 tar 带条目 mtime、gzip 头带压缩时间戳，整包哈希不可字节复现；要判等价就解包 `diff -r` 比载荷文件。

- **别只看端口 LISTEN**：要拿到 HTTP 响应才算活（socket 在听但请求被断，是上游服务还在重启的典型形态）。
- **桌面入口表**只在改了 `ui/config` 时才需要查；平台在卸载时清入口行、装机时按新 `ui/config` 自动重建，不需要手写 SQL：

  ```bash
  cd /tmp   # 不 cd 的话 psql 的 "could not change directory" 告警会污染 -tA 输出
  sudo -n -u postgres psql -d appcenter -tA -c \
    "select id,service_name,no_display,icon,url from app_service where service_name like '${APP}%'"
  ```

- **只换图标/静态载荷、入口字段没变** → 可不重装：原地覆盖 payload 层与 `/var/apps/<app>/target` 下对应文件即等价于重装，覆盖后照样跑上面五维验证（见 [temporary-remote-patches.md](temporary-remote-patches.md)，务必记录回滚方式）。
- 图标改动别以用户端截图为准：服务端图标 URL 带 7 天 `immutable`，以设备侧 `md5sum` 为准，并提醒用户强刷。

## 装机落点速查

官方口径（`/var/apps/{appname}` 作为稳定入口、`target`/`etc`/`var`/`home` 的语义）见 [official-contract.md](official-contract.md)。装机排查时按下面这张表找文件，**别走错层**：

| 位置 | 内容 | 注意 |
| :--- | :--- | :--- |
| `/var/apps/<app>/` | 稳定入口层：`manifest`、`cmd/`、`config/`、`ICON*.PNG`、`target`、`etc`、`var`、`home`、`tmp`、`meta` | `cmd/` 脚本**只在这一层**；`target` 常是指向实际安装位置的软链接 |
| `<vol>/@appcenter/<app>/` | 载荷层：真正的程序文件（`ui/*.cgi`、`bin/*`、二进制） | UI 文件**只在这一层**；不要写死这个路径，用 `TRIM_APPDEST` |
| `<vol>/@appconf/<app>/` | 配置 | 用 `TRIM_PKGETC` |
| `<vol>/@appdata/<app>/` | 数据 / 日志 / pid | 用 `TRIM_PKGVAR` |
| `<vol>/appcenter-downloads/` | 平台下载与解包留档（每个装过的版本一份） | 排查"上一个版本装了什么"很有用，但属设备实现细节，不得进入应用逻辑 |

- 装机后版本以 `/var/apps/<app>/manifest` 为准（这才是平台读的那份），不是构建目录里的 manifest。
- 卷号 `<vol>` 由安装卷决定（`0` 常常落在 `/usr/local/apps`、`/usr/local/etc`），系统盘应用尤其要按 `TRIM_*` 变量取值，不要拼 `/vol1/...`。

## 回滚

```bash
appcenter-cli stop $APP && appcenter-cli uninstall $APP && sleep 2
appcenter-cli check $APP                     # 同样要确认到 noinstall
appcenter-cli install-fpk /path/<上一个已验证版本>.fpk --volume <N>
appcenter-cli start $APP
tar xzf /vol1/${APP}-appdata-bak-$TS.tgz -C /vol1/@appdata   # 数据被破坏时
```

只用用户显式提供的回滚包，不从目录里"猜"上一版本。回滚后**必须重新核对 `manifest.version` 与载荷哈希**——只看 `status=running` 会误以为回到了旧版。新包安装失败若留下了应用登记，要先再卸一次并确认 `noinstall`，再装回滚包。

## 错误码与判读

| 码 / 现象 | 含义 | 处理 |
| :--- | :--- | :--- |
| `code 10500` | 已在运行 | 正常，改看 `status` |
| `code 11000` + journal `APP_START_FAILED_PORT_USAGE` | 启动前端口检查失败 | 常驻入口型应用 manifest 必须 `checkport=false`，否则停用后再也启不来 |
| `[Error]Something wrong with environment variables.` | `--env` 传了字符串 | 改传文件路径 |
| `APP_INSTALL_FAILED_INSTALL_CALLBACK_EXCEPTION` | 回调超约 190 秒被杀 | 秒回模式 |
| `APP_CRASH` | `cmd/main status` 返回非 0 被判崩溃，随后可能自动卸载清理 | 入口/守护型应用 `status` 恒 `exit 0`，并把这处偏离官方合约写进 README 与发布报告 |
| `panic: ApplyPermission … rpcbroker` | 非 root 跑 CLI | 用 root |
| `code 10111` | `install-fpk` 被拒 | 按 [troubleshooting.md](troubleshooting.md) 走人工安装路径 |
| `code 10150` | `NOT_UNINSTALL` | 需处理 AppCenter 库记录，见 [appcenter-state-db-runbook.md](appcenter-state-db-runbook.md) |

> 读 journal 的坑：`PORT_USAGE` 在 TRIMEVENT 的 JSON 里同时是**字段名**（值 `0` = 无端口问题）。`grep -oE "PORT_USAGE"` 数事件会把字段名数成错误次数——判端口问题要看值或 `eventId`。同理别用 `grep -c` 一个词代替读完整行。

## 冷启动等待参考

| 应用形态 | 实测等待量级 |
| :--- | :--- |
| 原生单进程 Web | 秒级（`sleep 5~8` 足够） |
| 带数据库迁移的服务 | 十秒级（只等 8 秒会误判失败） |
| Docker + 首次拉镜像 | 分钟级，取决于镜像源速度 |
| 虚拟机承载 | 开机秒级，guest 内服务就绪数十秒；**首次**初始化可到十几~几十分钟 |

首启慢要在**向导文案、入口页、README 三处**给一致口径，否则用户会以为装机失败并反复重装。

## 可直接抄的脚本

技能自带参数化完整版 **`assets/install-runbook/reinstall.sh`**（有离线测试
`tests/test_reinstall_script.py` 钉住四步顺序、卸载后置闸门、wizard/env 前置检查）：

```bash
# 先把 FPK 传到设备上；变量要写在远程命令里（env 不会随 ssh 透传）
scp <app>-<ver>-fnos-amd64.fpk root@<NAS_IP>:/tmp/
ssh root@<NAS_IP> "APP=<app> FPK=/tmp/<app>-<ver>-fnos-amd64.fpk PORT=<port> bash -s" \
  < assets/install-runbook/reinstall.sh
```

它比下面的手抄版多做四件事：包内有 `wizard/` 而没给 `ENV_FILE` 直接拒跑（防装到
一半撞环境变量错）、**卸载后置校验不过就拒绝继续**、备份失败即中止、`DRY_RUN=1`
只打印生命周期命令。系统盘应用记得 `VOLUME=0`。

写成文件后 `ssh root@<NAS_IP> 'bash -s' < run.sh` 执行——含中文或较长的命令串逐条敲容易被本地执行环境拦下。

```bash
#!/bin/bash
# 换应用只改三处：APP / FPK / PORT
set -u
cd /tmp                                  # 避免 psql 告警污染 -tA 输出
APP=<app>
FPK=/absolute/path/<app>-<ver>-fnos-amd64.fpk
PORT=<port>
TS=$(date +%Y%m%d%H%M%S)

sha256sum "$FPK"
tar czf /vol1/${APP}-appdata-bak-$TS.tgz -C /vol1/@appdata $APP 2>/dev/null \
  && sha256sum /vol1/${APP}-appdata-bak-$TS.tgz

appcenter-cli stop      $APP
appcenter-cli uninstall $APP
sleep 2
appcenter-cli check     $APP                       # 人工确认已到 noinstall

appcenter-cli install-fpk "$FPK" --volume <N>      # 有向导再加 --env /tmp/${APP}.env
appcenter-cli start     $APP || true               # 10500 属正常
sleep 8

echo "== status";  appcenter-cli status $APP
echo "== version"; grep -E '^version' /var/apps/$APP/manifest
echo "== listen";  ss -lntp | grep ":$PORT"
echo "== appdata"; ls -l /vol1/@appdata/$APP
echo "== entry";   sudo -n -u postgres psql -d appcenter -tA -c \
  "select id,service_name,no_display from app_service where service_name like '${APP}%'"
echo "== events";  journalctl -u trim_app_center.service --since "-3 min" --no-pager \
  | grep -oE 'APP_[A-Z_]+' | sort | uniq -c
```

## 一句话版

> 备份数据 → `stop` → `uninstall` → **`check` 确认真的没了** → `install-fpk`（`--env` 给文件、重活别放回调里）→ `start`（10500 忽略）→ 等冷启动 → 核 `status` + `manifest.version` + **载荷三方 md5** + 端口 **HTTP 响应** + journal 事件；只改载荷可以原地覆盖免重装。
