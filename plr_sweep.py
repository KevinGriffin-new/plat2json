"""Surrey PLR corpus sweep: enumerate Planning & Development Reports,
find the subdivision site-plan sheet in each, and stage them for the
drift pipeline (trace -> golden -> retrieval -> adjudication).

    python plr_sweep.py --corpus DIR probe --year 7914 --lo 100 --hi 130
    python plr_sweep.py --corpus DIR classify
    python plr_sweep.py --corpus DIR orient
    python plr_sweep.py --corpus DIR report

Reports live at
https://www.surrey.ca/sites/default/files/planning-reports/PLR_<APP>.pdf
with APP = 79YY-NNNN-00 (79YY encodes the year, NNNN a sequential file
number) — the whole archive is URL-enumerable. Downloads are throttled;
everything fetched is recorded in manifest.json with per-stage status so
the attrition (exists -> has site plan -> oriented -> traced -> adjudicated)
is reported honestly rather than papered over.
"""
import argparse
import base64
import json
import re
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE = "https://www.surrey.ca/sites/default/files/planning-reports/PLR_{app}.pdf"
UA = {"User-Agent": "Mozilla/5.0 (plat2json research; contact papa.legba404@gmail.com)"}
VLM = "http://192.168.50.219:8080"

CLASSIFY_Q = """Is this page a SITE PLAN or SUBDIVISION LAYOUT drawing showing
individual numbered lots drawn as parcels (a lot layout / lotting plan)?
Pages that are text, tables, location key maps, elevation drawings,
landscaping plant lists, or engineering profiles do NOT count.
Reply with ONLY YES or NO."""


def manifest_path(corpus):
    return Path(corpus) / "manifest.json"


def load_manifest(corpus):
    p = manifest_path(corpus)
    return json.loads(p.read_text()) if p.exists() else {}


def save_manifest(corpus, m):
    manifest_path(corpus).write_text(json.dumps(m, indent=1))


def vlm_ask(png_bytes, question, timeout=600):
    b64 = base64.b64encode(png_bytes).decode()
    body = {"model": "qwen2.5-vl", "temperature": 0, "max_tokens": 10,
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": question},
                {"type": "image_url",
                 "image_url": {"url": f"data:image/png;base64,{b64}"}}]}]}
    req = urllib.request.Request(
        VLM + "/v1/chat/completions", json.dumps(body).encode(),
        {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)["choices"][0]["message"]["content"].strip().upper()


def cmd_probe(a):
    corpus = Path(a.corpus)
    corpus.mkdir(parents=True, exist_ok=True)
    m = load_manifest(a.corpus)
    for n in range(a.lo, a.hi + 1):
        app = f"{a.year}-{n:04d}-00"
        if app in m and m[app].get("status") != "probe-error":
            continue
        url = BASE.format(app=app)
        dest = corpus / f"PLR_{app}.pdf"
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=60) as r:
                data = r.read()
            dest.write_bytes(data)
            m[app] = {"status": "fetched", "url": url, "bytes": len(data)}
            print(f"{app}: fetched {len(data)//1024} KB")
        except urllib.error.HTTPError as e:
            m[app] = {"status": f"http-{e.code}", "url": url}
            print(f"{app}: {e.code}")
        except Exception as e:
            m[app] = {"status": "probe-error", "url": url, "error": str(e)}
            print(f"{app}: ERROR {e}")
        save_manifest(a.corpus, m)
        time.sleep(a.throttle)


def cmd_classify(a):
    import fitz
    m = load_manifest(a.corpus)
    for app, rec in m.items():
        if rec.get("status") != "fetched" or "site_pages" in rec:
            continue
        pdf = Path(a.corpus) / f"PLR_{app}.pdf"
        try:
            doc = fitz.open(pdf)
        except Exception as e:
            rec["site_pages"] = []
            rec["classify_error"] = str(e)
            continue
        hits = []
        for i, page in enumerate(doc):
            # site plans are drawing sheets: mostly landscape in these
            # reports, but classify every page and let the VLM decide —
            # cheap thumbnails, one YES/NO each
            zoom = 700 / max(page.rect.width, page.rect.height)
            png = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom)).tobytes("png")
            try:
                ans = vlm_ask(png, CLASSIFY_Q)
            except Exception as e:
                ans = f"ERR {e}"
            if ans.startswith("YES"):
                hits.append(i)
            print(f"  {app} p{i}: {ans[:20]}")
        rec["site_pages"] = hits
        rec["n_pages"] = len(doc)
        save_manifest(a.corpus, m)
        print(f"{app}: {len(hits)} site-plan page(s) of {len(doc)}: {hits}")


def cmd_orient(a):
    import subprocess
    import sys
    m = load_manifest(a.corpus)
    for app, rec in m.items():
        # page 0 is the report cover, whose location-sketch map classifies
        # YES systematically; real drawing sheets cluster in the appendices,
        # so take the LAST non-cover hit (verified: 7914-0107's true site
        # plan is p20 of hits [0, 12, 13, 20])
        pages = [p for p in rec.get("site_pages", []) if p != 0]
        if not pages or "orient" in rec:
            continue
        page = pages[-1]
        pdf = Path(a.corpus) / f"PLR_{app}.pdf"
        out = Path(a.corpus) / f"PLR_{app}.p{page}.prior.json"
        r = subprocess.run(
            [sys.executable, "orient_prior.py", str(pdf), "--page", str(page),
             "--vlm", VLM, "-o", str(out)],
            capture_output=True, text=True, cwd=Path(__file__).parent)
        if r.returncode == 0 and out.exists():
            prior = json.loads(out.read_text())
            rec["orient"] = {k: prior[k] for k in
                             ("rot_prior", "rot_tol", "no_mirror", "confidence")}
            print(f"{app} p{page}: rot={prior['rot_prior']} "
                  f"conf={prior['confidence']}")
        else:
            rec["orient"] = {"error": (r.stderr or r.stdout)[-200:]}
            print(f"{app} p{page}: orient FAILED")
        save_manifest(a.corpus, m)


def cmd_report(a):
    m = load_manifest(a.corpus)
    total = len(m)
    fetched = [k for k, r in m.items() if r.get("status") == "fetched"]
    with_plan = [k for k in fetched if m[k].get("site_pages")]
    oriented = [k for k in with_plan
                if m[k].get("orient", {}).get("rot_prior") is not None]
    print(f"probed:   {total}")
    print(f"fetched:  {len(fetched)}  "
          f"({total - len(fetched)} absent/error)")
    print(f"site plan found: {len(with_plan)}")
    print(f"oriented (prior derived): {len(oriented)}")
    for k in fetched:
        r = m[k]
        o = r.get("orient", {})
        print(f"  {k}: pages={r.get('n_pages', '?')} "
              f"site={r.get('site_pages', '?')} "
              f"rot={o.get('rot_prior', '-')} conf={o.get('confidence', '-')}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--corpus", required=True, help="corpus directory")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("probe", help="enumerate + download PLR PDFs")
    p.add_argument("--year", required=True, help="79YY, e.g. 7914")
    p.add_argument("--lo", type=int, default=1)
    p.add_argument("--hi", type=int, default=50)
    p.add_argument("--throttle", type=float, default=1.0)
    p.set_defaults(fn=cmd_probe)
    p = sub.add_parser("classify", help="find site-plan pages (VLM)")
    p.set_defaults(fn=cmd_classify)
    p = sub.add_parser("orient", help="derive pose priors (orient_prior.py)")
    p.set_defaults(fn=cmd_orient)
    p = sub.add_parser("report", help="attrition summary")
    p.set_defaults(fn=cmd_report)
    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
