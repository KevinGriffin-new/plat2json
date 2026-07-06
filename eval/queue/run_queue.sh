#!/usr/bin/env bash
# plat-queue runner: executes jobs/*.sh in lexical order, once each.
#   - drop new NNN_name.sh files into jobs/ any time; picked up on rescan
#   - touch ~/plat-queue/STOP to halt after the current job
#   - state/done/<job> or state/failed/<job> marks completion; delete to re-run
#   - per-job logs in logs/<job>.log; queue log in logs/queue.log
set -u
QDIR="$HOME/plat-queue"
mkdir -p "$QDIR"/state/done "$QDIR"/state/failed "$QDIR"/logs "$QDIR"/results/reads "$QDIR"/jobs
MAIN="$QDIR/logs/queue.log"
echo "$(date '+%F %T') ===== queue runner start (pid $$) =====" >> "$MAIN"
while :; do
  [ -e "$QDIR/STOP" ] && { echo "$(date '+%F %T') STOP -> exit" >> "$MAIN"; exit 0; }
  ran=0
  for job in "$QDIR"/jobs/*.sh; do
    [ -e "$job" ] || continue
    name=$(basename "$job")
    { [ -e "$QDIR/state/done/$name" ] || [ -e "$QDIR/state/failed/$name" ]; } && continue
    [ -e "$QDIR/STOP" ] && break
    echo "$(date '+%F %T') RUN $name" >> "$MAIN"
    if bash "$job" >> "$QDIR/logs/${name%.sh}.log" 2>&1; then
      touch "$QDIR/state/done/$name";   echo "$(date '+%F %T') DONE $name" >> "$MAIN"
    else
      touch "$QDIR/state/failed/$name"; echo "$(date '+%F %T') FAILED $name (logs/${name%.sh}.log)" >> "$MAIN"
    fi
    ran=1
  done
  [ "$ran" -eq 0 ] && sleep 300
done
