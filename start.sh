#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ENV_NAME="data_collector_web"
LOG_DIR="$PROJECT_DIR/config/logs"
RUN_DIR="$PROJECT_DIR/config/run"
BOOT_ID="$(cat /proc/sys/kernel/random/boot_id)"
ACTION="${1:-start}"
BACKEND_PID=""
FRONTEND_PID=""

fail() { echo "错误：$*" >&2; exit 1; }
usage() {
  echo "用法：bash $PROJECT_DIR/start.sh [start|stop|restart|status|logs|--check]"
  echo "默认 start：后台启动，关闭终端或 VS Code 后继续运行。"
  echo "stop 停止；restart 重启；status 查看状态；logs 跟踪日志；--check 检查环境。"
}
address() {
  local server_ip
  server_ip="$(hostname -I 2>/dev/null | awk '{print $1}' || true)"
  echo "访问地址：http://${server_ip:-127.0.0.1}:5180/"
  echo "API 文档：http://${server_ip:-127.0.0.1}:8000/docs"
}

# 同时核验 PID、启动时刻、系统启动标识和命令，防止误用过期 PID。
process_stamp() {
  local value
  local -a fields
  value="$(cat "/proc/$1/stat" 2>/dev/null)" || return 1
  read -r -a fields <<< "${value##*) }"
  [[ "${fields[0]}" != Z ]] || return 1
  echo "${fields[19]}"
}
record_pid() {
  local role="$1" pid="$2" stamp
  stamp="$(process_stamp "$pid")" || return 1
  printf '%s %s %s\n' "$pid" "$stamp" "$BOOT_ID" > "$RUN_DIR/$role.pid.tmp"
  mv -- "$RUN_DIR/$role.pid.tmp" "$RUN_DIR/$role.pid"
}
pid_for() {
  local role="$1" pid stamp boot current cwd
  local -a command
  [[ -f "$RUN_DIR/$role.pid" ]] || return 1
  read -r pid stamp boot < "$RUN_DIR/$role.pid" || return 1
  [[ "$pid" =~ ^[0-9]+$ && "$stamp" =~ ^[0-9]+$ && "$boot" == "$BOOT_ID" ]] || return 1
  current="$(process_stamp "$pid")" || return 1
  [[ "$current" == "$stamp" ]] || return 1
  mapfile -d '' -t command < "/proc/$pid/cmdline" 2>/dev/null || return 1
  cwd="$(readlink "/proc/$pid/cwd" 2>/dev/null)" || return 1
  case "$role" in
    supervisor) [[ " ${command[*]} " == *" $PROJECT_DIR/start.sh __run "* ]] || return 1 ;;
    backend) [[ "$cwd" == "$PROJECT_DIR/backend" && " ${command[*]} " == *" run.py "* && "${command[0]##*/}" == python* ]] || return 1 ;;
    frontend) [[ "$cwd" == "$PROJECT_DIR/frontend" && " ${command[*]} " == *" node_modules/vite/bin/vite.js "* && "${command[0]##*/}" == node ]] || return 1 ;;
    *) return 1 ;;
  esac
  echo "$pid"
}
any_running() {
  local role
  for role in supervisor backend frontend; do
    if pid_for "$role" >/dev/null; then return 0; fi
  done
  return 1
}
healthy() {
  local path
  for path in '8000/api/health' '5180/api/health' '5180/'; do
    curl --noproxy '*' --fail --silent --max-time 2 "http://127.0.0.1:$path" >/dev/null || return 1
  done
}
check_environment() {
  if [[ "${ENV_READY:-0}" == 1 ]]; then return; fi
  local candidate conda_base program
  local -a candidates=()
  PYTHON_BIN="${DCW_PYTHON:-}"
  if [[ -z "$PYTHON_BIN" ]]; then
    if [[ "${CONDA_DEFAULT_ENV:-}" == "$ENV_NAME" && -n "${CONDA_PREFIX:-}" ]]; then
      candidates+=("$CONDA_PREFIX/bin/python")
    fi
    if command -v conda >/dev/null 2>&1; then
      conda_base="$(conda info --base)"
      candidates+=("$conda_base/envs/$ENV_NAME/bin/python")
    fi
    candidates+=("$HOME/miniconda3/envs/$ENV_NAME/bin/python" "/root/miniconda3/envs/$ENV_NAME/bin/python" "$HOME/anaconda3/envs/$ENV_NAME/bin/python")
    for candidate in "${candidates[@]}"; do
      if [[ -x "$candidate" ]]; then PYTHON_BIN="$candidate"; break; fi
    done
  fi
  [[ -n "$PYTHON_BIN" && -x "$PYTHON_BIN" ]] || fail "未找到 $ENV_NAME 环境，请按 README 安装，或用 DCW_PYTHON 指定解释器绝对路径。"
  for program in node curl flock setsid nohup; do
    command -v "$program" >/dev/null 2>&1 || fail "未找到 $program，请先安装相应依赖。"
  done
  node -e 'if (Number(process.versions.node.split(".")[0]) < 20) process.exit(1)' || fail "需要 Node.js 20 或以上版本。"
  [[ -f "$PROJECT_DIR/frontend/node_modules/vite/bin/vite.js" ]] || fail "前端依赖未安装，请在 $PROJECT_DIR/frontend 执行 npm install。"
  "$PYTHON_BIN" - <<'PY'
import importlib.util
import sys

packages = ("fastapi", "uvicorn", "sqlalchemy", "pydantic", "cv2", "numpy", "PIL", "ultralytics", "multipart", "yaml", "clip")
missing = [name for name in packages if importlib.util.find_spec(name) is None]
if missing:
    sys.exit("错误：后端依赖缺失：" + ", ".join(missing) + "。请安装 backend/requirements.txt。")
PY
  export DCW_PYTHON="$PYTHON_BIN"
  ENV_READY=1
  echo "环境检查通过：$PYTHON_BIN"
}

# 守护进程和两个服务位于独立会话，标准输入关闭、输出落盘。
supervise() {
  [[ -n "${DCW_PYTHON:-}" && -x "$DCW_PYTHON" ]] || fail "请使用 start 命令启动。"
  exec 9>"$PROJECT_DIR/config/start.lock"
  flock -n 9 || fail "已有本项目服务持有启动锁。"
  trap '' HUP
  trap cleanup EXIT
  trap 'exit 0' INT TERM
  record_pid supervisor "$$"
  (
    cd "$PROJECT_DIR/backend"
    exec "$DCW_PYTHON" -u run.py 9>&-
  ) >>"$LOG_DIR/backend.log" 2>&1 &
  BACKEND_PID=$!
  record_pid backend "$BACKEND_PID"
  (
    cd "$PROJECT_DIR/frontend"
    exec node node_modules/vite/bin/vite.js --host 0.0.0.0 --port 5180 --strictPort 9>&-
  ) >>"$LOG_DIR/frontend.log" 2>&1 &
  FRONTEND_PID=$!
  record_pid frontend "$FRONTEND_PID"
  echo "$(date -Is) 后台启动：守护进程 $$，后端 $BACKEND_PID，前端 $FRONTEND_PID"
  local result=0
  wait -n "$BACKEND_PID" "$FRONTEND_PID" || result=$?
  echo "$(date -Is) 服务退出（退出码 $result），正在停止其他服务。使用 restart 重新启动。"
  exit "$result"
}
cleanup() {
  trap - EXIT INT TERM
  local pid
  echo "$(date -Is) 正在停止前后端……"
  for pid in "$FRONTEND_PID" "$BACKEND_PID"; do
    if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then kill -TERM "$pid" 2>/dev/null || true; fi
  done
  for pid in "$FRONTEND_PID" "$BACKEND_PID"; do
    if [[ -n "$pid" ]]; then wait "$pid" 2>/dev/null || true; fi
  done
  rm -f -- "$RUN_DIR/supervisor.pid" "$RUN_DIR/backend.pid" "$RUN_DIR/frontend.pid"
  echo "$(date -Is) 服务已停止。"
}
stop_services() {
  local pid role deadline=$((SECONDS + 45))
  if ! any_running && ! flock -n "$PROJECT_DIR/config/start.lock" true; then
    echo "旧版启动脚本仍在运行，请在原终端按 Ctrl+C 停止。" >&2
    return 1
  fi
  if pid="$(pid_for supervisor)"; then
    echo "正在停止后台服务……"
    kill -TERM "$pid" 2>/dev/null || true
  else
    # 守护进程意外退出后，仅清理身份仍匹配的本项目子进程。
    for role in backend frontend; do
      if pid="$(pid_for "$role")"; then kill -TERM "$pid" 2>/dev/null || true; fi
    done
  fi
  while any_running || ! flock -n "$PROJECT_DIR/config/start.lock" true; do
    if ((SECONDS >= deadline)); then
      echo "服务仍在结束当前任务，请稍后执行 status；未强制终止文件写入。" >&2
      return 1
    fi
    sleep 0.2
  done
  rm -f -- "$RUN_DIR/supervisor.pid" "$RUN_DIR/backend.pid" "$RUN_DIR/frontend.pid"
  echo "DataCollectorWeb 已停止。"
}
start_services() {
  if any_running; then
    if pid_for supervisor >/dev/null && pid_for backend >/dev/null && pid_for frontend >/dev/null && healthy; then
      echo "DataCollectorWeb 已在后台运行，无需重复启动。"
      address
      return
    fi
    fail "存在尚未就绪或残留的本项目进程，请先执行 status 查看，或执行 restart 恢复。"
  fi
  check_environment
  flock -n "$PROJECT_DIR/config/start.lock" true || fail "旧版启动脚本仍在运行，请在原终端按 Ctrl+C 后重新启动。"
  "$PYTHON_BIN" - <<'PY'
import socket
import sys

for port in (8000, 5180):
    with socket.socket() as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("0.0.0.0", port))
        except OSError as exc:
            sys.exit(f"错误：端口 {port} 不可用（{exc}），请检查占用进程；不会自动结束其他服务。")
PY
  rm -f -- "$RUN_DIR/supervisor.pid" "$RUN_DIR/backend.pid" "$RUN_DIR/frontend.pid"
  nohup setsid bash "$PROJECT_DIR/start.sh" __run </dev/null >>"$LOG_DIR/supervisor.log" 2>&1 8>&- &
  local launcher_pid=$! deadline=$((SECONDS + 45))
  echo "正在后台启动，日志目录：$LOG_DIR"
  while ((SECONDS < deadline)); do
    if pid_for supervisor >/dev/null && pid_for backend >/dev/null && pid_for frontend >/dev/null && healthy; then
      echo "启动成功！现在可以关闭终端或 VS Code。"
      address
      echo "管理命令：bash $PROJECT_DIR/start.sh {stop|restart|status|logs}"
      return
    fi
    if ! kill -0 "$launcher_pid" 2>/dev/null && ! any_running; then break; fi
    sleep 0.5
  done
  stop_services || true
  tail -n 15 "$LOG_DIR/supervisor.log" "$LOG_DIR/backend.log" "$LOG_DIR/frontend.log" >&2
  fail "启动未成功，请查看以上日志。"
}
status() {
  local role pid complete=1
  for role in supervisor backend frontend; do
    if pid="$(pid_for "$role")"; then
      echo "$role：运行中（PID $pid）"
    else
      echo "$role：未运行"
      complete=0
    fi
  done
  if [[ "$complete" == 1 ]]; then
    if healthy; then echo "健康检查：前端、后端和 API 代理均正常。"; address; return; fi
    echo "健康检查：尚未就绪或正在停止，请查看日志。"
  fi
  return 1
}

[[ $# -le 1 ]] || { usage; exit 1; }
case "$ACTION" in
  -h|--help|help) usage; exit 0 ;;
  --check) check_environment; exit 0 ;;
  __run) supervise; exit 0 ;;
  status) status; exit ;;
  logs) exec tail -n 60 -F "$LOG_DIR/supervisor.log" "$LOG_DIR/backend.log" "$LOG_DIR/frontend.log" ;;
  start|stop|restart) ;;
  *) usage; fail "不支持的命令：$ACTION" ;;
esac

# 串行执行启停命令；后台守护进程不继承此操作锁。
command -v flock >/dev/null 2>&1 || fail "未找到 flock，请安装 util-linux。"
mkdir -p "$LOG_DIR" "$RUN_DIR"
exec 8>"$RUN_DIR/control.lock"
flock -n 8 || fail "另一个启动或停止命令正在执行，请稍后重试。"
case "$ACTION" in
  start) start_services ;;
  stop) stop_services ;;
  restart) check_environment; stop_services; start_services ;;
esac
