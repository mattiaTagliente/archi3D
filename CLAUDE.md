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

# Install dependencies (uses pyproject.toml + uv.lock)
uv pip install -e .
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
- Phase 2 tests: ✅ Complete (7 tests covering batch creation and job queue)
- Phase 3 tests: ✅ Complete (7 tests covering worker execution and lifecycle)
- Phase 4 tests: ✅ Complete (7 tests covering SSOT consolidation and reconciliation)
- Phase 5 tests: ✅ Complete (9 tests covering FScore computation and metrics upsert)
- Phase 6 tests: ✅ Complete (9 tests covering VFScore computation and visual fidelity metrics)

### Dependency Management
Dependencies are defined in `pyproject.toml`. The `uv.lock` file is auto-managed by `uv` for reproducible installs.

```bash
# Add a new dependency: edit pyproject.toml, then reinstall
uv pip install -e .

# Upgrade all dependencies and regenerate lockfile
uv lock --upgrade

# Sync environment to match lockfile exactly
uv sync
```

### Running the CLI
```bash
# Basic workflow (from project root)
archi3d catalog build                                    # Scan dataset, build items.csv
archi3d batch create --run-id "test-run"                 # Create job queue
archi3d batch create --run-id "test-run" --algos ecotest # Ecotest: auto-select algos by n_images
archi3d run worker --run-id "test-run" --adapter "..." --max-parallel 2
archi3d consolidate --run-id "test-run"                  # Reconcile SSOT with disk state
archi3d compute fscore --run-id "test-run"               # Compute geometry metrics
archi3d compute vfscore --run-id "test-run"              # Compute visual fidelity metrics
archi3d metrics compute --run-id "test-run"              # Compute additional metrics
archi3d report build --run-id "test-run"                 # Generate interactive HTML report

# Debug mode
archi3d run worker --run-id "test-run" --adapter "..." --limit 1 --dry-run
archi3d compute fscore --run-id "test-run" --dry-run     # Preview FScore computation
archi3d compute vfscore --run-id "test-run" --dry-run    # Preview VFScore computation
```

## Architecture

### Configuration System (3-Layer Merge + dotenv)
The configuration system separates secrets from configuration and resolves settings in priority order.

**Configuration Files**:
1. **`.env` file** (gitignored) - Secrets and machine-specific overrides:
   - `ARCHI3D_WORKSPACE`: Workspace directory path (REQUIRED)
   - `FAL_KEY`: fal.ai API credentials (REQUIRED for most adapters)
   - `ARCHI3D_WORKER_ID`: Optional worker identity override
   - `ARCHI3D_COMMIT`: Optional git commit hash (auto-populated in CI)

2. **`global.yaml`** (checked into git) - Project-wide configuration:
   - Enabled algorithms list
   - Quality thresholds (LPIPS, FScore)
   - Batch creation policies
   - **Tool paths** (Blender, etc.) - defaults for standard installations
   - **Metrics defaults** (FScore/VFScore parameters)

3. **User config file** (optional, platform-specific) - Per-user overrides:
   - Windows: `%LOCALAPPDATA%/archi3d/archi3d/config.yaml`
   - Linux: `~/.config/archi3d/config.yaml`
   - macOS: `~/Library/Application Support/archi3d/config.yaml`
   - **Tool path overrides** for non-standard installations

**Workspace Resolution Precedence** (highest to lowest):
1. Environment variable `ARCHI3D_WORKSPACE` (system-level)
2. `.env` file in repository root (auto-loaded via python-dotenv)
3. User config file workspace setting

**Tool Path Resolution Precedence** (highest to lowest):
1. User config `tools` section (e.g., custom Blender path)
2. Global config `tools` section (standard installation paths)

**Configuration Loading** (`archi3d.config.loader.load_config()`):
1. Finds the repo root (via pyproject.toml/global.yaml sentinel)
2. Loads `.env` file if present (populates `os.environ`)
3. Loads `global.yaml` for project settings
4. Loads user config from platformdirs location
5. Applies environment variable overrides

**Tool Path Access** (`archi3d.config.loader.get_tool_path()`):
- Get effective tool path with user config overrides
- Example: `blender_exe = get_tool_path(config, "blender_exe")`
- Returns `Path` object, respecting user overrides if set

Validated using Pydantic models in `archi3d.config.schema`. The `PathResolver` (in `archi3d.config.paths`) translates the workspace root into all derived paths (`dataset/`, `runs/`, `tables/`, `reports/`, `logs/`).

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
  - **Ecotest Mode** (`--algos ecotest`): Automatically selects algorithms based on item's n_images:
    - Items with 1 image: assigned only to single-image algorithms (`image_mode: "single"`)
    - Items with 2+ images: assigned only to multi-image algorithms (`image_mode: "multi"`)
    - This economical approach avoids wasting resources by not running multi-image algos on single-image items

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
│   ├── worker.py       # Token claiming, adapter execution, retries
│   └── consolidate.py  # SSOT reconciliation with disk artifacts (Phase 4)
├── metrics/            # Metrics computation
│   ├── fscore.py       # FScore (geometry metrics) computation (Phase 5)
│   ├── fscore_adapter.py  # FScore tool integration layer
│   ├── vfscore.py      # VFScore (visual fidelity metrics) computation (Phase 6)
│   ├── vfscore_adapter.py # VFScore tool integration layer
│   └── compute.py      # Legacy metrics computation (placeholder)
├── reporting/          # Report generation (CSV/YAML summaries)
├── utils/              # Shared utilities
│   ├── io.py           # Atomic I/O: write_text_atomic, append_log_record, update_csv_atomic
│   └── ...             # Other utilities (slugify, etc.)
└── cli.py              # Typer CLI app (5 subcommands: catalog/batch/run/metrics/report)

tests/
├── test_phase0_paths_and_io.py        # Phase 0: PathResolver and atomic I/O tests (21 tests)
├── test_phase1_catalog_build.py       # Phase 1: Catalog build functionality tests (8 tests)
├── test_phase2_batch_create.py        # Phase 2: Batch creation tests (7 tests)
├── test_phase3_run_worker.py          # Phase 3: Worker execution tests (7 tests)
├── test_phase4_consolidate.py         # Phase 4: SSOT consolidation tests (7 tests)
├── test_phase5_compute_fscore.py      # Phase 5: FScore computation tests (9 tests)
└── test_phase6_compute_vfscore.py     # Phase 6: VFScore computation tests (9 tests)
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

### FScore Metrics Computation (Phase 5)
Phase 5 implements geometry-based quality metrics via the `archi3d compute fscore` command:

**Key Components**:
1. **FScore Adapter Layer** (`metrics/fscore_adapter.py`):
   - Isolates external FScore tool integration
   - Resolution order: Python import API → CLI invocation fallback
   - Normalizes output into canonical payload schema (fscore, precision, recall, chamfer_l2, alignment, dist_stats, mesh_meta)
   - Returns `FScoreResponse` with success/error status

2. **Main Computation Logic** (`metrics/fscore.py`):
   - Job eligibility filtering (status, GT presence, existing metrics)
   - Job ID filtering (substring/glob/regex patterns)
   - Parallel execution with ThreadPoolExecutor
   - Per-job result artifacts: `runs/<run_id>/metrics/fscore/<job_id>/result.json`
   - Atomic CSV upserts to `tables/generations.csv` with 24 new FScore columns
   - Structured logging to `logs/metrics.log`

**FScore Columns in SSOT** (Phase 5):
- Core metrics: `fscore`, `precision`, `recall`, `chamfer_l2`
- Alignment: `fscore_scale`, `fscore_rot_{w,x,y,z}`, `fscore_t{x,y,z}`
- Distance stats: `fscore_dist_{mean,median,p95,p99,max}`
- Metadata: `fscore_n_points`, `fscore_runtime_s`, `fscore_tool_version`, `fscore_config_hash`
- Status: `fscore_status` (ok/error/skipped), `fscore_error`

**CLI Flags**:
- `--run-id`: Required, target run to process
- `--jobs`: Optional job ID filter (glob/regex/substring)
- `--only-status`: Comma-separated status filter (default: `completed`)
- `--with-gt-only`: Require GT object path (default: `true`)
- `--redo`: Force recomputation of existing metrics (default: `false`)
- `--n-points`: Poisson disk samples per mesh (default: `100000`)
- `--timeout-s`: Per-job timeout in seconds (optional)
- `--max-parallel`: Thread pool size (default: `1`)
- `--dry-run`: Preview selection without evaluator calls

**Idempotency & Safety**:
- Without `--redo`, skips jobs with `fscore_status="ok"` (prevents redundant computation)
- Uses Phase 0 atomic I/O (`update_csv_atomic`) with FileLock for safe concurrent access
- Creates output directories automatically before writing artifacts
- Graceful error handling: invalid inputs → `fscore_status="error"` with descriptive `fscore_error`

**Typical Workflow**:
```bash
# After worker completion
archi3d consolidate --run-id "2025-10-20-exp"       # Ensure SSOT is consistent
archi3d compute fscore --run-id "2025-10-20-exp"    # Compute geometry metrics

# Recompute specific failed jobs
archi3d compute fscore --run-id "2025-10-20-exp" --redo --only-status "failed"

# Preview before running
archi3d compute fscore --run-id "2025-10-20-exp" --dry-run
```

### VFScore Metrics Computation (Phase 6)
Phase 6 implements visual fidelity metrics via the `archi3d compute vfscore` command:

**Key Components**:
1. **VFScore Adapter Layer** (`metrics/vfscore_adapter.py`):
   - Isolates external VFScore tool integration
   - Resolution order: Python import API → CLI invocation fallback
   - Normalizes output into canonical payload schema (overall score, subscores, LLM stats, render settings)
   - Returns `VFScoreResponse` with success/error status

2. **Main Computation Logic** (`metrics/vfscore.py`):
   - Job eligibility filtering (status, generated object presence, reference images)
   - Job ID filtering (substring/glob/regex patterns)
   - Reference image selection (used_image_* or source_image_* columns)
   - Parallel execution with ThreadPoolExecutor
   - Per-job result artifacts: `runs/<run_id>/metrics/vfscore/<job_id>/` containing:
     - `result.json`: Canonical payload with all VFScore metrics
     - `config.json`: Effective VFScore config (render settings, rubric weights, LLM model)
     - `renders/`: Standardized Cycles renders used for scoring
     - `rationales/`: Text files with LLM explanations per repeat
   - Atomic CSV upserts to `tables/generations.csv` with 15 new VFScore columns
   - Structured logging to `logs/metrics.log`

**VFScore Columns in SSOT** (Phase 6 - Objective2 Pipeline):

The VFScore columns capture comprehensive metrics from the objective2 pipeline, which uses LPIPS perceptual distance combined with IoU/AR pose estimation (no LLM).

- **Status** (2): `vf_status` (ok/error/skipped), `vf_error`
- **Core Metrics** (6):
  - `vfscore_overall`: Final combined score (0-100)
  - `vf_lpips_distance`: Raw LPIPS perceptual distance (0-1, lower is better)
  - `vf_lpips_model`: LPIPS model used ("alex", "vgg", or "squeeze")
  - `vf_iou`: IoU between GT and rendered mask (0-1, higher is better)
  - `vf_mask_error`: Mask alignment error (1 - IoU)
  - `vf_pose_confidence`: Pose confidence (same as IoU)
- **Score Combination Parameters** (2):
  - `vf_gamma`: Pose confidence exponent (typically 1.0)
  - `vf_pose_compensation_c`: Max slack for poor poses (typically 0.5)
- **Final Pose Parameters** (5):
  - `vf_azimuth_deg`: Camera azimuth (degrees)
  - `vf_elevation_deg`: Camera elevation (degrees)
  - `vf_radius`: Camera distance from object
  - `vf_fov_deg`: Field of view (degrees)
  - `vf_obj_yaw_deg`: Object yaw rotation (degrees)
- **Pipeline Statistics** (5):
  - `vf_pipeline_mode`: Pipeline mode ("tri_criterion", "ar_based", etc.)
  - `vf_num_step2_candidates`: Coarse pose candidates count
  - `vf_num_step4_candidates`: Fine pose candidates count
  - `vf_num_selected_candidates`: Candidates passed to LPIPS scoring
  - `vf_best_lpips_idx`: Index of best candidate in selected set
- **Performance & Provenance** (4):
  - `vf_render_runtime_s`: Rendering time (seconds)
  - `vf_scoring_runtime_s`: Pose search + LPIPS time (seconds)
  - `vf_tool_version`: VFScore version string
  - `vf_config_hash`: Configuration hash for reproducibility
- **Artifact Paths** (3):
  - `vf_artifacts_dir`: Workspace-relative path to vfscore_artifacts directory
  - `vf_gt_image_path`: Relative path to GT image (from artifacts_dir)
  - `vf_render_image_path`: Relative path to HQ render (from artifacts_dir)
- **DEPRECATED** (9 columns kept for backward compatibility, will be removed):
  - `vf_finish`, `vf_texture_identity`, `vf_texture_scale_placement` (LLM subscores, always NULL)
  - `vf_repeats_n`, `vf_iqr`, `vf_std` (LLM stats, not applicable)
  - `vf_llm_model`, `vf_rubric_json`, `vf_rationales_dir` (LLM-related, always NULL)

**Score Combination Formula**:
```
slack = pose_compensation_c * (1 - pose_confidence^gamma)
adjusted_lpips = lpips_distance - slack
normalized_score = max(0, min(1, 1 - adjusted_lpips))
vfscore_overall = normalized_score * 100
```

Where higher `pose_confidence` (IoU) reduces the penalty from LPIPS distance, rewarding well-aligned poses.

**CLI Flags**:
- `--run-id`: Required, target run to process
- `--jobs`: Optional job ID filter (glob/regex/substring)
- `--only-status`: Comma-separated status filter (default: `completed`)
- `--use-images-from`: Reference image source - `used` or `source` (default: `used`)
- `--repeats`: Number of LLM scoring repeats for consistency (default: `3`)
- `--redo`: Force recomputation of existing metrics (default: `false`)
- `--max-parallel`: Thread pool size (default: `1`)
- `--timeout-s`: Per-job timeout in seconds (optional)
- `--dry-run`: Preview selection without evaluator calls

**Idempotency & Safety**:
- Without `--redo`, skips jobs with `vf_status="ok"` (prevents redundant computation)
- Uses Phase 0 atomic I/O (`update_csv_atomic`) with FileLock for safe concurrent access
- Creates output directories automatically before writing artifacts
- Graceful error handling: missing inputs → `vf_status="error"` with descriptive `vf_error`

**Typical Workflow**:
```bash
# After worker completion and consolidation
archi3d compute vfscore --run-id "2025-10-20-exp"    # Compute visual fidelity metrics

# Use source images instead of used images
archi3d compute vfscore --run-id "2025-10-20-exp" --use-images-from source

# Increase LLM repeats for more stable scores
archi3d compute vfscore --run-id "2025-10-20-exp" --repeats 5

# Parallel processing for faster computation
archi3d compute vfscore --run-id "2025-10-20-exp" --max-parallel 3

# Recompute specific jobs
archi3d compute vfscore --run-id "2025-10-20-exp" --redo --jobs "job_abc*"

# Preview before running
archi3d compute vfscore --run-id "2025-10-20-exp" --dry-run
```

### HTML Report Generation (Phase 7)
The reporting module generates interactive HTML reports with advanced visualizations and statistical analysis.

**Key Features**:
1. **Interactive Visualizations**: Plotly-based box plots, scatter plots, and leaderboard rankings
2. **Statistical Analysis**: Mann-Whitney U test for algorithm comparison with significance coloring
3. **Multi-Run Support**: Dropdown selector to switch between different test runs
4. **Visual Comparison**: Side-by-side GT vs. Render image comparison with pagination
5. **Workspace-Relative Paths**: All image paths are workspace-relative for portability

**HTML Report Components**:
- **Box Plots Tab**: Distribution visualization for F-Score, VF-Score, and execution time by category
- **Statistics Tab**: Descriptive statistics (mean, std) and pairwise significance testing
- **Algorithm Comparison Tab**: Scatter plots, center of mass visualization, and leaderboard ranking
- **Visual Comparison Tab**: Paginated grid of GT vs. rendered images with search functionality
- **Summary Tab**: DataTable with all items and their metadata

**Statistical Functions** (Pure Python Implementation):
- `mann_whitney_u()`: Two-sided Mann-Whitney U test for non-parametric comparison
- `calculate_rank()`: Rank assignment with tie handling for statistical tests
- `remove_outliers()`: IQR-based outlier removal (applied only to execution times)
- `calculate_stats()`: Comprehensive statistical summary per algorithm

**HTML Report Structure**:
- Saved to: `reports/report.html` (workspace-relative, single file for all runs)
- Self-contained HTML with embedded data (no external data files required)
- Uses CDN resources for Bootstrap, Plotly, DataTables, jQuery
- Responsive design with mobile support
- Image paths use `../` prefix for correct resolution from reports subfolder

**CLI Usage**:
```bash
# Generate interactive HTML report (includes all runs)
archi3d report build --run-id "2025-10-20-exp"

# Output location (automatic)
# reports/report.html (within workspace)
```

**Integration Points**:
- Reads from: `tables/generations.csv`, `tables/items.csv`
- Image paths resolved with `../` prefix: `../runs/<run_id>/metrics/vfscore/<job_id>/lpips_debug/`
- Requires: `fscore` and `vfscore_overall` columns in generations.csv
- Skips rows with missing or zero metrics

**Typical Workflow**:
```bash
# Complete workflow with HTML report
archi3d catalog build
archi3d batch create --run-id "exp-2025-12"
archi3d run worker --run-id "exp-2025-12" --adapter "tripo3d_v2p5_multi"
archi3d consolidate --run-id "exp-2025-12"
archi3d compute fscore --run-id "exp-2025-12"
archi3d compute vfscore --run-id "exp-2025-12"
archi3d report build --run-id "exp-2025-12"

# Open the report in browser (single file for all runs)
# Windows: start reports/report.html
# Linux/Mac: open reports/report.html
```

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