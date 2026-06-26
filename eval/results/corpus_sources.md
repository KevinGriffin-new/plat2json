# Corpus sources — where to find genuinely-scanned survey plans (and a golden)

A scan is only a *test* if it can be **scored**. Each source below is ranked by
whether it ships a license-free answer key. Source images are never committed
here (per-source redistribution); the manifest carries the public URL and
`harness/acquire.py` re-fetches.

Golden types, cheapest authoritative first:
- **self-golden** — the plat prints its own curve/line table (large horizontal
  text → read as the key; score the small rotated body labels against it).
- **bundled field notes** — a separate scanned course-and-distance record,
  OCR'd/VLM'd as the key.
- **external vector twin** — the same plat also exists as a vector/text PDF
  (a perfect key for a real scan of that sheet).
- **geometric self-consistency** — the survey's own invariants (closure,
  perpendicularity, equal opposite sides) validate a read with no key at all;
  strongest for high-entropy values.

What a real scan buys over synthetic degradation: scanner banding/moiré,
ink-bleed, paper foxing, microfilm halation, true (not affine) perspective, and
a real camera/codec JPEG pipeline — plus a genuine **quality gradient across
survey eras** (faint 1880s handwriting → crisp 1930s typewriter → modern offset).

---

## Source 1 — BLM Wyoming cadastral  (primary, public domain)
`https://www.wy.blm.gov/cadastral/countyplats/<county>/t<NN>nr<NN>w.pdf`
- Each PDF = a HIGH-RES scanned plat (~9584×7578 px) + scanned field notes
  (later pages). US-federal → public domain. No login, no viewer. Enumerate a
  county from `countyplats/<county>.htm` (grep `href=.*\.pdf`).
- **Scale:** thousands of townships statewide (e.g. one county ≈ 80–300 plats).
- **Golden:** bundled field notes (OCR for typed ≥~1900; VLM for handwritten
  pre-1900). `harness/locate_fieldnotes.py` + `ocr_fieldnotes.py` build a key.
- **Entropy caveat:** regular PLSS townships are low-entropy (cardinal section
  bearings + 40/80-chain distances) → score on DISTANCES, or prefer
  meander/HES/**mineral-survey** figures (below) for high-entropy bearings.

## Source 1b — BLM Mineral Survey plats  (high-entropy, public domain)
`https://www.wy.blm.gov/cadastral/mineralsurvey/<range>/ms<N>.pdf`
(+ `/fieldnotes/ms<N>_fn.pdf`)
- Standalone scanned lode-claim plats (~8000×6000 px): **non-cardinal,
  dimensioned, genuinely high-entropy** — what regular PLSS townships can't be.
- Enumerate from each township plat's Mineral-Survey index page; mining
  districts (e.g. South Pass / Fremont Co.) hold the densest claims.
- **Golden:** the field notes (often HANDWRITTEN pre-1900 → VLM read) AND a
  built-in **geometric self-consistency** key — lode claims are rectangles, so
  read courses are checkable with no answer key (see `blm_ms52a`, R-MS).

## Source 2 — Texas GLO  (partial: the free ∩ fitting ∩ golden set is empty)
- **Land Grant DB** — free direct PDFs (`cdn.glo.texas.gov/.../landgrants/PDFs/...`)
  but they are handwritten textual patents/field notes, NOT dimensioned plats.
- **Sketch Files** — dimensioned surveyor sketches WITH an authoritative
  bearing/distance index (GLO's 2022–24 validation project), but full-res digital
  is paywalled.
- Lesson: a catalog "having scanned plats" ≠ the free ∧ task-fitting ∧
  golden-bearing subset being non-empty. Verify that intersection on one item
  before committing a source.

## Source 3 — Modern subdivisions with curve+line tables  (self-golden, gated)
The ideal high-entropy + self-golden case, but most portals gate full-res behind
a viewer/login/paywall (e.g. state plat archives, county recorder viewers).
A flat-URL public source here would be valuable — contributions welcome.

---

## Notable corpus members (in manifest.json)
- `glo_t28nr71w` — BLM PLSS plat; the R1 + R-CLIFF subject.
- `blm_ms52a` — BLM Mineral Survey No. 52; the R-MS high-entropy subject.
- `nara_m121` — Truman Library sales plat; a **low-res (1337×2665)** historical
  scan = a genuine degradation case (no external key yet; self-check only).
- `county_test` — Boise County 2025 vector final plat; its text layer is a free,
  high-entropy golden = the **clean-render upper bound** on reading. Image is
  URL-only (public record); only the numeric key is committed.
- `ltsa_sample_1` — ParcelMap BC sample; reference-only (Crown copyright), run
  locally on your own copy.
