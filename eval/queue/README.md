# eval/queue — the standing GPU work queue (workstation-lewis)

Ops tooling that keeps the 8 GB RTX 4060 box reading plats unattended for days.
This directory is the **committed source of truth**; the runtime copy lives at
`~/plat-queue/` on the GPU box (outside the repo, because it accumulates
gigabytes of state/logs/results that don't belong in git).

## Deploy

```bash
rsync -av --exclude state --exclude logs --exclude results \
    eval/queue/ kevin@192.168.50.219:plat-queue/
ssh kevin@192.168.50.219 'chmod +x ~/plat-queue/run_queue.sh ~/plat-queue/jobs/*.sh; \
    pgrep -f run_queue.sh || nohup bash ~/plat-queue/run_queue.sh >/dev/null 2>&1 </dev/null &'
```

## Design

`run_queue.sh` executes `jobs/NNN_*.sh` in lexical order, once each:
- new job files dropped into `jobs/` are picked up on the next rescan (≤5 min);
- `state/done/<job>` / `state/failed/<job>` mark completion — delete a marker
  to re-run; jobs are written to be individually resumable (skip via
  `results/reads/<slug>.json`);
- `touch ~/plat-queue/STOP` halts after the current job;
- per-job logs in `logs/`, machine-readable outputs in `results/*.tsv` +
  `results/reads/*.json`.

Jobs source `lib.sh`: venv python, paths, and a self-healing `ensure_server`
that relaunches `serve_vl7.sh` (Qwen2.5-VL-7B) if the llama-server died. Jobs
that need a different model (e.g. `serve_vl8.sh` for the Qwen3-VL-8B A/B) manage
the server themselves and MUST kill it on exit so `ensure_server` restores the
7B. Kill with `pkill -f "[l]lama-server"` — the bracket trick stops pkill from
matching (and killing) your own ssh session's command line.

## Job history (waves 1–6, 2026-07-01 → 07-06)

| jobs | what | outcome (details in results/RESULTS.md once written up) |
|------|------|---------------------------------------------------------|
| 010–030 | enumerate + stage + read BLM WY mineral surveys | 565 found ms1–600; reads at ~50 s/sheet |
| 040 | 7B 5-sample consensus vs vector goldens | union beats the 32B single-pass on dense sheets |
| 050 | Qwen3-VL-8B A/B | mixed; distances regress on county |
| 060–070, 090, 160 | NCDOT ROW vector-golden harvest + reads | 100 sheets scored, mostly 85–100% recall |
| 080 | Qwen3-VL-8B consensus | still loses to 7B union → 7B stays |
| 100, 140–150 | ms wave 2/3 (through ms1200) | corpus complete: 648 sheets read |
| 110 | precision frontier (union_k / majN) | **union_k3 = efficiency point** |
| 120–130 | GLO Records scout + pilots | 244 MT minerals indexed; fieldnote conversion is browser-only |
| 170 | GLO MT harvest via `glo_stage.py` | native-zoom render fix (fixed-dpi = gigapixel bomb) |

Sheet-acquisition scripts are polite crawlers: 1.5–3 s spacing, resumable
downloads, and they skip anything already staged.
