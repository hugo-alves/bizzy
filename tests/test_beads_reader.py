"""Tests for the BeadsReader class, especially blocked status detection."""

import sqlite3
import sys
import tempfile
from pathlib import Path

import pytest

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from fizzy_sync import BeadsReader


@pytest.fixture
def beads_db():
    """Create a temporary beads database with test data."""
    with tempfile.TemporaryDirectory() as tmpdir:
        beads_path = Path(tmpdir)
        beads_dir = beads_path / ".beads"
        beads_dir.mkdir()
        db_path = beads_dir / "beads.db"

        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        # Create issues table (minimal schema for testing)
        cursor.execute("""
            CREATE TABLE issues (
                id TEXT PRIMARY KEY,
                content_hash TEXT,
                title TEXT NOT NULL,
                description TEXT,
                design TEXT,
                acceptance_criteria TEXT,
                notes TEXT,
                status TEXT DEFAULT 'open',
                priority INTEGER DEFAULT 2,
                issue_type TEXT DEFAULT 'task',
                assignee TEXT,
                estimated_minutes INTEGER,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                closed_at TEXT,
                external_ref TEXT,
                source_repo TEXT,
                close_reason TEXT,
                deleted_at TEXT,
                deleted_by TEXT,
                delete_reason TEXT,
                original_type TEXT,
                sender TEXT,
                ephemeral INTEGER DEFAULT 0,
                replies_to TEXT,
                relates_to TEXT,
                duplicate_of TEXT,
                superseded_by TEXT
            )
        """)

        # Create blocked_issues_cache table
        cursor.execute("""
            CREATE TABLE blocked_issues_cache (
                issue_id TEXT PRIMARY KEY,
                FOREIGN KEY (issue_id) REFERENCES issues(id) ON DELETE CASCADE
            )
        """)

        # Create dependencies table (for reference)
        cursor.execute("""
            CREATE TABLE dependencies (
                issue_id TEXT NOT NULL,
                depends_on_id TEXT NOT NULL,
                type TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                created_by TEXT,
                notes TEXT,
                PRIMARY KEY (issue_id, depends_on_id, type)
            )
        """)

        conn.commit()
        conn.close()

        yield beads_path, db_path


class TestBeadsReaderBlockedStatus:
    """Tests for blocked status detection from blocked_issues_cache."""

    def test_issue_in_blocked_cache_becomes_blocked(self, beads_db):
        """Issue with status=open but in blocked_issues_cache should return status=blocked."""
        beads_path, db_path = beads_db

        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        # Create two issues - A blocks B
        cursor.execute(
            "INSERT INTO issues (id, title, status) VALUES (?, ?, ?)",
            ("test-a", "Task A (blocker)", "open"),
        )
        cursor.execute(
            "INSERT INTO issues (id, title, status) VALUES (?, ?, ?)",
            ("test-b", "Task B (blocked)", "open"),
        )

        # Add B to blocked cache (simulating that A blocks B)
        cursor.execute(
            "INSERT INTO blocked_issues_cache (issue_id) VALUES (?)", ("test-b",)
        )

        conn.commit()
        conn.close()

        # Read issues through BeadsReader
        reader = BeadsReader(beads_path)
        issues = reader.all_issues()

        issues_by_id = {i["id"]: i for i in issues}

        # A should remain open (not blocked)
        assert issues_by_id["test-a"]["status"] == "open"

        # B should be blocked (derived from cache)
        assert issues_by_id["test-b"]["status"] == "blocked"

    def test_issue_not_in_blocked_cache_stays_open(self, beads_db):
        """Issue with status=open and not in cache should stay open."""
        beads_path, db_path = beads_db

        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        cursor.execute(
            "INSERT INTO issues (id, title, status) VALUES (?, ?, ?)",
            ("test-a", "Task A", "open"),
        )

        conn.commit()
        conn.close()

        reader = BeadsReader(beads_path)
        issues = reader.all_issues()

        assert len(issues) == 1
        assert issues[0]["status"] == "open"

    def test_unblocked_issue_returns_to_open(self, beads_db):
        """Issue with status=blocked but NOT in cache should become open."""
        beads_path, db_path = beads_db

        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        # Issue has status=blocked in DB but is NOT in blocked_issues_cache
        # (simulating that the blocker was closed)
        cursor.execute(
            "INSERT INTO issues (id, title, status) VALUES (?, ?, ?)",
            ("test-a", "Task A (was blocked)", "blocked"),
        )

        conn.commit()
        conn.close()

        reader = BeadsReader(beads_path)
        issues = reader.all_issues()

        # Should be treated as open since it's not in the blocked cache
        assert issues[0]["status"] == "open"

    def test_in_progress_issue_becomes_blocked_when_in_cache(self, beads_db):
        """Issue with status=in_progress but in blocked cache should become blocked."""
        beads_path, db_path = beads_db

        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        cursor.execute(
            "INSERT INTO issues (id, title, status) VALUES (?, ?, ?)",
            ("test-a", "Task A", "in_progress"),
        )
        cursor.execute(
            "INSERT INTO blocked_issues_cache (issue_id) VALUES (?)", ("test-a",)
        )

        conn.commit()
        conn.close()

        reader = BeadsReader(beads_path)
        issues = reader.all_issues()

        assert issues[0]["status"] == "blocked"

    def test_closed_issue_not_affected_by_blocked_cache(self, beads_db):
        """Closed issues should stay closed even if in blocked cache."""
        beads_path, db_path = beads_db

        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        cursor.execute(
            "INSERT INTO issues (id, title, status) VALUES (?, ?, ?)",
            ("test-a", "Task A", "closed"),
        )
        # Even if somehow in blocked cache, closed should stay closed
        cursor.execute(
            "INSERT INTO blocked_issues_cache (issue_id) VALUES (?)", ("test-a",)
        )

        conn.commit()
        conn.close()

        reader = BeadsReader(beads_path)
        issues = reader.all_issues(include_closed=True)

        closed_issue = [i for i in issues if i["id"] == "test-a"][0]
        assert closed_issue["status"] == "closed"

    def test_multiple_issues_mixed_blocked_status(self, beads_db):
        """Test a mix of blocked and non-blocked issues."""
        beads_path, db_path = beads_db

        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        # Create several issues
        cursor.execute(
            "INSERT INTO issues (id, title, status) VALUES (?, ?, ?)",
            ("test-1", "Open task", "open"),
        )
        cursor.execute(
            "INSERT INTO issues (id, title, status) VALUES (?, ?, ?)",
            ("test-2", "Blocked by 1", "open"),
        )
        cursor.execute(
            "INSERT INTO issues (id, title, status) VALUES (?, ?, ?)",
            ("test-3", "In progress", "in_progress"),
        )
        cursor.execute(
            "INSERT INTO issues (id, title, status) VALUES (?, ?, ?)",
            ("test-4", "Also blocked", "open"),
        )

        # test-2 and test-4 are blocked
        cursor.execute(
            "INSERT INTO blocked_issues_cache (issue_id) VALUES (?)", ("test-2",)
        )
        cursor.execute(
            "INSERT INTO blocked_issues_cache (issue_id) VALUES (?)", ("test-4",)
        )

        conn.commit()
        conn.close()

        reader = BeadsReader(beads_path)
        issues = reader.all_issues()
        issues_by_id = {i["id"]: i for i in issues}

        assert issues_by_id["test-1"]["status"] == "open"
        assert issues_by_id["test-2"]["status"] == "blocked"
        assert issues_by_id["test-3"]["status"] == "in_progress"
        assert issues_by_id["test-4"]["status"] == "blocked"

    def test_empty_blocked_cache(self, beads_db):
        """Test when blocked_issues_cache is empty."""
        beads_path, db_path = beads_db

        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        cursor.execute(
            "INSERT INTO issues (id, title, status) VALUES (?, ?, ?)",
            ("test-a", "Task A", "open"),
        )
        cursor.execute(
            "INSERT INTO issues (id, title, status) VALUES (?, ?, ?)",
            ("test-b", "Task B", "in_progress"),
        )

        conn.commit()
        conn.close()

        reader = BeadsReader(beads_path)
        issues = reader.all_issues()
        issues_by_id = {i["id"]: i for i in issues}

        assert issues_by_id["test-a"]["status"] == "open"
        assert issues_by_id["test-b"]["status"] == "in_progress"

    def test_blocked_cache_table_missing_graceful_fallback(self, beads_db):
        """If blocked_issues_cache table doesn't exist, should gracefully fallback."""
        beads_path, db_path = beads_db

        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        # Drop the blocked_issues_cache table (simulating older beads version)
        cursor.execute("DROP TABLE blocked_issues_cache")

        cursor.execute(
            "INSERT INTO issues (id, title, status) VALUES (?, ?, ?)",
            ("test-a", "Task A", "open"),
        )

        conn.commit()
        conn.close()

        reader = BeadsReader(beads_path)
        issues = reader.all_issues()

        # Should still work, just without blocked status override
        assert len(issues) == 1
        assert issues[0]["status"] == "open"


class TestBeadsReaderGetIssue:
    """Tests for get_issue method."""

    def test_get_issue_found(self, beads_db):
        """Get existing issue by ID."""
        beads_path, db_path = beads_db

        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO issues (id, title, status, priority) VALUES (?, ?, ?, ?)",
            ("test-123", "My Issue", "open", 1),
        )
        conn.commit()
        conn.close()

        reader = BeadsReader(beads_path)
        issue = reader.get_issue("test-123")

        assert issue is not None
        assert issue["id"] == "test-123"
        assert issue["title"] == "My Issue"
        assert issue["priority"] == 1

    def test_get_issue_not_found(self, beads_db):
        """Get non-existent issue returns None."""
        beads_path, _ = beads_db

        reader = BeadsReader(beads_path)
        issue = reader.get_issue("does-not-exist")

        assert issue is None


class TestBeadsReaderGetDependencies:
    """Tests for get_dependencies method."""

    def test_get_dependencies_found(self, beads_db):
        """Get dependencies for an issue."""
        beads_path, db_path = beads_db

        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        # Create issues
        cursor.execute(
            "INSERT INTO issues (id, title) VALUES (?, ?)", ("task-a", "Task A")
        )
        cursor.execute(
            "INSERT INTO issues (id, title) VALUES (?, ?)", ("task-b", "Task B")
        )

        # Add dependency: task-b depends on task-a
        cursor.execute(
            "INSERT INTO dependencies (issue_id, depends_on_id, type) VALUES (?, ?, ?)",
            ("task-b", "task-a", "blocks"),
        )

        conn.commit()
        conn.close()

        reader = BeadsReader(beads_path)
        deps = reader.get_dependencies("task-b")

        assert len(deps) == 1
        assert deps[0]["depends_on_id"] == "task-a"
        assert deps[0]["type"] == "blocks"

    def test_get_dependencies_none(self, beads_db):
        """Get dependencies returns empty list when none exist."""
        beads_path, db_path = beads_db

        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO issues (id, title) VALUES (?, ?)", ("task-a", "Task A")
        )
        conn.commit()
        conn.close()

        reader = BeadsReader(beads_path)
        deps = reader.get_dependencies("task-a")

        assert deps == []


class TestBeadsReaderChangedSince:
    """Tests for changed_since method."""

    def test_changed_since_returns_updated_issues(self, beads_db):
        """Return issues updated after given timestamp."""
        beads_path, db_path = beads_db
        from datetime import datetime, timedelta

        now = datetime.now()
        old_time = (now - timedelta(hours=2)).isoformat()
        recent_time = (now - timedelta(minutes=30)).isoformat()

        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        # Old issue (updated 2 hours ago)
        cursor.execute(
            "INSERT INTO issues (id, title, updated_at) VALUES (?, ?, ?)",
            ("old-issue", "Old Issue", old_time),
        )

        # Recent issue (updated 30 minutes ago)
        cursor.execute(
            "INSERT INTO issues (id, title, updated_at) VALUES (?, ?, ?)",
            ("recent-issue", "Recent Issue", recent_time),
        )

        conn.commit()
        conn.close()

        reader = BeadsReader(beads_path)

        # Get issues changed in the last hour
        since = now - timedelta(hours=1)
        changed = reader.changed_since(since)

        assert len(changed) == 1
        assert changed[0]["id"] == "recent-issue"

    def test_changed_since_returns_empty_when_none_changed(self, beads_db):
        """Return empty list when no issues changed after timestamp."""
        beads_path, db_path = beads_db
        from datetime import datetime, timedelta

        old_time = (datetime.now() - timedelta(hours=2)).isoformat()

        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO issues (id, title, updated_at) VALUES (?, ?, ?)",
            ("old-issue", "Old Issue", old_time),
        )
        conn.commit()
        conn.close()

        reader = BeadsReader(beads_path)

        # Check for changes in the last 30 minutes
        since = datetime.now() - timedelta(minutes=30)
        changed = reader.changed_since(since)

        assert changed == []
