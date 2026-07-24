# CI 构建与发布

本页说明怎样在干净的 macOS/Linux CI 中生成可审计的多架构 FPK。可复制模板位于 `assets/github-actions/fpk.yml`；先按项目语言替换“准备产物”步骤，再保留验证和组包边界。

依据：`https://developer.fnnas.com/llms-full.txt` 中的 `docs/cli/fnpack.md` 内容与下文固定提交的公开参考流水线。

## 目录

- [推荐流水线](#推荐流水线)
- [工具与依赖固定](#工具与依赖固定)
- [跨 job 数据流](#跨-job-数据流)
- [组包与发布门禁](#组包与发布门禁)
- [CI 安全](#ci-安全)
- [本地等价验证](#本地等价验证)
- [参考依据](#参考依据)

## 推荐流水线

```text
preflight
  -> build-common
  -> build-native[amd64, arm64]
  -> assemble-fpk on Ubuntu amd64
  -> inspect both FPKs
  -> upload immutable artifacts
```

`preflight`：

- 确认版本、appname 和 git revision。
- 检查 lockfile、必需源文件和工作树策略。
- 输出后续 job 使用的规范化版本与提交 SHA。

`build-common`：

- 构建 UI、静态资源和架构无关运行文件。
- 使用锁文件安装依赖。
- 打包成内部 artifact，不在此阶段生成 FPK。

`build-native`：

- matrix 分别生成 Linux amd64 与 arm64。
- 每个 job 在上传前验证 ELF 机器类型。
- artifact 名称包含目标架构，内容路径保持一致。

`assemble-fpk`：

- 在固定的 Ubuntu amd64 runner 下载前述 artifacts。
- 校验并安装 fnpack 1.2.3 Linux amd64。
- 对两个架构分别建立 staging、调用 `fpk.py build`、再调用 `fpk.py inspect`。
- 上传 FPK、`.sha256` 和机器可读审计报告。

## 工具与依赖固定

- GitHub Actions 使用完整 commit SHA，不使用浮动 `@main`。
- Node、Python、Go、Rust、Zig、Docker 镜像和包管理器版本显式固定。
- 使用 lockfile 和 frozen/locked 模式。
- fnpack URL 与 SHA-256 来自 `provenance.json`。
- runner 自带工具也要在日志中输出版本。
- 第三方下载失败或哈希不符时立即失败，不切换未知镜像。

官方 Linux arm64 fnpack 1.2.3 下载当前返回 404。这不影响在 Linux amd64 组包 ARM 目标，因为 fnpack 的主机架构与包内产物架构不同。

## 跨 job 数据流

内部 artifact 应只包含重建 FPK 所需的产物：

```text
common/
  app/ui/...
  app/static/...
native-amd64/
  app/bin/...
native-arm64/
  app/bin/...
```

规则：

- 不把整个工作区无差别上传。
- 打包前列出归档文件并检查路径。
- 下载后验证预期文件存在、大小非零、架构正确。
- 不允许两个架构 artifact 写入同一文件名后静默覆盖。
- GitHub artifact 会丢失 Unix 执行位；模板同时传输逐文件 SHA-256/执行位清单，下载后先做路径与哈希校验，再只恢复 0644/0755，并重新审计 overlay。
- artifact 保留期按调试需求设置，正式发布制品另外保存。
- 最终发布清单列出文件名、大小、SHA-256、appname、version、platform 和源提交。

## 组包与发布门禁

必须通过：

- Skill 自身单元测试和 CLI 集成测试。
- `quick_validate.py` 的 Skill 结构校验。
- 真实 fnpack 的 native/docker fixture 构建。
- amd64 与 arm64 FPK 的双层归档和 checksum 检查。
- 错误架构、Mach-O、PE、路径穿越、危险链接和敏感文件负面用例。
- 连续两轮全量测试；发布前再做一次干净目录重跑。

发布包应包含：

- 版本化 `.fpk`。
- 相应 `.sha256`。
- JSON 审计结果。
- 来源提交与构建工具版本。

除非用户明确要求，本 Skill 的 CI 只生成和上传 workflow artifacts，不创建 GitHub Release、不推送 tag、不覆盖远端资产。

## CI 安全

- 默认权限设为只读，只给需要发布的 job 增加最小权限。
- checkout 使用 `persist-credentials: false`。
- pull request 和外部贡献触发时不加载发布密钥。
- 环境文件、SSH key、Token 不得进入 artifact 或日志。
- 脚本参数通过结构化输入传递，不拼接未经验证的分支名或路径。
- 构建步骤不连接局域网 fnOS；实机烟测属于受控私有环境。
- 发布 job 只消费已通过验证的 artifact，不能重新从不可信分支取脚本。
- 上传前比较期望清单与实际目录，防止陈旧文件混入。

## 本地等价验证

提交 CI 前：

```bash
SKILL_DIR=/absolute/path/to/fn-fpk-builder-skill
python3 "$SKILL_DIR/scripts/fpk.py" toolchain
python3 "$SKILL_DIR/scripts/fpk.py" doctor --project /path/to/package
python3 -m unittest discover -s "$SKILL_DIR/tests"
```

按 `--help` 为 `build` 提供项目、输出目录和架构 overlay；构建后对每个 `.fpk` 再单独执行 `inspect`。本地命令和 CI 使用同一脚本，不能维护一套只在 workflow 中存在的打包逻辑。

CI 不应依赖：

- 开发机上未声明的全局工具。
- 预先存在的 `dist/`。
- 某台 fnOS 的旧版 fnpack。
- 能访问 `192.168.*` 的网络环境。
- 缓存命中才能正确构建。

## 参考依据

以下关键模式已经在独立的多架构 fnOS 发布流水线中验证：

- 各架构先独立构建。
- 最终在 Ubuntu amd64 下载已验证的 fnpack 1.2.3。
- 组装阶段只使用预构建输入。
- Actions 固定到 commit SHA。
- FPK 组装后再次解包，验证精确 ELF 架构和每包只含一个目标架构。

这些模式可以迁移；参考项目的身份、脚本名、发布服务、镜像仓库及凭据不属于通用规范，因此不在本 Skill 中记录。
