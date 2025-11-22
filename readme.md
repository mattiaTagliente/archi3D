# Archi3D: Image-to-3D Experiment Orchestrator

Archi3D is a command-line tool designed to orchestrate and manage large-scale 2D-to-3D model generation experiments in a way that is reproducible, scalable, and safe for multiple developers working in a shared environment.

## ► What is Archi3D?

This project provides a robust framework for benchmarking state-of-the-art Image-to-3D conversion algorithms. It automates the entire experimental pipeline:

1.  **Cataloging** a dataset of product images and ground truth models.
2.  **Creating** batches of jobs based on specific algorithms and image selection policies.
3.  **Executing** these jobs concurrently, preventing data corruption and conflicts.
4.  **Consolidating** distributed results into a master dataset, ensuring SSOT consistency.
5.  **Computing metrics** including geometry-based quality metrics (FScore) and visual fidelity metrics (VFScore) for evaluation.
6.  **Generating** reports for analysis.

The entire system is built to work seamlessly over a shared network drive (like OneDrive or Google Drive), making collaboration easy and reliable.

## ✨ Core Features

  * **Typer-based CLI**: A clean, modern command-line interface with commands for each step of the workflow: `catalog`, `batch`, `run`, `consolidate`, `compute`, `metrics`, and `report`.
  * **Layered Configuration**: A flexible configuration system that merges settings from a global `global.yaml`, a per-user `~/.archi3d/config.yaml`, and environment variables.
  * **Idempotent Job Creation**: The system intelligently skips jobs that have already been queued or completed, preventing redundant work and wasted resources.
  * **Conflict-Free Concurrent Execution**: Designed for multi-user safety. Workers write results to a staging area in unique, isolated files. This avoids the race conditions and file corruption common with cloud-sync services, allowing dozens of developers to run workers simultaneously without interfering with each other.
  * **Reproducible Runs**: Each experiment run is version-controlled and produces deterministic outputs, ensuring results can be reproduced reliably.
  * **Human-Readable Artifacts**: Generates clearly named files and directories, making it easy to navigate and inspect the outputs of any given run.

## 🚀 Getting Started

Follow these steps to set up your local development environment and run your first experiment.

### Prerequisites

Make sure you have the following software installed on your system:

  * **Python** (version 3.11 or higher)
  * **uv**: A fast, modern Python package installer and resolver. If you don't have it, install it with:
    ```
    pip install uv
    ```
  * **Git**: For cloning the project repository.

### 1\. Clone the Repository

First, clone the project code to your local machine.

```
git clone <your-repository-url>
cd archi3d
```

### 2\. Set Up the Workspace

Archi3D operates on a central "workspace" directory that is shared among all developers (e.g., a folder in OneDrive).

Create the following folder structure inside your shared workspace:

```
Testing/
├── dataset/                    # Product folders with images and ground truth models
│   ├── 335888/                 # Folder name = product ID
│   │   ├── images/             # Product images (_A.jpg, _B.jpg, etc.)
│   │   └── gt/                 # Ground truth 3D models (.glb preferred, .fbx fallback)
│   └── 335888 - Curved/        # Folder with variant: "335888 - Curved backrest"
├── products-with-3d.json       # (Optional) Product metadata for enrichment
├── reports/
├── runs/
└── tables/
```

**Product Metadata Enrichment (Optional)**

Place a `products-with-3d.json` file in the workspace root to enrich catalog items with:
- Manufacturer name
- Product name (Italian preferred, English fallback)
- Description
- Categories (up to 3 levels)

The JSON should be a list of products with `_id` matching folder product IDs:
```json
[
  {
    "_id": 335888,
    "Manufacturer": {"Name": "ACME Corp"},
    "Name": {"Value": {"it": "Poltrona Moderna", "en": "Modern Armchair"}},
    "ShortDescription": {"Value": {"it": "Descrizione...", "en": "Description..."}},
    "Categories": [{"Name": {"it": "Poltrone", "en": "Armchairs"}}]
  }
]
```

Next, you must tell Archi3D where to find this workspace.

**Configuration Precedence** (highest to lowest priority):
1. **Environment variable** `ARCHI3D_WORKSPACE` - for CI/CD and containers
2. **`.env` file** in repository root - recommended for local development
3. **User config file** at platform-specific location - for persistent defaults

**Recommended: Use a `.env` file**

1.  **Create a `.env` file** in the root of the project by copying the example template:
    ```powershell
    # In PowerShell
    copy .env.example .env
    ```
2.  **Edit the `.env` file** and set the `ARCHI3D_WORKSPACE` variable to the **absolute path** of your `Testing` folder. **Use forward slashes (`/`) for the path.**
    ```
    # .env
    ARCHI3D_WORKSPACE="C:/Users/yourname/path/to/Testing"
    ```
    > **Note:** The `.env` file is gitignored and will not be committed to the repository.

**Alternative: User config file**

For persistent configuration across projects, create a config file at:
- **Windows**: `%LOCALAPPDATA%/archi3d/archi3d/config.yaml`
- **Linux**: `~/.config/archi3d/config.yaml`
- **macOS**: `~/Library/Application Support/archi3d/config.yaml`

```yaml
# config.yaml
workspace: "C:/Users/yourname/path/to/Testing"
```

### 3\. Set Up the Python Environment

We will use `uv` to create an isolated virtual environment for the project.

1.  **Create the virtual environment** (this only needs to be done once):
    ```
    uv venv
    ```
2.  **Activate the environment**. You must do this every time you open a new terminal to work on the project.
    ```powershell
    # On Windows PowerShell
    .venv\Scripts\Activate.ps1
    ```
    Your terminal prompt should now be prefixed with `(.venv)`.

### 4\. Install Dependencies

Install all required packages directly from `pyproject.toml`. The `uv.lock` file ensures every developer gets the exact same versions, guaranteeing reproducibility.

This command installs the `archi3d` project in **editable mode** (`-e`), meaning any changes you make to the source code are immediately available without reinstalling.

```
uv pip install -e .
```

> **Note:** `uv` automatically uses `uv.lock` for reproducible installs. The lockfile is managed automatically - you don't need to interact with it directly.

You are now ready to use the application!

## 🛠️ Usage: The Experiment Workflow

The `archi3d` CLI guides you through the entire experiment process. Run these commands in order from the project's root directory.

**1. Build the Item Catalog**

Scan the `workspace/dataset/` folder to create an inventory of all products, images, and ground truth models. If `products-with-3d.json` is present in the workspace, it enriches catalog entries with manufacturer, product name, description, and categories.

```bash
# Basic usage (auto-discovers products-with-3d.json in workspace)
archi3d catalog build

# Specify custom paths
archi3d catalog build --dataset /path/to/dataset --products-json /path/to/products.json
```

**Output files:**
- `tables/items.csv` - Main catalog with all products and enrichment data
- `tables/items_issues.csv` - Issues found (missing images, multiple GT files, etc.)
- `logs/catalog_build.log` - Structured build log

**2. Create a Batch of Jobs (Phase 2)**

Define a new experiment run. This command reads `tables/items.csv` and creates:
- A per-run job registry in `tables/generations.csv` (the SSOT for all generated artifacts)
- A per-run manifest at `runs/<run_id>/manifest.csv`
- Structured logs in `logs/batch_create.log`

The system provides a detailed summary of how many jobs were enqueued and how many were skipped (with reasons).

**Basic Usage:**
```bash
# Auto-generates run_id, uses default algorithm
archi3d batch create

# Explicit run_id and algorithms
archi3d batch create --run-id "2025-10-20-experiment" --algos tripo3d_v2p5,trellis_single

# Ecotest mode: auto-select algorithms based on item's image count
archi3d batch create --run-id "2025-10-20-ecotest" --algos ecotest
```

**Common Options:**
  * `--run-id`: Give your experiment a unique name (auto-generated UTC timestamp if omitted)
  * `--algos`: Comma-separated algorithm keys, or `ecotest` for automatic selection by n_images
  * `--limit`: Process only first N items (useful for testing)
  * `--include`: Filter to include only matching products (substring match on product_id/variant/product_name)
  * `--exclude`: Filter to exclude matching products
  * `--with-gt-only`: Skip items without ground truth models
  * `--dry-run`: Preview changes without writing files

**Examples:**
```bash
# Test run with 10 items
archi3d batch create --run-id "test-run" --algos tripo3d_v2p5 --limit 10

# Only items with ground truth
archi3d batch create --run-id "gt-only-run" --algos tripo3d_v2p5 --with-gt-only

# Filter by product ID
archi3d batch create --run-id "product-335888" --include "335888" --algos tripo3d_v2p5

# Dry-run to preview
archi3d batch create --run-id "preview" --algos tripo3d_v2p5 --dry-run

# Ecotest mode: single-image algos for 1-image items, multi-image algos for 2+ image items
archi3d batch create --run-id "ecotest-run" --algos ecotest
```

**Key Features:**
- **Deterministic job IDs**: Re-running with same inputs creates same job IDs (idempotent)
- **SSOT Registry**: All jobs tracked in `tables/generations.csv` with full observability
- **Atomic operations**: Safe for concurrent access via file locking
- **Flexible filtering**: Include/exclude patterns, GT-only mode, item limits
- **Ecotest mode**: Automatic algorithm selection based on image count for efficient resource usage

**3. Run a Worker to Process Jobs (Phase 3)**

Execute the jobs created by batch create. Phase 3 brings robust lifecycle management, resumability, and concurrent execution support. Workers read from `tables/generations.csv` (the SSOT) and update it with execution results, worker metadata, and output paths.

**Basic Usage:**
```bash
# Process all enqueued jobs for a run
archi3d run worker --run-id "initial-test-run"

# Dry-run mode (test without calling adapters)
archi3d run worker --run-id "test-run" --dry-run

# Concurrent execution with 4 workers
archi3d run worker --run-id "prod-run" --max-parallel 4
```

**Common Options:**
  * `--run-id`: Run identifier (required)
  * `--jobs`: Filter job_id by substring (e.g., `--jobs "59ad"`)
  * `--only-status`: Comma-separated statuses to process (default: "enqueued")
  * `--max-parallel`: Maximum concurrent workers (default: 1)
  * `--adapter`: Force specific adapter for debugging
  * `--dry-run`: Simulate execution without calling adapters
  * `--fail-fast`: Stop on first failure

**Examples:**
```bash
# Resume stuck "running" jobs after interruption
archi3d run worker --run-id "interrupted-run" --only-status running

# Process specific jobs by substring
archi3d run worker --run-id "test-run" --jobs "59ad"

# Force specific adapter (debug mode)
archi3d run worker --run-id "test-run" --adapter test_algo_1

# Stop on first failure
archi3d run worker --run-id "test-run" --fail-fast
```

**Key Features:**
- **Resumable execution**: State markers prevent duplicate work after interruption
- **Concurrent execution**: Thread pool with configurable parallelism
- **Worker observability**: Captures host, user, GPU, environment, commit
- **Atomic updates**: Safe concurrent access to SSOT via FileLock
- **Cost tracking**: Automatic cost tracking from adapters.yaml
- **Error handling**: Full error details in error.txt, summary in CSV

**Output Locations:**
- Job outputs: `runs/<run_id>/outputs/<job_id>/generated.glb` (and optional previews)
- State markers: `runs/<run_id>/state/<job_id>.{inprogress|completed|failed}`
- Error details: `runs/<run_id>/state/<job_id>.error.txt`
- Execution logs: `logs/worker.log`
- SSOT updates: `tables/generations.csv` (with status, timing, worker, outputs, costs)

**4. Consolidate Results (Phase 4)**

After running workers, reconcile the SSOT (`tables/generations.csv`) with on-disk artifacts and state markers to ensure consistency, fill missing metadata, and resolve any conflicts. This step ensures your data is clean and complete before proceeding to metrics and reporting.

**Basic Usage:**
```bash
# Reconcile all jobs for a run
archi3d consolidate --run-id "2025-10-20-experiment"

# Dry-run mode (preview changes without writes)
archi3d consolidate --run-id "test-run" --dry-run
```

**Common Options:**
  * `--run-id`: Run identifier (required)
  * `--dry-run`: Compute changes without writing CSV (default: false)
  * `--strict`: Exit with error on any conflict (default: false)
  * `--only-status`: Comma-separated statuses to process (e.g., "completed,failed")
  * `--fix-status`: Apply status downgrades for missing outputs (default: true)
  * `--max-rows`: Maximum rows to process (optional cap for safety)

**Examples:**
```bash
# Process only specific statuses
archi3d consolidate --run-id "prod-run" --only-status "completed,failed"

# Disable status downgrades
archi3d consolidate --run-id "test-run" --fix-status=false

# Strict mode (fail on any conflict)
archi3d consolidate --run-id "test-run" --strict

# Safety cap (process max 100 rows)
archi3d consolidate --run-id "large-run" --max-rows 100
```

**Key Features:**
- **Status Validation**: Downgrades incorrect statuses (e.g., CSV says "completed" but generated.glb missing)
- **Duplicate Resolution**: Merges duplicate (run_id, job_id) rows intelligently
- **Metadata Filling**: Synthesizes missing timestamps, paths, error messages from disk artifacts
- **Idempotent**: Re-running yields minimal changes after first reconciliation
- **Atomic Updates**: Safe concurrent access via FileLock
- **Heartbeat Detection**: Identifies stale "running" jobs (>10 min old)

**What It Does:**
- Validates status against on-disk markers (`.completed`, `.failed`, `.inprogress`) and artifacts (`generated.glb`)
- Fills missing `gen_object_path`, `preview_*_path` with workspace-relative paths
- Recomputes `generation_duration_s` from timestamps
- Reads error messages from `error.txt` files when missing in CSV
- Deduplicates rows by keeping most complete information
- Emits structured summary to `logs/metrics.log`

**4b. Legacy Consolidate (Phase 0-2)**

For backward compatibility, the old consolidate command for results staging is still available:

```bash
archi3d catalog consolidate
```

This gathers individual result files from the staging area into the main `tables/results.parquet` file (legacy behavior from Phases 0-2).

**5. Compute FScore (Geometry Metrics) — Phase 5**

After consolidation, compute geometry-based quality metrics (F-score, precision, recall, Chamfer-L2, alignment transforms) for completed jobs with ground truth objects. This command evaluates geometric similarity between generated 3D models and their ground truth counterparts.

```bash
archi3d compute fscore --run-id "initial-test-run"
```

**Key Features:**
- Automatically selects eligible jobs (`status=completed`, GT object present)
- Computes per-job metrics using the FScore evaluator (Python API or CLI fallback)
- Upserts 24 metric columns to `tables/generations.csv` (SSOT)
- Persists detailed results to `runs/<run_id>/metrics/fscore/<job_id>/result.json`
- Idempotent by default: skips jobs already computed (use `--redo` to force recomputation)
- Supports parallel execution with `--max-parallel` flag
- Structured logging to `logs/metrics.log`

**Common Options:**

```bash
# Dry-run to preview selection
archi3d compute fscore --run-id "initial-test-run" --dry-run

# Compute with parallel execution
archi3d compute fscore --run-id "initial-test-run" --max-parallel 4

# Recompute specific jobs matching a pattern
archi3d compute fscore --run-id "initial-test-run" --jobs "product_123*" --redo

# Process only completed jobs with custom sampling
archi3d compute fscore --run-id "initial-test-run" --only-status "completed" --n-points 200000

# Set per-job timeout (useful for large meshes)
archi3d compute fscore --run-id "initial-test-run" --timeout-s 300
```

**Computed Metrics:**
- **Core**: `fscore`, `precision`, `recall`, `chamfer_l2`
- **Alignment**: `fscore_scale`, rotation quaternion (`fscore_rot_w/x/y/z`), translation (`fscore_tx/y/z`)
- **Distance Statistics**: `fscore_dist_mean/median/p95/p99/max`
- **Metadata**: `fscore_n_points`, `fscore_runtime_s`, `fscore_tool_version`, `fscore_config_hash`
- **Status**: `fscore_status` (ok/error/skipped), `fscore_error` (truncated to 2000 chars)

**Output Artifacts:**
- Updated CSV: `tables/generations.csv` with FScore columns
- Per-job details: `runs/<run_id>/metrics/fscore/<job_id>/result.json`
- Structured log: `logs/metrics.log` with event summary

**5b. Compute VFScore (Visual Fidelity Metrics) — Phase 6**

After consolidation, compute visual fidelity metrics by rendering generated 3D models under standardized lighting and comparing them to reference photos using LLM-based visual scoring. This command evaluates appearance quality (finish, texture identity, texture scale/placement).

```bash
archi3d compute vfscore --run-id "initial-test-run"
```

**Key Features:**
- Automatically selects eligible jobs (`status=completed`, generated object present, reference images available)
- Renders models with standardized Cycles setup (fixed camera, HDRI, settings)
- LLM-based visual scoring with configurable repeats for consistency
- Supports both `used_image_*` (default) and `source_image_*` reference sets
- Upserts 15 metric columns to `tables/generations.csv` (SSOT)
- Persists detailed results to `runs/<run_id>/metrics/vfscore/<job_id>/` containing:
  - `result.json`: Full VFScore metrics and statistics
  - `config.json`: Effective configuration (render settings, rubric weights, LLM model)
  - `renders/`: Standardized renders used for scoring
  - `rationales/`: LLM explanations per scoring repeat
- Idempotent by default: skips jobs already computed (use `--redo` to force recomputation)
- Supports parallel execution with `--max-parallel` flag
- Structured logging to `logs/metrics.log`

**Common Options:**

```bash
# Dry-run to preview selection
archi3d compute vfscore --run-id "initial-test-run" --dry-run

# Use source images instead of used images
archi3d compute vfscore --run-id "initial-test-run" --use-images-from source

# Increase LLM repeats for more stable scores
archi3d compute vfscore --run-id "initial-test-run" --repeats 5

# Parallel execution (renders + scoring can be CPU/GPU intensive)
archi3d compute vfscore --run-id "initial-test-run" --max-parallel 2

# Recompute specific jobs matching a pattern
archi3d compute vfscore --run-id "initial-test-run" --jobs "product_123*" --redo

# Set per-job timeout
archi3d compute vfscore --run-id "initial-test-run" --timeout-s 600
```

**Computed Metrics:**
- **Core Scores**: `vfscore_overall` (0-100 median), `vf_finish`, `vf_texture_identity`, `vf_texture_scale_placement`
- **Statistics**: `vf_repeats_n`, `vf_iqr` (interquartile range), `vf_std` (standard deviation)
- **Provenance**: `vf_llm_model`, `vf_rubric_json` (compact JSON of weights), `vf_config_hash`, `vf_rationales_dir`
- **Performance**: `vf_render_runtime_s`, `vf_scoring_runtime_s`
- **Status**: `vf_status` (ok/error/skipped), `vf_error` (truncated to 2000 chars)

**Output Artifacts:**
- Updated CSV: `tables/generations.csv` with VFScore columns
- Per-job details: `runs/<run_id>/metrics/vfscore/<job_id>/result.json`
- Render artifacts: `runs/<run_id>/metrics/vfscore/<job_id>/renders/`
- LLM rationales: `runs/<run_id>/metrics/vfscore/<job_id>/rationales/`
- Configuration snapshot: `runs/<run_id>/metrics/vfscore/<job_id>/config.json`
- Structured log: `logs/metrics.log` with event summary

**6. Compute Additional Metrics (Legacy)**

After FScore computation, you can run the legacy placeholder metrics command if needed:

```bash
archi3d metrics compute --run-id "initial-test-run"
```

**7. Build the Report**

Finally, consolidate all the results from the run into a set of summary reports (`overview.yaml`, `by_algo.csv`, etc.) in the `workspace/reports/initial-test-run/` directory.

```
archi3d report build --run-id "initial-test-run"
```

You have now completed a full end-to-end run of the experiment pipeline\! You can inspect the generated files in your shared workspace to see the results.

## 📂 Project Structure

```
├── archi3d/                  # The main Python source code package
│   ├── adapters/             # Connectors to 3D generation APIs
│   ├── config/               # Configuration models and loaders
│   ├── io/                   # Data input/output (e.g., catalog builder)
│   ├── metrics/              # Metrics computation logic
│   ├── orchestrator/         # Core logic for batching and running jobs
│   ├── reporting/            # Report generation logic
│   └── cli.py                # Typer CLI application definition
├── .env.example              # Template for environment variables
├── global.yaml               # Global, project-wide configuration
├── pyproject.toml            # Central project definition and dependencies
├── uv.lock                   # Lockfile for reproducible installs (auto-managed by uv)
└── README.md                 # This file
```

## ⚖️ License

This project is licensed under the MIT License. See the `LICENSE` file for details.

## Refactoring
We are in the middle of a refactoring of the code which aim is to integrate 3 projects into a single one "archi3D", to make the delivery as planned in archi3D_delivery_strategy_executive.txt.
The 3 projects are:
- C:\Users\matti\OneDrive - Politecnico di Bari (1)\Dev\archi3d (archi3D)
- C:\Users\matti\OneDrive - Politecnico di Bari (1)\Dev\FScore (FScore)
- C:\Users\matti\OneDrive - Politecnico di Bari (1)\Dev\VFScore (VFScore)
We already prepared a plan for this refactoring and we will disclose it one step at a time