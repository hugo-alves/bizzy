"""Integration tests for end-to-end sync flow."""

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from fizzy_sync import BeadsReader, Mapper, SyncEngine, SyncState


def create_test_db(beads_dir, issues):
    """Create test SQLite database with issues."""
    import sqlite3

    db_path = beads_dir / ".beads" / "beads.db"
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS issues (
            id TEXT PRIMARY KEY,
            title TEXT,
            description TEXT,
            status TEXT,
            priority INTEGER,
            issue_type TEXT,
            labels TEXT,
            created_at TEXT,
            updated_at TEXT,
            closed_at TEXT
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS blocked_issues_cache (
            issue_id TEXT PRIMARY KEY
        )
    """)

    for issue in issues:
        cursor.execute(
            "INSERT INTO issues (id, title, description, status, priority, issue_type, labels) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                issue["id"],
                issue["title"],
                issue.get("description", ""),
                issue.get("status", "open"),
                issue.get("priority", 2),
                issue.get("issue_type", "task"),
                "[]",
            ),
        )

    conn.commit()
    conn.close()


def add_to_blocked_cache(beads_dir, issue_id):
    """Add issue to blocked cache."""
    import sqlite3

    db_path = beads_dir / ".beads" / "beads.db"
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("INSERT INTO blocked_issues_cache (issue_id) VALUES (?)", (issue_id,))
    conn.commit()
    conn.close()


@pytest.fixture
def temp_beads_dir(tmp_path):
    """Create temporary beads directory."""
    beads_dir = tmp_path / ".beads"
    beads_dir.mkdir()
    return tmp_path


@pytest.fixture
def mock_client():
    """Create a mock FizzyClient."""
    client = MagicMock()
    client.list_columns.return_value = [
        {"name": "Doing", "id": "col-doing"},
        {"name": "Blocked", "id": "col-blocked"},
    ]
    client.create_card.return_value = {"number": 42}
    client.triage_card.return_value = None
    client.update_card.return_value = None
    client.close_card.return_value = None
    client.reopen_card.return_value = None
    client.toggle_tag.return_value = None
    client.create_column.return_value = {"id": "new-col", "name": "New"}
    return client


@pytest.fixture
def config(temp_beads_dir):
    """Create test config."""

    class TestConfig:
        fizzy_base_url = "http://test.local"
        fizzy_account_slug = "test-account"
        fizzy_api_token = "test-token"
        board_id = "board-123"
        column_mapping = {"in_progress": "Doing", "blocked": "Blocked"}
        sync_options = {
            "auto_create_columns": True,
            "priority_as_tag": True,
            "type_as_tag": True,
        }
        beads_path = temp_beads_dir

    return TestConfig()


class TestEndToEndSync:
    """Integration tests using real BeadsReader with mock client."""

    def test_fresh_sync_creates_cards(self, temp_beads_dir, mock_client, config):
        """Fresh sync creates cards for all open issues."""
        create_test_db(
            temp_beads_dir,
            [
                {"id": "test-1", "title": "First Issue"},
                {"id": "test-2", "title": "Second Issue"},
                {"id": "test-3", "title": "Third Issue"},
            ],
        )

        reader = BeadsReader(config.beads_path)
        state = SyncState(config.beads_path)
        mapper = Mapper(config.column_mapping)
        engine = SyncEngine(config, mock_client, reader, state, mapper)

        result = engine.sync_all()

        assert result["created"] == 3
        assert result["updated"] == 0
        assert result["skipped"] == 0
        assert len(result["errors"]) == 0
        assert mock_client.create_card.call_count == 3

    def test_incremental_sync_skips_unchanged(self, temp_beads_dir, mock_client, config):
        """Second sync skips unchanged issues."""
        create_test_db(temp_beads_dir, [{"id": "test-1", "title": "Test Issue"}])

        reader = BeadsReader(config.beads_path)
        state = SyncState(config.beads_path)
        mapper = Mapper(config.column_mapping)
        engine = SyncEngine(config, mock_client, reader, state, mapper)

        # First sync creates
        result1 = engine.sync_all()
        assert result1["created"] == 1

        # Second sync skips (no changes)
        result2 = engine.sync_all()
        assert result2["created"] == 0
        assert result2["updated"] == 0
        assert result2["skipped"] == 1

        # Only one create_card call total
        assert mock_client.create_card.call_count == 1

    def test_status_in_progress_triages_to_doing(self, temp_beads_dir, mock_client, config):
        """Issue with in_progress status is triaged to Doing column."""
        create_test_db(
            temp_beads_dir,
            [{"id": "test-1", "title": "Working Issue", "status": "in_progress"}],
        )

        reader = BeadsReader(config.beads_path)
        state = SyncState(config.beads_path)
        mapper = Mapper(config.column_mapping)
        engine = SyncEngine(config, mock_client, reader, state, mapper)

        engine.sync_all()

        mock_client.triage_card.assert_called_with(42, "col-doing")

    def test_blocked_from_cache_triages_to_blocked(self, temp_beads_dir, mock_client, config):
        """Issue in blocked_issues_cache is triaged to Blocked column."""
        create_test_db(temp_beads_dir, [{"id": "test-1", "title": "Blocked Issue"}])
        add_to_blocked_cache(temp_beads_dir, "test-1")

        reader = BeadsReader(config.beads_path)
        state = SyncState(config.beads_path)
        mapper = Mapper(config.column_mapping)
        engine = SyncEngine(config, mock_client, reader, state, mapper)

        engine.sync_all()

        mock_client.triage_card.assert_called_with(42, "col-blocked")

    def test_closed_issue_closes_card(self, temp_beads_dir, mock_client, config):
        """Closed issue triggers close_card."""
        create_test_db(
            temp_beads_dir,
            [{"id": "test-1", "title": "Closed Issue", "status": "closed"}],
        )

        reader = BeadsReader(config.beads_path)
        state = SyncState(config.beads_path)
        mapper = Mapper(config.column_mapping)
        engine = SyncEngine(config, mock_client, reader, state, mapper)

        engine.sync_all(include_closed=True)

        mock_client.close_card.assert_called_with(42)

    def test_dry_run_does_not_call_api(self, temp_beads_dir, mock_client, config):
        """Dry run reports actions without API calls."""
        create_test_db(temp_beads_dir, [{"id": "test-1", "title": "Test Issue"}])

        reader = BeadsReader(config.beads_path)
        state = SyncState(config.beads_path)
        mapper = Mapper(config.column_mapping)
        engine = SyncEngine(config, mock_client, reader, state, mapper)

        result = engine.sync_all(dry_run=True)

        assert result["created"] == 1
        mock_client.create_card.assert_not_called()
        assert not state.is_synced("test-1")

    def test_error_in_one_does_not_stop_others(self, temp_beads_dir, mock_client, config):
        """Error syncing one issue doesn't stop processing of others."""
        create_test_db(
            temp_beads_dir,
            [
                {"id": "test-1", "title": "Issue 1"},
                {"id": "test-2", "title": "Issue 2"},
                {"id": "test-3", "title": "Issue 3"},
            ],
        )

        # Second call fails
        mock_client.create_card.side_effect = [
            {"number": 1},
            Exception("API error"),
            {"number": 3},
        ]

        reader = BeadsReader(config.beads_path)
        state = SyncState(config.beads_path)
        mapper = Mapper(config.column_mapping)
        engine = SyncEngine(config, mock_client, reader, state, mapper)

        result = engine.sync_all()

        assert result["created"] == 2
        assert len(result["errors"]) == 1
        assert result["errors"][0]["beads_id"] == "test-2"

    def test_tags_applied_to_cards(self, temp_beads_dir, mock_client, config):
        """Priority and type tags are applied to cards."""
        create_test_db(
            temp_beads_dir,
            [{"id": "test-1", "title": "Bug", "priority": 0, "issue_type": "bug"}],
        )

        reader = BeadsReader(config.beads_path)
        state = SyncState(config.beads_path)
        mapper = Mapper(config.column_mapping)
        engine = SyncEngine(config, mock_client, reader, state, mapper)

        engine.sync_all()

        # Should toggle P0 and bug tags
        tag_calls = [call[0][1] for call in mock_client.toggle_tag.call_args_list]
        assert "P0" in tag_calls
        assert "bug" in tag_calls

    def test_state_persists_across_syncs(self, temp_beads_dir, mock_client, config):
        """State is persisted and reloaded correctly."""
        create_test_db(temp_beads_dir, [{"id": "test-1", "title": "Test Issue"}])

        # First sync
        reader1 = BeadsReader(config.beads_path)
        state1 = SyncState(config.beads_path)
        mapper1 = Mapper(config.column_mapping)
        engine1 = SyncEngine(config, mock_client, reader1, state1, mapper1)
        engine1.sync_all()

        # Create new state instance (simulates new session)
        state2 = SyncState(config.beads_path)
        assert state2.is_synced("test-1")
        assert state2.card_number_for("test-1") == 42


class TestColumnManagement:
    """Tests for column creation and management."""

    def test_creates_missing_columns(self, temp_beads_dir, mock_client, config):
        """Creates Doing and Blocked columns if missing."""
        create_test_db(temp_beads_dir, [])
        mock_client.list_columns.return_value = []  # No columns exist

        reader = BeadsReader(config.beads_path)
        state = SyncState(config.beads_path)
        mapper = Mapper(config.column_mapping)
        engine = SyncEngine(config, mock_client, reader, state, mapper)

        engine._ensure_columns_exist()

        # Should create both Doing and Blocked
        assert mock_client.create_column.call_count == 2
        call_names = [call[1]["name"] for call in mock_client.create_column.call_args_list]
        assert "Doing" in call_names
        assert "Blocked" in call_names

    def test_skips_existing_columns(self, temp_beads_dir, mock_client, config):
        """Doesn't recreate existing columns."""
        create_test_db(temp_beads_dir, [])
        # Columns already exist
        mock_client.list_columns.return_value = [
            {"name": "Doing", "id": "col-1"},
            {"name": "Blocked", "id": "col-2"},
        ]

        reader = BeadsReader(config.beads_path)
        state = SyncState(config.beads_path)
        mapper = Mapper(config.column_mapping)
        engine = SyncEngine(config, mock_client, reader, state, mapper)

        engine._ensure_columns_exist()

        mock_client.create_column.assert_not_called()
