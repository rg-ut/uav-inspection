#!/usr/bin/env bash
# 把一份 platedata 数据包装进运行中的容器，并验证网站确实换成了这一批。
#
# 为什么需要这个脚本：正式录制素材（两段视频 + 三份 JSON + 卡片，约 100 MB）
# 不进仓（见 .gitignore 的「数据在服务器命名卷，不进仓」），镜像里只有一份
# 2026-11-14 的旧批次兜底。数据包必须落到命名卷 /data/uav-inspection-backend/platedata
# 并重启后端才会生效——而 DATA_DIR 在导入时就定了，运行期目录为空时后端会
# **安静地**退回镜像里的旧批次，页面上除了顶栏任务编号看不出区别。
#
# 用法（在 deploy/ 下执行）：
#   ./scripts/install_platedata.sh /path/to/platedata
#   ./scripts/install_platedata.sh /path/to/platedata --no-backup
set -euo pipefail
cd "$(dirname "$0")/.."

PKG="${1:-}"
KEEP_BACKUP=1
shift || true
for arg in "$@"; do
  case "$arg" in
    --no-backup) KEEP_BACKUP=0 ;;
    *) echo "未知参数: $arg" >&2; exit 1 ;;
  esac
done
[ -n "$PKG" ] || { echo "用法: $0 <platedata 目录> [--no-backup]" >&2; exit 1; }
[ -d "$PKG" ] || { echo "[install] 数据包目录不存在: $PKG" >&2; exit 1; }

SERVICE=uav
REMOTE_ROOT=/data/uav-inspection-backend
REMOTE_DIR="$REMOTE_ROOT/platedata"
# .env 里的宿主侧回环端口（nginx upstream 对准它）
PORT="${UAV_PORT:-}"
if [ -z "$PORT" ] && [ -f .env ]; then
  PORT="$(grep -E '^UAV_PORT=' .env | tail -1 | cut -d= -f2- | tr -d '"' || true)"
fi
PORT="${PORT:-18020}"
API="http://127.0.0.1:$PORT/api/v1/plate/dataset"

echo "[install] 数据包: $PKG"

# ---- 1. 装机前先确认数据包本身是完整的、且批次自洽 -------------------------
# 检查器是纯标准库，服务器上有 python3 就能跑；没有就只做结构检查。
for must in timeline.json coverage.json acceptance.json media cards; do
  [ -e "$PKG/$must" ] || { echo "[install] 数据包缺 $must" >&2; exit 1; }
done
if command -v python3 >/dev/null 2>&1 && [ -f ../scripts/check_replay_data.py ]; then
  echo "[install] 运行数据自检（--strict-batch）"
  if ! python3 ../scripts/check_replay_data.py --data "$PKG" --strict-batch; then
    echo "[install] 自检未通过，终止安装（不把坏包推进生产）" >&2
    exit 1
  fi
else
  echo "[install] 跳过自检：需要 python3 与仓库里的 scripts/check_replay_data.py"
fi

EXPECTED_RUN=""
if command -v python3 >/dev/null 2>&1; then
  EXPECTED_RUN="$(python3 -c "
import json
print((json.load(open('$PKG/timeline.json', encoding='utf-8')).get('mission') or {}).get('run') or '')
" 2>/dev/null || true)"
fi
[ -n "$EXPECTED_RUN" ] || { echo "[install] 读不出 timeline.json 的 mission.run（需要 python3）" >&2; exit 1; }
echo "[install] 目标批次: $EXPECTED_RUN"

# ---- 2. 定位容器 -----------------------------------------------------------
CID="$(sudo docker compose ps -q "$SERVICE" || true)"
[ -n "$CID" ] || { echo "[install] 服务 $SERVICE 没在跑，先 ./deploy.sh up" >&2; exit 1; }
echo "[install] 容器: $CID"

# ---- 3. 备份现有运行期数据包 ----------------------------------------------
if [ "$KEEP_BACKUP" = "1" ]; then
  STAMP="$(date +%Y%m%d-%H%M%S)"
  sudo docker exec "$CID" sh -c "
    if [ -d '$REMOTE_DIR' ]; then
      mv '$REMOTE_DIR' '$REMOTE_ROOT/platedata.bak-$STAMP'
      echo '[install] 旧数据包已备份到 $REMOTE_ROOT/platedata.bak-$STAMP'
    else
      echo '[install] 运行期还没有数据包（当前在放镜像里的旧批次）'
    fi"
fi

# ---- 4. 拷进命名卷 ---------------------------------------------------------
# 跳过 _bak_* 目录：那是工作站上换批时留的历史备份，拷进生产只会白占体积
# （本批实测 platedata 182 MB，其中 media/_bak_20261121 一份就 77 MB）。
# docker cp 没有排除选项，所以走 tar 管道；python:*-slim 自带 tar。
sudo docker exec "$CID" mkdir -p "$REMOTE_DIR"
tar -C "$PKG" --exclude='_bak_*' --exclude='*/_bak_*' -cf - . \
  | sudo docker exec -i "$CID" tar -xf - -C "$REMOTE_DIR"
echo "[install] 已拷入 $REMOTE_DIR（已跳过 _bak_* 备份目录）"

# ---- 5. 重启让 DATA_DIR 重新解析 ------------------------------------------
# DATA_DIR 在模块导入时定死，热更不生效，必须重启进程。
sudo docker compose restart "$SERVICE" >/dev/null
echo "[install] 已重启 $SERVICE，等待就绪"

# ---- 6. 验证：确认服务在用运行期包，且批次与数据包一致 ---------------------
for _ in $(seq 1 30); do
  if curl -fsS --max-time 3 "$API" >/dev/null 2>&1; then break; fi
  sleep 2
done

if ! BODY="$(curl -fsS --max-time 5 "$API")"; then
  echo "[install] 验证失败：$API 无响应" >&2
  echo "[install] 用 ./deploy.sh logs 看后端日志" >&2
  exit 1
fi

# 用环境变量把 JSON 交给 python，别拼进 heredoc：路径里的引号或反斜杠会截断源码。
if command -v python3 >/dev/null 2>&1; then
  EXPECTED_RUN="$EXPECTED_RUN" BODY="$BODY" python3 - <<'PY'
import json, os, sys

expected = os.environ['EXPECTED_RUN']
d = (json.loads(os.environ['BODY']).get('data') or {})
print('[verify] 数据目录: %s' % d.get('data_dir'))
print('[verify] 来源    : %s' % d.get('source'))
print('[verify] 批次    : %s' % (d.get('mission') or {}).get('run'))
print('[verify] 路径    : %s 步（%s）' % ((d.get('path') or {}).get('steps'),
                                       '现场重建' if (d.get('path') or {}).get('fallback') else '实机日志'))
print('[verify] 覆盖图  : %s（历史批次，页面保留来源说明）' % d.get('coverage_map'))
print('[verify] 卡片/车 : %s / %s' % (d.get('cards'), d.get('vehicles')))
missing = d.get('missing') or []
if missing:
    print('[verify] 缺失    : %s' % ', '.join(missing))
problems = []
if d.get('source') != 'runtime':
    problems.append('来源仍是 packaged —— 数据包没落到 %s' % d.get('runtime_dir'))
if (d.get('mission') or {}).get('run') != expected:
    problems.append('批次不符：期望 %s，实际 %s'
                    % (expected, (d.get('mission') or {}).get('run')))
if missing:
    problems.append('数据包缺 %d 项' % len(missing))
for p in problems:
    print('[verify] 未通过：%s' % p)
if problems:
    sys.exit(1)
print('[verify] 通过：网站已在用这一批素材')
PY
else
  # 没有 python3 时退一步：只断言两个关键字段
  echo "[verify] 原始响应：$BODY"
  case "$BODY" in
    *'"source":"runtime"'*) ;;
    *) echo "[verify] 未通过：来源不是 runtime" >&2; exit 1 ;;
  esac
  case "$BODY" in
    *"\"run\":\"$EXPECTED_RUN\""*) ;;
    *) echo "[verify] 未通过：批次不是 $EXPECTED_RUN" >&2; exit 1 ;;
  esac
  echo "[verify] 通过（粗检）：网站已在用 $EXPECTED_RUN"
fi
