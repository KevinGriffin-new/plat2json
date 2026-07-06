# shared helpers for the plat work queue (sourced by every job)
PJ="$HOME/plat2json"; EVAL="$PJ/eval"; PY="$PJ/.venv/bin/python"
QDIR="$HOME/plat-queue"; RESULTS="$QDIR/results"
URL="http://127.0.0.1:8080"; PROMPT="$EVAL/read_prompt_local.txt"
mkdir -p "$RESULTS/reads" "$QDIR/logs"

qlog() { echo "$(date '+%F %T') $*"; }

# keep the score<->harness sources bridge alive
[ -e "$EVAL/score/_sources" ] || ln -s ../harness/_sources "$EVAL/score/_sources"

ensure_server() {
  curl -s --max-time 4 "$URL/v1/models" 2>/dev/null | grep -q '"id"' && return 0
  qlog "server down -> relaunch serve_vl7.sh"
  pkill -f "[l]lama-server"; sleep 4
  nohup bash "$PJ/serve_vl7.sh" >> "$QDIR/logs/server.log" 2>&1 </dev/null &
  for i in $(seq 1 48); do
    sleep 5
    curl -s --max-time 3 "$URL/v1/models" 2>/dev/null | grep -q '"id"' && return 0
  done
  qlog "server FAILED to come up"; return 1
}
