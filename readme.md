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

**2. Create a Batch of Jobs**

Define a new experiment run. This command reads `tables/items.csv` and creates a queue of jobs to be processed, providing a detailed summary of how many jobs were created and how many were skipped (and why).

  * `--run-id`: Give your experiment a unique name.
  * `--only`: (Optional) A filter to run the experiment on a small subset of products, perfect for testing.

<!-- end list -->

```
archi3d batch create --run-id "initial-test-run" --only "335888*"
```

This will create a new folder under `workspace/runs/initial-test-run/` containing the job queue and a historical log of all batch creation operations.

**3. Run a Worker to Process Jobs**

Execute the jobs waiting in the queue. Multiple users can run workers simultaneously without conflict. Each completed job will generate a unique result file in the `tables/results_staging/` directory.

```
archi3d run worker --run-id "initial-test-run" --algo "tripo3d_v2p5_multi" --limit 5
```

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