## Role

You are the implementation agent for **Phase 10 — Finalization & Acceptance (End-to-End SSOT Delivery)**. Your goal is to ensure the project can **run end-to-end** on a fresh workspace and satisfy the **Definition of Done** across Phases **0–9**, producing all expected artifacts, logs, CSV upserts, metrics, and reports—**without changing prior semantics**.

> All behaviors, schemas, thresholds, and adapter contracts come from Phases 0–9. Respect them exactly. (See schema and command recaps below.)

---

## High-Level Objectives

1. **Ship an e2e “happy path”** from raw curated dataset → `catalog build` → `batch create` → `run worker` → `consolidate` → `compute fscore` → `compute vfscore` → `report run`, writing **only workspace-relative paths**, **UTF-8-SIG CSVs**, and **atomic/locked** updates.
2. Provide **verification tooling** (a one-shot script and minimal tests) that:

   * validates schemas (via Phase-9 models/validators),
   * checks invariants (keys uniqueness, statuses, path relativity, idempotency),
   * asserts artifacts exist where required,
   * generates a concise PASS/FAIL summary suitable for CI.
3. Finalize **packaging and docs** (quickstart & operator docs) so a new developer can create a minimal workspace and reproduce the happy path **without modifying code**.

---

## Hard Constraints (do not change)

* **No behavior changes** to prior phases; only fix defects that prevent meeting their acceptance criteria. Keep command flags, thresholds, columns, and adapter contracts **exactly** as defined.
* **SSOT = CSVs**: `tables/items.csv` (parents) and `tables/generations.csv` (children/jobs). Upserts via Phase-0 `update_csv_atomic(..., key_cols=[...])`. All paths stored must be **workspace-relative**; encoding **utf-8-sig**. 
* **Idempotency** & **atomicity**: reruns must not duplicate rows; writes are temp+rename under a file lock. 
* **Adapters & discovery**: FScore/VFScore use the import-first / CLI-fallback discovery and optional extras; same canonical payloads and column names. 

---

## Canonical Schemas (copy exactly)

### A) `tables/items.csv` (Phase-1; PK = `product_id, variant`) 

1. `product_id` (str)
2. `variant` (str, default `"default"`)
3. `manufacturer` (str)
4. `product_name` (str)
5. `category_l1` (str)
6. `category_l2` (str)
7. `category_l3` (str)
8. `description` (str)
9. `n_images` (int)
10. `image_1_path` … `image_6_path` (str; rel)
11. `gt_object_path` (str; rel)
12. `dataset_dir` (str; rel)
13. `build_time` (ISO8601)
14. `source_json_present` (`true|false`)

### B) `tables/generations.csv` (SSOT for jobs; PK = `run_id, job_id`)

#### B1) Phase-2 carry-overs + batch/job metadata (in this order) 

**Carry-over from parent:**

1. `product_id`, 2. `variant`, 3. `manufacturer`, 4. `product_name`, 5. `category_l1`, 6. `category_l2`, 7. `category_l3`, 8. `description`,
2. `source_n_images`, 10–15. `source_image_1_path` … `source_image_6_path`, 16. `gt_object_path`

**Batch/job:**
17. `run_id`, 18. `job_id`, 19. `algo`, 20. `algo_version` (empty at Phase-2),
21. `used_n_images`, 22–27. `used_image_1_path` … `used_image_6_path`,
28. `image_set_hash`, 29. `status` (`enqueued` initially), 30. `created_at` (ISO8601), 31. `notes`

#### B2) Phase-3 execution/upserts (append; names exactly) 

* `generation_start` (ISO8601), `generation_end` (ISO8601), `generation_duration_s` (float)
* `worker_host`, `worker_user`, `worker_gpu`, `worker_env`, `worker_commit`
* `gen_object_path` (rel), `preview_1_path`, `preview_2_path`, `preview_3_path`, `algo_version`
* `unit_price_usd` (float), `estimated_cost_usd` (float), `price_source` (str)
* `error_msg` (str; truncated)
* `status` transitions: `enqueued → running → completed|failed`

#### B3) Phase-5 FScore (append; exact names) 

* `fscore`, `precision`, `recall`, `chamfer_l2`, `fscore_n_points`
* `fscore_scale`, `fscore_rot_w`, `fscore_rot_x`, `fscore_rot_y`, `fscore_rot_z`
* `fscore_tx`, `fscore_ty`, `fscore_tz`
* `fscore_dist_mean`, `fscore_dist_median`, `fscore_dist_p95`, `fscore_dist_p99`, `fscore_dist_max`
* `fscore_runtime_s`, `fscore_tool_version`, `fscore_config_hash`
* `fscore_status` (`ok|error|skipped`), `fscore_error`

#### B4) Phase-6 VFScore (append; exact names) 

* **Core**: `vfscore_overall`, `vf_finish`, `vf_texture_identity`, `vf_texture_scale_placement`, `vf_repeats_n`, `vf_iqr`, `vf_std`
* **Provenance/perf**: `vf_llm_model`, `vf_rubric_json`, `vf_render_runtime_s`, `vf_scoring_runtime_s`, `vf_config_hash`, `vf_rationales_dir`
* **Status**: `vf_status` (`ok|error|skipped`), `vf_error`

> Phase-7 reporting reads only from `generations.csv` (optional join to `items.csv`) and must not mutate SSOT. 
> Phase-8 adapters/discovery must remain intact (no new flags in Phase-10). 
> Phase-9 models/validators codify these schemas and must pass. 

---

## Tasks to Implement (Phase 10)

### 1) End-to-End “Acceptance Runner”

Create `scripts/dev/phase10_e2e.py` that:

* Accepts `--workspace <path>` and `--run-id <str|auto>`, `--algos <csv>` (optional), `--dry-run` (global).
* Performs the pipeline in order (skip steps if `--dry-run`):

  1. `archi3d catalog build` (with auto-discovery of dataset/json when flags omitted, per Phase-1). 
  2. `archi3d batch create` (default image policy `use_up_to_6`). 
  3. `archi3d run worker` (default `--only-status enqueued`). 
  4. `archi3d consolidate`. 
  5. `archi3d compute fscore` (default selection). 
  6. `archi3d compute vfscore` (default repeats=3; used images). 
  7. `archi3d report run` (default md). 
* After each step, run **Phase-9 validators** on the relevant CSV(s); abort on **strict** failures and print an actionable error. Generate/refresh `docs/SCHEMA.md` once via Phase-9 script. 
* Print a compact final summary: counts by status, fscore/vfscore medians, report path, and PASS/FAIL.

### 2) Minimal Sample Workspace & Fixture Builder

Add `scripts/dev/make_sample_workspace.py` that creates a **tiny** workspace suitable for CI:

* `dataset/` with 1–2 items following the **folder naming**, **image selection (A..F then lexicographic, ≤6)**, and **GT selection** rules. 
* A minimal `products-with-3d.json` containing fields to populate manufacturer/name/description/categories (with IT preferred, EN fallback). 
* Option: a tiny canned `.glb` for `--dry-run` worker mode.

### 3) Final Validation & Invariants Gate

Implement a small module `src/archi3d/qa/acceptance.py` with functions used by the e2e script:

* **Schema checks** using Phase-9 registry/models (items & generations). Enforce:

  * key uniqueness: `(product_id,variant)` for items; `(run_id,job_id)` for generations.
  * path relativity on all stored paths (no absolute/drive letters).
  * image count invariants: `used_n_images` == non-empty `used_image_*` columns; `source_n_images` ditto.
  * status/metrics implications: if `fscore_status=="ok"` then core FScore numeric fields present; similarly for VFScore. 
* **Idempotency checks**:

  * Re-invoke `batch create` and ensure **no new** inserts for same inputs.
  * Re-invoke `compute fscore`/`compute vfscore` **without** `--redo` and confirm affected rows are unchanged (skipped).
* **Artifact checks**:

  * For `status=completed`, verify `runs/<run_id>/outputs/<job_id>/generated.glb` exists and `gen_object_path` points to it; previews if present. 
* **Reporting check**:

  * Ensure `reports/run_<run_id>/report.md` exists and tables CSVs were written; verify quality gate logic used `fscore_min` (and optional VF threshold if configured). 

### 4) Packaging & Docs polish

* Ensure `pip install .` (core) and `.[fscore]`, `.[vfscore]` extras work (no missing entry points). Provide a short **docs/INTEGRATION.md** pointer in the README. 
* Add a concise **docs/QUICKSTART.md**:

  * creating a workspace,
  * running Phase-10 e2e with/without `--dry-run`,
  * expected outputs and where to find them.
* Add **CHANGELOG** entry: `feat(phase10): e2e acceptance runner, sample workspace, final QA invariants; no behavior changes to prior phases.`

### 5) CI-friendly Self-Tests

* Add `scripts/dev/phase10_selftest.py` (or `tests/test_phase10_e2e.py`) that:

  * builds a sample workspace,
  * runs the e2e with `--dry-run` first, then a real run (with stubbed/fast adapters if needed),
  * asserts PASS across schema/paths/invariants and report generation.

---

## Acceptance Criteria (Definition of Done)

1. **E2E runs cleanly** on a fresh sample workspace:

   * All commands execute in order and produce expected CSV/log/report artifacts with **workspace-relative paths** and **utf-8-sig** encoding. 
2. **Schemas & validators pass** (strict) for both SSOT tables; **SCHEMA.md** regenerates. 
3. **Idempotency**:

   * Re-running `batch create` does not insert duplicates. 
   * Re-running metrics without `--redo` leaves existing `ok` rows unchanged; with `--redo` they update.
4. **Artifacts linked**:

   * Every `completed` job has a valid `gen_object_path` pointing to an existing `generated.glb`. 
5. **Report generated** with correct aggregations and quality gate decisions; CSV extracts present under `reports/run_<run_id>/tables/`. 
6. **Adapters discovery** works (import-first, CLI fallback), with friendly errors if missing (no flag changes). 
7. **No regressions**: prior commands’ flags/semantics unchanged; all writes are atomic & locked; SSOT remains the sole truth.

---

## Non-Goals (Phase 10)

* No new CLI flags or schema columns.
* No changes to selection rules, thresholds, or adapter payloads.
* No dataset downloader; rely on curated `dataset/` + `products-with-3d.json` (or run with `--dry-run`). 

---

## Implementation Hints

* Reuse **PathResolver** & atomic I/O (`update_csv_atomic`, `append_log_record`, `write_text_atomic`). 
* Use Phase-9 `schema_registry`, `models`, `validators` directly for checks (don’t re-spec types). 
* Keep console output succinct; the e2e script prints a final summary with locations of artifacts and a clear PASS/FAIL.

---

**Deliver this phase as a single PR** adding the e2e runner, sample-workspace builder, acceptance checks, and docs. Ensure a new developer can follow `docs/QUICKSTART.md` and complete the pipeline in minutes (with `--dry-run` if adapters aren’t installed).

---