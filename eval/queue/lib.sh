# shared helpers for the plat work queue (sourced by every job)
PJ="$HOME/plat2json"; EVAL="$PJ/eval"; PY="$PJ/.venv/bin/python"
QDIR="$HOME/plat-queue"; RESULTS="$QDIR/results"
# URL is the llama-server endpoint ensure_server checks. NEVER declare another
# variable named URL in a job — `declare -A URL=(...)` silently shadows this
# scalar, "$URL" then expands EMPTY, and ensure_server kills healthy servers
# forever (wave-8 job 240 lost four runs to exactly that).
URL="http://127.0.0.1:8080"; PROMPT="$EVAL/read_prompt_local.txt"
mkdir -p "$RESULTS/reads" "$QDIR/logs"

qlog() { echo "$(date '+%F %T') $*"; }

# keep the score<->harness sources bridge alive
[ -e "$EVAL/score/_sources" ] || ln -s ../harness/_sources "$EVAL/score/_sources"

ensure_server() {
  # NEVER pkill first: a LOADING server doesn't answer yet, and killing it
  # restarts the multi-minute load from zero — that loop cost wave 8 two job
  # aborts (each retry killed the previous retry's almost-loaded server).
  _vl_up() { curl -s --max-time 4 "$URL/v1/models" 2>/dev/null | grep -q '"id"'; }
  _vl_up && return 0
  if pgrep -f "[l]lama-server" >/dev/null; then
    qlog "server process alive but not answering -> wait for model load"
  else
    qlog "server down -> launch serve_vl7.sh"
    nohup bash "$PJ/serve_vl7.sh" >> "$QDIR/logs/server.log" 2>&1 </dev/null &
  fi
  for i in $(seq 1 60); do    # 5 min: normal load finishes here
    sleep 5
    _vl_up && return 0
    pgrep -f "[l]lama-server" >/dev/null || \
      nohup bash "$PJ/serve_vl7.sh" >> "$QDIR/logs/server.log" 2>&1 </dev/null &
  done
  qlog "server unresponsive after 5 min -> kill + one clean relaunch"
  pkill -f "[l]lama-server"; sleep 6
  nohup bash "$PJ/serve_vl7.sh" >> "$QDIR/logs/server.log" 2>&1 </dev/null &
  for i in $(seq 1 90); do    # generous window for the cold relaunch
    sleep 5
    _vl_up && return 0
  done
  qlog "server FAILED to come up"; return 1
}
