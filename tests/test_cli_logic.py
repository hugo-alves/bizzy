"""Tests for CLI logic functions."""

import sys
from dataclasses import dataclass, field
from pathlib import Path
from unittest.mock import MagicMock

import httpx
import pytest

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from fizzy_sync import (
    AuthResult,
    InitResult,
    Mapper,
    SetupResult,
    StatusInfo,
    get_status,
    init_config,
    setup_board,
    verify_auth,
)

# =============================================================================
# Test Fixtures
# =============================================================================


@dataclass
class MockConfig:
    """Mock Config for testing."""

    fizzy_base_url: str = "https://app.fizzy.do"
    fizzy_account_slug: str = "test-account"
    fizzy_api_token: str = "test-token"
    board_id: str = "board-123"
    column_mapping: dict = field(default_factory=dict)
    sync_options: dict = field(default_factory=dict)
    beads_path: Path = field(default_factory=lambda: Path("."))


class MockSyncState:
    """Mock SyncState for testing."""

    def __init__(self):
        self.synced = {}

    def checksum_for(self, beads_id: str) -> str | None:
        entry = self.synced.get(beads_id)
        return entry["checksum"] if entry else None

    def stats(self) -> dict:
        return {"total_synced": len(self.synced), "last_sync": "2026-01-01T00:00:00"}


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
    client.get_identity.return_value = {
        "accounts": [
            {
                "slug": "/test-account",
                "name": "Test Account",
                "user": {"name": "Test User", "email_address": "test@example.com"},
            }
        ]
    }
    client.get_board.return_value = {"name": "Test Board"}
    client.list_boards.return_value = [{"name": "Test Board", "id": "board-123"}]
    client.list_columns.return_value = []
    client.create_board.return_value = {"id": "new-board-456", "name": "New Board"}
    client.create_column.return_value = {"id": "col-1", "name": "Test"}
    return client


# =============================================================================
# init_config Tests
# =============================================================================


class TestInitConfig:
    """Tests for init_config function."""

    def test_creates_config_file(self, tmp_path):
        """Create config file when it doesn't exist."""
        config_path = tmp_path / ".fizzy-sync.yml"
        result = init_config(config_path)

        assert result.success is True
        assert result.config_path == config_path
        assert result.already_exists is False
        assert config_path.exists()

    def test_returns_already_exists_when_file_exists(self, tmp_path):
        """Return already_exists when config file exists."""
        config_path = tmp_path / ".fizzy-sync.yml"
        config_path.write_text("existing content")

        result = init_config(config_path, force=False)

        assert result.success is False
        assert result.already_exists is True

    def test_overwrites_with_force(self, tmp_path):
        """Overwrite existing config when force=True."""
        config_path = tmp_path / ".fizzy-sync.yml"
        config_path.write_text("old content")

        result = init_config(config_path, force=True)

        assert result.success is True
        assert result.already_exists is False
        assert "old content" not in config_path.read_text()

    def test_config_contains_template_content(self, tmp_path):
        """Created config contains expected template sections."""
        config_path = tmp_path / ".fizzy-sync.yml"
        init_config(config_path)

        content = config_path.read_text()
        assert "fizzy:" in content
        assert "board:" in content
        assert "columns:" in content
        assert "sync:" in content


# =============================================================================
# verify_auth Tests
# =============================================================================


class TestVerifyAuth:
    """Tests for verify_auth function."""

    def test_returns_error_when_no_token(self, mock_client):
        """Return error when API token not set."""
        config = MockConfig(fizzy_api_token="")
        result = verify_auth(config, mock_client)

        assert result.success is False
        assert "token not set" in result.error

    def test_successful_auth_with_matching_account(self, mock_client):
        """Successful auth returns user and account info."""
        config = MockConfig()
        result = verify_auth(config, mock_client)

        assert result.success is True
        assert result.user_name == "Test User"
        assert result.user_email == "test@example.com"
        assert result.account_name == "Test Account"

    def test_successful_auth_with_board_access(self, mock_client):
        """Auth includes board info when board_id configured."""
        config = MockConfig(board_id="board-123")
        result = verify_auth(config, mock_client)

        assert result.success is True
        assert result.board_name == "Test Board"
        assert result.board_id == "board-123"

    def test_auth_without_board_id(self, mock_client):
        """Auth succeeds without board when no board_id."""
        config = MockConfig(board_id="")
        result = verify_auth(config, mock_client)

        assert result.success is True
        assert result.board_name is None

    def test_handles_board_access_error(self, mock_client):
        """Handle board access error gracefully."""
        mock_client.get_board.side_effect = Exception("Board not found")
        config = MockConfig(board_id="invalid-board")

        result = verify_auth(config, mock_client)

        assert result.success is True  # Auth succeeded
        assert "Board access" in result.error

    def test_handles_http_401_error(self, mock_client):
        """Handle 401 unauthorized error."""
        response = MagicMock()
        response.status_code = 401
        mock_client.get_identity.side_effect = httpx.HTTPStatusError(
            "Unauthorized", request=MagicMock(), response=response
        )
        config = MockConfig()

        result = verify_auth(config, mock_client)

        assert result.success is False
        assert result.error_code == 401

    def test_handles_connection_error(self, mock_client):
        """Handle connection error."""
        mock_client.get_identity.side_effect = Exception("Connection refused")
        config = MockConfig()

        result = verify_auth(config, mock_client)

        assert result.success is False
        assert "Connection failed" in result.error


# =============================================================================
# get_status Tests
# =============================================================================


class TestGetStatus:
    """Tests for get_status function."""

    def test_returns_issue_counts(self):
        """Return correct issue counts."""
        config = MockConfig()
        reader = MockBeadsReader(
            [
                {"id": "test-1", "title": "Open", "status": "open"},
                {"id": "test-2", "title": "In Progress", "status": "in_progress"},
                {"id": "test-3", "title": "Closed", "status": "closed"},
            ]
        )
        state = MockSyncState()

        status = get_status(config, reader, state)

        assert status.open_issues == 2  # open + in_progress
        assert status.total_issues == 3

    def test_returns_sync_stats(self):
        """Return sync statistics."""
        config = MockConfig()
        reader = MockBeadsReader([])
        state = MockSyncState()
        state.synced = {"test-1": {}, "test-2": {}}

        status = get_status(config, reader, state)

        assert status.synced_count == 2
        assert status.last_sync == "2026-01-01T00:00:00"

    def test_calculates_pending_sync(self):
        """Calculate pending sync count correctly."""
        config = MockConfig()
        reader = MockBeadsReader(
            [
                {"id": "test-1", "title": "Issue 1", "status": "open"},
                {"id": "test-2", "title": "Issue 2", "status": "open"},
            ]
        )
        state = MockSyncState()
        # Only test-1 is synced with matching checksum
        state.synced = {"test-1": {"checksum": "different"}}

        status = get_status(config, reader, state)

        # Both should be pending (test-1 has wrong checksum, test-2 not synced)
        assert status.pending_sync == 2

    def test_no_pending_when_all_synced(self):
        """Return zero pending when all issues synced with current checksum."""
        config = MockConfig()
        issue = {"id": "test-1", "title": "Issue 1", "status": "open"}
        reader = MockBeadsReader([issue])
        state = MockSyncState()

        # Pre-calculate the checksum that get_status will calculate
        import hashlib
        import json

        checksum = hashlib.sha256(
            json.dumps(
                {
                    k: issue.get(k)
                    for k in [
                        "id",
                        "title",
                        "description",
                        "status",
                        "priority",
                        "issue_type",
                        "labels",
                    ]
                },
                sort_keys=True,
            ).encode()
        ).hexdigest()[:16]
        state.synced = {"test-1": {"checksum": checksum}}

        status = get_status(config, reader, state)

        assert status.pending_sync == 0


# =============================================================================
# setup_board Tests
# =============================================================================


class TestSetupBoard:
    """Tests for setup_board function."""

    def test_returns_error_when_no_board_id(self, mock_client):
        """Return error when no board ID and not creating new."""
        config = MockConfig(board_id="")
        mapper = Mapper()

        result = setup_board(config, mock_client, mapper)

        assert result.success is False
        assert "No board ID" in result.error

    def test_returns_error_when_board_not_found(self, mock_client):
        """Return error when board not found."""
        mock_client.get_board.side_effect = Exception("Not found")
        config = MockConfig(board_id="invalid")
        mapper = Mapper()

        result = setup_board(config, mock_client, mapper)

        assert result.success is False
        assert "Board not found" in result.error

    def test_creates_new_board(self, mock_client):
        """Create new board when new_board specified."""
        config = MockConfig(board_id="")
        mapper = Mapper()

        result = setup_board(config, mock_client, mapper, new_board="My New Board")

        assert result.success is True
        assert result.board_id == "new-board-456"
        mock_client.create_board.assert_called_once_with("My New Board")

    def test_creates_columns_on_empty_board(self, mock_client):
        """Create Doing and Blocked columns on empty board."""
        mock_client.list_columns.return_value = []
        config = MockConfig()
        mapper = Mapper()

        result = setup_board(config, mock_client, mapper)

        assert result.success is True
        assert "Doing" in result.columns_created
        assert "Blocked" in result.columns_created
        assert mock_client.create_column.call_count == 2

    def test_skips_existing_columns(self, mock_client):
        """Skip columns that already exist."""
        mock_client.list_columns.return_value = [
            {"name": "Doing", "id": "col-1"},
            {"name": "Blocked", "id": "col-2"},
        ]
        config = MockConfig()
        mapper = Mapper()

        result = setup_board(config, mock_client, mapper)

        assert result.success is True
        assert result.columns_created == []
        assert "Doing" in result.columns_existing
        assert "Blocked" in result.columns_existing
        mock_client.create_column.assert_not_called()

    def test_reset_requires_force(self, mock_client):
        """Reset without force returns error when columns exist."""
        mock_client.list_columns.return_value = [{"name": "Existing", "id": "col-1"}]
        config = MockConfig()
        mapper = Mapper()

        result = setup_board(config, mock_client, mapper, reset=True, force=False)

        assert result.success is False
        assert "Use --force" in result.error

    def test_reset_with_force_deletes_columns(self, mock_client):
        """Reset with force deletes existing columns."""
        existing = [{"name": "Old Column", "id": "col-old"}]
        # First call returns existing, subsequent calls return empty (after delete)
        mock_client.list_columns.side_effect = [existing, [], []]
        config = MockConfig()
        mapper = Mapper()

        result = setup_board(config, mock_client, mapper, reset=True, force=True)

        assert result.success is True
        assert "Old Column" in result.columns_deleted
        mock_client.delete_column.assert_called_once()

    def test_handles_api_error(self, mock_client):
        """Handle API errors gracefully."""
        response = MagicMock()
        response.status_code = 500
        # Error occurs after board is found, during list_columns
        mock_client.list_columns.side_effect = httpx.HTTPStatusError(
            "Server error", request=MagicMock(), response=response
        )
        config = MockConfig()
        mapper = Mapper()

        result = setup_board(config, mock_client, mapper)

        assert result.success is False
        assert "API error: 500" in result.error


# =============================================================================
# Result Type Tests
# =============================================================================


class TestResultTypes:
    """Tests for result dataclasses."""

    def test_init_result_defaults(self):
        """InitResult has correct defaults."""
        result = InitResult(success=True)
        assert result.config_path is None
        assert result.error is None
        assert result.already_exists is False

    def test_auth_result_defaults(self):
        """AuthResult has correct defaults."""
        result = AuthResult(success=True)
        assert result.user_name is None
        assert result.error_code is None

    def test_status_info_fields(self):
        """StatusInfo stores all required fields."""
        status = StatusInfo(
            open_issues=5,
            total_issues=10,
            synced_count=3,
            last_sync="2026-01-01",
            pending_sync=2,
        )
        assert status.open_issues == 5
        assert status.total_issues == 10
        assert status.pending_sync == 2

    def test_setup_result_defaults(self):
        """SetupResult has correct defaults."""
        result = SetupResult(success=True)
        assert result.columns_created == []
        assert result.columns_deleted == []
        assert result.columns_existing == []
