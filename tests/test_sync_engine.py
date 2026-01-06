"""Tests for the SyncEngine class."""

import sys
from dataclasses import dataclass, field
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from fizzy_sync import Mapper, SyncEngine


@dataclass
class MockConfig:
    """Mock Config for testing."""

    fizzy_base_url: str = "https://app.fizzy.do"
    fizzy_account_slug: str = "test-account"
    fizzy_api_token: str = "test-token"
    board_id: str = "test-board-123"
    column_mapping: dict = field(default_factory=dict)
    sync_options: dict = field(default_factory=lambda: {
        "auto_create_columns": True,
        "priority_as_tag": True,
        "type_as_tag": True,
    })
    beads_path: Path = field(default_factory=lambda: Path("."))


class MockSyncState:
    """Mock SyncState for testing."""

    def __init__(self):
        self.synced = {}

    def checksum_for(self, beads_id: str) -> str | None:
        entry = self.synced.get(beads_id)
        return entry["checksum"] if entry else None

    def card_number_for(self, beads_id: str) -> int | None:
        entry = self.synced.get(beads_id)
        return entry["card_number"] if entry else None

    def is_synced(self, beads_id: str) -> bool:
        return beads_id in self.synced

    def record_sync(self, beads_id: str, card_number: int, checksum: str) -> None:
        self.synced[beads_id] = {"card_number": card_number, "checksum": checksum}


class MockBeadsReader:
    """Mock BeadsReader for testing."""

    def __init__(self, issues: list[dict] = None):
        self._issues = issues or []

    def all_issues(self, include_closed: bool = False) -> list[dict]:
        if include_closed:
            return self._issues
        return [i for i in self._issues if i.get("status") != "closed"]


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
    return client


@pytest.fixture
def sync_engine(mock_client):
    """Create a SyncEngine with mocked dependencies."""
    config = MockConfig()
    reader = MockBeadsReader()
    state = MockSyncState()
    mapper = Mapper()

    engine = SyncEngine(config, mock_client, reader, state, mapper)
    # Pre-populate column cache
    engine.column_cache = {"Doing": "col-doing", "Blocked": "col-blocked"}
    return engine


class TestSyncEngineChecksum:
    """Tests for checksum calculation."""

    def test_checksum_is_consistent(self, sync_engine):
        """Same issue data produces same checksum."""
        issue = {
            "id": "test-1",
            "title": "Test issue",
            "description": "Description",
            "status": "open",
            "priority": 2,
            "issue_type": "task",
            "labels": [],
        }
        checksum1 = sync_engine._calculate_checksum(issue)
        checksum2 = sync_engine._calculate_checksum(issue)
        assert checksum1 == checksum2

    def test_checksum_changes_with_title(self, sync_engine):
        """Different title produces different checksum."""
        issue1 = {"id": "test-1", "title": "Title A", "status": "open"}
        issue2 = {"id": "test-1", "title": "Title B", "status": "open"}
        assert sync_engine._calculate_checksum(issue1) != sync_engine._calculate_checksum(issue2)

    def test_checksum_changes_with_status(self, sync_engine):
        """Different status produces different checksum."""
        issue1 = {"id": "test-1", "title": "Test", "status": "open"}
        issue2 = {"id": "test-1", "title": "Test", "status": "in_progress"}
        assert sync_engine._calculate_checksum(issue1) != sync_engine._calculate_checksum(issue2)

    def test_checksum_changes_with_priority(self, sync_engine):
        """Different priority produces different checksum."""
        issue1 = {"id": "test-1", "title": "Test", "status": "open", "priority": 1}
        issue2 = {"id": "test-1", "title": "Test", "status": "open", "priority": 2}
        assert sync_engine._calculate_checksum(issue1) != sync_engine._calculate_checksum(issue2)

    def test_checksum_is_16_chars(self, sync_engine):
        """Checksum is truncated to 16 characters."""
        issue = {"id": "test-1", "title": "Test", "status": "open"}
        checksum = sync_engine._calculate_checksum(issue)
        assert len(checksum) == 16


class TestSyncEngineSyncIssue:
    """Tests for sync_issue() method."""

    def test_skip_unchanged_issue(self, sync_engine):
        """Skip issue when checksum hasn't changed."""
        issue = {"id": "test-1", "title": "Test", "status": "open"}
        checksum = sync_engine._calculate_checksum(issue)

        # Pre-sync the issue
        sync_engine.state.synced["test-1"] = {"card_number": 42, "checksum": checksum}

        result = sync_engine.sync_issue(issue)
        assert result["action"] == "skipped"
        assert result["reason"] == "unchanged"

    def test_create_new_issue(self, sync_engine, mock_client):
        """Create card for new issue."""
        issue = {"id": "test-new", "title": "New issue", "status": "open", "description": ""}

        result = sync_engine.sync_issue(issue)

        assert result["action"] == "created"
        assert result["card_number"] == 42
        mock_client.create_card.assert_called_once()

    def test_update_existing_issue(self, sync_engine, mock_client):
        """Update card when issue has changed."""
        issue = {"id": "test-1", "title": "Updated title", "status": "open", "description": ""}

        # Pre-sync with old checksum
        sync_engine.state.synced["test-1"] = {"card_number": 42, "checksum": "old-checksum"}

        result = sync_engine.sync_issue(issue)

        assert result["action"] == "updated"
        assert result["card_number"] == 42
        mock_client.update_card.assert_called_once()

    def test_dry_run_create(self, sync_engine, mock_client):
        """Dry run reports what would be created."""
        issue = {"id": "test-new", "title": "New issue", "status": "open"}

        result = sync_engine.sync_issue(issue, dry_run=True)

        assert result["action"] == "created"
        assert result["dry_run"] is True
        mock_client.create_card.assert_not_called()

    def test_dry_run_update(self, sync_engine, mock_client):
        """Dry run reports what would be updated."""
        issue = {"id": "test-1", "title": "Updated", "status": "open"}
        sync_engine.state.synced["test-1"] = {"card_number": 42, "checksum": "old"}

        result = sync_engine.sync_issue(issue, dry_run=True)

        assert result["action"] == "updated"
        assert result["dry_run"] is True
        mock_client.update_card.assert_not_called()

    def test_error_handling(self, sync_engine, mock_client):
        """Return error result when API fails."""
        issue = {"id": "test-1", "title": "Test", "status": "open", "description": ""}
        mock_client.create_card.side_effect = Exception("API error")

        result = sync_engine.sync_issue(issue)

        assert result["action"] == "error"
        assert "API error" in result["error"]

    def test_triage_to_doing_column(self, sync_engine, mock_client):
        """Triage card to Doing column for in_progress status."""
        issue = {"id": "test-1", "title": "Test", "status": "in_progress", "description": ""}

        sync_engine.sync_issue(issue)

        mock_client.triage_card.assert_called_with(42, "col-doing")

    def test_triage_to_blocked_column(self, sync_engine, mock_client):
        """Triage card to Blocked column for blocked status."""
        issue = {"id": "test-1", "title": "Test", "status": "blocked", "description": ""}

        sync_engine.sync_issue(issue)

        mock_client.triage_card.assert_called_with(42, "col-blocked")

    def test_close_card_for_closed_issue(self, sync_engine, mock_client):
        """Close card when issue is closed."""
        issue = {"id": "test-1", "title": "Test", "status": "closed", "description": ""}

        sync_engine.sync_issue(issue)

        mock_client.close_card.assert_called_with(42)

    def test_state_recorded_after_sync(self, sync_engine, mock_client):
        """State is recorded after successful sync."""
        issue = {"id": "test-1", "title": "Test", "status": "open", "description": ""}

        sync_engine.sync_issue(issue)

        assert sync_engine.state.is_synced("test-1")
        assert sync_engine.state.card_number_for("test-1") == 42


class TestSyncEngineSyncAll:
    """Tests for sync_all() method."""

    def test_sync_all_empty_list(self, sync_engine):
        """Handle empty issue list."""
        sync_engine.reader._issues = []

        result = sync_engine.sync_all()

        assert result["created"] == 0
        assert result["updated"] == 0
        assert result["skipped"] == 0
        assert result["errors"] == []

    def test_sync_all_creates_new_issues(self, sync_engine, mock_client):
        """Create cards for all new issues."""
        sync_engine.reader._issues = [
            {"id": "test-1", "title": "Issue 1", "status": "open", "description": ""},
            {"id": "test-2", "title": "Issue 2", "status": "open", "description": ""},
        ]

        result = sync_engine.sync_all()

        assert result["created"] == 2
        assert result["updated"] == 0
        assert result["skipped"] == 0

    def test_sync_all_skips_unchanged(self, sync_engine, mock_client):
        """Skip unchanged issues."""
        issue = {"id": "test-1", "title": "Test", "status": "open", "description": ""}
        checksum = sync_engine._calculate_checksum(issue)
        sync_engine.state.synced["test-1"] = {"card_number": 42, "checksum": checksum}
        sync_engine.reader._issues = [issue]

        result = sync_engine.sync_all()

        assert result["created"] == 0
        assert result["skipped"] == 1

    def test_sync_all_mixed_actions(self, sync_engine, mock_client):
        """Handle mix of create, update, skip."""
        # Issue 1: unchanged (skip)
        issue1 = {"id": "test-1", "title": "Unchanged", "status": "open", "description": ""}
        checksum1 = sync_engine._calculate_checksum(issue1)
        sync_engine.state.synced["test-1"] = {"card_number": 1, "checksum": checksum1}

        # Issue 2: changed (update)
        sync_engine.state.synced["test-2"] = {"card_number": 2, "checksum": "old"}
        issue2 = {"id": "test-2", "title": "Changed", "status": "open", "description": ""}

        # Issue 3: new (create)
        issue3 = {"id": "test-3", "title": "New", "status": "open", "description": ""}

        sync_engine.reader._issues = [issue1, issue2, issue3]

        result = sync_engine.sync_all()

        assert result["skipped"] == 1
        assert result["updated"] == 1
        assert result["created"] == 1

    def test_sync_all_dry_run(self, sync_engine, mock_client):
        """Dry run doesn't call API."""
        sync_engine.reader._issues = [
            {"id": "test-1", "title": "Issue 1", "status": "open", "description": ""},
        ]

        result = sync_engine.sync_all(dry_run=True)

        assert result["created"] == 1
        mock_client.create_card.assert_not_called()

    def test_sync_all_excludes_closed_by_default(self, sync_engine, mock_client):
        """Closed issues excluded by default."""
        sync_engine.reader._issues = [
            {"id": "test-1", "title": "Open", "status": "open", "description": ""},
            {"id": "test-2", "title": "Closed", "status": "closed", "description": ""},
        ]

        result = sync_engine.sync_all(include_closed=False)

        assert result["created"] == 1  # Only the open one

    def test_sync_all_includes_closed_when_requested(self, sync_engine, mock_client):
        """Include closed issues when requested."""
        sync_engine.reader._issues = [
            {"id": "test-1", "title": "Open", "status": "open", "description": ""},
            {"id": "test-2", "title": "Closed", "status": "closed", "description": ""},
        ]

        result = sync_engine.sync_all(include_closed=True)

        assert result["created"] == 2

    def test_sync_all_collects_errors(self, sync_engine, mock_client):
        """Collect errors without stopping."""
        mock_client.create_card.side_effect = [
            {"number": 1},
            Exception("API error"),
            {"number": 3},
        ]
        sync_engine.reader._issues = [
            {"id": "test-1", "title": "Issue 1", "status": "open", "description": ""},
            {"id": "test-2", "title": "Issue 2", "status": "open", "description": ""},
            {"id": "test-3", "title": "Issue 3", "status": "open", "description": ""},
        ]

        result = sync_engine.sync_all()

        assert result["created"] == 2
        assert len(result["errors"]) == 1
        assert result["errors"][0]["beads_id"] == "test-2"


class TestSyncEngineColumns:
    """Tests for column management."""

    def test_ensure_columns_creates_missing(self, sync_engine, mock_client):
        """Create missing columns."""
        mock_client.list_columns.return_value = []  # No columns exist
        sync_engine.column_cache = {}

        with patch("fizzy_sync.console"):  # Suppress output
            sync_engine._ensure_columns_exist()

        assert mock_client.create_column.call_count == 2  # Doing and Blocked

    def test_ensure_columns_skips_existing(self, sync_engine, mock_client):
        """Don't recreate existing columns."""
        mock_client.list_columns.return_value = [
            {"name": "Doing", "id": "col-1"},
            {"name": "Blocked", "id": "col-2"},
        ]

        sync_engine._ensure_columns_exist()

        mock_client.create_column.assert_not_called()

    def test_column_cache_populated(self, sync_engine, mock_client):
        """Column cache is populated after ensure_columns."""
        mock_client.list_columns.return_value = [
            {"name": "Doing", "id": "col-doing"},
            {"name": "Blocked", "id": "col-blocked"},
        ]
        sync_engine.column_cache = {}

        sync_engine._ensure_columns_exist()

        assert sync_engine.column_cache["Doing"] == "col-doing"
        assert sync_engine.column_cache["Blocked"] == "col-blocked"

    def test_get_column_id(self, sync_engine):
        """Get column ID from cache."""
        sync_engine.column_cache = {"Doing": "col-123"}

        assert sync_engine._get_column_id("Doing") == "col-123"
        assert sync_engine._get_column_id("Unknown") == ""


class TestSyncEngineExtractCardNumber:
    """Tests for extracting card number from response."""

    def test_extract_from_number_field(self, sync_engine):
        """Extract card number from 'number' field."""
        response = {"number": 42}
        assert sync_engine._extract_card_number(response) == 42

    def test_extract_from_url_field(self, sync_engine):
        """Extract card number from 'url' field."""
        response = {"url": "https://app.fizzy.do/123/cards/42"}
        assert sync_engine._extract_card_number(response) == 42

    def test_extract_from_url_with_json_suffix(self, sync_engine):
        """Extract card number from URL with .json suffix."""
        response = {"url": "https://app.fizzy.do/123/cards/42.json"}
        assert sync_engine._extract_card_number(response) == 42

    def test_extract_raises_on_invalid_response(self, sync_engine):
        """Raise error when card number cannot be extracted."""
        response = {"something": "else"}
        with pytest.raises(ValueError, match="Could not extract card number"):
            sync_engine._extract_card_number(response)
