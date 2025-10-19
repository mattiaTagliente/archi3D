## Role

Implement **Phase 7 — Reports & Quality Gate**. Add a robust `archi3d report` command set that:

1. **Builds per-run analytical reports** from the SSOT (`tables/generations.csv`, optionally joined with `tables/items.csv`).
2. **Evaluates quality gates** against configured thresholds and emits clear pass/fail summaries.
3. **Exports artifacts** (Markdown/HTML report + tidy CSVs) under `reports/`, and **appends structured logs**.

**Do not** change prior phases’ behavior. Treat Phases 0–6 as done and available.

---

## Objectives

1. Provide a **per-run report** summarizing:

   * Job counts & statuses (completed/failed/skipped).
   * **FScore** metrics (fscore, precision, recall, chamfer, distance stats). 
   * **VFScore** metrics (overall + subscores, dispersion across repeats). 
   * Time/cost aggregates (duration, cost fields if present). 
2. Implement a **Quality Gate**:

   * Use thresholds from config (Phase-0/Schema), e.g., `fscore_min` (required) and optional visual gate if present; compute **per-job pass/fail** and **run-level pass rate**. 
3. Export **report files** to `reports/run_<run_id>/`:

   * `report.md` (always), plus optional `report.html`.
   * `tables/*.csv` (normalized extracts ready for BI).
4. Write a **structured summary record** to `logs/metrics.log` (event: `"report_run"`), reusing Phase-0 atomic log utilities. 

**Non-Goals:** No (re)execution of jobs, no consolidation logic, no metric recomputation. (Those are Phases 3–6.)

---

## Repository Pointers (where to work)

* **CLI**: extend `src/archi3d/cli.py` with `report` subcommands. (Follow CLI patterns used by earlier phases.) 
* **New**: `src/archi3d/reporting/report_run.py` — core analytics/quality gate & export.
* **Reuse**: Phase-0 path + I/O utilities (`PathResolver`, `update_csv_atomic`, `append_log_record`, `write_text_atomic`). **Do not** reimplement atomic I/O. 

---

## Functional Requirements

### A) CLI

Add a top-level `report` group with subcommand:

```
archi3d report run
  --run-id <string>                 (required)
  [--format <md|html|both>]         (default: md)
  [--include <glob-or-regex>]       (filter on product_id|variant|product_name)
  [--exclude <glob-or-regex>]
  [--only-status <csv-list>]        (default: completed,failed,skipped)
  [--quality-gate]                  (default: true)
  [--export-csv]                    (default: true)
  [--open]                          (default: false; attempt to open report)
  [--dry-run]                       (default: false; compute but do not write)
```

* Keep console succinct; detailed content goes to exported files and the log entry—same style as Phases 2/5/6 summaries.

### B) Inputs

* **SSOT**: `tables/generations.csv` (Phase-2+). 
* Optional join with `tables/items.csv` (Phase-1) for parent fields if needed. 
* **Workspace**: use `PathResolver` to resolve `reports_root`, `metrics_log_path()`, and relative path normalization. 

### C) Selection

A row is considered if:

1. `run_id` matches.
2. `status` ∈ `--only-status` (default: `completed,failed,skipped`).
3. If `--include`/`--exclude` provided, filter by **contains/regex/glob** on `product_id|variant|product_name` (same semantics as Phase-2 filters). 

### D) Analytics

Compute, at minimum:

1. **Volume & status**

   * Totals by status; failures with top-N error reasons (from `error_msg`). (Phases 3/4 wrote these fields.)

2. **Geometry (FScore)**

   * Distribution: mean/median/p95 of `fscore`, plus `precision/recall/chamfer_l2`.
   * Distance stats if available (`fscore_dist_*`).
   * Count of jobs with `fscore_status="ok"` vs `"error"`. 

3. **Visual (VFScore)**

   * Overall distribution (median, IQR, std) of `vfscore_overall`.
   * Per-dimension medians: `vf_finish`, `vf_texture_identity`, `vf_texture_scale_placement`.
   * Count of jobs with `vf_status="ok"` vs `"error"`. 

4. **Performance & cost**

   * Totals/averages for `generation_duration_s`.
   * Sum/avg of `estimated_cost_usd` if present. 

5. **Category cuts**

   * Grouped aggregates by `manufacturer`, `category_l1`, and `algo` (mean fscore, mean vfscore, counts). (Fields originate in Phase-1 and Phase-2.)

### E) Quality Gate

* **Config:** Read thresholds from the existing config model (Phase-0 schema). At minimum, **require `fscore_min`**. If `vfscore_overall` threshold exists in config, apply it; otherwise, treat VF as descriptive only. 
* **Per-job gate:**
  `job_pass = (fscore >= fscore_min) AND (optional: vfscore_overall >= vfscore_min)`
  Mark missing metrics as **fail** for that metric; if a metric is globally disabled (no threshold), ignore it in gate.
* **Run-level gate:**
  Compute **pass rate** = `% jobs with job_pass==true` over the **eligible** set (`status=completed` with required metrics present). Report both:

  * “of completed jobs” pass rate
  * “of all selected jobs” pass rate (treat missing metrics as fail).
* Emit a clear **PASS/FAIL** summary with the concrete threshold values used and counts.

### F) Outputs (per run)

Under `reports/run_<run_id>/`:

* `report.md` (always):

  * Title, timestamp, filters used.
  * Status histogram table.
  * FScore section (tables + brief commentary).
  * VFScore section (tables + dispersion lines).
  * Category/Algo pivots (compact tables).
  * **Quality Gate** section with explicit thresholds and final decision.
  * Pointers to the CSVs below.
* If `--format html|both`: render a simple HTML from the same content (Markdown → HTML); **no JS deps** required.
* `tables/` CSV extracts (workspace-relative paths only):

  * `jobs_selected.csv` — the exact rows (and subset of columns) used for the report.
  * `agg_by_algo.csv`, `agg_by_category_l1.csv`, `agg_by_manufacturer.csv`.
  * `gate_by_job.csv` — `(run_id, job_id, product_id, variant, algo, fscore, vfscore_overall, job_pass, reasons_if_fail)`.

If `--dry-run`, **do not write** files; still compute and print a short preview to console and write the log record with `"dry_run": true`.

### G) Logging (structured)

Append a JSON record to `logs/metrics.log` (Phase-0 utility) with at least:

```json
{
  "event": "report_run",
  "timestamp": "...",
  "run_id": "...",
  "selected": <int>,
  "completed": <int>,
  "failed": <int>,
  "skipped": <int>,
  "fscore_ok": <int>,
  "vfscore_ok": <int>,
  "gate_enabled": true,
  "gate_thresholds": { "fscore_min": <float>, "vfscore_min": <float|null> },
  "gate_pass_rate_completed": <float>,
  "gate_pass_rate_all": <float>,
  "export_dir": "reports/run_<run_id>/",
  "format": "md|html|both",
  "export_csv": true,
  "dry_run": false
}
```

Use the same append semantics as Phases 2/5/6.

### H) Paths, Encoding, Safety

* All paths written in CSVs must be **workspace-relative** and UTF-8-SIG encoded (Phase-0).
* Use Phase-0 atomic write utilities for files (no partials). 

---

## Acceptance Criteria (Definition of Done)

1. **CLI**
   `archi3d report run --run-id <id>` generates `reports/run_<id>/report.md` and `tables/*.csv` by default, honors filters, and supports `--format`, `--dry-run`, `--open`.

2. **Quality Gate**

   * Applies `fscore_min` (required), and optional `vfscore_min` if present in config.
   * Produces per-job pass/fail and run-level pass rates with explicit numbers and thresholds.

3. **Analytics**

   * Status histogram correct; FScore & VFScore aggregates computed; category/algo pivots present.
   * All calculations derive **exclusively** from `tables/generations.csv` (+ optional join to `items.csv`).

4. **Exports**

   * Markdown (and optional HTML) report written under `reports/run_<run_id>/`.
   * CSV extracts present and consistent with the report.
   * All CSVs use workspace-relative paths and UTF-8-SIG.

5. **Logging**

   * A structured `report_run` JSON line is appended to `logs/metrics.log` with accurate counters.

6. **Idempotency**

   * Re-running (same inputs) overwrites the same report files atomically; summaries remain consistent.

---

## Minimal Tests / Self-Tests

Create `tests/test_phase7_report.py` or `scripts/dev/phase7_selftest.py`:

**Test 1 — Happy path**

* Workspace with a run containing mixed `completed/failed` jobs and populated FScore & VFScore fields.
* Run: `archi3d report run --run-id <id>`
* Assert: report files exist; CSV extracts match selected rows; log line appended; gate computed.

**Test 2 — Dry run**

* Same inputs with `--dry-run`.
* Assert: no files under `reports/`; console shows preview; log `"dry_run": true`.

**Test 3 — Filters**

* Use `--include` / `--exclude` to restrict to a subset; verify counts & CSVs reflect filtering.

**Test 4 — Gate with missing visuals**

* Remove VFScore columns; ensure gate uses only `fscore_min` and still produces decision.

**Test 5 — Path & encoding**

* Open a produced CSV and verify **no absolute paths**; encoding is UTF-8-SIG.

---

## Deliverables

* **New**:

  * `src/archi3d/reporting/report_run.py`
* **Modified**:

  * `src/archi3d/cli.py` (register/wire `report run`)
* **Optional**:

  * `tests/test_phase7_report.py` or `scripts/dev/phase7_selftest.py`
* **CHANGELOG**:
  `feat(phase7): add per-run reporting with quality gate, Markdown/HTML exports, and structured logging; reuses SSOT and atomic I/O.`

---

## Implementation Notes

* Use `PathResolver.reports_root` and `metrics_log_path()` from Phase-0; create `reports/run_<run_id>/` with `mkdir(parents=True, exist_ok=True)`. 
* For Markdown: build with string templates; for HTML: a trivial Markdown→HTML conversion (no heavy deps).
* Ensure **no mutation** of `tables/generations.csv` here.
* Keep column names exactly as defined in Phases 2, 5, 6 (`fscore_*`, `vf*`, duration/cost fields).
* Summaries should be deterministic; sort groups by count desc or name asc for stability.

---

By completing **Phase 7** per this spec, the project delivers **auditable, portable reports** driven solely by the **Single Source of Truth** and applies a transparent **Quality Gate** aligned with configured thresholds—cleanly building on Phases 0–6 without altering their behavior.

---