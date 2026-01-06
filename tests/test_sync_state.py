"""Tests for the SyncState class."""

import json
import sys
from datetime import datetime
from pathlib import Path

import pytest

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from fizzy_sync import SyncState


@pytest.fixture
def temp_beads_dir(tmp_path):
    """Create a temporary .beads directory."""
    beads_dir = tmp_path / ".beads"
    beads_dir.mkdir()
    return tmp_path


@pytest.fixture
def sync_state(temp_beads_dir):
    """Create a SyncState instance with temporary directory."""
    return SyncState(temp_beads_dir)


class TestSyncStateInit:
    """Tests for SyncState initialization."""

    def test_creates_empty_state_when_no_file(self, temp_beads_dir):
        """Create empty state when no state file exists."""
        state = SyncState(temp_beads_dir)
        assert state.state == {"synced_issues": {}, "last_sync": None}

    def test_loads_existing_state_file(self, temp_beads_dir):
        """Load existing state from file."""
        state_file = temp_beads_dir / ".beads" / ".fizzy-sync-state.json"
        state_data = {
            "synced_issues": {
                "test-1": {"card_number": 42, "checksum": "abc123", "synced_at": "2026-01-01T00:00:00"}
            },
            "last_sync": "2026-01-01T00:00:00",
        }
        state_file.write_text(json.dumps(state_data))

        state = SyncState(temp_beads_dir)

        assert state.state["synced_issues"]["test-1"]["card_number"] == 42
        assert state.state["last_sync"] == "2026-01-01T00:00:00"

    def test_creates_beads_dir_if_missing(self, tmp_path):
        """Create .beads directory if it doesn't exist."""
        state = SyncState(tmp_path)
        state.record_sync("test-1", 42, "abc123")

        state_file = tmp_path / ".beads" / ".fizzy-sync-state.json"
        assert state_file.exists()


class TestSyncStateIsSynced:
    """Tests for is_synced() method."""

    def test_returns_false_for_unsynced_issue(self, sync_state):
        """Return False for issue not yet synced."""
        assert sync_state.is_synced("test-new") is False

    def test_returns_true_for_synced_issue(self, sync_state):
        """Return True for synced issue."""
        sync_state.record_sync("test-1", 42, "abc123")
        assert sync_state.is_synced("test-1") is True


class TestSyncStateCardNumberFor:
    """Tests for card_number_for() method."""

    def test_returns_none_for_unsynced_issue(self, sync_state):
        """Return None for issue not yet synced."""
        assert sync_state.card_number_for("test-new") is None

    def test_returns_card_number_for_synced_issue(self, sync_state):
        """Return card number for synced issue."""
        sync_state.record_sync("test-1", 42, "abc123")
        assert sync_state.card_number_for("test-1") == 42


class TestSyncStateChecksumFor:
    """Tests for checksum_for() method."""

    def test_returns_none_for_unsynced_issue(self, sync_state):
        """Return None for issue not yet synced."""
        assert sync_state.checksum_for("test-new") is None

    def test_returns_checksum_for_synced_issue(self, sync_state):
        """Return checksum for synced issue."""
        sync_state.record_sync("test-1", 42, "abc123")
        assert sync_state.checksum_for("test-1") == "abc123"


class TestSyncStateRecordSync:
    """Tests for record_sync() method."""

    def test_records_new_sync(self, sync_state):
        """Record sync for new issue."""
        sync_state.record_sync("test-1", 42, "abc123")

        assert sync_state.is_synced("test-1")
        assert sync_state.card_number_for("test-1") == 42
        assert sync_state.checksum_for("test-1") == "abc123"

    def test_updates_existing_sync(self, sync_state):
        """Update sync for existing issue."""
        sync_state.record_sync("test-1", 42, "abc123")
        sync_state.record_sync("test-1", 42, "def456")  # New checksum

        assert sync_state.checksum_for("test-1") == "def456"

    def test_updates_last_sync_timestamp(self, sync_state):
        """Update last_sync timestamp on record."""
        before = datetime.now()
        sync_state.record_sync("test-1", 42, "abc123")
        after = datetime.now()

        last_sync = sync_state.last_sync_time()
        assert last_sync is not None
        assert before <= last_sync <= after

    def test_persists_to_file(self, sync_state, temp_beads_dir):
        """Persist state to file on record."""
        sync_state.record_sync("test-1", 42, "abc123")

        state_file = temp_beads_dir / ".beads" / ".fizzy-sync-state.json"
        assert state_file.exists()

        saved_state = json.loads(state_file.read_text())
        assert saved_state["synced_issues"]["test-1"]["card_number"] == 42


class TestSyncStateLastSyncTime:
    """Tests for last_sync_time() method."""

    def test_returns_none_when_never_synced(self, sync_state):
        """Return None when no sync has occurred."""
        assert sync_state.last_sync_time() is None

    def test_returns_datetime_after_sync(self, sync_state):
        """Return datetime after sync."""
        sync_state.record_sync("test-1", 42, "abc123")

        last_sync = sync_state.last_sync_time()
        assert isinstance(last_sync, datetime)


class TestSyncStateStats:
    """Tests for stats() method."""

    def test_empty_stats(self, sync_state):
        """Return zero count for empty state."""
        stats = sync_state.stats()
        assert stats["total_synced"] == 0
        assert stats["last_sync"] is None

    def test_stats_after_syncs(self, sync_state):
        """Return correct counts after syncs."""
        sync_state.record_sync("test-1", 1, "abc")
        sync_state.record_sync("test-2", 2, "def")
        sync_state.record_sync("test-3", 3, "ghi")

        stats = sync_state.stats()
        assert stats["total_synced"] == 3
        assert stats["last_sync"] is not None


class TestSyncStatePersistence:
    """Tests for state persistence across instances."""

    def test_state_survives_reload(self, temp_beads_dir):
        """State persists across instances."""
        # First instance - record sync
        state1 = SyncState(temp_beads_dir)
        state1.record_sync("test-1", 42, "abc123")
        state1.record_sync("test-2", 43, "def456")

        # Second instance - should load from file
        state2 = SyncState(temp_beads_dir)

        assert state2.is_synced("test-1")
        assert state2.is_synced("test-2")
        assert state2.card_number_for("test-1") == 42
        assert state2.card_number_for("test-2") == 43

    def test_state_file_is_valid_json(self, sync_state, temp_beads_dir):
        """State file is valid JSON."""
        sync_state.record_sync("test-1", 42, "abc123")

        state_file = temp_beads_dir / ".beads" / ".fizzy-sync-state.json"
        content = state_file.read_text()

        # Should not raise
        parsed = json.loads(content)
        assert "synced_issues" in parsed
        assert "last_sync" in parsed


class TestSyncStateEdgeCases:
    """Tests for edge cases and error handling."""

    def test_handles_corrupted_state_file(self, temp_beads_dir):
        """Handle corrupted state file gracefully."""
        state_file = temp_beads_dir / ".beads" / ".fizzy-sync-state.json"
        state_file.write_text("not valid json {{{")

        # Should raise JSONDecodeError - we don't silently ignore corruption
        with pytest.raises(json.JSONDecodeError):
            SyncState(temp_beads_dir)

    def test_handles_empty_state_file(self, temp_beads_dir):
        """Handle empty state file."""
        state_file = temp_beads_dir / ".beads" / ".fizzy-sync-state.json"
        state_file.write_text("")

        # Should raise JSONDecodeError for empty file
        with pytest.raises(json.JSONDecodeError):
            SyncState(temp_beads_dir)

    def test_records_sync_timestamp(self, sync_state):
        """Record synced_at timestamp for each issue."""
        sync_state.record_sync("test-1", 42, "abc123")

        entry = sync_state.state["synced_issues"]["test-1"]
        assert "synced_at" in entry
        # Should be valid ISO format
        datetime.fromisoformat(entry["synced_at"])

    def test_multiple_issues_independent(self, sync_state):
        """Multiple issues tracked independently."""
        sync_state.record_sync("issue-a", 1, "aaa")
        sync_state.record_sync("issue-b", 2, "bbb")
        sync_state.record_sync("issue-c", 3, "ccc")

        # Each has its own state
        assert sync_state.card_number_for("issue-a") == 1
        assert sync_state.card_number_for("issue-b") == 2
        assert sync_state.card_number_for("issue-c") == 3

        # Updating one doesn't affect others
        sync_state.record_sync("issue-b", 2, "bbb-updated")
        assert sync_state.checksum_for("issue-a") == "aaa"
        assert sync_state.checksum_for("issue-b") == "bbb-updated"
        assert sync_state.checksum_for("issue-c") == "ccc"
