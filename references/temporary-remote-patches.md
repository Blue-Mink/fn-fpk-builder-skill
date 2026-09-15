# 远端临时补丁、调试 hook 与回滚记录规范

远端排障时常需要临时改已安装应用、数据库或容器配置。临时补丁如果不记录，会污染后续验证和最终包判断。每次临时修改都必须能回答：改了什么、为什么改、是否仍生效、怎么回滚、是否已固化进源码。

## 什么时候使用

- 修改 `/var/apps/{app}`、`/volN/@appcenter/{app}`、`/volN/@appdata/{app}`、`/volN/@appconf/{app}` 中的文件。
- 修改 AppCenter PostgreSQL 记录。
- 改 `docker-compose.yml`、`.env`、bind mount、容器启动环境变量。
- 注入 Node/Python/LD_PRELOAD hook、代理脚本、访问日志或调试中间件。
- 临时切换 `app_service.type`、`gatewaySocket`、端口、入口 URL。

## 每次临时补丁的记录文件

建议在目标应用数据目录下创建：

```text
{TRIM_PKGVAR}/debug-patches/{timestamp}/MANIFEST.txt
```

内容模板：

```text
appname={appname}
time={ISO-8601}
purpose=一句话说明验证假设
changed_files=
  /path/file -> /path/file.bak-{timestamp}
changed_db=
  app_service.type iframe -> url
service_restarted=yes/no
validation=
  health 200, status running, header X removed ...
rollback=
  cp bak file back; SQL update ...; docker compose up -d
source_status=not-merged | merged-to-source | abandoned
risk=影响范围和已知副作用
```

## 操作原则

- 先备份再改；能用 `cp -a` 就不要只靠记忆。
- 只改目标应用命名空间；不要清空共享 `/tmp`、全局 Docker network 或别的应用数据。
- 调试日志只记录必要元信息，例如 path、status、hasCookie、host、origin；不要记录 cookie/token/body。
- 临时补丁生效后，下一轮验证报告必须明确“当前远端仍带临时补丁”。
- 多轮实验失败后及时回滚到最近已知可用基线，不要无限叠加。
- 只有经过源码修改、重构建、inspect 和安装验证的内容，才能称为“已固化”。

## 结束条件

排障结束时必须三选一：

1. **固化**：把补丁写回源码，重构建 FPK，审计并安装验证。
2. **回滚**：恢复备份、撤销 DB 改动、重启服务，验证回到基线状态。
3. **保留临时补丁**：明确告知用户它不是正式包状态，并记录下次继续的位置。

如果无法确认当前远端是否有临时补丁，先做只读 diff/DB 查询/容器 inspect，不要继续叠加新补丁。
