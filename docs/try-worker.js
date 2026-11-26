/* try-worker.js - runs the vector-only plat2json path in Pyodide, off the UI
 * thread so the page can stop it on a timeout. The PDF bytes stay in this tab:
 * nothing is uploaded. Python comes from the repo itself (vector_plan.py,
 * plan2dxf.py, eval/harness/vector_golden.py), not a copy.
 *
 * Load order: same-origin next to this worker (the assembled Pages site),
 * then one directory up (a repo-root checkout), then jsDelivr pinned to a
 * commit SHA — never @main. The Pages workflow overwrites PINNED_SHA with
 * GITHUB_SHA when it assembles the site.
 */
const PYODIDE = "https://cdn.jsdelivr.net/pyodide/v0.29.5/full/";
const PINNED_SHA = "08c2935c851d5a8e0647a621746f2564221f33c4";
const REPO_CDN = "https://cdn.jsdelivr.net/gh/KevinGriffin-new/plat2json@";
const FILES = ["vector_plan.py", "plan2dxf.py", "eval/harness/vector_golden.py"];

let pyodide = null;

function fileUrls(path, ref) {
  const here = self.location.href;
  const sha = ref || PINNED_SHA;
  return [
    new URL(path, here).href,                 // assembled site / Pages Actions
    new URL("../" + path, here).href,         // python -m http.server at repo root
    REPO_CDN + sha + "/" + path,              // SHA-pinned fallback, not @main
  ];
}

async function fetchRepoFile(path, ref) {
  for (const url of fileUrls(path, ref)) {
    try {
      const r = await fetch(url, { cache: "no-cache" });
      if (!r.ok) continue;
      const text = await r.text();
      if (/^\s*</.test(text)) continue;          // an HTML 404 page, not Python
      return { text, url };
    } catch (e) { /* try the next source */ }
  }
  throw new Error("could not load " + path + " from the repo");
}

async function boot(ref) {
  const t0 = performance.now();
  importScripts(PYODIDE + "pyodide.js");
  pyodide = await loadPyodide({ indexURL: PYODIDE });
  const tCore = performance.now();
  await pyodide.loadPackage(["pymupdf", "micropip"]);
  const micropip = pyodide.pyimport("micropip");
  await micropip.install("ezdxf");                 // pure-Python wheel
  const tPkgs = performance.now();
  const root = "/home/pyodide/plat2json";
  const sources = {};
  for (const f of FILES) {
    const { text, url } = await fetchRepoFile(f, ref);
    const dir = (root + "/" + f).split("/").slice(0, -1).join("/");
    pyodide.FS.mkdirTree(dir);
    pyodide.FS.writeFile(root + "/" + f, text);
    sources[f] = url;
  }
  pyodide.runPython(`
import os, sys
os.environ["EZDXF_USE_C_EXTENSIONS"] = "false"
sys.path.insert(0, "${root}")
import vector_plan, plan2dxf
`);
  const t1 = performance.now();
  return { core_ms: tCore - t0, packages_ms: tPkgs - tCore, total_ms: t1 - t0, sources };
}

self.onmessage = async (ev) => {
  const msg = ev.data || {};
  try {
    if (msg.type === "boot") {
      const timing = await boot(msg.ref);
      self.postMessage({ type: "ready", timing });
    } else if (msg.type === "run") {
      const t0 = performance.now();
      pyodide.globals.set("pdf_bytes", pyodide.toPy(new Uint8Array(msg.bytes)));
      pyodide.globals.set("page_no", msg.page);
      pyodide.globals.set("plot_scale", msg.plotScale);
      const out = pyodide.runPython(`
import io, json
res = vector_plan.analyze_pdf(pdf_bytes.tobytes(), page_no, plot_scale)
dxf = None
if not res["scanned"]:
    buf = io.StringIO()
    plan2dxf.write_dxf(res["plan"], buf)
    dxf = buf.getvalue()
json.dumps({"res": res, "dxf": dxf}, ensure_ascii=False)
`);
      const parsed = JSON.parse(out);
      parsed.extract_ms = performance.now() - t0;
      self.postMessage({ type: "result", ...parsed });
    }
  } catch (e) {
    let m = String(e && e.message || e);
    const last = m.trim().split("\n").filter(Boolean).pop();   // Python's last line
    self.postMessage({ type: "error", message: last || m });
  }
};
