# 公开发布净化与 FnDepot/GitHub 资产约束

本页适用于把 FPK 应用发布到公开 GitHub 仓库、FnDepot 源或 Release 的最后检查。目标是交付必要制品，同时避免泄露测试设备、路径、日志、凭据和临时调试过程。

## 允许公开的最小集合

通常只发布：

- 最终 `.fpk`。
- 对应 `.fpk.sha256`。
- 必要源码包或可审计源码目录。
- `manifest`、`README.md`、图标、`ui/config`、`wizard/`、`cmd/` 等安装所需文件。
- FnDepot 索引文件，如 `fnpack.json` / `fndepot.json`。

## 不应公开的内容

不要提交到仓库或 Release assets：

- 远程测试报告，例如 `REMOTE_TEST_FINAL.md`、`REMOTE_TEST*.md`、`remote-test-report.md`。
- 含设备 IP、SSH 用户、卷路径、AppCenter DB SQL dump、容器日志的 Markdown 或文本。
- `build-records/` 中的私有验证记录，除非已彻底脱敏且用户明确要求公开。
- `.env`、安装向导真实 env、token、PAT、cookie、私钥、证书私钥。
- 临时补丁说明里带的真实设备路径、调试账号、内网地址。
- 失败日志原文，尤其是应用日志、数据库输出、HTTP headers 中的 cookie/token。

## 发布前 grep 清单

在公开目录或待上传 release asset 目录运行：

```bash
find . -iname '*remote*.md' -o -iname '*test*.md' -o -iname '*report*.md' -o -iname '*.log' -o -iname '.env*'
grep -RInE '192\.168\.|10\.|172\.(1[6-9]|2[0-9]|3[0-1])\.|root@|password|passwd|token|cookie|Authorization|/vol[0-9]/@app|/var/apps|TRIM_' .
grep -RInE 'ghp_[A-Za-z0-9]|github_pat_|gho_|ghs_|sshpass|id_rsa|BEGIN [A-Z ]*PRIVATE KEY|sk-[A-Za-z0-9]{20,}' .
```

命中不一定都是错误，但必须逐项确认是否可公开。不要把“已脱敏日志”默认当成可公开材料。

## GitHub / FnDepot 发布规则

- 同应用新版本是覆盖既有目录还是新增目录，先让用户明确确认。
- `fnpack.json` 与 `fndepot.json` 保持现有格式、排序和字段风格；同一应用通常只保留当前主版本 key，除非用户要求并行多个版本。
- 商店图标优先指向清晰的 256 图标，例如 `./sample-app/ICON_256.PNG`，同时确保包内根图标与 UI 图标同步。
- 上传同名 Release asset 后，重新下载验证 HTTP 状态、大小和 SHA-256；不要只相信上传 API 返回成功。
- 如果聊天或日志里出现过 GitHub PAT/API token，发布完成后提醒用户撤销并重建；不要在提交、Release notes 或 issue 中复述 token。

## Git 推送与 blob 级核验

- **push 前必须确认新提交真的存在**：新建克隆没有 `user.name`/`user.email` 时 `git commit` 直接失败，而紧随的 `git push HEAD:main` 会 `rc=0` 报成功（其实等于 up-to-date，什么都没推）。养成 `git log --oneline -1` 再看一眼的习惯。
- 全局 git 常配下载加速镜像的 `insteadOf`，会把 push 目标改写掉并报 `could not read Username for '<mirror>'`。绕过：`env GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_SYSTEM=/dev/null git -c credential.helper= push <token-url> HEAD:main`。
- **整库同步不要走 Git Data API**：`POST /git/trees` 的 `content` 与 `POST /git/blobs` 的 `_encoding:"base64"` 都**不解码**，会把 base64 字符串当正文存下来（blob SHA 变成那段 base64 文本的哈希，内容全错且很难发现）。正确做法是真 `git push`：浅克隆目标 → `git fetch <本地仓库> refs/tags/vX` → 用 `GIT_INDEX_FILE` 临时索引 `read-tree <main>` → 清旧子树 `git ls-files --cached <前缀> | git update-index --force-remove --stdin`（不清会 `read-tree --prefix` 报 overlaps）→ `read-tree --prefix=<前缀> <tree>` → `write-tree`/`commit-tree` → push。
- 少量文件更新用 Contents API：**更新是 `PUT` 不是 `POST`**（POST 404），已存在路径必须带上当前 `sha`（否则 422）。核验用**blob SHA 比对**（内容寻址，强于 size 比对）。
- 判定一律 `out=$(cmd); rc=$?`：`git push … | tail` 会把管道尾命令的退出码当结果，掩盖失败。

## Release 资产

- 上传端点是 `https://uploads.github.com/repos/{owner}/{repo}/releases/{id}/assets?name=…`；用 `api.github.com` 的同路径会 **404**（不是 405/400）。
- **建 Release 时若 tag 不存在，GitHub 会按 `target_commitish` 自动建一个指向 commit 的轻量标签**；事后再 push 同名**附注**标签会被拒（`updates were rejected because the tag already exists`）。要附注标签，先打标签再建 Release。
- `GET /releases/tags/{tag}` 响应里**没有** `target` 字段；查标签指向走 `GET /git/ref/tags/{tag}`。
- 回读核验优先用 asset 的 **`digest` 字段**（GitHub 自己算的 SHA-256），比 size 比对强；再配合下载回读。
- 从本机下载 Release 资产复核常被 `objects.githubusercontent.com` 卡死，但**上传方向稳定**。内容复核可走镜像 URL，但**绝不跨代理断点续传**——续传会拼出"大小对、哈希错"的混合文件；要重下就同源整包重下。
- Release 正文只写用户口径改动、安装步骤与 SHA-256；不含测试记录、设备地址、内部路径。

## Token 卫生

- 常见现象：PAT 在转述/上下文里被中途替换成占位符，拿去调用 API 得到 `401 Bad credentials`。因此**先做一次最小 API 调用确认身份**（如 `GET /user` 比对预期登录名），再开始任何写操作，别拿占位符空跑一整条流水线。
- token 只在内存/临时文件里存在：`(umask 077; printf '%s' "$TOK" > /tmp/.gh_tok)`，后续脚本一律读文件；用完立即 `rm`，含 token 字面量的临时脚本也要删。
- 绝不出现在：源码树、commit message、Release notes、issue 回复、日志。聊天里出现过就提醒用户 revoke 并重建。

## 索引 JSON 卫生（FnDepot 类清单仓库）

- `fnpack.json` 与 `fndepot.json` 保持**字节一致**；任何改动在推送前后都跑 `json.loads` 严格校验。
- 在 heredoc / shell 字符串里给 JSON 字段写换行要用 `\\n`——单个 `\n` 会注入裸换行符破坏整个 JSON。
- **JSON 没有注释语法**：要临时下线某条目，把条目原文摘到 `disabled/<app>.json`，另加 `disabled/README.md` 写下线原因与恢复步骤，而不是在索引里塞 `//`。
- 二进制只进 Release，不进源码树；树内只放 `<app>/fpk.sha256` 之类的校验文件（命名统一到 `fpk.sha256`，Release 资产名仍是 `<fpk>.sha256`）。
- 声称遵循某许可，仓库里就必须真有 `LICENSE`，否则 README 里的 `LICENSE` 链接是死链。

## README 与文档外链

- 逐条 `curl` 外链。两类常见误判：**下载站的目录索引返回 403 但真实文件 206 正常**——这种主机名不能做成可点链接（用户点开是 Forbidden 页），改成行内代码 + 指向可浏览的上游发布页；境外商店/CDN 本机超时属出口限制，不是死链。
- 上游仓库链接要核实 GitHub 组织与仓库**真实存在**：下载用的 CDN 域名（`fw.*` 之类）不等于同名 GitHub org 存在。
- 版本徽章写**范围**（如支持的上游系统版本区间）而不是钉死某个上游版本，长期不过期。
- 改文档前先比对本地与远端 blob SHA；跨轮引用"它还有 X"必须先 grep 现场文件。

## 本地保留证据

测试细节可以保存在本地或私有构建记录中：

```text
build-records/{app}/{timestamp}/
  doctor.json
  inspect.json
  source-sha256.txt
  remote-validation-redacted.md
```

这些记录用于复盘和再次构建，不应自动进入公开仓库。
