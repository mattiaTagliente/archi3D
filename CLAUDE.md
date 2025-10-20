# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Archi3D is a CLI orchestrator for large-scale 2D-to-3D model generation experiments. It enables reproducible, concurrent benchmarking of Image-to-3D algorithms across a shared workspace (typically OneDrive/cloud sync). The system is designed for multi-user safety with conflict-free concurrent execution.

**Current Status**: Undergoing refactoring to integrate three projects (archi3D, FScore, VFScore) into a unified deliverable per `archi3d_delivery_strategy_executive.txt`. The integration strategy uses an "open-core" model where archi3D remains open (MIT) while FScore/VFScore are distributed as binary wheels.

## Development Commands

### Environment Setup
```bash
# Create virtual environment (Windows)
uv venv
.venv\Scripts\Activate.ps1

# Install dependencies
uv pip install -r requirements.lock.txt -e .
```

### Linting and Type Checking
```bash
# Run Ruff linter (auto-fix enabled)
ruff check --fix src/

# Run Black formatter
black src/

# Run mypy type checker
mypy src/archi3d
```

### Testing
```bash
# Run all tests
pytest

# Run specific test file
pytest tests/test_phase0_paths_and_io.py

# Run with verbose output
pytest -v

# Run with coverage (if coverage is installed)
pytest --cov=src/archi3d
```

**Test Suite Status**:
- Phase 0 tests: ✅ Complete (21 tests covering PathResolver and atomic I/O utilities)
- Phase 1 tests: ✅ Complete (8 tests covering catalog build functionality)

### Dependency Management
```bash
# Compile new lock file from pyproject.toml
pip-compile pyproject.toml -o requirements.lock.txt

# Upgrade all dependencies
pip-compile --upgrade pyproject.toml -o requirements.lock.txt
```

### Running the CLI
```bash
# Basic workflow (from project root)
archi3d catalog build                              # Scan dataset, build items.csv
archi3d batch create --run-id "test-run"          # Create job queue
archi3d run worker --run-id "test-run" --algo "tripo3d_v2p5_multi" --limit 5
archi3d catalog consolidate                        # Merge results from staging
archi3d metrics compute --run-id "test-run"       # Compute metrics
archi3d report build --run-id "test-run"          # Generate reports

# Debug mode
archi3d run worker --run-id "test-run" --algo "..." --limit 1 --dry-run
```

## Architecture

### Configuration System (3-Layer Merge)
The configuration system resolves settings in priority order:
1. **`global.yaml`** (repo root): Project-wide algorithms, thresholds, batch policies
2. **`~/.archi3d/config.yaml`** (user-specific): Workspace path overrides
3. **Environment variables**: `ARCHI3D_WORKSPACE` (highest priority)

Configuration is loaded via `archi3d.config.loader.load_config()` and validated using Pydantic models in `archi3d.config.schema`. The `PathResolver` (in `archi3d.config.paths`) translates the workspace root into all derived paths (`dataset/`, `runs/`, `tables/`, `reports/`, `logs/`).

**PathResolver Capabilities (Phase 0)**:
- **Directory Properties**: `tables_root`, `runs_root`, `reports_root`, `logs_root` (with backward-compatible `_dir` aliases)
- **SSOT File Paths**: Canonical getters for all CSV tables and log files:
  - Tables: `items_csv_path()`, `items_issues_csv_path()`, `generations_csv_path()`
  - Logs: `catalog_build_log_path()`, `batch_create_log_path()`, `worker_log_path()`, `metrics_log_path()`
- **Tree Initialization**: `ensure_mutable_tree()` creates all required workspace directories (idempotent)
- **Relative Paths**: `rel_to_workspace(path)` returns paths relative to workspace root

**Adapter Configuration**: The system loads adapter-specific settings (endpoints, pricing, defaults) from `src/archi3d/config/adapters.yaml` via `archi3d.config.adapters_cfg.load_adapters_cfg()`. This function handles both development and PyInstaller-bundled environments by detecting `sys.frozen` and using `sys._MEIPASS` for bundled executables.

### Adapter Registry Pattern
All 3D generation algorithms are implemented as **adapters** inheriting from `ModelAdapter` (`archi3d.adapters.base`). Each adapter:
- Implements `execute(token: Token, deadline_s: int) -> ExecResult`
- Handles upload/prepare → invoke API → download/materialize
- Must raise `AdapterTransientError` (retryable) or `AdapterPermanentError`
- Configured via `src/archi3d/config/adapters.yaml` with pricing metadata

The `REGISTRY` dict (`archi3d.adapters.registry`) maps algorithm keys (e.g., `"tripo3d_v2p5_multi"`) to adapter classes. **When adding a new algorithm**, you must:
1. Create a new adapter class in `src/archi3d/adapters/<algo_name>.py`
2. Add it to `REGISTRY` in `src/archi3d/adapters/registry.py`
3. Add the key to `global.yaml` under `algorithms:`
4. Configure pricing in `src/archi3d/config/adapters.yaml`

### Job Orchestration (Batch + Worker)
- **Batch Creation** (`orchestrator.batch.create_batch`):
  - Reads `tables/items.csv`
  - Applies per-algorithm image selection policies (single/multi-view requirements)
  - Generates deterministic `job_id` via SHA1 hash of (algo|product|variant|images)
  - Writes `.todo.json` tokens to `runs/<run_id>/queue/`
  - Skips jobs already in `results.parquet` with `status=completed` for the same `run_id`

- **Worker Execution** (`orchestrator.worker.run_worker`):
  - Claims tokens by atomically renaming `.todo.json` → `.inprogress.<worker_id>.json`
  - Validates job_id integrity before execution
  - Retries transient errors with exponential backoff (10s, 30s, 60s)
  - Writes per-job `.parquet` files to `tables/results_staging/` (conflict-free)
  - Renames tokens to `.completed.json` or `.failed.json` on finish

### Conflict-Free Concurrency
Multiple workers can run simultaneously without conflicts:
- **Token claiming**: Atomic file rename prevents double-processing
- **Results staging**: Each worker writes unique `{job_id}.parquet` files
- **Consolidation**: `catalog consolidate` locks `results.parquet` with `FileLock` when merging staging files

### Atomic I/O Utilities (Phase 0)
The `archi3d.utils.io` module provides thread-safe, atomic file operations for safe concurrent access:

**`write_text_atomic(path, text)`**:
- Atomically writes text files using temp file + rename pattern
- Uses `os.replace()` for atomic rename (POSIX/Windows compatible)
- Includes `fsync()` for durability guarantee
- Automatically creates parent directories

**`append_log_record(path, record: str | dict)`**:
- Thread-safe log appending with FileLock
- Automatically prefixes each line with ISO8601 UTC timestamp
- Serializes dict records as single-line JSON
- UTF-8 encoding with automatic parent directory creation

**`update_csv_atomic(path, df_new, key_cols) -> (inserted, updated)`**:
- Upserts DataFrame rows into CSV using key columns
- Thread-safe via FileLock (uses `.lock` sibling file)
- Deduplicates input data (keeps last occurrence per key)
- Returns tuple of (inserted_count, updated_count)
- Preserves column order: existing columns first, new columns appended
- Uses `utf-8-sig` encoding for Excel compatibility
- Atomically writes via temp file + rename

**Usage Pattern**:
```python
from archi3d.config.loader import load_config
from archi3d.config.paths import PathResolver
from archi3d.utils.io import append_log_record, update_csv_atomic
import pandas as pd

# Get canonical log path
paths = PathResolver(load_config())
log_path = paths.catalog_build_log_path()

# Append log entry
append_log_record(log_path, {"event": "scan_start", "items": 42})

# Upsert CSV data
csv_path = paths.items_csv_path()
df = pd.DataFrame({"product_id": [1, 2], "status": ["ok", "ok"]})
inserted, updated = update_csv_atomic(csv_path, df, key_cols=["product_id"])
```

### Legacy Version Hashing
The `--legacy-version` flag in `batch create` enables backward compatibility with old job_ids. When re-running a batch for an existing run created with version 0.1.0, use:
```bash
archi3d batch create --run-id "2025-08-17_v1" --legacy-version "0.1.0"
```
This ensures the hash function includes the version string, matching historical job_ids in `results.parquet`.

## File Organization

```
src/archi3d/
├── adapters/           # Algorithm connectors (ModelAdapter subclasses)
│   ├── base.py         # Base adapter interface + Token/ExecResult
│   ├── registry.py     # REGISTRY dict mapping algo keys to classes
│   └── <algo>.py       # Specific adapter implementations
├── config/             # Configuration loading and validation
│   ├── schema.py       # Pydantic models (GlobalConfig, UserConfig, etc.)
│   ├── loader.py       # 3-layer merge logic (_find_repo_root, load_config)
│   ├── paths.py        # PathResolver (workspace paths, SSOT file getters)
│   └── adapters_cfg.py # Adapter-specific config loader
├── db/                 # Database/catalog operations (SSOT builders)
│   └── catalog.py      # Catalog build: scan dataset, write items.csv + items_issues.csv
├── io/                 # Legacy data I/O (deprecated in favor of db/)
│   └── catalog.py      # Old dataset scanning (superseded by db.catalog)
├── orchestrator/       # Core batch/worker logic
│   ├── batch.py        # Job queue creation, image selection policies
│   └── worker.py       # Token claiming, adapter execution, retries
├── metrics/            # Metrics computation (placeholder stubs)
├── reporting/          # Report generation (CSV/YAML summaries)
├── utils/              # Shared utilities
│   ├── io.py           # Atomic I/O: write_text_atomic, append_log_record, update_csv_atomic
│   └── ...             # Other utilities (slugify, etc.)
└── cli.py              # Typer CLI app (5 subcommands: catalog/batch/run/metrics/report)

tests/
├── test_phase0_paths_and_io.py     # Phase 0: PathResolver and atomic I/O tests (21 tests)
└── test_phase1_catalog_build.py    # Phase 1: Catalog build functionality tests (8 tests)
```

## Key Design Patterns

### Path Handling
- **Never hardcode workspace paths**. Always use `PathResolver` methods.
- All file paths in tables use **portable relative paths** prefixed with `dataset/` (e.g., `dataset/335888/images/foo_A.jpg`).
- Use `.as_posix()` when writing paths to CSV/JSON to ensure cross-platform compatibility.
- **Workspace-relative paths** (Phase 1): All paths stored in SSOT CSV tables must be workspace-relative (no drive letters, no absolute paths). Use `paths.rel_to_workspace(abs_path).as_posix()` when storing paths.

### Catalog Build Patterns (Phase 1)
- **Folder Naming**: Dataset folders follow `{product_id}` or `{product_id} - {variant}` format (e.g., `335888` or `335888 - Curved backrest`)
- **Image Selection Rules**:
  1. Tagged images (`_A.jpg` through `_F.jpg`) are selected first, sorted by tag letter
  2. Untagged images follow, sorted lexicographically (case-insensitive)
  3. Maximum 6 images per item (capped, generates "too_many_images" issue if exceeded)
- **GT File Selection**:
  1. Prefer `.glb` over `.fbx` extensions (priority order)
  2. If multiple files of same extension exist, pick lexicographically first
  3. Generate "multiple_gt_candidates" issue when multiple files of same extension found
- **JSON Enrichment**:
  1. Locale preference: IT (Italian) preferred over EN (English) for Name, Description
  2. Categories: Extract deepest category path, split by " > " separator, take first 3 levels
  3. Generate "missing_*" issues for absent enrichment fields (manufacturer, product_name, description, categories)
- **CSV Type Handling**: When reading SSOT CSVs with pandas, always specify `dtype={"product_id": str, "variant": str}` to prevent numeric inference

### Token File Naming
Token filenames are human-readable and contain:
- `{product_id}_{variant}_{algo}_N{n_images}_{img_suffixes}_{run_id}_h{job_id[:8]}`
- **Preserve suffix casing** (`_A-B-C`, not `_a-b-c`) via `_img_suffixes_from_list()`
- Soft-cap at 120 chars, intelligently truncating variant before hard cut

### Job ID Integrity
Workers validate token authenticity by recomputing `job_id` from token contents using `_compose_job_id()`. Mismatches raise `ValueError` and fail the job (prevents manual edits or corruption).

### Error Handling in Adapters
- **Transient errors** (rate limits, timeouts): Raise `AdapterTransientError` → worker retries 3 times
- **Permanent errors** (invalid input, API rejection): Raise `AdapterPermanentError` → worker fails immediately
- Always log to `adapter.logger` (instance-specific logger in `base.py`)

## Important Constraints

1. **Workspace Structure**: The workspace must have `dataset/`, `runs/`, `tables/`, `reports/`, `logs/` subdirs. The mutable directories (`runs/`, `tables/`, `reports/`, `logs/`) are created automatically by `PathResolver.ensure_mutable_tree()` on initialization. The catalog builder expects dataset folders named `{product_id}` or `{product_id} - {variant}` with `images/` and `gt/` subdirs.

2. **Image Naming Convention**: Multi-view images should use `_A.jpg`, `_B.jpg`, etc. suffixes. The system automatically sorts by letter and extracts suffixes for readable token names.

3. **UTF-8 Enforcement**: `cli.py` calls `_force_utf8_stdio()` to ensure proper encoding on Windows. Always use `encoding="utf-8"` or `"utf-8-sig"` (for Excel compatibility) when writing CSVs.

4. **Atomic File Operations**: When renaming tokens, use `_rename_atomic()` to correctly strip multi-part suffixes (`.todo.json`, `.inprogress.json`, etc.).

5. **FileLock for Shared Resources**: Any code writing to `results.parquet` or `manifest_inputs.csv` must acquire the appropriate lock via `paths.results_lock_path()` or `paths.manifest_lock_path()`.

6. **Atomic I/O for SSOT Tables** (Phase 0+): When writing to canonical CSV tables or logs, always use the atomic utilities from `archi3d.utils.io`:
   - Use `update_csv_atomic()` for CSV upserts (handles locking automatically)
   - Use `append_log_record()` for structured logging (handles timestamps and locking)
   - Use `write_text_atomic()` for safe text file writes
   - These utilities prevent corruption under concurrent access and provide consistent error handling

7. **Platform-Specific Notes**:
   - Use Git Bash on Windows (per user environment)
   - Prefer `uv` over `pip` for package management
   - Use forward slashes in `.env` paths even on Windows

## Planned Integration Work

Here is the rationale for the delivery plan:

* The goal is to deliver a functional tool while protecting the intellectual property of the core components, `FScore` and `VFScore`.
* The `archi3D` orchestrator will remain open-source, but `FScore` and `VFScore` will be delivered as closed-source binary wheels (e.g., compiled via Cython/Nuitka).
* This "open-core" approach is feasible because the core components are already designed as separable CLI/packages.
* This strategy is contractually justified because the official deliverables do not explicitly require source code and include a confidentiality clause.
* The binaries will be protected by a restrictive license (EULA) that explicitly forbids reverse-engineering.

When working on this integration:
- Keep the orchestrator agnostic to the metrics backend implementation
- Store metrics outputs in a consistent format regardless of backend
- Document the API contract for external metrics tools

The detailed plans for the integration are contained in the `.\plans` directory.
When prompted, execute the plans in discrete steps one phase at a time, stopping after each phase, following the directions of the user.
Remember to always read a file before modifying it, to assess if the planned modifications are valid.

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

**Next Phase**: Phase 3 (worker execution and status updates, to be implemented based on future plans)
