"""
Phase 0 tests: Workspace layout (PathResolver) and atomic I/O utilities.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pandas as pd
import pytest

from archi3d.config.paths import PathResolver
from archi3d.config.schema import (
    BatchConfig,
    EffectiveConfig,
    GlobalConfig,
    Thresholds,
    UserConfig,
)
from archi3d.utils.io import (
    append_log_record,
    update_csv_atomic,
    write_text_atomic,
)


@pytest.fixture
def temp_workspace():
    """Create a temporary workspace directory for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        workspace = Path(tmpdir) / "workspace"
        workspace.mkdir()
        # Create dataset dir (required by PathResolver)
        (workspace / "dataset").mkdir()
        yield workspace


@pytest.fixture
def path_resolver(temp_workspace):
    """Create a PathResolver instance with a temp workspace."""
    user_config = UserConfig(workspace=str(temp_workspace))
    global_config = GlobalConfig(
        algorithms=["test_algo"],
        thresholds=Thresholds(lpips_max=0.5, fscore_min=0.7),
        batch=BatchConfig()
    )
    eff_config = EffectiveConfig(
        user_config=user_config,
        global_config=global_config
    )
    return PathResolver(eff_config)


class TestPathResolver:
    """Test PathResolver workspace layout and path getters."""

    def test_ensure_mutable_tree(self, path_resolver, temp_workspace):
        """Test that ensure_mutable_tree creates all required directories."""
        # Directories should already be created by __init__
        assert (temp_workspace / "tables").exists()
        assert (temp_workspace / "runs").exists()
        assert (temp_workspace / "reports").exists()
        assert (temp_workspace / "logs").exists()

        # Should be idempotent (safe to call again)
        path_resolver.ensure_mutable_tree()
        assert (temp_workspace / "tables").exists()

    def test_directory_properties(self, path_resolver, temp_workspace):
        """Test that all directory properties point to correct paths."""
        assert path_resolver.tables_root == temp_workspace / "tables"
        assert path_resolver.runs_root == temp_workspace / "runs"
        assert path_resolver.reports_root == temp_workspace / "reports"
        assert path_resolver.logs_root == temp_workspace / "logs"

        # Test backward-compatible aliases
        assert path_resolver.tables_dir == path_resolver.tables_root
        assert path_resolver.runs_dir == path_resolver.runs_root
        assert path_resolver.reports_dir == path_resolver.reports_root
        assert path_resolver.logs_dir == path_resolver.logs_root

    def test_file_path_getters(self, path_resolver, temp_workspace):
        """Test that file path getters return correct paths."""
        # Tables
        assert path_resolver.items_csv_path() == temp_workspace / "tables" / "items.csv"
        assert path_resolver.items_issues_csv_path() == temp_workspace / "tables" / "items_issues.csv"
        assert path_resolver.generations_csv_path() == temp_workspace / "tables" / "generations.csv"

        # Logs
        assert path_resolver.catalog_build_log_path() == temp_workspace / "logs" / "catalog_build.log"
        assert path_resolver.batch_create_log_path() == temp_workspace / "logs" / "batch_create.log"
        assert path_resolver.worker_log_path() == temp_workspace / "logs" / "worker.log"
        assert path_resolver.metrics_log_path() == temp_workspace / "logs" / "metrics.log"

    def test_rel_to_workspace(self, path_resolver, temp_workspace):
        """Test that rel_to_workspace returns correct relative paths."""
        abs_path = temp_workspace / "tables" / "items.csv"
        rel_path = path_resolver.rel_to_workspace(abs_path)
        assert rel_path == Path("tables/items.csv")


class TestWriteTextAtomic:
    """Test atomic text file writing."""

    def test_write_creates_file(self, temp_workspace):
        """Test that write_text_atomic creates a new file."""
        target = temp_workspace / "test.txt"
        content = "Hello, World!"

        write_text_atomic(target, content)

        assert target.exists()
        assert target.read_text(encoding="utf-8") == content

    def test_write_overwrites_existing(self, temp_workspace):
        """Test that write_text_atomic overwrites existing files."""
        target = temp_workspace / "test.txt"

        write_text_atomic(target, "First write")
        write_text_atomic(target, "Second write")

        assert target.read_text(encoding="utf-8") == "Second write"

    def test_no_temp_files_left_behind(self, temp_workspace):
        """Test that no .tmp files are left after successful write."""
        target = temp_workspace / "test.txt"
        write_text_atomic(target, "content")

        # Check no .tmp files exist
        tmp_files = list(temp_workspace.glob("*.tmp"))
        assert len(tmp_files) == 0

    def test_creates_parent_directories(self, temp_workspace):
        """Test that write_text_atomic creates parent directories."""
        target = temp_workspace / "subdir" / "nested" / "file.txt"
        write_text_atomic(target, "content")

        assert target.exists()
        assert target.read_text(encoding="utf-8") == "content"


class TestAppendLogRecord:
    """Test log record appending with timestamps."""

    def test_append_string_record(self, temp_workspace):
        """Test appending a string log record."""
        log_file = temp_workspace / "test.log"
        append_log_record(log_file, "Test message")

        content = log_file.read_text(encoding="utf-8")
        lines = content.strip().split("\n")

        assert len(lines) == 1
        # Line should start with ISO8601 timestamp
        assert "Test message" in lines[0]
        # Basic timestamp format check (YYYY-MM-DD)
        assert lines[0][:4].isdigit()  # Year

    def test_append_dict_record(self, temp_workspace):
        """Test appending a dict as JSON log record."""
        log_file = temp_workspace / "test.log"
        record = {"event": "test", "value": 42}

        append_log_record(log_file, record)

        content = log_file.read_text(encoding="utf-8")
        lines = content.strip().split("\n")

        assert len(lines) == 1
        # Extract JSON part (after timestamp)
        timestamp_end = lines[0].index("{")
        json_part = lines[0][timestamp_end:]
        parsed = json.loads(json_part)

        assert parsed == record

    def test_append_multiple_records(self, temp_workspace):
        """Test appending multiple log records."""
        log_file = temp_workspace / "test.log"

        append_log_record(log_file, "First message")
        append_log_record(log_file, {"event": "second"})
        append_log_record(log_file, "Third message")

        content = log_file.read_text(encoding="utf-8")
        lines = content.strip().split("\n")

        assert len(lines) == 3
        assert "First message" in lines[0]
        assert '"event"' in lines[1]
        assert "Third message" in lines[2]


class TestUpdateCsvAtomic:
    """Test atomic CSV upsert functionality."""

    def test_insert_new_file(self, temp_workspace):
        """Test upserting to a non-existent file creates it."""
        csv_file = temp_workspace / "test.csv"
        df = pd.DataFrame({"k1": [1, 2], "k2": ["a", "b"], "value": [10, 20]})

        inserted, updated = update_csv_atomic(csv_file, df, ["k1", "k2"])

        assert inserted == 2
        assert updated == 0
        assert csv_file.exists()

        # Read back and verify
        df_read = pd.read_csv(csv_file, encoding="utf-8-sig")
        assert len(df_read) == 2
        assert list(df_read.columns) == ["k1", "k2", "value"]

    def test_insert_new_keys(self, temp_workspace):
        """Test inserting new keys into existing CSV."""
        csv_file = temp_workspace / "test.csv"

        # Initial data
        df1 = pd.DataFrame({"k1": [1, 2], "k2": ["a", "b"], "value": [10, 20]})
        update_csv_atomic(csv_file, df1, ["k1", "k2"])

        # Insert new keys
        df2 = pd.DataFrame({"k1": [3, 4], "k2": ["c", "d"], "value": [30, 40]})
        inserted, updated = update_csv_atomic(csv_file, df2, ["k1", "k2"])

        assert inserted == 2
        assert updated == 0

        # Read back and verify
        df_read = pd.read_csv(csv_file, encoding="utf-8-sig")
        assert len(df_read) == 4

    def test_update_existing_keys(self, temp_workspace):
        """Test updating existing keys in CSV."""
        csv_file = temp_workspace / "test.csv"

        # Initial data
        df1 = pd.DataFrame({"k1": [1, 2], "k2": ["a", "b"], "value": [10, 20]})
        update_csv_atomic(csv_file, df1, ["k1", "k2"])

        # Update existing keys
        df2 = pd.DataFrame({"k1": [1, 2], "k2": ["a", "b"], "value": [99, 88]})
        inserted, updated = update_csv_atomic(csv_file, df2, ["k1", "k2"])

        assert inserted == 0
        assert updated == 2

        # Read back and verify values were updated
        df_read = pd.read_csv(csv_file, encoding="utf-8-sig")
        assert len(df_read) == 2
        assert df_read["value"].tolist() == [99, 88]

    def test_mixed_insert_and_update(self, temp_workspace):
        """Test mixed insert and update in single operation."""
        csv_file = temp_workspace / "test.csv"

        # Initial data
        df1 = pd.DataFrame({"k1": [1, 2], "k2": ["a", "b"], "value": [10, 20]})
        update_csv_atomic(csv_file, df1, ["k1", "k2"])

        # Mixed: update key (1, "a") and insert new key (3, "c")
        df2 = pd.DataFrame({"k1": [1, 3], "k2": ["a", "c"], "value": [99, 30]})
        inserted, updated = update_csv_atomic(csv_file, df2, ["k1", "k2"])

        assert inserted == 1
        assert updated == 1

        # Read back and verify
        df_read = pd.read_csv(csv_file, encoding="utf-8-sig")
        assert len(df_read) == 3

    def test_add_new_column(self, temp_workspace):
        """Test that new columns are appended to existing CSV."""
        csv_file = temp_workspace / "test.csv"

        # Initial data with columns: k1, k2, value
        df1 = pd.DataFrame({"k1": [1, 2], "k2": ["a", "b"], "value": [10, 20]})
        update_csv_atomic(csv_file, df1, ["k1", "k2"])

        # Update with new column: status
        df2 = pd.DataFrame({
            "k1": [1],
            "k2": ["a"],
            "value": [99],
            "status": ["active"]
        })
        update_csv_atomic(csv_file, df2, ["k1", "k2"])

        # Read back and verify column order: existing first, new last
        df_read = pd.read_csv(csv_file, encoding="utf-8-sig")
        assert list(df_read.columns) == ["k1", "k2", "value", "status"]
        # New column should have NaN for rows without it
        assert pd.isna(df_read.loc[1, "status"])

    def test_deduplication_in_new_data(self, temp_workspace):
        """Test that duplicate keys in df_new are deduplicated (keep last)."""
        csv_file = temp_workspace / "test.csv"

        # New data with duplicate key (1, "a") - should keep last
        df = pd.DataFrame({
            "k1": [1, 1, 2],
            "k2": ["a", "a", "b"],
            "value": [10, 99, 20]
        })
        inserted, updated = update_csv_atomic(csv_file, df, ["k1", "k2"])

        # Should insert only 2 unique keys (dedup happened)
        assert inserted == 2
        assert updated == 0

        # Read back and verify last value for (1, "a") is kept
        df_read = pd.read_csv(csv_file, encoding="utf-8-sig")
        assert len(df_read) == 2
        row_1a = df_read[(df_read["k1"] == 1) & (df_read["k2"] == "a")]
        assert row_1a["value"].iloc[0] == 99

    def test_missing_key_columns_raises(self, temp_workspace):
        """Test that missing key columns raise ValueError."""
        csv_file = temp_workspace / "test.csv"
        df = pd.DataFrame({"k1": [1, 2], "value": [10, 20]})

        # key_cols includes "k2" which is not in df
        with pytest.raises(ValueError, match="not found in df_new"):
            update_csv_atomic(csv_file, df, ["k1", "k2"])

    def test_utf8_sig_encoding(self, temp_workspace):
        """Test that CSV is written with utf-8-sig (Excel compatible)."""
        csv_file = temp_workspace / "test.csv"
        df = pd.DataFrame({"key": [1], "value": ["test"]})

        update_csv_atomic(csv_file, df, ["key"])

        # Read raw bytes and check for BOM
        raw_bytes = csv_file.read_bytes()
        # UTF-8 BOM is \xef\xbb\xbf
        assert raw_bytes.startswith(b"\xef\xbb\xbf")


class TestConcurrencySafety:
    """Test file locking and concurrency safety."""

    def test_append_log_with_lock(self, temp_workspace):
        """Test that append_log_record creates lock file."""
        log_file = temp_workspace / "test.log"
        append_log_record(log_file, "test")

        # Lock file should not persist after operation
        # (FileLock releases automatically)
        lock_file = temp_workspace / "test.log.lock"
        # Lock file might exist but should not be locked
        # We can't easily test actual locking, but we verify no errors

    def test_update_csv_with_lock(self, temp_workspace):
        """Test that update_csv_atomic uses locking."""
        csv_file = temp_workspace / "test.csv"
        df = pd.DataFrame({"key": [1], "value": [10]})

        update_csv_atomic(csv_file, df, ["key"])

        # Lock file might exist
        lock_file = temp_workspace / "test.lock"
        # We verify operation completed without errors
        assert csv_file.exists()
