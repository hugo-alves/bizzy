"""Tests for the Mapper class."""

import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from fizzy_sync import Mapper


class TestMapper:
    """Tests for Mapper class."""

    def test_column_for_status_default_mapping(self):
        """Test status to column mapping with defaults.

        Note: 'open' and 'closed' return None because they use Fizzy's
        built-in columns (Maybe? and Done) rather than custom columns.
        """
        mapper = Mapper()
        assert mapper.column_for_status("open") is None  # Uses Fizzy's Maybe?
        assert mapper.column_for_status("in_progress") == "Doing"
        assert mapper.column_for_status("blocked") == "Blocked"
        assert mapper.column_for_status("closed") is None  # Uses Fizzy's Done

    def test_column_for_status_unknown_defaults_to_none(self):
        """Unknown status should default to None (stays in Maybe?)."""
        mapper = Mapper()
        assert mapper.column_for_status("unknown") is None
        assert mapper.column_for_status("") is None

    def test_column_for_status_custom_mapping(self):
        """Test custom column mapping."""
        custom = {"open": "Todo", "in_progress": "Working"}
        mapper = Mapper(column_mapping=custom)
        assert mapper.column_for_status("open") == "Todo"
        assert mapper.column_for_status("in_progress") == "Working"

    def test_color_for_column(self):
        """Test column color mapping.

        Note: Only Doing and Blocked have custom colors. Other columns
        (including unknown) use the default color.
        """
        mapper = Mapper()
        assert mapper.color_for_column("Doing") == "var(--color-card-4)"
        assert mapper.color_for_column("Blocked") == "var(--color-card-8)"
        # Unknown columns get default color
        assert mapper.color_for_column("Unknown") == "var(--color-card-default)"

    def test_color_for_column_unknown_defaults_to_backlog_color(self):
        """Unknown column should default to Backlog color."""
        mapper = Mapper()
        assert mapper.color_for_column("Unknown") == "var(--color-card-default)"

    def test_tags_for_issue_priority(self):
        """Test priority tag generation."""
        mapper = Mapper()
        issue = {"priority": 0}
        assert "P0" in mapper.tags_for_issue(issue)

        issue = {"priority": 2}
        assert "P2" in mapper.tags_for_issue(issue)

    def test_tags_for_issue_type(self):
        """Test issue type tag generation."""
        mapper = Mapper()
        issue = {"issue_type": "bug"}
        assert "bug" in mapper.tags_for_issue(issue)

        issue = {"issue_type": "feature"}
        assert "feature" in mapper.tags_for_issue(issue)

    def test_tags_for_issue_labels_as_list(self):
        """Test labels as list."""
        mapper = Mapper()
        issue = {"labels": ["frontend", "urgent"]}
        tags = mapper.tags_for_issue(issue)
        assert "frontend" in tags
        assert "urgent" in tags

    def test_tags_for_issue_labels_as_json_string(self):
        """Test labels as JSON string (from SQLite)."""
        mapper = Mapper()
        issue = {"labels": '["frontend", "urgent"]'}
        tags = mapper.tags_for_issue(issue)
        assert "frontend" in tags
        assert "urgent" in tags

    def test_tags_for_issue_combined(self):
        """Test combined tags (priority + type + labels)."""
        mapper = Mapper()
        issue = {
            "priority": 1,
            "issue_type": "bug",
            "labels": ["critical"],
        }
        tags = mapper.tags_for_issue(issue)
        assert "P1" in tags
        assert "bug" in tags
        assert "critical" in tags

    def test_tags_for_issue_no_duplicates(self):
        """Test that duplicate tags are removed."""
        mapper = Mapper()
        issue = {
            "issue_type": "bug",
            "labels": ["bug", "frontend"],  # "bug" duplicates issue_type
        }
        tags = mapper.tags_for_issue(issue)
        assert tags.count("bug") == 1

    def test_extract_beads_id_found(self):
        """Test extracting beads ID from description."""
        mapper = Mapper()
        desc = "Some description\n\n[beads:bizzy-123]"
        assert mapper.extract_beads_id(desc) == "bizzy-123"

    def test_extract_beads_id_not_found(self):
        """Test when beads ID is not in description."""
        mapper = Mapper()
        assert mapper.extract_beads_id("No marker here") is None
        assert mapper.extract_beads_id("") is None
        assert mapper.extract_beads_id(None) is None

    def test_beads_to_fizzy_card_basic(self):
        """Test basic issue to card transformation."""
        mapper = Mapper()
        issue = {
            "id": "bizzy-42",
            "title": "Fix the bug",
            "description": "It's broken",
        }
        card = mapper.beads_to_fizzy_card(issue)
        assert card["title"] == "Fix the bug"
        assert "It's broken" in card["description"]
        assert "[beads:bizzy-42]" in card["description"]

    def test_beads_to_fizzy_card_no_description(self):
        """Test card transformation when issue has no description."""
        mapper = Mapper()
        issue = {
            "id": "bizzy-42",
            "title": "Fix the bug",
            "description": None,
        }
        card = mapper.beads_to_fizzy_card(issue)
        assert card["title"] == "Fix the bug"
        assert card["description"] == "[beads:bizzy-42]"

    def test_beads_to_fizzy_card_empty_description(self):
        """Test card transformation when issue has empty description."""
        mapper = Mapper()
        issue = {
            "id": "bizzy-42",
            "title": "Fix the bug",
            "description": "",
        }
        card = mapper.beads_to_fizzy_card(issue)
        assert card["description"] == "[beads:bizzy-42]"
