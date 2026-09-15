# fnOS 远程部署与实机验证

远程操作会改变 NAS 状态。默认只读诊断；只有用户明确要求部署、启停或卸载时才执行相应动作。自动化不得把某个局域网地址写死到脚本中。

依据：`https://developer.fnnas.com/llms-full.txt` 中的 `docs/cli/appcentercli.md` 内容、下文固定提交的公开参考项目、2026-07-25 用户授权设备上的只读探测与隔离烟测，以及目标系统已确认的覆盖安装缺陷。

## 目录

- [连接与预检](#连接与预检)
- [部署流程](#部署流程)
- [状态、日志与证据](#状态日志与证据)
- [替换、清理与回滚](#替换清理与回滚)
- [隔离冒烟测试](#隔离冒烟测试)
- [实机观察](#实机观察)
- [参考实现](#参考实现)

## 连接与预检

通过 `--host` 或 `FNOS_HOST` 接收 SSH 目标。不要把 IP、用户名、密钥路径或密码写入仓库。

只读预检：

```bash
uname -s
uname -m
command -v appcenter-cli
appcenter-cli --help
sha256sum "$(command -v appcenter-cli)"
appcenter-cli list
appcenter-cli default-volume
```

还应确认：

- SSH 主机密钥策略已经由用户建立，不能静默关闭校验。
- 远端临时目录是本次任务专属路径。
- appname 与将要操作的应用完全一致。
- 目标架构与待上传 FPK 匹配。
- 安装卷可用且空间足够。
- 环境文件不含会被输出到日志的秘密。

架构规范化：

- `x86_64`、`amd64` → 选择 `*-fnos-amd64.fpk`。
- `aarch64`、`arm64` → 选择 `*-fnos-arm64.fpk`。
- 其他值默认拒绝，不猜测。

## 部署流程

实机 FPK 部署只支持“确认未安装后安装”或“卸载后重装”，不支持原位升级。目标系统存在缺陷：对已安装应用直接调用 `appcenter-cli install-fpk` 不能可靠完成更新。

1. 本地 `inspect` 通过并取得 SHA-256。
2. 查询远端架构、应用状态和版本。
3. 上传到唯一临时文件，例如 `/tmp/fnos-fpk-{random}/package.fpk`。
4. 在远端重新计算 SHA-256，与本地精确比较。
5. 若提供安装向导 env，单独上传、权限设为 600，绝不回显。
6. 在任何卸载动作前解析并确认安装卷；默认 volume 为 0 时，可从唯一已有 `/volN/@appcenter` 推导，有多个候选时必须显式选择。
7. 若状态不是 `noinstall`，依次 stop、uninstall，并再次查询状态。只有明确得到 `noinstall` 才能继续；卸载命令退出 0 但状态仍存在时必须中止。
8. 调用 `appcenter-cli install-fpk`，必要时提供 `--env` 和 `--volume`。
9. 同时检查退出码和输出中的 `[Error]`；启动前上传本地生成的载荷 SHA-256 清单，在 `/var/apps/{appname}/target` 核对全部普通文件及文件数量。
10. 随后等待 appcenter（即包内 `cmd/main status` 合约）进入 running，超时或 noinstall 立即失败。
11. 精确比较包内 manifest 与安装后 manifest 的 SHA-256，核对版本，并收集经脱敏的关键日志。
12. 删除远端 FPK、校验清单与 env 临时文件。

“更新”“重新部署”“安装新版本”都执行同一卸载重装流程，不存在直接覆盖安装或原位升级路径。首次安装在初始状态已确认为 `noinstall` 时无需执行无意义的卸载。卸载可能触发生命周期脚本或数据清理，部署前必须明确持久数据保留预期。

从源目录 `install-local` 适合设备内快速开发，但发布验证应使用最终 FPK，确保测试对象与交付制品一致。

## 状态、日志与证据

应用管理：

```bash
appcenter-cli list
appcenter-cli status <appname>
appcenter-cli start <appname>
appcenter-cli stop <appname>
```

设备版本的帮助文本可能遗漏位置参数，且实测某些失败仍返回进程退出码 0。不能据此省略 appname，也不能只依赖退出码；同时解析 `[Error]`、`noinstall` 和状态文本。

部署验证至少收集：

- 远端 `uname -m`。
- 本地与远端 FPK SHA-256。
- appcenter 安装命令退出码及原始输出。
- 安装后 appname、version、status。
- `/var/apps/{appname}` 是否存在。
- appcenter 状态及其代表的 `cmd/main status` 进程/容器合约结果。
- `app.tgz` 全部普通文件与安装目标的逐文件 SHA-256、文件数量和校验清单哈希。
- 包内与安装后 manifest 的精确 SHA-256 比较。
- 应用日志尾部和 `/var/log/trim_app_center/error.log` 的相关错误。

日志路径由应用决定。优先从 `/var/apps/{appname}/var` 和生命周期脚本读取，不要只依赖 `/usr/local/apps/@appdata/...` 这种设备实现路径。

轮询状态要有明确超时和间隔。`running` 才成功；`starting` 不是最终成功，`noinstall` 应立即失败。

`logs` 会在输出前遮蔽私钥块、AWS/GitHub token、Bearer 值和常见 password/token/secret 赋值；这只是高置信兜底，应用本身仍不得记录秘密。

## 替换、清理与回滚

`deploy` 对已安装应用固定执行：

1. 停止目标 app。
2. 卸载目标 app。
3. 验证 noinstall。
4. 安装新 FPK。

`--clean` 仅为兼容旧调用保留，不再改变行为，也不是启用卸载重装的开关。不得为了避开卸载风险而改为直接 `install-fpk`。

独立卸载必须同时给出准确 appname 与 `--yes`。卸载前记录版本、状态和数据保留预期；命令返回后必须验证 `status=noinstall`，否则卸载整体失败。

回滚仅使用用户显式提供的 `--rollback-fpk`：

- 回滚包先进行本地审计和远端 SHA 校验。
- 不从目录中“猜”上一版本。
- 新包安装失败后，记录原始错误；若失败安装留下应用状态，必须再次卸载并确认 `noinstall`，再安装回滚包。
- 回滚命令返回后仍要验证状态、回滚版本和已安装 manifest 哈希；回滚成功也应把本次部署标记为失败并报告两个阶段结果。
- 如果包的迁移不可逆，必须停止并要求人工恢复，不承诺自动回滚数据。

任何失败都要尽力删除只属于本次任务的临时文件，但不得删除应用数据、共享目录或其他任务的 `/tmp` 内容。

## 隔离冒烟测试

烟测应用 ID 使用唯一前缀：

```text
fpk-skill-smoke-{timestamp-or-random}
```

烟测 FPK 自身也必须经过真实 fnpack 和 inspect。流程：

1. 确认唯一 appname 尚未安装。
2. 上传并核对 SHA。
3. 安装到测试卷。
4. 启动并验证 running、版本、目标架构和最小功能。
5. 收集日志。
6. stop 后验证 stopped。
7. uninstall 并验证 noinstall。
8. 删除唯一临时目录；若 appcenter 只遗留本烟测应用的目录或专用包账号，先严格核对唯一命名空间与对象类型。`@appcenter/@appconf/@apphome/@apptemp/@appmeta` 只允许删除空目录；`@appdata` 只允许在内容恰为一个非符号链接普通文件 `smoke.log` 时删除该文件和空目录；账号只允许在 home 与 nologin shell 均匹配时清理。
9. 检查 `/var/apps`、实际 appcenter/data/conf/home/temp 路径、`@appmeta`、专用用户与组均无残留。
10. 再次确认既有应用状态未改变。

不得复用系统应用或用户现有 appname 作为烟测目标。即使用户授权 root，也只操作烟测命名空间。

## 实机观察

在用户提供的 LAN fnOS 设备上，2026-07-25 只读观察并使用唯一 `fpk-skill-smoke-*` 应用完成隔离烟测，观察到：

- 系统架构：x86_64。
- 用户空间基础：Debian GNU/Linux 12（bookworm）。
- `/usr/local/bin/fnpack`：1.0.0。
- `/usr/local/bin/appcenter-cli`：1.0.1。
- 一项既有应用在烟测前后均保持 running，未被修改。
- `/var/apps/{appname}/target` 等稳定入口可能是指向实际安装位置的软链接。
- appcenter-cli 报卸载成功后可能保留 `@appdata/{appname}/smoke.log`、空 `@appconf/@apphome/@appmeta/{appname}` 目录和专用包账号；烟测只对唯一测试命名空间执行上述严格内容核对后的文件删除、空目录 `rmdir` 和经过身份属性核对的账号清理。
- 已安装应用不能依赖再次执行 `install-fpk` 完成更新；实机测试与更新必须先卸载、确认 `noinstall`，再安装目标 FPK。

这些信息只用于兼容判断，不代表所有 fnOS 设备。特别是远端 fnpack 1.0.0 不应替代干净构建环境使用的 1.2.3。

实测 appcenter-cli 1.0.1 提供 `install-fpk`、`install-local`、`start`、`stop`、`list`、`default-volume` 等子命令；安装命令支持 env 与 volume 选项。每次仍应以目标设备实际帮助和返回值为准。

## 参考实现

远程流程已在独立 fnOS 项目中验证。可复用的经验是上传、卸载后重装、等待状态、收集日志和验证文件哈希。Docker Web FPK 还必须额外验证 AppCenter stop/start 是否真正传递到 Docker、端口配置是否传递到 compose/DB/UI；完整流程见 [Docker 应用 FPK 构建、问题处理与验证流程](docker-app-flow.md)。

参考实现包含项目专属地址、端口、物理路径和卸载策略，这些信息不属于通用规范，因此不在本 Skill 中记录。通用流程必须使用本文定义的更安全默认值。

## Docker Web 应用专项调试记录（fnOS 1.2.19-0）

### 背景
该 FPK 在构建和容器运行上均通过，但 AppCenter 应用设置页报错"无法连接到服务器"。经过多轮排查最终定位为数据库状态字段问题。

### 问题现象
- 容器 `sample-web`：`docker ps` 显示 `Up 42 minutes (healthy)`，端口映射正常
- API：`curl http://127.0.0.1:{port}/api/health` → `{"status":"ok"}`
- 登录：`POST /api/auth/login` → 返回 JWT token（有效期 8h）
- AppCenter 应用设置页：`HTTP/1.1 502 Bad Gateway`，前端提示"无法连接到服务器"
- 点击"打开"按钮：HTTP 200 但返回 `404 page not found`（前置反代的 redirect 目标错误）
- `appcenter-cli status` 显示：`running`（但初始为 `start`）
- `app_service` 表 `service_url` 字段：**为空**（应为 `http://${host}:{port}/`）

### 根因定位
通过逐步排查定位到两个关键问题：

1. **`service_url` 为空**：初始手工 INSERT 时 `service_url` 字段漏填（原值为空字符串），AppCenter 后端无法获取实际访问地址。
2. **状态字段 `status = 'start'`**：数据库记录为 `start` 而非 `running`，导致 AppCenter 按钮显示"卸载"而非"打开"；且某些后端逻辑依赖 `status=running` 才判定服务可用。

### 修复操作
```sql
-- 修复 service_url
UPDATE app_service SET service_url='http://${host}:{port}/' WHERE id={app_service_row_id};

-- 修复状态字段（start → running）
UPDATE app SET status='running' WHERE id={app_row_id};
```
重启 `trim_app_center.service` 后，按钮切换为"打开"，应用设置页不再报错。

### 已知问题
- **不要只依赖 `/vol1/@appcenter/{appname}` 判断 payload 是否落地**：实机最终验证中，安装后的稳定入口是 `/var/apps/{appname}`，payload 位于 `/var/apps/{appname}/target`。生命周期脚本必须做路径归一化，而不是写死 `/vol1/@appcenter/{appname}`。
- **`docker-project` resource 在该实机组合下会干扰停止链路**：AppCenter 内置 docker-project stop 曾报 `open /docker/docker-compose.yaml: no such file or directory`，表现为 AppCenter `stopped` 但 Docker 仍 `Up healthy`。最终方案移除 `docker-project`，保留 `ctl_stop=true`，由 `cmd/main` lifecycle 管理 Docker Compose。
- **图标兼容**：官方 logo 为白色背景 PNG，直接替换会覆盖原有圆角效果。处理顺序：先裁剪白边+透明化（ImageMagick `DstAtop`），再叠加 fnOS 圆角 mask（`DstOut` + circle fill）。
- **图标尺寸**：fnOS 要求同时提供 `icon_64.png`（256x256 内容缩到 64x64）和 `icon_256.png`（原始 256x256）。

### 调试 checklist（今后同类问题可复用）
1. 容器 `healthy` 且 API 200 → 排除容器/网络层故障
2. `curl localhost:port/api/health` → 确认端口映射正常
3. 检查 `app` 表 `status` 字段：应为 `running`，非 `start`
4. 检查 `app_service` 表 `service_url` 字段：不应为空
5. 检查应用自带前置反代的 `/appui` location 配置是否存在
6. 检查图标目录 `ui/images/` 是否包含 `icon_64.png` 和 `icon_256.png`
7. 重启 `trim_app_center.service` 后验证按钮状态
8. 查看 AppCenter 后端日志（如有权限）定位 502 来源
