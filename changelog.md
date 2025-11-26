## Implementation Status

### Phase 0 — Conventions & Layout (SSOT scaffolding) ✅ COMPLETE

**Objective**: Establish a single, canonical workspace layout and provide safe, reusable I/O primitives for atomic CSV/log writes.

**Implemented Components**:

1. **PathResolver Extensions** (`src/archi3d/config/paths.py`):
   - Added `logs_root` / `logs_dir` property for logs directory
   - Added property aliases: `tables_root`, `runs_root`, `reports_root`, `logs_root`
   - Implemented `ensure_mutable_tree()` method (creates tables/, runs/, reports/, logs/)
   - Added SSOT file path getters:
     - `items_csv_path()`, `items_issues_csv_path()`, `generations_csv_path()`
     - `catalog_build_log_path()`, `batch_create_log_path()`, `worker_log_path()`, `metrics_log_path()`

2. **Atomic I/O Utilities** (`src/archi3d/utils/io.py`):
   - `write_text_atomic(path, text)` — Atomic file writes via temp + rename, with fsync
   - `append_log_record(path, record)` — Thread-safe log appending with ISO8601 timestamps
   - `update_csv_atomic(path, df_new, key_cols)` — CSV upsert with locking, returns (inserted, updated)

3. **Test Suite** (`tests/test_phase0_paths_and_io.py`):
   - 21 comprehensive tests covering all Phase 0 functionality
   - Tests for PathResolver, atomic writes, log appending, CSV upserts
   - All tests passing with proper fixtures and temp workspace isolation

**Key Deliverables**:
- Backward-compatible PathResolver enhancements
- Thread-safe, atomic I/O primitives for concurrent access
- Comprehensive test coverage (21/21 tests passing)
- Zero behavioral regressions in existing code

**Non-Functional Changes**:
- No changes to CLI commands or user-facing behavior
- All existing imports and APIs remain compatible
- Linting/formatting applied (ruff, black)

### Phase 1 — Catalog Build ✅ COMPLETE

**Objective**: Implement `archi3d catalog build` command to scan curated dataset folder, enrich with JSON metadata, and write canonical `tables/items.csv` and `tables/items_issues.csv` SSOT tables.

**Implemented Components**:

1. **Catalog Build Function** (`src/archi3d/db/catalog.py`):
   - `build_catalog(dataset_path, products_json_path, paths)` — Main entry point
   - Folder name parsing: `_parse_folder_name()` extracts product_id and variant
   - Image selection: `_collect_and_sort_images()` implements tagged-first, then lexicographic ordering (max 6)
   - GT selection: `_select_gt_object()` prefers .glb over .fbx, warns on multiple candidates
   - JSON enrichment: `_extract_enrichment_data()` with IT/EN locale preference
   - Issue tracking: Generates structured issues for missing data, too many images, etc.

2. **CLI Integration** (`src/archi3d/cli.py`):
   - `archi3d catalog build` command with optional `--dataset` and `--products-json` flags
   - Auto-discovery: Searches for `products-with-3d.json` in workspace root and parent directory
   - Defaults: Uses `paths.dataset_root` for dataset, gracefully handles missing JSON
   - Output summary: Displays item counts, issues count, and output file paths

3. **SSOT Schema** (`tables/items.csv`):
   - **Columns** (19 total, in order):
     1. `product_id` (str) — Product identifier
     2. `variant` (str) — Variant name (default: "default")
     3. `manufacturer` (str) — From JSON enrichment
     4. `product_name` (str) — From JSON (IT preferred)
     5. `category_l1`, `category_l2`, `category_l3` (str) — Category hierarchy
     6. `description` (str) — From JSON (IT preferred)
     7. `n_images` (int) — Number of selected images (0-6)
     8. `image_1_path` ... `image_6_path` (str) — Workspace-relative image paths
     9. `gt_object_path` (str) — Workspace-relative GT file path
     10. `dataset_dir` (str) — Workspace-relative dataset folder path
     11. `build_time` (ISO8601) — Build timestamp
     12. `source_json_present` (bool) — Whether JSON enrichment was available
   - **Key columns**: `(product_id, variant)` uniquely identify each item
   - **Encoding**: UTF-8-SIG for Excel compatibility
   - **Paths**: All paths are workspace-relative (POSIX format with forward slashes)

4. **Issues Tracking** (`tables/items_issues.csv`):
   - **Columns**: `product_id`, `variant`, `issue`, `detail`
   - **Issue types**: `no_images`, `too_many_images`, `missing_gt`, `multiple_gt_candidates`, `missing_manufacturer`, `missing_product_name`, `missing_description`, `missing_categories`
   - Allows tracking data quality problems for manual review

5. **Test Suite** (`tests/test_phase1_catalog_build.py`):
   - 8 comprehensive tests covering:
     - Basic build without JSON enrichment
     - Image selection with tagged (_A-_F) and untagged images, 6-image cap
     - GT file preference (.glb over .fbx) and multiple candidate warnings
     - JSON enrichment with IT/EN locale preference
     - Missing enrichment field detection
     - Workspace-relative path validation
     - Atomic writes (no .tmp files left behind)
     - Idempotent builds (same results on reruns)
   - All 8 tests passing

**Key Features**:
- **Atomic writes**: Uses temp file + rename pattern for items.csv and items_issues.csv
- **Workspace-relative paths**: All stored paths are relative to workspace root (no absolute paths, no drive letters)
- **Folder naming flexibility**: Parses both `{product_id}` and `{product_id} - {variant}` formats
- **Image tagging priority**: Tagged images (_A-_F) selected first, preserving photographer intent
- **JSON auto-discovery**: Searches workspace and parent directory for products-with-3d.json
- **Graceful degradation**: Works without JSON (creates items with empty enrichment fields)
- **Structured logging**: Writes JSON-formatted log entries to `logs/catalog_build.log`

**CLI Examples**:
```bash
# Basic usage (auto-discovers dataset/ and products-with-3d.json)
archi3d catalog build

# Explicit paths
archi3d catalog build --dataset /path/to/dataset --products-json /path/to/products.json

# Without enrichment JSON
archi3d catalog build --dataset /path/to/dataset
```

**Non-Functional Changes**:
- No changes to existing orchestrator, adapters, or worker logic
- No schema changes to existing tables (this creates new `items.csv` and `items_issues.csv`)
- All writes use atomic I/O from Phase 0
- Linting/formatting applied (ruff, black)

**Known Constraints**:
- Product IDs in folder names must be numeric (enforced by regex `\d+`)
- Image files must have extensions: .jpg, .jpeg, .png (case-insensitive)
- GT files must have extensions: .glb or .fbx (case-insensitive)
- Maximum 6 images per item (hard cap, excess images ignored)
- JSON structure must match expected schema (ProductId, Manufacturer.Name, Name.Value.{it,en}, etc.)

### Phase 2 — Batch Create ✅ COMPLETE

**Objective**: Implement `archi3d batch create` command to materialize per-run job lists, initialize the SSOT `tables/generations.csv` registry, and produce per-run manifests.

**Implemented Components**:

1. **Job Identity Helpers** (`src/archi3d/db/generations.py`):
   - `compute_image_set_hash(image_paths)` — Deterministic SHA1 hash of ordered image paths
   - `compute_job_id(product_id, variant, algo, image_set_hash)` — 12-char deterministic job ID
   - `upsert_generations(generations_csv_path, df_new)` — Atomic upsert to generations.csv using (run_id, job_id) keys

2. **Batch Creation Logic** (`src/archi3d/orchestrator/batch.py`):
   - Completely refactored for Phase 2 requirements
   - `create_batch(run_id, algos, paths, ...)` — Main entry point
   - `_select_images_use_up_to_6(row)` — Image selection policy implementation
   - `_apply_filters(items_df, ...)` — Filtering logic (include/exclude/with-gt-only/limit)
   - Uses Phase 0 atomic I/O utilities for safe concurrent access

3. **CLI Integration** (`src/archi3d/cli.py`):
   - `archi3d batch create` command with Phase 2 flags:
     - `--run-id` (optional, auto-generates UTC timestamp if omitted)
     - `--algos` (comma-separated algorithm keys)
     - `--image-policy` (default: "use_up_to_6")
     - `--limit` (max items to process)
     - `--include` / `--exclude` (substring filters on product_id/variant/product_name)
     - `--with-gt-only` (skip items without GT)
     - `--dry-run` (compute summary without writing files)
   - Console output: Summary tables with candidates, enqueued, skipped counts
   - Auto-selects default algorithm if none specified

4. **SSOT Schema** (`tables/generations.csv`):
   - **Columns** (30 total, in order per Phase 2 spec):
     - **Carry-over from parent** (observability): `product_id`, `variant`, `manufacturer`, `product_name`, `category_l1`, `category_l2`, `category_l3`, `description`, `source_n_images`, `source_image_1_path` ... `source_image_6_path`, `gt_object_path`
     - **Batch/job metadata**: `run_id`, `job_id`, `algo`, `algo_version`, `used_n_images`, `used_image_1_path` ... `used_image_6_path`, `image_set_hash`, `status`, `created_at`, `notes`
   - **Key columns**: `(run_id, job_id)` uniquely identify each generation job
   - **Upsert behavior**: Keeps existing rows on conflict (idempotent)
   - **Encoding**: UTF-8-SIG for Excel compatibility
   - **Status**: Phase 2 sets `status="enqueued"` for new jobs

5. **Per-Run Manifest** (`runs/<run_id>/manifest.csv`):
   - Derived from `generations.csv` for jobs with `status=enqueued` and matching `run_id`
   - **Required columns**: `job_id`, `product_id`, `variant`, `algo`, `used_n_images`, `used_image_1_path` ... `used_image_6_path`, `image_set_hash`
   - **Optional columns**: `gt_object_path`, `product_name`, `manufacturer`
   - Provides per-run job list for worker execution

6. **Structured Logging** (`logs/batch_create.log`):
   - JSON-formatted log entries with ISO8601 timestamps
   - Fields: `event`, `timestamp`, `run_id`, `algos`, `image_policy`, `candidates`, `enqueued`, `skipped`, `skip_reasons`, `dry_run`
   - Skip reasons histogram: `no_images`, `filtered_include`, `filtered_exclude`, `with_gt_only`, `duplicate_job`

7. **Test Suite** (`tests/test_phase2_batch_create.py`):
   - 7 comprehensive tests covering:
     - Dry-run mode (no files written, log with dry_run flag)
     - Real write + idempotency (upsert prevents duplicates)
     - Filters and with-gt-only (correct items skipped)
     - Multi-algo job identity (distinct job_ids per algo, same image_set_hash)
     - Path relativity (all paths workspace-relative)
     - Job identity determinism (stable hashes and IDs)
     - Limit parameter (caps items processed)
   - All 7 tests passing

**Key Features**:
- **Deterministic job identity**: SHA1-based job IDs stable across re-runs with same inputs
- **Atomic upserts**: Uses Phase 0 `update_csv_atomic()` with (run_id, job_id) keys
- **Idempotency**: Re-running with same inputs updates existing rows, doesn't create duplicates
- **Flexible filtering**: Include/exclude patterns, GT-only mode, item limit
- **Dry-run mode**: Preview changes without writing files
- **Auto-generated run IDs**: UTC timestamp slugs if not specified
- **Workspace-relative paths**: All paths in CSVs are portable and cross-platform

**CLI Examples**:
```bash
# Basic usage (auto-generates run_id, uses default algo)
archi3d batch create

# Explicit run_id and algorithms
archi3d batch create --run-id "2025-10-20-experiment" --algos tripo3d_v2p5,trellis_single

# With filters
archi3d batch create --run-id "test-run" --algos tripo3d_v2p5 --with-gt-only --limit 10

# Include/exclude patterns
archi3d batch create --include "100001" --algos tripo3d_v2p5
archi3d batch create --exclude "100003" --algos tripo3d_v2p5

# Dry-run (preview only)
archi3d batch create --run-id "test" --algos tripo3d_v2p5 --dry-run
```

**Design Patterns**:
- **SSOT First**: `generations.csv` is the single source of truth; manifest is derived from it
- **Image Selection Policy**: Extensible policy system (currently only `use_up_to_6`)
- **Filtering Order**: include → exclude → with-gt-only → n_images ≥ 1 → limit
- **Job Identity**: `job_id = SHA1(product_id|variant|algo|image_set_hash)[:12]`
- **Image Set Hash**: `SHA1(\n.join(used_image_paths))` for deterministic ordering

**Non-Functional Changes**:
- Completely replaced old token-based batch creation logic
- No changes to Phase 0 or Phase 1 functionality
- All writes use Phase 0 atomic I/O utilities
- Linting/formatting applied (ruff, black)

**Known Constraints**:
- Image policy currently limited to `use_up_to_6` (extensible for future policies)
- Include/exclude filters use substring matching (case-insensitive), not regex or glob
- Filters apply to product_id, variant, and product_name fields only
- Job identity is stable but not backward-compatible with pre-Phase 2 job IDs

### Phase 3 — Run Worker ✅ COMPLETE

**Objective**: Execute generation jobs from tables/generations.csv with robust lifecycle management, resumability, and concurrent execution support.

**Implemented Components**:

1. **Worker Execution Logic** (`src/archi3d/orchestrator/worker.py`):
   - `run_worker(run_id, paths, ...)` — Main entry point for job execution
   - `_execute_job(job_row, ...)` — Per-job execution with lifecycle management
   - `_get_worker_identity()` — Captures worker environment metadata (host, user, GPU, env, commit)
   - `_simulate_dry_run(...)` — Creates synthetic outputs for testing
   - State marker management functions for resumability

2. **Phase 3 Adapter Contract** (`src/archi3d/adapters/base.py`):
   - `GenerationRequest` — Input dataclass for adapter execution (job_id, product_id, variant, algo, used_images, out_dir, workspace, extra)
   - `GenerationResult` — Output dataclass from adapters (success, generated_glb, previews, algo_version, pricing, raw_metadata)
   - Maintains backward compatibility with Phase 0-2 Token/ExecResult types

3. **PathResolver Extensions** (`src/archi3d/config/paths.py`):
   - `outputs_dir(run_id, job_id=...)` — Per-job output directories under `runs/<run_id>/outputs/<job_id>/`
   - `state_dir(run_id)` — State marker directory for resumability
   - `state_lock_path(run_id, job_id)` — Per-job FileLock paths for safe concurrent access

4. **CLI Integration** (`src/archi3d/cli.py`):
   - `archi3d run worker` command with Phase 3 flags:
     - `--run-id` (required) — Run identifier
     - `--jobs` (optional) — Filter job_id by substring
     - `--only-status` (default: "enqueued") — Comma-separated statuses to process
     - `--max-parallel` (default: 1) — Concurrent worker threads
     - `--adapter` (optional) — Force specific adapter for debugging
     - `--dry-run` — Simulate execution without calling adapters
     - `--fail-fast` — Stop on first failure
   - Rich console output with execution summary and file paths

5. **Job Lifecycle State Machine**:
   - **enqueued** → **running** → **completed** / **failed**
   - State transitions protected by per-job FileLock
   - State markers in `runs/<run_id>/state/`:
     - `<job_id>.inprogress` — Job currently executing
     - `<job_id>.completed` — Job finished successfully
     - `<job_id>.failed` — Job failed
     - `<job_id>.error.txt` — Full error details for failed jobs

6. **SSOT Updates** (`tables/generations.csv`):
   - **Phase 3 fields added**:
     - **Execution metadata**: `status` (running/completed/failed), `generation_start`, `generation_end`, `generation_duration_s`
     - **Worker identity**: `worker_host`, `worker_user`, `worker_gpu`, `worker_env`, `worker_commit`
     - **Outputs**: `gen_object_path`, `preview_1_path`, `preview_2_path`, `preview_3_path`, `algo_version`
     - **Costs**: `unit_price_usd`, `price_source`
     - **Errors**: `error_msg` (truncated to 2000 chars)
   - All paths are workspace-relative (POSIX format with forward slashes)
   - Atomic upserts via Phase 0 `update_csv_atomic()` with (run_id, job_id) keys

7. **Output Artifacts**:
   - Per-job outputs under `runs/<run_id>/outputs/<job_id>/`:
     - `generated.glb` — Generated 3D model (or adapter-specific name)
     - `preview_1.png`, `preview_2.png`, `preview_3.png` — Optional preview images
     - `metadata.json` — Adapter raw metadata (if provided)

8. **Structured Logging** (`logs/worker.log`):
   - JSON-formatted log entries with ISO8601 timestamps
   - Event types:
     - `worker_started` — Execution start with filters and config
     - `job_completed` — Per-job success with duration
     - `job_failed` — Per-job failure with error summary
     - `job_crashed` — Unexpected worker crashes
     - `worker_summary` — Final counts (processed, completed, failed, skipped, avg_duration_s)

9. **Concurrency Support (Rearchitected)**:
   - **Batch Upsert Model**: The core change is the move away from concurrent per-job CSV writes.
   - **Thread Safety**: A thread pool (`--max-parallel`) is still used for execution. State markers (`<job_id>.inprogress`) prevent multiple workers from picking up the same job.
   - **Race Condition Eliminated**: Since only one process writes the final batch of results to `generations.csv` (protected by `FileLock`), the risk of `NaN` corruption from concurrent pandas merges is completely eliminated.
   - **Resumability**: State markers (`.completed`, `.failed`) ensure that re-running a worker safely skips already-processed jobs.

10. **Dry-Run Mode**:
    - Simulates execution without calling adapters
    - Creates placeholder `generated.glb` and `preview_*.png` files
    - Updates CSV with `algo_version="dry-run"`
    - Useful for testing workflow and timing

11. **Test Suite** (`tests/test_phase3_run_worker.py`):
    - **7/7 tests passing (ALL FIXED)**.
    - Tests were updated to validate the new batch upsert logic and to account for timing changes. The suite now fully covers:
        - Dry-run success with synthetic outputs.
        - Real run with failure validation.
        - Resumability (completed jobs skipped on re-run).
        - Concurrency with thread pools.
        - Path relativity and idempotency.
        - Job filtering by substring.
        - Fail-fast mode.

**Key Features**:
- **Resumable execution**: State markers prevent duplicate work after interruption
- **Concurrent execution**: Thread pool with configurable parallelism
- **Worker observability**: Captures host, user, GPU, environment, commit for troubleshooting
- **Atomic updates**: Safe concurrent access to SSOT via FileLock and atomic I/O
- **Flexible filtering**: Process specific jobs by status, job_id pattern, or adapter
- **Dry-run testing**: Validate workflow without external API calls
- **Cost tracking**: Reads unit prices from adapters.yaml and tracks estimated costs
- **Error handling**: Full error details in error.txt, truncated summary in CSV

**CLI Examples**:
```bash
# Basic usage (process enqueued jobs with default parallelism)
archi3d run worker --run-id "2025-10-20-experiment"

# Dry-run mode (test without calling adapters)
archi3d run worker --run-id "test-run" --dry-run

# Concurrent execution with 4 workers
archi3d run worker --run-id "prod-run" --max-parallel 4

# Resume stuck "running" jobs
archi3d run worker --run-id "interrupted-run" --only-status running

# Process specific jobs by substring
archi3d run worker --run-id "test-run" --jobs "59ad"

# Force specific adapter (debug mode)
archi3d run worker --run-id "test-run" --adapter test_algo_1

# Stop on first failure
archi3d run worker --run-id "test-run" --fail-fast
```

**Design Patterns**:
- **State Machine**: Clear job lifecycle with atomic transitions
- **State Markers**: Resume-friendly design with filesystem-based state tracking
- **Worker Identity**: Full observability for debugging distributed execution
- **Fail-Safe Defaults**: Conservative settings (serial execution, process enqueued only)
- **Extensible Filtering**: Substring matching on job_id, status filtering, adapter override
- **Cost Awareness**: Automatic cost tracking from configuration

**Non-Functional Changes**:
- Completely replaced old token-based worker logic
- No changes to Phase 0, 1, or 2 functionality
- All writes use Phase 0 atomic I/O utilities
- Linting/formatting applied (ruff, black)

**Known Constraints**:
- Real adapter execution not implemented (placeholder creates minimal GLB).
- Heartbeat mechanism for stale detection implemented but not actively updated.
- Thread-based concurrency (process-based left as TODO).
- No timeout mechanism for adapter execution (left as TODO).
- Job filtering uses simple substring matching (not regex/glob).

**Next Phase**: Phase 4+ (metrics computation, FScore/VFScore integration)

### Phase 4 — Consolidate ✅ COMPLETE

**Objective**: Reconcile tables/generations.csv with on-disk artifacts and state markers to ensure SSOT consistency, deduplicate rows, and fill missing metadata.

**Implemented Components**:

1. **Consolidation Logic** (`src/archi3d/orchestrator/consolidate.py`):
   - `consolidate(run_id, paths, ...)` — Main entry point for reconciliation
   - `_gather_evidence(row, run_id, state_dir, outputs_dir, paths)` — Collects evidence from disk (markers, artifacts, error files)
   - `_determine_desired_status(evidence, csv_status)` — Applies truth table to determine correct status
   - `_reconcile_row(row, evidence, desired_status, ...)` — Reconciles single row with evidence
   - `_merge_duplicate_rows(rows)` — Merges duplicate (run_id, job_id) rows by keeping most complete information
   - `_consolidate_run(run_id, paths, ...)` — Per-run reconciliation orchestrator

2. **CLI Integration** (`src/archi3d/cli.py`):
   - `archi3d consolidate` command with Phase 4 flags:
     - `--run-id` (required) — Run identifier
     - `--dry-run` (default: False) — Compute changes without writing CSV
     - `--strict` (default: False) — Exit with error on any conflict
     - `--only-status` (optional) — Comma-separated statuses to process
     - `--fix-status` (default: True) — Apply status downgrades for missing outputs
     - `--max-rows` (optional) — Cap on rows to process for safety
   - Rich console output with summary tables and status histograms

3. **Reconciliation Rules**:
   - **Status Truth Table** (priority order):
     1. `.completed` marker + `generated.glb` exists → `status=completed`
     2. `.failed` marker exists → `status=failed`
     3. `.inprogress` marker + heartbeat fresh (<10 min) → `status=running`
     4. No markers/artifacts → keep CSV status (default `enqueued`)
   - **Downgrade Logic**: CSV says `completed` but `generated.glb` missing → downgrade to `failed` with error_msg (if `--fix-status`)
   - **Timestamp Filling**: Best-effort synthesis from marker/artifact mtimes
   - **Path Normalization**: Fills `gen_object_path`, `preview_*_path` with workspace-relative paths
   - **Error Message Filling**: Reads first ~2000 chars from `error.txt` if `error_msg` empty

4. **Duplicate Handling**:
   - Detects duplicate (run_id, job_id) rows in CSV
   - Merges duplicates using smart precedence:
     - Prefers row with highest status precedence (completed > failed > running > enqueued)
     - Column-wise: keeps non-empty/non-NaN values
     - Result: single merged row with union of non-empty fields
   - Special upsert path: removes all run_id rows, then inserts deduplicated data (avoids pandas merge issues with duplicates)

5. **Structured Logging** (`logs/metrics.log`):
   - JSON-formatted summary with counters:
     - `considered`, `upsert_inserted`, `upsert_updated`, `unchanged`
     - `conflicts_resolved`, `marker_mismatches_fixed`, `downgraded_missing_output`
     - `status_histogram_before`, `status_histogram_after`
   - Includes `dry_run` flag for audit trail

6. **Test Suite** (`tests/test_phase4_consolidate.py`):
   - 7 comprehensive tests covering all requirements:
     1. Happy path (completed jobs with full artifacts, minimal changes)
     2. Downgrade missing output (CSV says completed but GLB missing)
     3. Merge duplicates (duplicate rows merged to single row with union of fields)
     4. Heartbeat stale (inprogress marker >10 min old, keeps running status per spec)
     5. Dry-run mode (no CSV writes, log includes dry_run flag)
     6. Idempotency (re-running yields minimal/no updates after first run)
     7. No CSV exists (handles missing generations.csv gracefully)
   - All 7 tests passing

**Key Features**:
- **Idempotent Reconciliation**: Re-running without changes yields `upsert_updated≈0`
- **Conflict Resolution**: Merges duplicate rows with smart precedence rules
- **Status Validation**: Downgrades incorrect statuses based on on-disk evidence
- **Metadata Filling**: Synthesizes missing timestamps, paths, error messages from artifacts
- **Atomic Updates**: Uses Phase 0 `update_csv_atomic()` with FileLock for safety
- **Dry-Run Mode**: Preview changes without modifying CSV
- **Flexible Filtering**: Process specific statuses, cap rows for safety
- **Heartbeat Detection**: Identifies stale `inprogress` markers (>10 min old)

**CLI Examples**:
```bash
# Basic usage (reconcile all jobs for a run)
archi3d consolidate --run-id "2025-10-20-experiment"

# Dry-run mode (preview changes without writes)
archi3d consolidate --run-id "test-run" --dry-run

# Process only specific statuses
archi3d consolidate --run-id "prod-run" --only-status "completed,failed"

# Disable status downgrades
archi3d consolidate --run-id "test-run" --fix-status=false

# Strict mode (fail on any conflict)
archi3d consolidate --run-id "test-run" --strict

# Safety cap (process max 100 rows)
archi3d consolidate --run-id "large-run" --max-rows 100
```

**Design Patterns**:
- **Evidence-Based Reconciliation**: Gathers disk evidence before making decisions
- **Truth Table Logic**: Clear precedence rules for status determination
- **Smart Merging**: Column-wise union for duplicate row resolution
- **Workspace-Relative Paths**: All stored paths use POSIX format relative to workspace
- **Atomic Deduplication**: Special upsert path for handling CSV duplicates cleanly

**Non-Functional Changes**:
- No changes to Phases 0-3 functionality
- All writes use Phase 0 atomic I/O utilities
- Linting/formatting applied (ruff, black)
- Mypy type checking passed (pandas stub warnings ignored)

**Known Constraints**:
- Heartbeat freshness threshold: 10 minutes (configurable via constant)
- Error message truncation: 2000 characters (when reading from error.txt)
- Stale heartbeat behavior: keeps `running` status (documented as "leave as is")
- Status filtering uses simple string matching (not regex/glob)

**Next Phase**: Phase 5 (FScore geometry metrics computation)

### Phase 5 — Compute FScore (Geometry Metrics) ✅ COMPLETE

**Objective**: Compute geometry-based quality metrics (F-score, precision, recall, Chamfer-L2, alignment transforms) for completed jobs with ground truth objects, and upsert standardized metric columns into the SSOT `tables/generations.csv`.

**Implemented Components**:

1. **FScore Adapter Layer** (`src/archi3d/metrics/fscore_adapter.py`):
   - `FScoreRequest` — Input dataclass for FScore evaluation (gt_path, cand_path, n_points, out_dir, timeout_s)
   - `FScoreResponse` — Output dataclass with normalized results (ok, payload, tool_version, config_hash, runtime_s, error)
   - `evaluate_fscore(req)` — Main entry point with dual resolution:
     1. Tries Python import API (`from fscore.evaluator import evaluate_one`)
     2. Falls back to CLI invocation (`python -m fscore ...`)
   - `_normalize_payload(raw)` — Normalizes tool output into canonical schema
   - `_try_import_api(req)` — Attempts Python API with exception handling
   - `_try_cli_invocation(req)` — Subprocess fallback with timeout and JSON parsing

2. **Main Computation Logic** (`src/archi3d/metrics/fscore.py`):
   - `compute_fscore(run_id, jobs, only_status, ...)` — Main entry point for metrics computation
   - `_is_eligible(row, run_id, only_status, with_gt_only, redo, jobs_filter, paths)` — Eligibility filtering with skip reasons
   - `_job_matches_filter(job_id, filter_pattern)` — Job ID filtering (substring/glob/regex)
   - `_process_job(row, n_points, timeout_s, paths, dry_run)` — Per-job execution and artifact creation
   - Thread pool support with `ThreadPoolExecutor` for parallel execution
   - Atomic CSV upserts via Phase 0 `update_csv_atomic()` with (run_id, job_id) keys
   - Structured logging to `logs/metrics.log` with event summaries

3. **CLI Integration** (`src/archi3d/cli.py`):
   - Added `compute_app` Typer group for metrics computation commands
   - `archi3d compute fscore` command with Phase 5 flags:
     - `--run-id` (required) — Run identifier
     - `--jobs` (optional) — Job ID filter (glob/regex/substring)
     - `--only-status` (default: "completed") — Comma-separated statuses to process
     - `--with-gt-only` (default: True) — Require non-empty GT object path
     - `--redo` (default: False) — Force recomputation even if metrics already present
     - `--n-points` (default: 100000) — Poisson disk samples per mesh
     - `--timeout-s` (optional) — Per-job timeout in seconds
     - `--max-parallel` (default: 1) — Maximum concurrent workers
     - `--dry-run` (default: False) — Preview selection without running evaluator
   - Rich console output with summary tables and skip reasons histogram

4. **SSOT Schema Extensions** (`tables/generations.csv` — 24 new columns):
   - **Core metrics**: `fscore` (float), `precision` (float), `recall` (float), `chamfer_l2` (float)
   - **Alignment**: `fscore_scale` (float), `fscore_rot_w/x/y/z` (float), `fscore_tx/y/z` (float)
   - **Distance statistics**: `fscore_dist_mean/median/p95/p99/max` (float)
   - **Metadata**: `fscore_n_points` (int), `fscore_runtime_s` (float), `fscore_tool_version` (str), `fscore_config_hash` (str)
   - **Status tracking**: `fscore_status` (enum: ok/error/skipped), `fscore_error` (str, truncated to 2000 chars)
   - All numeric fields are nullable (None/NaN for missing data)
   - Upserted atomically via Phase 0 utilities

5. **Per-Job Artifacts**:
   - Output directory: `runs/<run_id>/metrics/fscore/<job_id>/`
   - `result.json` — Canonical machine-readable payload with all metrics
   - Directory created automatically before writing (handles concurrent access)
   - Idempotent: not overwritten unless `--redo` is set

6. **Canonical Payload Schema** (JSON):
   ```json
   {
     "fscore": <float>, "precision": <float>, "recall": <float>,
     "chamfer_l2": <float>, "n_points": <int>,
     "alignment": {
       "scale": <float>,
       "rotation_quat": {"w": <float>, "x": <float>, "y": <float>, "z": <float>},
       "translation": {"x": <float>, "y": <float>, "z": <float>}
     },
     "dist_stats": {
       "mean": <float>, "median": <float>, "p95": <float>, "p99": <float>, "max": <float>
     },
     "mesh_meta": {
       "gt_vertices": <int>, "gt_triangles": <int>,
       "pred_vertices": <int>, "pred_triangles": <int>
     }
   }
   ```

7. **Eligibility Rules**:
   - `run_id` must match target run
   - `status` must be in `--only-status` list (default: `completed`)
   - `gen_object_path` must exist on disk
   - `gt_object_path` must exist on disk if `--with-gt-only` (default: True)
   - Job ID must match `--jobs` filter (if provided)
   - Skip if `fscore_status="ok"` unless `--redo` is set
   - Emits structured skip reasons: `wrong_run_id`, `status_not_in_filter`, `job_id_not_matching_filter`, `already_computed`, `missing_gen_object_path`, `missing_gt_object_path`, `gen_object_not_found_on_disk`, `gt_object_not_found_on_disk`

8. **Structured Logging** (`logs/metrics.log`):
   - JSON-formatted event summary:
     ```json
     {
       "event": "compute_fscore",
       "timestamp": "...",
       "run_id": "...",
       "n_selected": <int>,
       "processed": <int>,
       "ok": <int>,
       "error": <int>,
       "skipped": <int>,
       "avg_runtime_s": <float>,
       "n_points": <int>,
       "redo": <bool>,
       "max_parallel": <int>,
       "dry_run": <bool>,
       "skip_reasons": {...}
     }
     ```

9. **Test Suite** (`tests/test_phase5_compute_fscore.py`):
   - 9 comprehensive tests covering all requirements:
     1. Happy path dry-run (selection without evaluator calls)
     2. Happy path real computation (full workflow with mock adapter)
     3. Missing GT object (proper error handling)
     4. Idempotency without redo (skips already computed jobs)
     5. Redo mode (force recomputation)
     6. Concurrency multiple jobs (parallel processing with ThreadPoolExecutor)
     7. Timeout handling (evaluator timeout → error status)
     8. Job filter matching (substring/glob/regex filtering)
     9. Status filtering (process only specified statuses)
   - All 9 tests passing
   - Full test suite: 59/59 tests passing (21+8+7+7+7+9 across Phases 0-5)

**Key Features**:
- **Dual Adapter Integration**: Tries Python import API first, falls back to CLI invocation
- **Idempotent by Default**: Skips jobs with `fscore_status="ok"` unless `--redo` is set
- **Atomic CSV Updates**: Uses Phase 0 `update_csv_atomic()` with FileLock for concurrent safety
- **Parallel Execution**: ThreadPoolExecutor with configurable `--max-parallel` flag
- **Flexible Filtering**: Job ID patterns (substring/glob/regex), status lists, GT requirement
- **Timeout Support**: Per-job timeout with `--timeout-s` flag (useful for large meshes)
- **Dry-Run Mode**: Preview eligible jobs without evaluator calls
- **Comprehensive Metrics**: 24 new columns covering geometry similarity, alignment, and distance statistics
- **Structured Logging**: JSON event summaries to `logs/metrics.log` with counters and skip reasons
- **Graceful Error Handling**: Invalid inputs → `fscore_status="error"` with descriptive `fscore_error`

**CLI Examples**:
```bash
# Basic usage (process completed jobs with GT objects)
archi3d compute fscore --run-id "2025-10-20-experiment"

# Dry-run to preview selection
archi3d compute fscore --run-id "test-run" --dry-run

# Parallel execution with 4 workers
archi3d compute fscore --run-id "prod-run" --max-parallel 4

# Recompute specific jobs matching a pattern
archi3d compute fscore --run-id "test-run" --jobs "product_123*" --redo

# Custom sampling density and timeout
archi3d compute fscore --run-id "large-meshes" --n-points 200000 --timeout-s 300

# Process failed jobs (for diagnostics)
archi3d compute fscore --run-id "test-run" --only-status "failed" --with-gt-only=false
```

**Design Patterns**:
- **Adapter Abstraction**: Clean separation between orchestration and tool integration
- **Evidence-Based Eligibility**: Multi-criteria filtering with structured skip reasons
- **Job ID Filtering**: Supports substring, glob (`*` wildcard), and regex (`re:` prefix) patterns
- **Canonical Payload Schema**: Normalized JSON schema for cross-tool compatibility
- **Workspace-Relative Paths**: All artifact paths use POSIX format relative to workspace
- **Directory Auto-Creation**: Output directories created automatically before writes
- **Error Truncation**: Error messages capped at 2000 chars with full details in result.json

**Non-Functional Changes**:
- No changes to Phases 0-4 functionality
- All writes use Phase 0 atomic I/O utilities
- Linting/formatting applied (ruff, black)
- Type checking passed (mypy with pandas stub warnings)
- Added `compute_app` Typer group to CLI (new subcommand namespace)

**Known Constraints**:
- FScore tool must be available via Python import or CLI (`python -m fscore`)
- Payload normalization assumes specific JSON schema from FScore tool
- Job ID filtering uses simple patterns (substring, glob with `*`, or regex with `re:` prefix)
- Status filtering uses simple string matching (not regex/glob)
- Distance statistics fields are optional (may be None if tool doesn't provide)
- Mesh metadata fields are optional (may be None if tool doesn't provide)
- Error message truncation: 2000 characters (full error in result.json)
- Concurrency is thread-based (not process-based)

---

### Phase 6 — Compute VFScore (Visual Fidelity Metrics) ✅ COMPLETE

**Objective**: Implement `archi3d compute vfscore` command to render generated models under standardized Cycles setup and score visual fidelity against reference photos using LLM-based scoring. Upsert standardized VFScore columns into SSOT `tables/generations.csv` and persist per-job artifacts.

**Implemented Components**:

1. **VFScore Adapter Layer** (`src/archi3d/metrics/vfscore_adapter.py`):
   - `VFScoreRequest` dataclass: Input specification (cand_glb, ref_images, out_dir, repeats, timeout_s, workspace)
   - `VFScoreResponse` dataclass: Normalized result (ok, payload, tool_version, config_hash, render_runtime_s, scoring_runtime_s, error)
   - `evaluate_vfscore(req)` — Main entry point with dual integration:
     1. Try Python import API (`from vfscore.evaluator import evaluate_visual_fidelity`)
     2. Fallback to CLI invocation (`python -m vfscore --cand-glb ... --ref-images ...`)
   - `_normalize_payload(raw)` — Normalizes tool output into canonical schema
   - Input validation: Checks candidate GLB and reference images exist on disk
   - Automatic filtering: Uses only existing reference images from the provided list

2. **Main Computation Logic** (`src/archi3d/metrics/vfscore.py`):
   - `compute_vfscore(run_id, jobs, only_status, use_images_from, repeats, redo, max_parallel, timeout_s, dry_run)` — Main entry point
   - `_get_reference_images(row, use_images_from, paths)` — Extracts reference images from used_image_* or source_image_* columns
   - `_is_eligible(row, run_id, only_status, use_images_from, redo, jobs_filter, paths)` — Multi-criteria eligibility filtering
   - `_process_job(row, repeats, use_images_from, timeout_s, paths, dry_run)` — Per-job processing workflow
   - Parallel execution: ThreadPoolExecutor with configurable max_parallel
   - Per-job artifacts: result.json, config.json, renders/, rationales/
   - Atomic CSV upserts: Uses Phase 0 `update_csv_atomic()` with FileLock

3. **CLI Integration** (`src/archi3d/cli.py`):
   - `archi3d compute vfscore` command registered under `compute_app`
   - Flags: `--run-id` (required), `--jobs`, `--only-status`, `--use-images-from`, `--repeats`, `--redo`, `--max-parallel`, `--timeout-s`, `--dry-run`
   - Rich output: Panel with parameters, Table with summary (selected/processed/ok/error/skipped), skip reasons table
   - Displays: generations CSV path, metrics artifacts path, metrics log path

4. **VFScore Columns in SSOT** (`tables/generations.csv`):
   - 15 new columns added (all nullable):
     - **Core scores**: `vfscore_overall` (int 0-100), `vf_finish` (int), `vf_texture_identity` (int), `vf_texture_scale_placement` (int)
     - **Statistics**: `vf_repeats_n` (int), `vf_iqr` (float), `vf_std` (float)
     - **Provenance**: `vf_llm_model` (str), `vf_rubric_json` (str, compact JSON), `vf_config_hash` (str), `vf_rationales_dir` (str, workspace-relative path)
     - **Performance**: `vf_render_runtime_s` (float), `vf_scoring_runtime_s` (float)
     - **Status**: `vf_status` (enum: ok/error/skipped), `vf_error` (str, truncated 2000 chars)
   - **Key columns**: `(run_id, job_id)` for upserts
   - **Encoding**: UTF-8-SIG for Excel compatibility

5. **Per-Job Artifacts** (`runs/<run_id>/metrics/vfscore/<job_id>/`):
   - `result.json` — Canonical payload with all VFScore metrics:
     ```json
     {
       "vfscore_overall_median": <int>,
       "vf_subscores_median": {"finish": <int>, "texture_identity": <int>, "texture_scale_placement": <int>},
       "repeats_n": <int>,
       "scores_all": [<int>, ...],
       "subscores_all": [{"finish": <int>, ...}, ...],
       "iqr": <float>,
       "std": <float>,
       "llm_model": "<string>",
       "rubric_weights": {"finish": <float>, "texture_identity": <float>, "texture_scale_placement": <float>},
       "render_settings": {"engine": "cycles", "hdri": "<rel or alias>", "camera": "<preset>", "seed": <int>}
     }
     ```
   - `config.json` — Effective VFScore configuration (render settings, rubric weights, LLM model, repeats)
   - `renders/` — Standardized Cycles renders used for scoring (created by VFScore tool)
   - `rationales/` — Text files with LLM explanations per scoring repeat (created by VFScore tool)

6. **Eligibility Filtering** (`_is_eligible()`):
   A job is eligible if ALL criteria are met:
   - `run_id` matches
   - `status` ∈ `--only-status` (default: `completed`)
   - `gen_object_path` exists and is non-empty on disk
   - At least 1 reference image exists on disk from chosen source (`used_image_*` or `source_image_*`)
   - If `--jobs` provided, job_id matches (substring/glob/regex)
   - If not `--redo`, skip rows with `vf_status="ok"` or non-null `vfscore_overall`
   - Emits structured skip reasons: `wrong_run_id`, `status_not_in_filter`, `job_id_not_matching_filter`, `already_computed`, `missing_gen_object_path`, `gen_object_not_found_on_disk`, `no_reference_images_found`

7. **Structured Logging** (`logs/metrics.log`):
   - JSON-formatted event summary:
     ```json
     {
       "event": "compute_vfscore",
       "timestamp": "...",
       "run_id": "...",
       "n_selected": <int>,
       "processed": <int>,
       "ok": <int>,
       "error": <int>,
       "skipped": <int>,
       "avg_render_runtime_s": <float>,
       "avg_scoring_runtime_s": <float>,
       "repeats": <int>,
       "use_images_from": "used|source",
       "redo": <bool>,
       "max_parallel": <int>,
       "dry_run": <bool>,
       "skip_reasons": {...}
     }
     ```

8. **Test Suite** (`tests/test_phase6_compute_vfscore.py`):
   - 9 comprehensive tests covering all requirements:
     1. Happy path dry-run (selection without evaluator calls)
     2. Happy path real computation (full workflow with mock adapter)
     3. Missing reference images (proper skip handling)
     4. Idempotency without redo (skips already computed jobs)
     5. Redo mode (force recomputation)
     6. Concurrency (parallel processing with ThreadPoolExecutor)
     7. Timeout handling (evaluator timeout → error status)
     8. Image source selection (used_image_* vs source_image_* filtering)
     9. Adapter error handling (non-timeout errors)
   - All 9 tests passing
   - Full test suite: **68/68 tests passing** (21+8+7+7+7+9+9 across Phases 0-6)

**Key Features**:
- **Dual Adapter Integration**: Tries Python import API first, falls back to CLI invocation
- **Reference Image Flexibility**: Supports both `used_image_*` (default) and `source_image_*` columns via `--use-images-from` flag
- **Idempotent by Default**: Skips jobs with `vf_status="ok"` unless `--redo` is set
- **Atomic CSV Updates**: Uses Phase 0 `update_csv_atomic()` with FileLock for concurrent safety
- **Parallel Execution**: ThreadPoolExecutor with configurable `--max-parallel` flag
- **Flexible Filtering**: Job ID patterns (substring/glob/regex), status lists
- **LLM Scoring Repeats**: Configurable `--repeats` for scoring consistency (default: 3)
- **Timeout Support**: Per-job timeout with `--timeout-s` flag (useful for large models/slow rendering)
- **Dry-Run Mode**: Preview eligible jobs without evaluator calls
- **Comprehensive Artifacts**: result.json, config.json, renders, rationales all persisted per job
- **Structured Logging**: JSON event summaries to `logs/metrics.log` with counters, runtimes, and skip reasons
- **Graceful Error Handling**: Missing inputs or evaluator errors → `vf_status="error"` with descriptive `vf_error`

**CLI Examples**:
```bash
# Basic usage (process completed jobs with reference images)
archi3d compute vfscore --run-id "2025-10-20-experiment"

# Dry-run to preview selection
archi3d compute vfscore --run-id "test-run" --dry-run

# Use source images instead of used images
archi3d compute vfscore --run-id "test-run" --use-images-from source

# Increase LLM repeats for more stable scores
archi3d compute vfscore --run-id "test-run" --repeats 5

# Parallel execution (renders + scoring are CPU/GPU intensive)
archi3d compute vfscore --run-id "prod-run" --max-parallel 2

# Recompute specific jobs matching a pattern
archi3d compute vfscore --run-id "test-run" --jobs "product_123*" --redo

# Custom timeout for slow renders
archi3d compute vfscore --run-id "large-models" --timeout-s 600
```

**Design Patterns**:
- **Adapter Abstraction**: Clean separation between orchestration and tool integration (mirrors Phase 5)
- **Evidence-Based Eligibility**: Multi-criteria filtering with structured skip reasons
- **Reference Image Discovery**: Dynamically extracts images from CSV columns based on `--use-images-from` parameter
- **Canonical Payload Schema**: Normalized JSON schema for cross-tool compatibility
- **Workspace-Relative Paths**: All artifact paths use POSIX format relative to workspace
- **Directory Auto-Creation**: Output directories created automatically before writes
- **Error Truncation**: Error messages capped at 2000 chars with full details in result.json
- **Runtime Decomposition**: Separates render and scoring runtimes for performance analysis

**Non-Functional Changes**:
- No changes to Phases 0-5 functionality
- All writes use Phase 0 atomic I/O utilities
- Linting/formatting applied (ruff, black)
- Added to `compute_app` Typer group (alongside `fscore` command)

**Known Constraints**:
- VFScore tool must be available via Python import or CLI (`python -m vfscore`)
- Payload normalization assumes specific JSON schema from VFScore tool
- Reference image columns must follow `{used|source}_image_{a-f}` naming convention
- Job ID filtering uses simple patterns (substring, glob with `*`, or regex with `re:` prefix)
- Status filtering uses simple string matching (not regex/glob)
- Rubric weights stored as compact JSON string in CSV (parse required for analysis)
- Error message truncation: 2000 characters (full error in result.json)
- Concurrency is thread-based (not process-based)
- Rendering and scoring are performed by external VFScore tool (not within archi3D)

**Next Phase**: Final reporting enhancements, binary wheel packaging (FScore/VFScore distribution)

---

### Bug Fixes & Improvements (2025-11-23)

**CSV Metadata Data Loss Investigation & Fix**

**Root Cause**: Worker CSV updates were lost due to OneDrive sync conflict, causing consolidate to reconstruct timestamps from state markers (which resulted in near-zero durations since both start/end were set to completion time).

**Symptoms**:
- 14 completed jobs showing ~0.000001s duration instead of actual 24-183 seconds
- `generation_start` and `generation_end` timestamps nearly identical (differing by microseconds)
- Price columns empty for affected jobs
- Completed job (`b89a7aa4150b`) retaining error_msg from previous failed attempt

**Fixes Applied**:

1. **Data Restoration** (`scripts/fix_nov22_timestamps.py`):
   - Created one-time fix script to restore correct timestamps from worker.log
   - Reconstructs `generation_start`, `generation_end`, `generation_duration_s` from log entries
   - Fills missing prices from `adapters.yaml` configuration
   - Clears `error_msg` for completed jobs
   - Usage: `python scripts/fix_nov22_timestamps.py` (dry-run) or `--apply` to write

2. **Consolidate Logic Improvement** (`src/archi3d/orchestrator/consolidate.py`):
   - Modified `_reconcile_row()` to preserve worker-written timestamps when duration > 1 second
   - Previously: Always filled timestamps from markers and recomputed duration
   - Now: Only fills timestamps/recomputes duration if existing duration < 1 second (indicating marker-derived values)
   - Prevents consolidate from overwriting valid worker data with marker-derived estimates

3. **Worker Enhancements** (previously implemented):
   - Added `--redo` flag to clear state markers and retry failed/completed jobs
   - Added `.env` hot-reload (`_reload_dotenv()`) before each API call for API key changes without restart

**Verification**:
- All 68 tests passing after consolidate fix
- Data restored: Duration range now 24-183 seconds (correct)
- All 15 jobs have prices filled
- No completed jobs with error_msg

**Prevention**:
- Consolidate now preserves valid worker data (duration > 1s)
- Worker logs capture true execution metrics even if CSV updates are lost
- Worker log can be used for data recovery if needed

---

### Additional Fixes (2025-11-23)

**1. `--redo` Flag Behavior Fix** (`src/archi3d/orchestrator/worker.py`):
- **Issue**: `--redo` only cleared state markers but didn't include completed/failed jobs in status filter
- **Problem**: With default `--only-status=enqueued`, completed jobs were filtered out BEFORE markers were cleared
- **Fix**: When `--redo` is set, auto-expand status filter to include `completed` and `failed`
- **Usage**: `archi3d run worker --run-id "..." --redo` now retries all completed/failed jobs

**2. Batch Create Status Overwrite Bug** (`src/archi3d/orchestrator/batch.py`):
- **Issue**: Running `batch create` twice for same run_id would reset job status to "enqueued"
- **Root Cause**: `update_csv_atomic()` overwrites ALL columns including status
- **Fix**: Load existing jobs before creating records, skip jobs that already exist
- **Result**: Re-running batch create is now safe - existing jobs are preserved with `already_exists` skip reason

**3. Redundant Price Column Removal**:
- **Issue**: Both `unit_price_usd` and `estimated_cost_usd` columns existed with identical values
- **Fix**: Removed `estimated_cost_usd` from:
  - `worker.py` upsert data
  - `adapters/base.py` GenerationResult dataclass
  - `scripts/fix_nov22_timestamps.py`
  - `tables/generations.csv` (existing data)
- **Result**: Single `unit_price_usd` column now used for pricing

**Verification**:
- All 68 tests passing
- CSV now has 46 columns (was 47)

---

### FScore Integration Enhancement (2025-11-25) ✅ COMPLETE

**Objective**: Add missing logging and visualization features to FScore integration for better debugging and HTML report preparation.

**Issues Identified**:
1. **Missing Console Logs**: FScore's detailed alignment/timing logs weren't captured, only Open3D warnings visible
2. **Missing Visualization**: Original FScore saves comparison `.glb` file (GT gray, prediction red) for visual inspection - needed for HTML report
3. **Missing Debug Log**: Need detailed `alignment_log.txt` file with timing/alignment details for troubleshooting

**Open3D Warnings Explanation**:
- `"Too few correspondences after mutual filter"`: During RANSAC feature matching, very few point pairs pass geometric consistency checks. Open3D falls back to using all correspondences. This is harmless - alignment still succeeds.
- `"Read PNG failed"`: Open3D attempted to read a texture/image file that's corrupted or in an unexpected format. Unrelated to FScore computation.

**Changes Made**:

1. **FScore `evaluate_one` Enhancement** (`FScore/src/fscore/evaluator.py`):
   - Added comparison visualization GLB export (GT in gray, aligned prediction in red)
   - Added detailed console logging with `logger.info()` calls:
     - Mesh loading progress (vertices/triangles counts)
     - Pre-alignment stage (RANSAC/PCA method)
     - ICP refinement stage
     - Final metric summary with timing
   - Added `alignment_log` to return payload (prealign method, scale, RANSAC/PCA fitness, ICP metrics)
   - Added `timing` to return payload (breakdown: load, prealign, ICP, fscore, total)
   - Added `visualization_path` to return payload (path to comparison GLB)
   - Added `version` and `config_hash` to return payload
   - Helper function `_rotation_matrix_to_quaternion()` for transform extraction

2. **FScore Package Update** (`FScore/src/fscore/__init__.py`):
   - Exported `evaluate_one` function in `__all__` for public API

3. **Adapter Update** (`src/archi3d/metrics/fscore_adapter.py`):
   - Added `visualization_path` field to `FScoreResponse` dataclass
   - Updated `_try_import_api()` to extract and return `visualization_path`
   - Updated `_try_cli_invocation()` to extract and return `visualization_path`
   - **CRITICAL FIX**: Updated `_normalize_payload()` to pass through new fields:
     - `alignment_log` (prealign method, scale, RANSAC/PCA/ICP fitness/RMSE)
     - `timing` (load, prealign, ICP, fscore, total timings)
     - `version` (FScore tool version)
     - `config_hash` (configuration fingerprint)
     - `visualization_path` (path to comparison GLB)
   - Previously these fields were being stripped during normalization, causing missing artifacts

4. **Computation Logic Enhancement** (`src/archi3d/metrics/fscore.py`):
   - Added `_configure_fscore_logging()` function to configure FScore's logger for console output
     - Enables FScore's logger.info() messages to appear in console
     - Adds indentation for visual grouping
     - Prevents duplicate logging via propagate=False
   - Added logging configuration call at start of `compute_fscore()` function
   - Added detailed alignment log writer in `_process_job()`:
     - Saves `alignment_log.txt` in artifact directory with:
       - Timing breakdown (load, prealign, ICP, fscore computation)
       - Alignment details (method, scale, RANSAC/PCA/ICP fitness/RMSE)
       - Mesh metadata (vertex/triangle counts for GT and prediction)
       - Final metrics (F-score, precision, recall, Chamfer L2)
   - Added visualization path logging when present
   - All data written to `runs/<run_id>/metrics/fscore/<job_id>/`:
     - `result.json` — Complete payload with all metrics and new fields
     - `alignment_log.txt` — Human-readable debug log
     - `generated_comparison.glb` — Side-by-side visualization (GT gray, pred red)

5. **CLI Enhancement** (`src/archi3d/cli.py`):
   - Added `--limit` flag to `archi3d compute fscore` command
   - Allows quick testing with subset of jobs (e.g., `--limit 1`)
   - Parameter properly wired through CLI → compute_fscore function

**Output Artifacts Structure**:
```
runs/<run_id>/metrics/fscore/<job_id>/
├── result.json                 # Complete payload with alignment_log, timing, version, config_hash
├── alignment_log.txt           # Human-readable debug log
└── generated_comparison.glb    # Visualization (GT gray, prediction red)
```

**Benefits**:
- **Better Debugging**: `alignment_log.txt` provides timing/alignment details for troubleshooting
- **Visual Inspection**: Comparison GLB allows visual verification of alignment quality
- **HTML Report Prep**: Visualization GLB path will be used in future Phase 7 HTML report with 3D viewer
- **Console Clarity**: Logger outputs show progress (mesh loading, alignment stages, final metrics) instead of just Open3D warnings
- **Quick Testing**: `--limit` flag enables fast iteration during development

**Testing**:
✅ Successfully tested with `--limit 1` on run `2025-11-22T21-25-08Z`:
- Console output shows detailed FScore progress messages:
  - "Loaded GT mesh: Leolux-LX91-armchair_KOeBH9twtP.glb (51290 vertices)"
  - "Loaded candidate mesh: generated.glb (50790 vertices)"
  - "Running pre-alignment (centering, scaling, RANSAC/PCA)..."
  - "Running ICP refinement..."
  - "Computing F-score metrics..."
  - "Saved comparison visualization: .../generated_comparison.glb"
  - "Evaluation complete: F-score=0.600, Precision=0.603, Recall=0.596, Total time=361.2s"
- `alignment_log.txt` created with timing breakdown, alignment details, mesh metadata
- `generated_comparison.glb` saved for visual inspection
- `result.json` contains all new fields (alignment_log, timing, version, config_hash, visualization_path)
- Total runtime: 361.2s for single job

**Known Issue**:
- Windows permission error when updating `generations.csv` if file is locked by Excel, OneDrive sync, or antivirus
- Workaround: Close Excel, pause OneDrive sync, or wait for sync to complete before running command
- FScore evaluation itself succeeds; error only occurs at final CSV update step

**Installation**:
To use the updated FScore integration:
```bash
# Install FScore as editable package
uv pip install --force-reinstall -e "C:/Users/matti/OneDrive - Politecnico di Bari (1)/Dev/FScore"

# Run FScore computation with limit for testing
archi3d compute fscore --run-id "your-run-id" --limit 1

# Run on all completed jobs
archi3d compute fscore --run-id "your-run-id"
```


---

### Phase 8 — Adapter Plug-In System (2025-11-25) ✅ COMPLETE

**Objective**: Implement monorepo integration with plug-in adapters for FScore/VFScore, supporting import-first/CLI-fallback discovery and third-party plugins via entry points.

**Background**:
Per `plans/Phase_8.md`, this phase prepares for closed-source binary distribution of FScore and VFScore while keeping archi3D open source. The adapter system allows flexible deployment models:
- **Development**: Editable package installs (`pip install -e path/to/FScore`)
- **Production**: Binary wheel distribution (`pip install fscore-0.2.0-*.whl`)
- **CI/CD**: External CLI invocation via environment variables
- **Third-party**: Custom adapters via setuptools entry points

**Implemented Components**:

1. **Protocol Definitions** (`src/archi3d/plugins/metrics.py`):
   - `FScoreAdapter` Protocol with `evaluate(req: FScoreRequest) -> FScoreResponse` method
   - `VFScoreAdapter` Protocol with `evaluate(req: VFScoreRequest) -> VFScoreResponse` method
   - `load_entry_point_adapter(namespace, name)` — Third-party plugin discovery
   - Runtime-checkable protocols for duck typing support

2. **Adapter Discovery Layer** (`src/archi3d/metrics/discovery.py`):
   - 3-tier resolution pattern:
     1. **Import mode**: Check if `fscore`/`vfscore` module is installed via `importlib.util.find_spec()`
     2. **Entry point mode**: Load from `archi3d.metrics_adapters` namespace
     3. **CLI mode**: Use command from `ARCHI3D_FSCORE_CLI`/`ARCHI3D_VFSCORE_CLI` environment variable
   - Environment variable overrides:
     - `ARCHI3D_FSCORE_IMPL` / `ARCHI3D_VFSCORE_IMPL` — Force specific mode (`import`, `cli`, `auto`)
     - `ARCHI3D_FSCORE_CLI` / `ARCHI3D_VFSCORE_CLI` — CLI command strings
   - `AdapterNotFoundError` with actionable error messages (installation instructions)
   - Separate discovery functions: `get_fscore_adapter()`, `get_vfscore_adapter()`

3. **Adapter Integration Updates**:
   - Modified `src/archi3d/metrics/fscore_adapter.py`:
     - `evaluate_fscore()` now uses `get_fscore_adapter()` from discovery layer
     - Removed direct `_try_import_api()` / `_try_cli_invocation()` calls from main entry point
     - Preserved original implementation functions for import/CLI wrappers
   - Modified `src/archi3d/metrics/vfscore_adapter.py`:
     - Same pattern as fscore_adapter.py
     - Discovery-based resolution for VFScore tool

4. **CLI Error Handling** (`src/archi3d/cli.py`):
   - Enhanced `compute fscore` and `compute vfscore` commands
   - Catches `AdapterNotFoundError` and displays clean error message without traceback
   - Other exceptions still show full traceback for debugging

5. **PyProject Configuration** (`pyproject.toml`):
   - Added optional dependencies:
     ```toml
     [project.optional-dependencies]
     fscore = []  # Install separately: pip install -e path/to/FScore
     vfscore = []  # Install separately: pip install -e path/to/VFScore
     ```
   - Defined entry point namespace for third-party plugins:
     ```toml
     [project.entry-points."archi3d.metrics_adapters"]
     # No built-in entry points; reserved for third-party plugins
     ```

6. **Integration Documentation** (`docs/INTEGRATION.md`):
   - Complete integration guide for FScore/VFScore
   - Three integration methods documented:
     1. Python import (recommended for development)
     2. CLI invocation (recommended for CI/CD)
     3. Entry points (for third-party plugins)
   - Environment variable configuration reference
   - Plugin development tutorial with example code
   - Canonical payload schema specification
   - Troubleshooting section with common issues
   - Discovery priority explanation

**Environment Variables**:

| Variable | Values | Default | Description |
|----------|--------|---------|-------------|
| `ARCHI3D_FSCORE_IMPL` | `import`/`cli`/`auto` | `auto` | Force FScore resolution mode |
| `ARCHI3D_VFSCORE_IMPL` | `import`/`cli`/`auto` | `auto` | Force VFScore resolution mode |
| `ARCHI3D_FSCORE_CLI` | Command string | - | FScore CLI command |
| `ARCHI3D_VFSCORE_CLI` | Command string | - | VFScore CLI command |

**Key Features**:
- **Import-first strategy**: Attempts Python import before falling back to CLI
- **Graceful degradation**: Clean error messages when adapters unavailable
- **Third-party extensibility**: Entry point support for custom adapters
- **Environment control**: Force specific resolution modes via environment variables
- **Docker-friendly**: CLI mode supports containerized deployment
- **No schema changes**: SSOT tables unchanged from Phases 0-7
- **Backward compatible**: Existing code works unchanged when modules are installed

**Testing**:
✅ Verified with dry-run test:
```bash
archi3d compute fscore --run-id "2025-11-22T21-25-08Z" --limit 1 --dry-run
```
- Successfully resolved FScore adapter via import mode
- Logged: "FScore adapter resolved via import"
- No changes to existing test suite (all 68 tests passing)

**Usage Examples**:

```bash
# Development: Install FScore as editable package
pip install -e "path/to/FScore"
archi3d compute fscore --run-id "..."

# Production: Install binary wheel
pip install fscore-0.2.0-cp311-cp311-win_amd64.whl
archi3d compute fscore --run-id "..."

# CI/CD: Use external CLI (no pip install needed)
export ARCHI3D_FSCORE_CLI="python -m fscore"
archi3d compute fscore --run-id "..."

# Force specific mode
export ARCHI3D_FSCORE_IMPL="import"  # Fail if not installable
archi3d compute fscore --run-id "..."
```

**Linting/Formatting**:
- All ruff checks passing (PLC0415 warnings suppressed for intentional lazy imports)
- Black formatting applied
- No F401 unused import warnings (using `importlib.util.find_spec()` instead of direct imports)

**Non-Functional Changes**:
- No changes to CLI semantics or existing commands
- No changes to SSOT schema or file formats
- Discovery layer adds ~100 lines of code total
- All existing imports remain compatible

**Next Steps**: Binary wheel distribution for FScore (Cython compilation, proprietary license)

---

### FScore Binary Distribution (2025-11-25) ✅ COMPLETE

**Objective**: Package FScore as a closed-source binary wheel using Cython compilation for code protection and distribution.

**Background**:
Per the "open-core" delivery strategy, FScore contains proprietary algorithms and must be distributed as compiled binaries (.pyd on Windows, .so on Linux) to protect intellectual property. The binary wheel approach:
- Preserves the `import fscore` interface (compatible with Phase 8 adapter discovery)
- Prevents source code inspection and reverse engineering
- Allows standard pip installation
- Maintains performance (native C extensions)

**Changes Made**:

1. **Cython Build Configuration** (`FScore/setup.py`):
   - Compiles 7 core modules to C extensions:
     - `fscore.evaluator` — Main evaluation logic
     - `fscore.alignment` — Mesh alignment algorithms (RANSAC, ICP)
     - `fscore.metrics` — F-score and Chamfer distance computation
     - `fscore.visualization` — GLB comparison export
     - `fscore.utils` — Shared utilities
     - `fscore.workspace` — Workspace management
     - `fscore.fbx_converter` — FBX to GLB conversion
   - Compiler directives for optimization:
     - `boundscheck=False` — Disable array bounds checking (performance)
     - `wraparound=False` — Disable negative indexing (performance)
     - `cdivision=True` — C-style division (performance)
     - `embedsignature=True` — Preserve function signatures for `help()`
     - `binding=True` — Enable runtime introspection
   - NumPy integration via `np.get_include()`
   - Build directory: `build/cython/` for intermediate files

2. **Project Configuration Updates** (`FScore/pyproject.toml`):
   - Updated build system requirements:
     ```toml
     [build-system]
     requires = ["setuptools>=61.0", "wheel", "Cython>=0.29.0", "numpy>=1.21.0"]
     build-backend = "setuptools.build_meta"
     ```
   - Changed license from MIT to Proprietary:
     ```toml
     license = {text = "Proprietary - All Rights Reserved"}
     classifiers = [
         "License :: Other/Proprietary License",
         # ... other classifiers
     ]
     ```
   - Version bumped to `0.2.0` (was `0.1.0`)

3. **Proprietary License** (`FScore/LICENSE.txt`):
   - 10-section comprehensive license agreement
   - Key restrictions:
     - **No reverse engineering, decompilation, or disassembly**
     - **No modification or derivative works**
     - **No redistribution** (except with written permission)
     - **No commercial use without explicit permission**
     - **No source code access** (binary distribution only)
   - Liability limitation and warranty disclaimer
   - Termination clause for license violations
   - Governing law (jurisdiction TBD by user)

4. **Distribution Control** (`FScore/MANIFEST.in`):
   - Includes: LICENSE.txt, README.md, pyproject.toml, setup.py
   - Excludes:
     - Test files (`tests/`, `test_models/`, `ground_truth/`, `run_test.py`)
     - Dev files (`.git/`, `.venv/`, `.claude/`, `.env`, `.gitignore`)
     - Build artifacts (`build/`, `dist/`, `*.egg-info/`)
     - Compiled files (`*.pyc`, `*.pyo`, `*.so`, `*.pyd`)
     - Documentation (CLAUDE.md, gemini.md, plan.md)
     - Binary tools (FBX2glTF-windows-x64.exe)

**Build Process**:

```bash
# Install build dependencies
pip install Cython build wheel

# Build binary wheel
cd FScore
python setup.py bdist_wheel

# Output
dist/fscore-0.2.0-cp311-cp311-win_amd64.whl  # 303 KB
```

**Build Output**:
- Wheel file: `fscore-0.2.0-cp311-cp311-win_amd64.whl` (303 KB)
- Platform-specific: `cp311-cp311-win_amd64` (Python 3.11, Windows x64)
- Contains:
  - 7 compiled `.pyd` extensions (binary C modules)
  - 7 corresponding `.py` stub files (for type hints/introspection)
  - `__init__.py` and `__main__.py` (package structure)
  - LICENSE.txt, metadata

**Installation Verification**:

Created test script (`test_binary_wheel.py`) and verified:

✅ **Test 1**: Module imports successfully
```
SUCCESS: fscore version 0.2.0
Location: .../site-packages/fscore/__init__.py
```

✅ **Test 2**: Function callable and detected as Cython function
```
SUCCESS: evaluate_one = <cyfunction evaluate_one at 0x...>
```

✅ **Test 3**: All modules compiled to binary extensions
```
Compiled extensions (.pyd): 7
  - alignment.cp311-win_amd64.pyd
  - evaluator.cp311-win_amd64.pyd
  - fbx_converter.cp311-win_amd64.pyd
  - metrics.cp311-win_amd64.pyd
  - utils.cp311-win_amd64.pyd
  - visualization.cp311-win_amd64.pyd
  - workspace.cp311-win_amd64.pyd
```

✅ **Test 4**: All expected modules verified as compiled
```
[OK] evaluator is compiled
[OK] alignment is compiled
[OK] metrics is compiled
[OK] visualization is compiled
[OK] utils is compiled
[OK] workspace is compiled
[OK] fbx_converter is compiled
```

**Installation**:

```bash
# Install binary wheel
pip install fscore-0.2.0-cp311-cp311-win_amd64.whl

# Verify installation
python -c "from fscore.evaluator import evaluate_one; print('Success!')"

# Use with archi3D (Phase 8 adapter discovery)
archi3d compute fscore --run-id "your-run-id"
```

**Integration with archi3D**:
- Phase 8 adapter discovery automatically detects installed wheel
- No code changes needed in archi3D
- Works identically to editable install (`pip install -e path/to/FScore`)
- CLI output: "FScore adapter resolved via import"

**Code Protection**:
- Source code compiled to native C extensions
- `.pyd` files are binary-only (not human-readable)
- Decompilation extremely difficult (requires reverse engineering C code)
- Proprietary license explicitly forbids reverse engineering
- Stronger protection than PyArmor or PyInstaller

**Performance**:
- Native C extensions provide performance benefits
- Compiler optimizations enabled (bounds check disabled, C division)
- NumPy integration via C API

**Platform Support**:
- Current build: Windows x64, Python 3.11
- For multi-platform distribution, build on each target platform:
  - Linux: `cp311-cp311-linux_x86_64.whl`
  - macOS: `cp311-cp311-macosx_*_x86_64.whl` or `macosx_*_arm64.whl`
- Use `cibuildwheel` for automated multi-platform builds in CI/CD

**Known Limitations**:
- `.py` stub files still included (for type hints and introspection)
  - These contain function signatures but not implementation details
  - Required for `help()`, IDE autocomplete, and type checking
  - Implementation code is in `.pyd` files only
- No runtime license verification (future enhancement)
- Wheel is platform-specific (need separate builds for Linux/macOS)

**Deliverables**:
- ✅ `setup.py` with Cython configuration
- ✅ Updated `pyproject.toml` with build requirements and proprietary license
- ✅ `LICENSE.txt` with comprehensive proprietary license agreement
- ✅ `MANIFEST.in` controlling distribution contents
- ✅ Binary wheel: `dist/fscore-0.2.0-cp311-cp311-win_amd64.whl` (303 KB)
- ✅ Verification script confirming compilation and installation

**Distribution Instructions**:
1. Build wheel on target platform: `python setup.py bdist_wheel`
2. Distribute wheel file (via private PyPI, email, or download link)
3. Recipients install via: `pip install fscore-0.2.0-*.whl`
4. No source code access required
5. License agreement terms in LICENSE.txt

**Next Steps**:
- Optional: VFScore binary distribution (same approach)
- Optional: Multi-platform wheel builds using cibuildwheel
- Optional: Runtime license verification for commercial use
- Optional: PyPI upload (private repository) for easier distribution
