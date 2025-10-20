# Archi3D: Image-to-3D Experiment Orchestrator

Archi3D is a command-line tool designed to orchestrate and manage large-scale 2D-to-3D model generation experiments in a way that is reproducible, scalable, and safe for multiple developers working in a shared environment.

## ► What is Archi3D?

This project provides a robust framework for benchmarking state-of-the-art Image-to-3D conversion algorithms. It automates the entire experimental pipeline:

1.  **Cataloging** a dataset of product images and ground truth models.
2.  **Creating** batches of jobs based on specific algorithms and image selection policies.
3.  **Executing** these jobs concurrently, preventing data corruption and conflicts.
4.  **Consolidating** distributed results into a master dataset.
5.  **Generating** reports for analysis.

The entire system is built to work seamlessly over a shared network drive (like OneDrive or Google Drive), making collaboration easy and reliable.

## ✨ Core Features

  * **Typer-based CLI**: A clean, modern command-line interface with commands for each step of the workflow: `catalog`, `batch`, `run`, `metrics`, and `report`.
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
├── dataset/
├── reports/
├── runs/
└── tables/
```

Next, you must tell Archi3D where to find this workspace. The recommended way is to use a `.env` file.

1.  **Create a `.env` file** in the root of the project by copying the example template:
    ```powershell
    # In PowerShell
    copy .env.example .env
    ```
2.  **Edit the `.env` file** and set the `ARCHI3D_WORKSPACE` variable to the **absolute path** of your `Testing` folder. **Use forward slashes (`/`) for the path.**
    ```
    # .env
    ARCHI3D_WORKSPACE="C:/Users/matti/Politecnico di Bari(1)/B4V - Archiproducts - General/Testing"
    ```
    > **Note:** The `.env` file is ignored by Git, so your local path will not be committed to the repository.

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

Install all required packages using the `requirements.lock.txt` file. This ensures every developer has the exact same version of every dependency, guaranteeing reproducibility.

This command also installs the `archi3d` project itself in **editable mode** (`-e`), meaning any changes you make to the source code are immediately available without reinstalling.

```
uv pip install -r requirements.lock.txt -e .
```

You are now ready to use the application\!

## 🛠️ Usage: The Experiment Workflow

The `archi3d` CLI guides you through the entire experiment process. Run these commands in order from the project's root directory.

**1. Build the Item Catalog**

Scan the `workspace/dataset/` folder to create an inventory of all products, images, and ground truth models. This creates `tables/items.csv`.

```
archi3d catalog build
```

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
```

**Common Options:**
  * `--run-id`: Give your experiment a unique name (auto-generated UTC timestamp if omitted)
  * `--algos`: Comma-separated algorithm keys (uses first configured algorithm if omitted)
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
```

**Key Features:**
- **Deterministic job IDs**: Re-running with same inputs creates same job IDs (idempotent)
- **SSOT Registry**: All jobs tracked in `tables/generations.csv` with full observability
- **Atomic operations**: Safe for concurrent access via file locking
- **Flexible filtering**: Include/exclude patterns, GT-only mode, item limits

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

**4. Consolidate Results**

Before you can compute metrics or build reports, you must consolidate the individual result files from the staging area into the main `tables/results.parquet` file.

```
archi3d catalog consolidate
```

This command safely gathers all new results, merges them into the master table, and cleans up the staging area.

**5. Compute Metrics**

After consolidating, run this command to generate placeholder metric files for each new output.

```
archi3d metrics compute --run-id "initial-test-run"
```

**6. Build the Report**

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
├── requirements.lock.txt     # Pinned versions for reproducible installs
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