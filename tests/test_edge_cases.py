"""Tests for error handling and edge cases."""

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from fizzy_sync import Mapper, SyncEngine

# Note: FizzyClient network and HTTP error tests are in test_client.py
# which properly handles the retry mechanism


# =============================================================================
# Mapper Edge Cases Tests
# =============================================================================


class TestMapperEdgeCases:
    """Tests for Mapper edge cases."""

    @pytest.fixture
    def mapper(self):
        return Mapper()

    def test_empty_title(self, mapper):
        """Handle empty title."""
        issue = {"id": "test-1", "title": "", "description": "Some desc"}
        card_data = mapper.beads_to_fizzy_card(issue)
        assert card_data["title"] == ""

    def test_very_long_title(self, mapper):
        """Handle very long title (Fizzy may truncate)."""
        long_title = "A" * 1000
        issue = {"id": "test-1", "title": long_title}
        card_data = mapper.beads_to_fizzy_card(issue)
        assert card_data["title"] == long_title

    def test_very_long_description(self, mapper):
        """Handle very long description."""
        long_desc = "B" * 10000
        issue = {"id": "test-1", "title": "Test", "description": long_desc}
        card_data = mapper.beads_to_fizzy_card(issue)
        assert long_desc in card_data["description"]

    def test_special_characters_in_title(self, mapper):
        """Handle special characters in title."""
        issue = {"id": "test-1", "title": "Test <script>alert('xss')</script> & \"quotes\""}
        card_data = mapper.beads_to_fizzy_card(issue)
        # Should pass through unchanged - Fizzy handles escaping
        assert "<script>" in card_data["title"]

    def test_unicode_in_title(self, mapper):
        """Handle Unicode characters in title."""
        issue = {"id": "test-1", "title": "Test with unicode: cafe\u0301 naive\u0308"}
        card_data = mapper.beads_to_fizzy_card(issue)
        assert "cafe\u0301" in card_data["title"]

    def test_emoji_in_title(self, mapper):
        """Handle emoji in title."""
        issue = {"id": "test-1", "title": "Bug fix 🐛 complete ✅"}
        card_data = mapper.beads_to_fizzy_card(issue)
        assert "🐛" in card_data["title"]
        assert "✅" in card_data["title"]

    def test_emoji_in_description(self, mapper):
        """Handle emoji in description."""
        issue = {"id": "test-1", "title": "Test", "description": "Fixed the 🐛 bug!"}
        card_data = mapper.beads_to_fizzy_card(issue)
        assert "🐛" in card_data["description"]

    def test_newlines_in_description(self, mapper):
        """Handle newlines in description."""
        issue = {
            "id": "test-1",
            "title": "Test",
            "description": "Line 1\nLine 2\n\nLine 4",
        }
        card_data = mapper.beads_to_fizzy_card(issue)
        assert "Line 1\nLine 2" in card_data["description"]

    def test_markdown_in_description(self, mapper):
        """Handle markdown formatting in description."""
        issue = {
            "id": "test-1",
            "title": "Test",
            "description": "# Header\n\n- Item 1\n- Item 2\n\n```python\ncode()\n```",
        }
        card_data = mapper.beads_to_fizzy_card(issue)
        assert "# Header" in card_data["description"]
        assert "```python" in card_data["description"]

    def test_none_description_becomes_empty(self, mapper):
        """None description becomes empty string."""
        issue = {"id": "test-1", "title": "Test", "description": None}
        card_data = mapper.beads_to_fizzy_card(issue)
        assert "[beads:test-1]" in card_data["description"]

    def test_missing_description_key(self, mapper):
        """Missing description key is handled."""
        issue = {"id": "test-1", "title": "Test"}
        card_data = mapper.beads_to_fizzy_card(issue)
        assert "[beads:test-1]" in card_data["description"]

    def test_priority_none_becomes_default(self, mapper):
        """None priority becomes default (no P tag)."""
        issue = {"id": "test-1", "title": "Test", "priority": None}
        tags = mapper.tags_for_issue(issue)
        # No P tag should be added
        assert not any(t.startswith("P") for t in tags)

    def test_priority_out_of_range(self, mapper):
        """Priority outside 0-4 still maps."""
        issue = {"id": "test-1", "title": "Test", "priority": 10}
        tags = mapper.tags_for_issue(issue)
        assert "P10" in tags  # Mapper doesn't enforce range

    def test_unknown_issue_type(self, mapper):
        """Unknown issue type becomes tag."""
        issue = {"id": "test-1", "title": "Test", "issue_type": "unknown_type"}
        tags = mapper.tags_for_issue(issue)
        assert "unknown_type" in tags

    def test_labels_with_special_characters(self, mapper):
        """Handle labels with special characters."""
        issue = {"id": "test-1", "title": "Test", "labels": ["label-with-dash", "label:colon"]}
        tags = mapper.tags_for_issue(issue)
        assert "label-with-dash" in tags
        assert "label:colon" in tags

    def test_beads_marker_always_present(self, mapper):
        """Beads marker is always in card description."""
        issue = {"id": "test-123", "title": "Test", "description": ""}
        card_data = mapper.beads_to_fizzy_card(issue)
        assert "[beads:test-123]" in card_data["description"]

    def test_beads_marker_appended_to_description(self, mapper):
        """Beads marker is always appended to description."""
        issue = {
            "id": "test-123",
            "title": "Test",
            "description": "Original desc",
        }
        card_data = mapper.beads_to_fizzy_card(issue)
        # Marker is always appended at the end
        assert card_data["description"].endswith("[beads:test-123]")


# =============================================================================
# SyncEngine Edge Cases Tests
# =============================================================================


class MockConfig:
    """Mock Config for testing."""

    def __init__(self):
        self.fizzy_base_url = "https://app.fizzy.do"
        self.fizzy_account_slug = "test-account"
        self.fizzy_api_token = "test-token"
        self.board_id = "test-board-123"
        self.column_mapping = {}
        self.sync_options = {
            "auto_create_columns": True,
            "priority_as_tag": True,
            "type_as_tag": True,
        }
        self.beads_path = Path(".")


class MockSyncState:
    """Mock SyncState for testing."""

    def __init__(self):
        self.synced = {}

    def checksum_for(self, beads_id: str):
        entry = self.synced.get(beads_id)
        return entry["checksum"] if entry else None

    def card_number_for(self, beads_id: str):
        entry = self.synced.get(beads_id)
        return entry["card_number"] if entry else None

    def is_synced(self, beads_id: str):
        return beads_id in self.synced

    def record_sync(self, beads_id: str, card_number: int, checksum: str):
        self.synced[beads_id] = {"card_number": card_number, "checksum": checksum}


class MockBeadsReader:
    """Mock BeadsReader for testing."""

    def __init__(self, issues=None):
        self._issues = issues or []

    def all_issues(self, include_closed=False):
        if include_closed:
            return self._issues
        return [i for i in self._issues if i.get("status") != "closed"]


class TestSyncEngineEdgeCases:
    """Tests for SyncEngine edge cases."""

    @pytest.fixture
    def mock_client(self):
        client = MagicMock()
        client.list_columns.return_value = [
            {"name": "Doing", "id": "col-doing"},
            {"name": "Blocked", "id": "col-blocked"},
        ]
        client.create_card.return_value = {"number": 42}
        client.triage_card.return_value = None
        client.update_card.return_value = None
        client.close_card.return_value = None
        client.toggle_tag.return_value = None
        return client

    @pytest.fixture
    def sync_engine(self, mock_client):
        config = MockConfig()
        reader = MockBeadsReader()
        state = MockSyncState()
        mapper = Mapper()

        engine = SyncEngine(config, mock_client, reader, state, mapper)
        engine.column_cache = {"Doing": "col-doing", "Blocked": "col-blocked"}
        return engine

    def test_issue_with_empty_id(self, sync_engine, mock_client):
        """Handle issue with empty ID."""
        issue = {"id": "", "title": "Test", "status": "open", "description": ""}
        result = sync_engine.sync_issue(issue)
        # Should still create - ID validation is on Beads side
        assert result["action"] == "created"

    def test_issue_with_special_id(self, sync_engine, mock_client):
        """Handle issue with special characters in ID."""
        issue = {"id": "test-123/456", "title": "Test", "status": "open", "description": ""}
        result = sync_engine.sync_issue(issue)
        assert result["action"] == "created"

    def test_sync_with_api_returning_unexpected_format(self, sync_engine, mock_client):
        """Handle unexpected API response format."""
        mock_client.create_card.return_value = {"unexpected": "format"}
        issue = {"id": "test-1", "title": "Test", "status": "open", "description": ""}

        result = sync_engine.sync_issue(issue)
        # Should error since card number can't be extracted
        assert result["action"] == "error"

    def test_sync_with_zero_card_number(self, sync_engine, mock_client):
        """Handle card number of 0."""
        mock_client.create_card.return_value = {"number": 0}
        issue = {"id": "test-1", "title": "Test", "status": "open", "description": ""}

        result = sync_engine.sync_issue(issue)
        # 0 is valid card number
        assert result["action"] == "created"
        assert result["card_number"] == 0

    def test_sync_with_negative_card_number(self, sync_engine, mock_client):
        """Handle negative card number (invalid but possible)."""
        mock_client.create_card.return_value = {"number": -1}
        issue = {"id": "test-1", "title": "Test", "status": "open", "description": ""}

        result = sync_engine.sync_issue(issue)
        # Should still work - validation is on Fizzy side
        assert result["action"] == "created"
        assert result["card_number"] == -1

    def test_column_not_in_cache(self, sync_engine, mock_client):
        """Handle column not in cache."""
        issue = {"id": "test-1", "title": "Test", "status": "in_progress", "description": ""}
        sync_engine.column_cache = {}  # Empty cache

        result = sync_engine.sync_issue(issue)
        # Should create but triage may fail/skip
        assert result["action"] == "created"

    def test_sync_all_with_large_batch(self, sync_engine, mock_client):
        """Handle large batch of issues."""
        sync_engine.reader._issues = [
            {"id": f"test-{i}", "title": f"Issue {i}", "status": "open", "description": ""}
            for i in range(100)
        ]

        result = sync_engine.sync_all()
        assert result["created"] == 100

    def test_sync_preserves_order(self, sync_engine, mock_client):
        """Issues processed in order."""
        call_order = []

        def track_create(board_id, title, description):
            call_order.append(title)
            return {"number": len(call_order)}

        mock_client.create_card.side_effect = track_create

        sync_engine.reader._issues = [
            {"id": "a", "title": "First", "status": "open", "description": ""},
            {"id": "b", "title": "Second", "status": "open", "description": ""},
            {"id": "c", "title": "Third", "status": "open", "description": ""},
        ]

        sync_engine.sync_all()
        assert call_order == ["First", "Second", "Third"]

    def test_partial_failure_continues(self, sync_engine, mock_client):
        """Continue processing after individual failure."""
        call_count = 0

        def create_with_failure(board_id, title, description):
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                raise Exception("API error on second")
            return {"number": call_count}

        mock_client.create_card.side_effect = create_with_failure

        sync_engine.reader._issues = [
            {"id": "a", "title": "A", "status": "open", "description": ""},
            {"id": "b", "title": "B", "status": "open", "description": ""},
            {"id": "c", "title": "C", "status": "open", "description": ""},
        ]

        result = sync_engine.sync_all()
        assert result["created"] == 2
        assert len(result["errors"]) == 1
        assert result["errors"][0]["beads_id"] == "b"


class TestSyncEngineStatusEdgeCases:
    """Tests for status-related edge cases."""

    @pytest.fixture
    def mock_client(self):
        client = MagicMock()
        client.list_columns.return_value = []
        client.create_card.return_value = {"number": 42}
        client.triage_card.return_value = None
        client.close_card.return_value = None
        client.reopen_card.return_value = None
        return client

    @pytest.fixture
    def sync_engine(self, mock_client):
        config = MockConfig()
        reader = MockBeadsReader()
        state = MockSyncState()
        mapper = Mapper()

        engine = SyncEngine(config, mock_client, reader, state, mapper)
        engine.column_cache = {}
        return engine

    def test_unknown_status(self, sync_engine, mock_client):
        """Handle unknown status value."""
        issue = {"id": "test-1", "title": "Test", "status": "unknown_status", "description": ""}

        result = sync_engine.sync_issue(issue)
        # Should create, status just won't trigger column move
        assert result["action"] == "created"

    def test_null_status(self, sync_engine, mock_client):
        """Handle null status."""
        issue = {"id": "test-1", "title": "Test", "status": None, "description": ""}

        result = sync_engine.sync_issue(issue)
        assert result["action"] == "created"

    def test_status_transition_open_to_closed(self, sync_engine, mock_client):
        """Transition from open to closed."""
        sync_engine.state.synced["test-1"] = {"card_number": 42, "checksum": "old"}
        issue = {"id": "test-1", "title": "Test", "status": "closed", "description": ""}

        sync_engine.sync_issue(issue)
        mock_client.close_card.assert_called_with(42)

    def test_status_transition_closed_to_open(self, sync_engine, mock_client):
        """Transition from closed to open (reopen)."""
        sync_engine.state.synced["test-1"] = {"card_number": 42, "checksum": "old"}
        issue = {"id": "test-1", "title": "Test", "status": "open", "description": ""}

        sync_engine.sync_issue(issue)
        mock_client.reopen_card.assert_called_with(42)
