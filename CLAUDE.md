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

You will find the implementation status details in the changelog.md file. The new changes must be written in that file.