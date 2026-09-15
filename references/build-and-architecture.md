# 构建与多架构策略

本页用于把已经准备好的应用内容组装成可重复审计的 FPK。构建器不负责猜测项目语言，也不在打包阶段执行任意 prebuild 命令。

依据：`https://developer.fnnas.com/llms-full.txt` 与下文固定提交的公开参考项目。

## 目录

- [主机与目标架构](#主机与目标架构)
- [可信 fnpack](#可信-fnpack)
- [隔离 staging](#隔离-staging)
- [架构产物接入](#架构产物接入)
- [语言与运行形态](#语言与运行形态)
- [构建后验证](#构建后验证)
- [参考实现](#参考实现)

## 主机与目标架构

正式支持的构建主机：

| 主机 | fnpack | 状态 |
| --- | --- | --- |
| macOS arm64 | darwin-arm64 | 一等支持 |
| macOS amd64 | darwin-amd64 | 一等支持 |
| Linux amd64 | linux-amd64 | 一等支持、推荐 CI |
| Linux arm64 | linux-arm64 | 官方 1.2.3 URL 当前为 404，需显式提供可信二进制 |
| Windows amd64 | windows-amd64 | 仅诊断和人工指引 |

构建主机和 FPK 目标架构互相独立。目标规范化为：

| CLI 目标 | Manifest | Linux ELF |
| --- | --- | --- |
| `amd64` | `x86` | `EM_X86_64`，机器号 62 |
| `arm64` | `arm` | `EM_AARCH64`，机器号 183 |
| `all` | `all` | 不得包含特定架构原生二进制 |

审计器仍识别 `EM_ARM`（机器号 40），但本 Skill 的 `arm64` 目标会拒绝 32 位 ARM；没有提供 arm32 构建目标。

默认交付 `amd64` 与 `arm64` 两个独立包。只有纯脚本、静态资源或由 fnOS 运行时承载且包内没有本机二进制时，才生成 `all`。

## 可信 fnpack

默认版本固定为 1.2.3。下载地址与 SHA-256 见 `provenance.json`；下载后必须先校验哈希，再赋予执行权限。

不得：

- 静默使用 PATH 中版本不明的 fnpack。
- 哈希失败后继续构建。
- 在 Linux arm64 下载 404 时自动换成未经验证的镜像。
- 把目标架构误当成 fnpack 自身架构。

允许：

- 用户显式传入 fnpack 路径。
- 在显示实际版本、主机架构和文件哈希后使用该覆盖。
- CI 将已校验工具放到 runner 临时目录，而非修改系统全局路径。

## 隔离 staging

每个目标使用独立临时目录：

```text
source project
  -> stage-amd64
  -> stage-arm64
  -> fnpack build
  -> inspect unpack
  -> final output
```

复制原则：

1. 源目录只读，不原地重写 manifest。
2. 先复制公共包骨架，再覆盖该架构的准备产物。
3. 排除旧 `.fpk`、VCS/主机元数据和 `.DS_Store`；密钥直接报错。不要笼统忽略名为 `dist` 的目录，因为 `app/` 内可能合法使用该名称。
4. 在 staging 中重写 `version` 与 `platform`。
5. 验证 `cmd/*`、CGI 和原生入口的必要执行位；发布构建中缺失时失败，不静默掩盖源仓库的权限错误。
6. 先审计 staging，再调用 fnpack；成功后解包再次审计。
7. 无论成功失败都清理临时目录，但保留明确请求的诊断报告。

产物命名：

```text
{appname}-{version}-fnos-amd64.fpk
{appname}-{version}-fnos-arm64.fpk
{appname}-{version}-fnos-all.fpk
```

同目录生成 `{filename}.sha256`，内容使用标准的 `hash  filename` 格式。

## 架构产物接入

公共项目目录提供 manifest、cmd、config、wizard、图标和架构无关文件。overlay 只包含需要替换或新增的相对路径。

合并后必须检查：

- overlay 不能通过 `..` 或绝对路径逃离 staging。
- 目标包中只保留目标架构的二进制。
- 同一路径被公共目录和 overlay 同时提供时，记录覆盖事实。
- overlay 不得删除必需的包文件。
- 可执行文件确实带有执行位。

不要只按文件名判断架构。直接读取文件头：

- ELF：验证 class、endianness 和 `e_machine`。
- Mach-O：拒绝进入 fnOS Linux FPK。
- PE/COFF：拒绝进入 fnOS Linux FPK。
- 脚本：验证 shebang 对应运行时已经存在或已声明依赖。

共享库、Node native addon、Python extension、JRE/JDK 内二进制及 vendored 工具同样属于原生二进制，不能只检查主程序。

## 语言与运行形态

### Go

- 对每个目标显式设置 `GOOS=linux` 与 `GOARCH=amd64|arm64`。
- 使用 CGO 时还需匹配目标工具链；不能把 macOS 链接结果打入包。
- 构建后检查 ELF，而不是相信环境变量。

### Rust

- 可使用 `cargo-zigbuild` 对 `x86_64-unknown-linux-gnu`、`aarch64-unknown-linux-gnu` 交叉构建。
- 或用固定镜像、固定 `--platform linux/amd64|linux/arm64` 的 Docker 构建。
- 使用 lockfile；若依赖 glibc 特性，应在目标 fnOS 上验证兼容性。
- 经过实机验证的参考实现按架构先产出 Rust 后端，再在统一的 Linux amd64 job 组包。

### Node.js、Python、Java

- 纯源码可依赖 fnOS 提供的打包运行时。
- Manifest 必须声明如 `nodejs_v22`、`python312`、`java-21-openjdk`。
- `node_modules`、wheel、JAR 及其依赖若包含原生模块，仍要按目标架构分别准备和审计。
- 不要从开发机复制虚拟环境或本机 `node_modules` 后直接声称跨架构。

### Docker

- `platform=all` 只说明 FPK 本身无特定架构二进制，不保证镜像支持所有设备。
- 发布前检查镜像 manifest 是否包含每个声明目标。
- 固定镜像 digest 优于浮动 tag。
- Compose 的项目名、容器名、端口或 gateway socket 要与状态脚本和入口配置一致。
- 对需要 AppCenter 打开、停止、启动和改端口的 Docker Web 应用，优先阅读 [Docker 应用 FPK 构建、问题处理与验证流程](docker-app-flow.md)。实测结论：当内置 `docker-project` stop/start 路径不可靠时，应移除 `docker-project` resource，保留 `ctl_stop=true`，统一由 `cmd/main` lifecycle 管理 Docker Compose。

### 静态/CGI

- 纯静态资源通常可以使用 `all`。
- CGI 脚本本身若调用包内二进制，仍按原生包处理。
- Shebang 使用目标系统可用的解释器，不能引用开发机绝对路径。

## 构建后验证

对每个 FPK：

1. 安全列出外层 tar，拒绝路径穿越、绝对路径、重复目标和危险链接。
2. 读取 manifest，验证 appname、version、platform 与期望一致。
3. 将 `app.tgz` 流式写入受控临时文件，校验 `manifest.checksum`。
4. 安全遍历内层 tar，不把成员路径直接提取到文件系统。
5. 扫描所有文件类型和 ELF 机器号。
6. 确认只有一个目标架构，不包含 Mach-O/PE。
7. 校验 JSON、向导、入口、图标、执行位和敏感文件。
8. 生成外层 SHA-256。
9. 报告成员计数、原生文件清单、架构证据和所有警告。

原生包通过的最低证据不是“fnpack 退出 0”，而是“真实 fnpack 产物可以安全解开，manifest 与 checksum 一致，所有本机文件都属于目标架构”。

## 参考实现

实现模式已在独立的多架构 fnOS 项目中验证，保留的通用结论包括：

- 使用独立 staging、架构 overlay、manifest 重写、真实 fnpack 和解包复验。
- 使用 zig 或固定 Docker 工具链生成 Rust 的 Linux 目标产物。
- 在组包前分别准备公共文件和各架构产物。
- 验证打包过程不修改源目录，并确保每个包只含目标架构。

参考项目的身份、目录名、二进制名、端口和应用 ID 不属于通用 FPK 规范，因此不记录在本 Skill 中。
