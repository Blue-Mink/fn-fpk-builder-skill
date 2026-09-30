#!/bin/bash
# fnOS 测试装机四步操作单（备份 → stop → uninstall → check → install-fpk → start → 冒烟）
#
# 这是 references/install-runbook.md 的可执行版本。在 NAS 上以 root 执行
# （变量写在远程命令里，env 不随 ssh 透传）：
#   ssh root@<NAS_IP> "APP=<app> FPK=/tmp/<app>.fpk bash -s" < reinstall.sh
# （含中文或较长的命令串逐条敲，容易被本地执行环境拦下，所以整段喂给 bash。）
#
# 必填环境变量：
#   APP            包内 manifest 的 appname
#   FPK            设备上可见的 .fpk 绝对路径
# 可选：
#   VOLUME=1       安装卷号（系统盘应用可能是 0）
#   ENV_FILE       包内含 wizard/ 时必填；--env 传的是**文件路径**不是 key=value
#   PORT           冒烟端口；给了就做 TCP+HTTP 探测，没给只查监听
#   HTTP_PATH=/    HTTP 冒烟路径
#   VERIFY_FILES   载荷三方 md5 的设备侧路径列表（空格分隔），与构建树/包内比对
#   ENTRY_LIKE     桌面入口表 LIKE 模式，默认 "<APP>%"
#   WAIT=8         start 后等待秒数（带迁移的服务要加大，分钟级也正常）
#   BAK_DIR=/vol1  备份落点
#   DRY_RUN=1      只打印生命周期命令不执行（冒烟段照常跑）
#
# 退出码：0 全绿；1 生命周期失败（卸载后置校验不过 / status 非 running）；
#         2 参数或前置检查问题（缺变量、包内有 wizard 但没给 ENV_FILE）。
set -u
: "${APP:?APP is required}"
: "${FPK:?FPK is required}"
VOLUME="${VOLUME:-1}"
WAIT="${WAIT:-8}"
BAK_DIR="${BAK_DIR:-/vol1}"
HTTP_PATH="${HTTP_PATH:-/}"
ENTRY_LIKE="${ENTRY_LIKE:-${APP}%}"
DRY_RUN="${DRY_RUN:-0}"

cd /tmp    # 避免 psql 的 "could not change directory" 告警污染 -tA 输出

fail() { echo "[FAIL] $*" >&2; exit 1; }
die()  { echo "[die ] $*" >&2; exit 2; }

# 生命周期命令统一走这里：DRY_RUN 只打印，且伪造成功返回
cli() {
  if [ "$DRY_RUN" = "1" ]; then
    echo "[dry-run] appcenter-cli $*"
    return 0
  fi
  appcenter-cli "$@"
}

[ -f "$FPK" ] || die "FPK not found on this device: $FPK"

echo "== 1. package"
sha256sum "$FPK" || die "sha256sum failed"

# 有向导的包没给 env 文件 → 直接拒跑，别让装机走到一半撞
# "[Error]Something wrong with environment variables."
WIZARD_COUNT=$(tar -tf "$FPK" 2>/dev/null | grep -c '^wizard/' || true)
if [ "${WIZARD_COUNT:-0}" -gt 0 ] && [ -z "${ENV_FILE:-}" ]; then
  die "package contains ${WIZARD_COUNT} wizard/ entries; set ENV_FILE to an env FILE path (KEY=VALUE per line). Reinstall must reuse the ORIGINAL env file of the first install."
fi
if [ -n "${ENV_FILE:-}" ] && [ ! -f "$ENV_FILE" ]; then
  die "ENV_FILE does not exist: $ENV_FILE"
fi

if [ "$DRY_RUN" != "1" ]; then
  echo "== 2. currently installed version (don't trust memory)"
  appcenter-cli check "$APP" || echo "   (not installed)"
  grep -E '^version' "/var/apps/$APP/manifest" 2>/dev/null || true
fi

echo "== 3. backup @appdata / @appconf (uninstall semantics differ per app type)"
TS=$(date +%Y%m%d%H%M%S)
for base in appdata appconf; do
  if [ -d "/vol1/@${base}/${APP}" ]; then
    BAK="${BAK_DIR}/${APP}-${base}-bak-${TS}.tgz"
    tar czf "$BAK" -C "/vol1/@${base}" "$APP" && sha256sum "$BAK" || die "backup of @${base}/${APP} failed"
  else
    echo "   (no /vol1/@${base}/${APP})"
  fi
done
# 虚拟机类额外两查（磁盘在 libvirt 池、卸载不删，但身份信息要留）：
#   cat /vol1/vm/<app>.disk-version ; cat /vol1/vm/<app>.vm-mac
# 验大磁盘没被动用 stat -c '%s %y' <qcow2>，别整盘 md5。

echo "== 4. lifecycle"
cli stop "$APP" || echo "   (stop returned nonzero; 记账背离时 stop 可能是空操作，见 runbook §stop 三坑)"
cli uninstall "$APP" || fail "uninstall failed"
sleep "${CHECK_WAIT:-2}"
if [ "$DRY_RUN" != "1" ] && appcenter-cli check "$APP" >/dev/null 2>&1; then
  fail "still installed after uninstall — refusing to install over it"
fi
if [ -n "${ENV_FILE:-}" ]; then
  cli install-fpk "$FPK" --volume "$VOLUME" --env "$ENV_FILE" || fail "install-fpk failed"
else
  cli install-fpk "$FPK" --volume "$VOLUME" || fail "install-fpk failed"
fi
cli start "$APP" || echo "   (start nonzero: code 10500 = already started by the platform, that is normal)"
sleep "$WAIT"

echo "== 5. smoke"
STATUS=$(appcenter-cli status "$APP" 2>/dev/null || true)
echo "status: $STATUS"
grep -E '^version' "/var/apps/$APP/manifest" 2>/dev/null || true
if [ -n "${PORT:-}" ]; then
  ss -lntp 2>/dev/null | grep ":$PORT" || echo "   (port $PORT not listening yet — cold start can take minutes)"
  if command -v curl >/dev/null 2>&1; then
    echo "http: $(curl -s -o /dev/null -m 5 -w '%{http_code}' "http://127.0.0.1:${PORT}${HTTP_PATH}")"
  fi
fi
# 服务活没活要看 HTTP 响应，端口 LISTEN 不够（见 runbook 判读表）。
if [ -n "${VERIFY_FILES:-}" ]; then
  echo "== payload md5 on device (compare with build tree and package)"
  # shellcheck disable=SC2086
  md5sum $VERIFY_FILES 2>/dev/null || true
fi
if command -v sudo >/dev/null 2>&1 && sudo -n -u postgres psql -d appcenter -tA -c 'select 1' >/dev/null 2>&1; then
  echo "== desktop entries"
  sudo -n -u postgres psql -d appcenter -tA -c \
    "select id,service_name,no_display from app_service where service_name like '${ENTRY_LIKE}'" || true
fi
if command -v journalctl >/dev/null 2>&1; then
  echo "== platform events"
  journalctl -u trim_app_center.service --since "-3 min" --no-pager 2>/dev/null \
    | grep -oE 'APP_[A-Z_]+' | sort | uniq -c || true
fi

case "$STATUS" in
  *running*) echo "[ok] installed and running: $APP" ;;
  *) [ "$DRY_RUN" = "1" ] && exit 0
     fail "status is not running: '$STATUS' — check journal and cold-start wait" ;;
esac
