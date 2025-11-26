# Archi3D Quickstart Guide

This guide will help you install and run Archi3D, a CLI orchestrator for large-scale 2D-to-3D model generation experiments with integrated FScore (geometry) and VFScore (visual fidelity) evaluation.

## Contents of Distribution Package

The distributed `.zip` file contains:
- `archi3d/` — Main archi3D repository (open source, MIT license)
- `wheels/fscore-*.whl` — FScore binary wheel (proprietary, geometry metrics)
- `wheels/vfscore_rt-*.whl` — VFScore binary wheel (proprietary, visual fidelity metrics)
- `quickstart.md` — This file

## System Requirements

- **Operating System**: Windows 10/11 (64-bit), Linux, or macOS
- **Python**: 3.11 or higher
- **Git**: Required for version control
- **Disk Space**: ~5 GB minimum (more for large datasets)
- **RAM**: 8 GB minimum, 16 GB recommended
- **GPU**: Optional but recommended for faster processing (CUDA-compatible NVIDIA GPU)

## Prerequisites Installation

### 1. Install Python 3.11+

**Windows**:
```bash
# Download from python.org or use winget
winget install Python.Python.3.11
```

**Linux (Ubuntu/Debian)**:
```bash
sudo apt update
sudo apt install python3.11 python3.11-venv python3-pip
```

**macOS**:
```bash
brew install python@3.11
```

Verify installation:
```bash
python --version  # Should show Python 3.11.x or higher
```

### 2. Install uv (Fast Python Package Manager)

**Windows (PowerShell)**:
```powershell
powershell -c "irm https://astral.sh/uv/install.ps1 | iex"
```

**Linux/macOS**:
```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Verify installation:
```bash
uv --version
```

### 3. Install Git

**Windows**:
```bash
winget install Git.Git
```

**Linux (Ubuntu/Debian)**:
```bash
sudo apt install git
```

**macOS**:
```bash
brew install git
```

## Installation Steps

### Step 1: Extract the Distribution Package

```bash
# Extract the .zip file to a directory of your choice
unzip archi3d-distribution.zip -d ~/archi3d
cd ~/archi3d/archi3d
```

### Step 2: Create and Activate Virtual Environment

**Windows (PowerShell)**:
```powershell
# Create virtual environment
uv venv

# Activate
.venv\Scripts\Activate.ps1
```

**Windows (Git Bash)**:
```bash
# Create virtual environment
uv venv

# Activate
source .venv/Scripts/activate
```

**Linux/macOS**:
```bash
# Create virtual environment
uv venv

# Activate
source .venv/bin/activate
```

You should see `(.venv)` prefix in your terminal prompt.

### Step 3: Install Archi3D

```bash
# Install archi3D in editable mode with all dependencies
uv pip install -e .
```

This will install:
- Archi3D core orchestrator
- All required dependencies (pandas, typer, pydantic, etc.)
- CLI command: `archi3d`

Verify installation:
```bash
archi3d --version
```

### Step 4: Install FScore and VFScore Wheels

```bash
# Navigate to wheels directory
cd ../wheels

# Install FScore (geometry metrics)
uv pip install fscore-0.2.0-cp311-cp311-win_amd64.whl

# Install VFScore (visual fidelity metrics)
uv pip install vfscore_rt-0.2.0-cp311-cp311-win_amd64.whl

# Return to archi3d directory
cd ../archi3d
```

Verify installation:
```bash
python -c "import fscore; print(f'FScore version: {fscore.__version__}')"
python -c "import vfscore; print(f'VFScore version: {vfscore.__version__}')"
```

For automatic FBX to GLB conversion (for FScore computation):
- download proper FBX2gLTF binary for your plaform from https://github.com/facebookincubator/FBX2glTF/releases
- place it in the `archi3d` virtual environment `lib` subdirectory

```bash
cp /path/to/your/FBX2glTF-windows-x64.exe .venv/Lib
```

## Configuration

### Step 1: Set Up Workspace Directory

Create a workspace directory to store your dataset, results, and reports:

```bash
# Example workspace location
mkdir -p ~/archi3d_workspace
```

### Step 2: Configure Archi3D

Create a `.env` file in the `archi3d/` directory:

**Windows**:
```bash
# Create .env file
echo ARCHI3D_WORKSPACE=C:/Users/YourUsername/archi3d_workspace > .env
```

**Linux/macOS**:
```bash
# Create .env file
echo "ARCHI3D_WORKSPACE=$HOME/archi3d_workspace" > .env
```

**Alternative**: Create platform-specific user config file:

**Windows**:
```yaml
# File: %LOCALAPPDATA%/archi3d/archi3d/config.yaml
workspace: C:/Users/YourUsername/archi3d_workspace
```

**Linux**:
```yaml
# File: ~/.config/archi3d/config.yaml
workspace: /home/youruser/archi3d_workspace
```

**macOS**:
```yaml
# File: ~/Library/Application Support/archi3d/config.yaml
workspace: /Users/youruser/archi3d_workspace
```

### Step 3: Prepare Dataset Structure

Your workspace should follow this structure:

```
archi3d_workspace/
├── dataset/              # Your 3D objects dataset
│   ├── 335888/           # Product ID
│   │   ├── images/       # Reference images
│   │   │   ├── img_A.jpg
│   │   │   ├── img_B.jpg
│   │   │   └── ...
│   │   └── gt/           # Ground truth 3D model
│   │       └── model.glb
│   ├── 335889 - Variant Name/  # Product ID with variant
│   │   ├── images/
│   │   └── gt/
│   └── ...
├── tables/               # SSOT CSV tables (auto-created)
├── runs/                 # Run outputs (auto-created)
├── reports/              # Generated reports (auto-created)
└── logs/                 # Execution logs (auto-created)
```

## End-to-End Test Run

This test will process a single object through the entire Archi3D pipeline.

### Step 1: Create Sample Dataset

```bash
# Navigate to workspace
cd ~/archi3d_workspace

# Create sample dataset structure
mkdir -p dataset/test_001/images
mkdir -p dataset/test_001/gt

# Copy your test images and GT model
# (Replace these paths with your actual files)
cp /path/to/your/test_image_1.jpg dataset/test_001/images/img_A.jpg
cp /path/to/your/test_image_2.jpg dataset/test_001/images/img_B.jpg
cp /path/to/your/ground_truth.glb dataset/test_001/gt/model.glb
```

### Step 2: Build Catalog

Scan the dataset and create the items catalog:

```bash
cd ~/archi3d/archi3d
archi3d catalog build
```

**Expected Output**:
```
Catalog build complete.
Items written: 1
Issues written: 0 (if any)
Output: ~/archi3d_workspace/tables/items.csv
Issues: ~/archi3d_workspace/tables/items_issues.csv
```

Verify the catalog:
```bash
cat ~/archi3d_workspace/tables/items.csv
```

### Step 3: Create Batch

Generate job queue for a test run:

```bash
archi3d batch create --run-id "quickstart-test"
```

**Expected Output**:
```
Batch created: quickstart-test
Jobs generated: 1-5 (depending on configured algorithms)
Queue directory: ~/archi3d_workspace/runs/quickstart-test/queue/
```

### Step 4: Configure Algorithm Adapter

Before running the worker, you need to configure at least one algorithm adapter. For this test, we'll use a placeholder or the simplest available adapter.

**Option A: Dry Run (No actual API calls)**:
```bash
archi3d run worker --run-id "quickstart-test" --adapter "tripo3d_v2p5_multi" --dry-run --limit 1
```

**Option B: Actual Run (Requires API credentials)**:

First, set up your API credentials in `.env`:
```bash
# Add to .env file
echo "TRIPO3D_API_KEY=your_api_key_here" >> .env
```

Then run:
```bash
archi3d run worker --run-id "quickstart-test" --adapter "tripo3d_v2p5_multi" --limit 1 --max-parallel 1
```

**Expected Output**:
```
Worker started for run: quickstart-test
Processing job: test_001_default_tripo3d_v2p5_multi...
Job completed successfully.
Results written to: ~/archi3d_workspace/runs/quickstart-test/results/test_001_*.glb
```

### Step 5: Consolidate Results

Reconcile SSOT with disk artifacts:

```bash
archi3d consolidate --run-id "quickstart-test"
```

**Expected Output**:
```
Consolidation complete.
Updated generations table: ~/archi3d_workspace/tables/generations.csv
```

### Step 6: Compute FScore (Geometry Metrics)

```bash
archi3d compute fscore --run-id "quickstart-test"
```

**Expected Output**:
```
FScore computation complete.
Jobs processed: 1
Jobs successful: 1
Jobs failed: 0
Updated generations table with FScore metrics.
```

Verify FScore results:
```bash
# Check the generations table for fscore, precision, recall, chamfer_l2 columns
cat ~/archi3d_workspace/tables/generations.csv
```

### Step 7: Compute VFScore (Visual Fidelity Metrics)

```bash
archi3d compute vfscore --run-id "quickstart-test"
```

**Expected Output**:
```
VFScore computation complete.
Jobs processed: 1
Jobs successful: 1
Jobs failed: 0
Updated generations table with VFScore metrics.
```

Verify VFScore results:
```bash
# Check for vfscore_overall, vf_finish, vf_texture_identity columns
cat ~/archi3d_workspace/tables/generations.csv
```

### Step 8: Generate Report

```bash
archi3d report build --run-id "quickstart-test"
```

**Expected Output**:
```
Report generated: ~/archi3d_workspace/reports/quickstart-test/index.html
```

Open the report in your browser:
```bash
# Windows
start ~/archi3d_workspace/reports/quickstart-test/index.html

# Linux
xdg-open ~/archi3d_workspace/reports/quickstart-test/index.html

# macOS
open ~/archi3d_workspace/reports/quickstart-test/index.html
```

## Verifying Installation

Run these checks to ensure everything is installed correctly:

```bash
# 1. Check archi3d installation
archi3d --version

# 2. Check FScore installation
python -c "from fscore import compute_geometry_metrics; print('FScore: OK')"

# 3. Check VFScore installation
python -c "from vfscore import evaluate_visual_fidelity; print('VFScore: OK')"

# 4. List available commands
archi3d --help

# 5. Check configuration
python -c "from archi3d.config.loader import load_config; cfg = load_config(); print(f'Workspace: {cfg.workspace}')"
```

## Uninstalling Modules

If you need to uninstall or upgrade the proprietary wheels:

```bash
# Uninstall FScore
uv pip uninstall fscore

# Uninstall VFScore
uv pip uninstall vfscore-rt

# Uninstall archi3D
uv pip uninstall archi3d

# Remove virtual environment entirely
deactivate
rm -rf .venv
```

## Common Issues and Troubleshooting

### Issue 1: `archi3d` command not found

**Solution**: Ensure virtual environment is activated:
```bash
# Windows
.venv\Scripts\Activate.ps1

# Linux/macOS
source .venv/bin/activate
```

### Issue 2: `ModuleNotFoundError: No module named 'fscore'` or `'vfscore'`

**Solution**: Install the wheel files:
```bash
cd ../wheels
uv pip install fscore-*.whl vfscore_rt-*.whl
```

### Issue 3: Workspace not found error

**Solution**: Check `.env` file or user config file has correct `ARCHI3D_WORKSPACE` path:
```bash
cat .env
```

### Issue 4: Permission denied on Windows

**Solution**: Run PowerShell as Administrator or adjust execution policy:
```powershell
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
```

### Issue 5: GPU/CUDA errors during VFScore computation

**Solution**: VFScore uses PyTorch. Ensure CUDA-compatible PyTorch is installed:
```bash
# For CUDA 11.8
uv pip install torch torchvision --index-url https://download.pytorch.org/whl/cu118

# For CPU-only (slower)
uv pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu
```

### Issue 6: API credentials not working

**Solution**: Verify `.env` file format (no quotes needed):
```bash
# Correct
TRIPO3D_API_KEY=sk_1234567890abcdef

# Incorrect
TRIPO3D_API_KEY="sk_1234567890abcdef"
```

## Command Reference

### Catalog Commands
```bash
# Build catalog from dataset
archi3d catalog build

# Specify custom dataset path
archi3d catalog build --dataset /path/to/dataset

# Specify products JSON for enrichment
archi3d catalog build --products-json /path/to/products.json
```

### Batch Commands
```bash
# Create batch with all algorithms
archi3d batch create --run-id "my-run"

# Ecotest mode (auto-select algorithms by image count)
archi3d batch create --run-id "my-run" --algos ecotest

# Specific algorithms only
archi3d batch create --run-id "my-run" --algos "tripo3d_v2p5_multi,meshy_v4"
```

### Worker Commands
```bash
# Run worker with parallel jobs
archi3d run worker --run-id "my-run" --adapter "tripo3d_v2p5_multi" --max-parallel 3

# Dry run (test without API calls)
archi3d run worker --run-id "my-run" --adapter "tripo3d_v2p5_multi" --dry-run

# Limit number of jobs
archi3d run worker --run-id "my-run" --adapter "tripo3d_v2p5_multi" --limit 5
```

### Consolidation Commands
```bash
# Reconcile SSOT with disk artifacts
archi3d consolidate --run-id "my-run"
```

### Metrics Commands
```bash
# Compute FScore (geometry metrics)
archi3d compute fscore --run-id "my-run"

# FScore with custom point sampling
archi3d compute fscore --run-id "my-run" --n-points 200000

# FScore dry run
archi3d compute fscore --run-id "my-run" --dry-run

# Recompute existing metrics
archi3d compute fscore --run-id "my-run" --redo

# Compute VFScore (visual fidelity metrics)
archi3d compute vfscore --run-id "my-run"

# VFScore with limit
archi3d compute vfscore --run-id "my-run" --limit 5

# VFScore using source images instead of used images
archi3d compute vfscore --run-id "my-run" --use-images-from source

# VFScore dry run
archi3d compute vfscore --run-id "my-run" --dry-run

# Recompute existing metrics
archi3d compute vfscore --run-id "my-run" --redo
```

### Report Commands
```bash
# Generate HTML report
archi3d report build --run-id "my-run"
```

## License Information

- **Archi3D**: MIT License (open source)
- **FScore**: Proprietary License (see `wheels/fscore-*/LICENSE.txt`)
- **VFScore**: Proprietary License (see `wheels/vfscore_rt-*/LICENSE.txt`)

## Getting Help

### Documentation
- See `readme.md` for project overview

### Command Help
```bash
# General help
archi3d --help

# Command-specific help
archi3d catalog --help
archi3d batch --help
archi3d run --help
archi3d consolidate --help
archi3d compute --help
archi3d report --help
```

### Logs
Check logs for debugging:
```bash
# Catalog build log
cat ~/archi3d_workspace/logs/catalog_build.log

# Batch creation log
cat ~/archi3d_workspace/logs/batch_create.log

# Worker execution log
cat ~/archi3d_workspace/logs/worker.log

# Metrics computation log
cat ~/archi3d_workspace/logs/metrics.log
```

## Next Steps

After completing the quickstart test:

1. **Add More Dataset Items**: Populate `dataset/` with your full 3D object collection
2. **Configure Algorithms**: Set up API credentials for desired 3D generation services
3. **Run Full Batch**: Process entire dataset with `archi3d batch create` and `archi3d run worker`
4. **Analyze Results**: Use the generated HTML reports to compare algorithm performance
5. **Export Metrics**: Access `tables/generations.csv` for quantitative analysis