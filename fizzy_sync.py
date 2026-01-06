#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "httpx>=0.27",
#     "pyyaml>=6.0",
#     "rich>=13.0",
#     "watchfiles>=0.21",
# ]
# ///
"""
Fizzy-Beads Sync: Sync Beads issues to Fizzy Kanban cards.

Usage:
    uv run fizzy_sync.py wizard            # Interactive setup (RECOMMENDED)
    uv run fizzy_sync.py auth              # Test API connection
    uv run fizzy_sync.py status            # Show sync status
    uv run fizzy_sync.py sync              # Sync issues to Fizzy
    uv run fizzy_sync.py watch             # Watch for changes and auto-sync

Advanced:
    uv run fizzy_sync.py init              # Create config file manually
    uv run fizzy_sync.py setup             # Set up board columns
    uv run fizzy_sync.py setup --new-board "My Board"  # Create new board
    uv run fizzy_sync.py setup --reset --force         # Reset existing columns
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
import yaml
from rich.console import Console
from rich.table import Table

console = Console()


# =============================================================================
# CLI Result Types (for testability)
# =============================================================================


@dataclass
class InitResult:
    """Result of init command."""

    success: bool
    config_path: Path | None = None
    error: str | None = None
    already_exists: bool = False


@dataclass
class AuthResult:
    """Result of auth command."""

    success: bool
    user_name: str | None = None
    user_email: str | None = None
    account_name: str | None = None
    account_slug: str | None = None
    board_name: str | None = None
    board_id: str | None = None
    error: str | None = None
    error_code: int | None = None


@dataclass
class StatusInfo:
    """Status information returned by status command."""

    open_issues: int
    total_issues: int
    synced_count: int
    last_sync: str | None
    pending_sync: int


@dataclass
class SetupResult:
    """Result of setup command."""

    success: bool
    board_id: str | None = None
    board_name: str | None = None
    columns_created: list[str] = field(default_factory=list)
    columns_deleted: list[str] = field(default_factory=list)
    columns_existing: list[str] = field(default_factory=list)
    error: str | None = None


# =============================================================================
# CLI Logic Functions (testable, no console output)
# =============================================================================


def init_config(config_path: Path, force: bool = False) -> InitResult:
    """Create config file. Returns result without console output."""
    if config_path.exists() and not force:
        return InitResult(
            success=False, config_path=config_path, already_exists=True
        )

    config_path.write_text(CONFIG_TEMPLATE)
    return InitResult(success=True, config_path=config_path)


def verify_auth(config: Config, client: FizzyClient) -> AuthResult:
    """Verify API authentication. Returns result without console output."""
    if not config.fizzy_api_token:
        return AuthResult(success=False, error="API token not set")

    try:
        identity = client.get_identity()
        accounts = identity.get("accounts", [])

        # Find matching account
        account = None
        for acc in accounts:
            slug = acc.get("slug", "")
            if slug.lstrip("/") == config.fizzy_account_slug:
                account = acc
                break

        result = AuthResult(success=True)
        if account:
            user = account.get("user", {})
            result.user_name = user.get("name")
            result.user_email = user.get("email_address")
            result.account_name = account.get("name")
            result.account_slug = account.get("slug")

        # Test board access
        if config.board_id:
            try:
                board = client.get_board(config.board_id)
                result.board_name = board.get("name", "Unknown")
                result.board_id = config.board_id
            except Exception as e:
                result.error = f"Board access: {e}"

        return result

    except httpx.HTTPStatusError as e:
        return AuthResult(
            success=False,
            error="Auth failed",
            error_code=e.response.status_code,
        )
    except Exception as e:
        return AuthResult(success=False, error=f"Connection failed: {e}")


def get_status(config: Config, reader: BeadsReader, state: SyncState) -> StatusInfo:
    """Get sync status information. Returns data without console output."""
    issues = reader.all_issues(include_closed=False)
    all_issues = reader.all_issues(include_closed=True)
    stats = state.stats()

    # Calculate pending syncs
    pending = 0
    for issue in issues:
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
        if state.checksum_for(issue["id"]) != checksum:
            pending += 1

    return StatusInfo(
        open_issues=len(issues),
        total_issues=len(all_issues),
        synced_count=stats["total_synced"],
        last_sync=stats["last_sync"],
        pending_sync=pending,
    )


def setup_board(
    config: Config,
    client: FizzyClient,
    mapper: Mapper,
    new_board: str | None = None,
    reset: bool = False,
    force: bool = False,
) -> SetupResult:
    """Set up Fizzy board. Returns result without console output."""
    board_id = config.board_id
    board_name = None
    columns_created = []
    columns_deleted = []
    columns_existing = []

    try:
        # Option 1: Create new board
        if new_board:
            board_name = new_board
            result = client.create_board(board_name)
            board_id = result.get("id")
            if not board_id:
                return SetupResult(success=False, error="Failed to create board")
        else:
            # Use existing board
            if not board_id:
                return SetupResult(
                    success=False,
                    error="No board ID configured. Use --new-board to create one.",
                )
            try:
                board = client.get_board(board_id)
                board_name = board.get("name", "Unknown")
            except Exception:
                return SetupResult(
                    success=False, error=f"Board not found: {board_id}"
                )

        # Get existing columns
        existing_columns = client.list_columns(board_id)

        # Delete existing columns (if --reset or --new-board)
        if reset or new_board:
            if existing_columns and not force:
                return SetupResult(
                    success=False,
                    board_id=board_id,
                    board_name=board_name,
                    error=f"Would delete {len(existing_columns)} column(s). Use --force to confirm.",
                )

            for col in existing_columns:
                try:
                    client.delete_column(board_id, col["id"])
                    columns_deleted.append(col.get("name"))
                except Exception:
                    pass  # Continue on error

        # Create columns for active work states
        beads_columns = ["Doing", "Blocked"]
        for name in beads_columns:
            existing = client.list_columns(board_id)
            existing_names = [c.get("name") for c in existing]
            if name in existing_names:
                columns_existing.append(name)
                continue

            color = mapper.color_for_column(name)
            client.create_column(board_id, name=name, color=color)
            columns_created.append(name)

        return SetupResult(
            success=True,
            board_id=board_id,
            board_name=board_name,
            columns_created=columns_created,
            columns_deleted=columns_deleted,
            columns_existing=columns_existing,
        )

    except httpx.HTTPStatusError as e:
        return SetupResult(
            success=False, error=f"API error: {e.response.status_code}"
        )
    except Exception as e:
        return SetupResult(success=False, error=f"Setup failed: {e}")


# =============================================================================
# Config Class
# =============================================================================


@dataclass
class Config:
    """Configuration loaded from .fizzy-sync.yml"""

    fizzy_base_url: str
    fizzy_account_slug: str
    fizzy_api_token: str
    board_id: str
    column_mapping: dict[str, str] = field(default_factory=dict)
    sync_options: dict[str, Any] = field(default_factory=dict)
    beads_path: Path = field(default_factory=lambda: Path("."))

    @classmethod
    def load(cls, config_path: Path | None = None) -> Config:
        """Load config from .fizzy-sync.yml, expanding ${ENV_VAR} references."""
        if config_path is None:
            config_path = cls.find_config_file()

        if config_path is None or not config_path.exists():
            raise FileNotFoundError(
                "Config file not found. Run 'fizzy-sync init' to create one."
            )

        content = config_path.read_text()
        # Expand environment variables
        content = cls._expand_env_vars(content)
        data = yaml.safe_load(content)

        fizzy = data.get("fizzy", {})
        board = data.get("board", {})
        columns = data.get("columns", {})
        sync = data.get("sync", {})
        beads = data.get("beads", {})

        return cls(
            fizzy_base_url=fizzy.get("base_url", "http://localhost:3000"),
            fizzy_account_slug=str(fizzy.get("account_slug", "")),
            fizzy_api_token=fizzy.get("api_token", ""),
            board_id=board.get("id", ""),
            column_mapping=columns,
            sync_options=sync,
            beads_path=Path(beads.get("path", ".")),
        )

    @classmethod
    def find_config_file(cls) -> Path | None:
        """Search current dir, then parent dirs for .fizzy-sync.yml."""
        current = Path.cwd()
        for directory in [current, *current.parents]:
            config_path = directory / ".fizzy-sync.yml"
            if config_path.exists():
                return config_path
        return None

    @staticmethod
    def _expand_env_vars(content: str) -> str:
        """Expand ${ENV_VAR} patterns in content."""

        def replacer(match: re.Match) -> str:
            var_name = match.group(1)
            return os.environ.get(var_name, "")

        return re.sub(r"\$\{(\w+)\}", replacer, content)


# =============================================================================
# FizzyClient Class
# =============================================================================


class FizzyClient:
    """REST API client for Fizzy."""

    # Retry configuration
    MAX_RETRIES = 3
    RETRY_BACKOFF_FACTOR = 1.0  # seconds
    RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}

    def __init__(self, base_url: str, account_slug: str, api_token: str):
        self.base_url = base_url.rstrip("/")
        self.account_slug = account_slug
        self.headers = {
            "Authorization": f"Bearer {api_token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        self._client = httpx.Client(timeout=30.0)

    def _request(
        self,
        method: str,
        path: str,
        json_data: dict | None = None,
        allow_404: bool = False,
    ) -> httpx.Response:
        """Make HTTP request to Fizzy API with retry logic."""
        url = f"{self.base_url}{path}"
        last_exception: Exception | None = None

        for attempt in range(self.MAX_RETRIES + 1):
            try:
                response = self._client.request(
                    method=method,
                    url=url,
                    headers=self.headers,
                    json=json_data,
                )

                # Handle 404 if allowed
                if allow_404 and response.status_code == 404:
                    return response

                # Check if we should retry on this status code
                if response.status_code in self.RETRYABLE_STATUS_CODES:
                    if attempt < self.MAX_RETRIES:
                        wait_time = self._get_retry_wait(response, attempt)
                        time.sleep(wait_time)
                        continue
                    # Last attempt, raise the error
                    response.raise_for_status()

                response.raise_for_status()
                return response

            except (httpx.ConnectError, httpx.ReadTimeout, httpx.WriteTimeout) as e:
                last_exception = e
                if attempt < self.MAX_RETRIES:
                    wait_time = self.RETRY_BACKOFF_FACTOR * (2**attempt)
                    time.sleep(wait_time)
                    continue
                raise

        # Should not reach here, but just in case
        if last_exception:
            raise last_exception
        raise RuntimeError("Unexpected retry loop exit")

    def _get_retry_wait(self, response: httpx.Response, attempt: int) -> float:
        """Calculate wait time for retry, respecting Retry-After header."""
        # Check for Retry-After header (rate limiting)
        retry_after = response.headers.get("Retry-After")
        if retry_after:
            try:
                return float(retry_after)
            except ValueError:
                pass
        # Exponential backoff: 1s, 2s, 4s, ...
        return self.RETRY_BACKOFF_FACTOR * (2**attempt)

    def _account_path(self, path: str) -> str:
        """Build account-scoped path."""
        return f"/{self.account_slug}{path}"

    # Identity
    def get_identity(self) -> dict:
        """Get current user identity and accounts."""
        response = self._request("GET", "/my/identity")
        return response.json()

    # Boards
    def list_boards(self) -> list[dict]:
        """List all boards in account."""
        response = self._request("GET", self._account_path("/boards"))
        return response.json()

    def get_board(self, board_id: str) -> dict:
        """Get board details."""
        response = self._request("GET", self._account_path(f"/boards/{board_id}"))
        return response.json()

    # Columns
    def list_columns(self, board_id: str) -> list[dict]:
        """List columns in a board."""
        response = self._request(
            "GET", self._account_path(f"/boards/{board_id}/columns")
        )
        return response.json()

    def create_column(
        self, board_id: str, name: str, color: str | None = None
    ) -> dict:
        """Create a new column."""
        payload = {"column": {"name": name}}
        if color:
            payload["column"]["color"] = color
        response = self._request(
            "POST",
            self._account_path(f"/boards/{board_id}/columns"),
            json_data=payload,
        )
        # Handle 201 Created - may return empty body
        if response.status_code == 201:
            # Try to get ID from Location header or re-fetch columns
            location = response.headers.get("Location", "")
            if location:
                # Extract ID from location
                match = re.search(r"/columns/([^/]+)$", location)
                if match:
                    return {"id": match.group(1), "name": name}
            # Fallback: return what we can
            try:
                return response.json()
            except Exception:
                return {"name": name}
        return response.json()

    def delete_column(self, board_id: str, column_id: str) -> None:
        """Delete a column from a board."""
        self._request(
            "DELETE",
            self._account_path(f"/boards/{board_id}/columns/{column_id}"),
        )

    def create_board(self, name: str) -> dict:
        """Create a new board."""
        payload = {"board": {"name": name}}
        response = self._request(
            "POST",
            self._account_path("/boards"),
            json_data=payload,
        )
        # Handle 201 Created - extract board ID from Location header
        if response.status_code == 201:
            location = response.headers.get("Location", "")
            if location:
                match = re.search(r"/boards/([^/\.]+)", location)
                if match:
                    return {"id": match.group(1), "name": name}
            try:
                return response.json()
            except Exception:
                return {"name": name}
        return response.json()

    # Cards
    def list_cards(self, board_id: str | None = None) -> list[dict]:
        """List cards, optionally filtered by board."""
        path = self._account_path("/cards")
        if board_id:
            path += f"?board_id={board_id}"
        response = self._request("GET", path)
        return response.json()

    def get_card(self, number: int) -> dict | None:
        """Get card by number."""
        response = self._request(
            "GET", self._account_path(f"/cards/{number}"), allow_404=True
        )
        if response.status_code == 404:
            return None
        return response.json()

    def create_card(
        self, board_id: str, title: str, description: str | None = None
    ) -> dict:
        """Create a new card."""
        payload: dict[str, Any] = {"card": {"title": title}}
        if description:
            payload["card"]["description"] = description
        response = self._request(
            "POST",
            self._account_path(f"/boards/{board_id}/cards"),
            json_data=payload,
        )
        # Handle 201 Created
        if response.status_code == 201:
            location = response.headers.get("Location", "")
            if location:
                match = re.search(r"/cards/(\d+)(?:\.json)?$", location)
                if match:
                    return {"number": int(match.group(1)), "title": title}
            try:
                data = response.json()
                if "number" in data:
                    return data
            except Exception:
                pass
            # Fallback: search for the card we just created by beads ID in description
            if description:
                beads_match = re.search(r"\[beads:(\S+)\]", description)
                if beads_match:
                    beads_id = beads_match.group(1)
                    card = self.find_card_by_beads_id(beads_id, board_id)
                    if card:
                        return card
            return {"title": title}
        return response.json()

    def find_card_by_beads_id(self, beads_id: str, board_id: str | None = None) -> dict | None:
        """Find a card by its beads ID stored in the description."""
        cards = self.list_cards(board_id)
        marker = f"[beads:{beads_id}]"
        for card in cards:
            desc = card.get("description") or ""
            if marker in desc:
                return card
        return None

    def update_card(
        self,
        number: int,
        title: str | None = None,
        description: str | None = None,
    ) -> dict:
        """Update an existing card."""
        payload: dict[str, Any] = {"card": {}}
        if title is not None:
            payload["card"]["title"] = title
        if description is not None:
            payload["card"]["description"] = description
        response = self._request(
            "PUT",
            self._account_path(f"/cards/{number}"),
            json_data=payload,
        )
        return response.json() if response.content else {}

    def delete_card(self, number: int) -> None:
        """Delete a card."""
        self._request("DELETE", self._account_path(f"/cards/{number}"))

    def close_card(self, number: int) -> None:
        """Close a card."""
        self._request("POST", self._account_path(f"/cards/{number}/closure"))

    def reopen_card(self, number: int) -> None:
        """Reopen a closed card."""
        self._request(
            "DELETE", self._account_path(f"/cards/{number}/closure"), allow_404=True
        )

    def triage_card(self, number: int, column_id: str) -> None:
        """Move card to a column (triage)."""
        self._request(
            "POST",
            self._account_path(f"/cards/{number}/triage"),
            json_data={"column_id": column_id},
        )

    def untriage_card(self, number: int) -> None:
        """Send card back to triage (Maybe? column)."""
        self._request(
            "DELETE",
            self._account_path(f"/cards/{number}/triage"),
            allow_404=True,  # May not be triaged
        )

    # Tags
    def list_tags(self) -> list[dict]:
        """List all tags in account."""
        response = self._request("GET", self._account_path("/tags"))
        return response.json()

    def toggle_tag(self, card_number: int, tag_title: str) -> None:
        """Toggle a tag on a card (add if not present, remove if present)."""
        self._request(
            "POST",
            self._account_path(f"/cards/{card_number}/taggings"),
            json_data={"tag_title": tag_title},
        )

    def close(self) -> None:
        """Close the HTTP client."""
        self._client.close()


# =============================================================================
# BeadsReader Class
# =============================================================================


class BeadsReader:
    """Read issues from Beads SQLite database."""

    def __init__(self, beads_path: Path):
        self.beads_path = beads_path
        self.db_path = beads_path / ".beads" / "beads.db"
        self._validate_database()

    def _validate_database(self) -> None:
        """Validate that the Beads database exists."""
        if not self.db_path.exists():
            raise FileNotFoundError(
                f"Beads database not found at {self.db_path}. "
                "Run 'bd init' to initialize Beads."
            )

    def _connect(self) -> sqlite3.Connection:
        """Create database connection."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _get_blocked_issue_ids(self, conn: sqlite3.Connection) -> set[str]:
        """Get IDs of all issues that are blocked by dependencies."""
        try:
            cursor = conn.execute("SELECT issue_id FROM blocked_issues_cache")
            return {row[0] for row in cursor.fetchall()}
        except sqlite3.OperationalError:
            # Table may not exist in older beads versions
            return set()

    def _apply_blocked_status(self, issues: list[dict], blocked_ids: set[str]) -> list[dict]:
        """Override status to 'blocked' for issues in the blocked cache.

        Also handles the reverse: if an issue has status='blocked' but is no longer
        in the blocked cache, it should effectively be 'open'.
        """
        for issue in issues:
            issue_id = issue["id"]
            current_status = issue["status"]

            if issue_id in blocked_ids:
                # Issue is blocked by dependencies - set status to blocked
                # (unless it's closed, which takes precedence)
                if current_status not in ("closed", "tombstone"):
                    issue["status"] = "blocked"
            elif current_status == "blocked":
                # Issue has status='blocked' but is not in blocked cache
                # This means it was unblocked - treat as 'open'
                issue["status"] = "open"

        return issues

    def all_issues(self, include_closed: bool = False) -> list[dict]:
        """Return all issues from SQLite database."""
        conn = self._connect()
        try:
            if include_closed:
                query = "SELECT * FROM issues ORDER BY priority, created_at"
            else:
                query = "SELECT * FROM issues WHERE status != 'closed' ORDER BY priority, created_at"
            cursor = conn.execute(query)
            issues = [dict(row) for row in cursor.fetchall()]

            # Apply blocked status from the blocked_issues_cache
            blocked_ids = self._get_blocked_issue_ids(conn)
            return self._apply_blocked_status(issues, blocked_ids)
        finally:
            conn.close()

    def get_issue(self, issue_id: str) -> dict | None:
        """Get single issue by ID."""
        conn = self._connect()
        try:
            cursor = conn.execute("SELECT * FROM issues WHERE id = ?", (issue_id,))
            row = cursor.fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def get_dependencies(self, issue_id: str) -> list[dict]:
        """Get dependencies for an issue."""
        conn = self._connect()
        try:
            cursor = conn.execute(
                "SELECT * FROM dependencies WHERE issue_id = ?", (issue_id,)
            )
            return [dict(row) for row in cursor.fetchall()]
        finally:
            conn.close()

    def changed_since(self, timestamp: datetime) -> list[dict]:
        """Issues with updated_at > timestamp."""
        conn = self._connect()
        try:
            cursor = conn.execute(
                "SELECT * FROM issues WHERE updated_at > ? ORDER BY updated_at",
                (timestamp.isoformat(),),
            )
            return [dict(row) for row in cursor.fetchall()]
        finally:
            conn.close()


# =============================================================================
# Mapper Class
# =============================================================================


class Mapper:
    """Transform data between Beads and Fizzy formats."""

    COLUMN_COLORS = {
        "Doing": "var(--color-card-4)",  # Lime
        "Blocked": "var(--color-card-8)",  # Pink
    }

    # Map Beads statuses to Fizzy triage columns
    # - "open" → None (stays in Maybe?, Fizzy's built-in inbox/backlog)
    # - "closed" → None (uses Fizzy's built-in Done via close_card API)
    STATUS_TO_COLUMN = {
        "open": None,  # Stays in Maybe? (the inbox IS the backlog)
        "in_progress": "Doing",
        "blocked": "Blocked",
        "closed": None,  # Uses Fizzy's built-in Done (via close_card API)
    }

    def __init__(self, column_mapping: dict[str, str] | None = None):
        self.column_mapping = column_mapping or self.STATUS_TO_COLUMN

    def beads_to_fizzy_card(self, issue: dict) -> dict:
        """Transform Beads issue to Fizzy card payload."""
        return {
            "title": issue["title"],
            "description": self._build_description(issue),
        }

    def column_for_status(self, status: str) -> str | None:
        """Get column name for a Beads status. Returns None for Maybe?/Done."""
        return self.column_mapping.get(status)

    def color_for_column(self, column_name: str) -> str:
        """Get color CSS variable for a column."""
        return self.COLUMN_COLORS.get(column_name, "var(--color-card-default)")

    def tags_for_issue(self, issue: dict) -> list[str]:
        """Get tags to apply to a card based on issue metadata."""
        tags = []
        if issue.get("priority") is not None:
            tags.append(f"P{issue['priority']}")
        if issue.get("issue_type"):
            tags.append(issue["issue_type"])
        if issue.get("labels"):
            labels = issue["labels"]
            if isinstance(labels, str):
                try:
                    labels = json.loads(labels)
                except json.JSONDecodeError:
                    labels = []
            if labels:
                tags.extend(labels)
        return list(set(tags))

    def extract_beads_id(self, description: str | None) -> str | None:
        """Parse [beads:xxx] from description."""
        if not description:
            return None
        match = re.search(r"\[beads:(\S+)\]", description)
        return match.group(1) if match else None

    def _build_description(self, issue: dict) -> str:
        """Build description with Beads ID marker."""
        desc = issue.get("description") or ""
        # Append Beads ID marker (plain text survives Action Text processing)
        beads_marker = f"[beads:{issue['id']}]"
        if desc:
            return f"{desc}\n\n{beads_marker}"
        return beads_marker


# =============================================================================
# SyncState Class
# =============================================================================


class SyncState:
    """Track sync state between Beads and Fizzy."""

    STATE_FILE = ".fizzy-sync-state.json"

    def __init__(self, beads_path: Path):
        self.state_file = beads_path / ".beads" / self.STATE_FILE
        self._load_state()

    def is_synced(self, beads_id: str) -> bool:
        """Check if an issue has been synced."""
        return beads_id in self.state["synced_issues"]

    def card_number_for(self, beads_id: str) -> int | None:
        """Get Fizzy card number for a Beads issue."""
        entry = self.state["synced_issues"].get(beads_id, {})
        return entry.get("card_number")

    def checksum_for(self, beads_id: str) -> str | None:
        """Get stored checksum for a Beads issue."""
        entry = self.state["synced_issues"].get(beads_id, {})
        return entry.get("checksum")

    def record_sync(self, beads_id: str, card_number: int, checksum: str) -> None:
        """Record a successful sync."""
        self.state["synced_issues"][beads_id] = {
            "card_number": card_number,
            "checksum": checksum,
            "synced_at": datetime.now().isoformat(),
        }
        self.state["last_sync"] = datetime.now().isoformat()
        self._save_state()

    def last_sync_time(self) -> datetime | None:
        """Get timestamp of last sync."""
        if ts := self.state.get("last_sync"):
            return datetime.fromisoformat(ts)
        return None

    def stats(self) -> dict:
        """Get sync statistics."""
        return {
            "total_synced": len(self.state["synced_issues"]),
            "last_sync": self.state.get("last_sync"),
        }

    def _load_state(self) -> None:
        """Load state from file."""
        if self.state_file.exists():
            self.state = json.loads(self.state_file.read_text())
        else:
            self.state = {"synced_issues": {}, "last_sync": None}

    def _save_state(self) -> None:
        """Save state to file."""
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        self.state_file.write_text(json.dumps(self.state, indent=2))


# =============================================================================
# SyncEngine Class
# =============================================================================


class SyncEngine:
    """Orchestrate syncing from Beads to Fizzy."""

    def __init__(
        self,
        config: Config,
        client: FizzyClient,
        reader: BeadsReader,
        state: SyncState,
        mapper: Mapper,
    ):
        self.config = config
        self.client = client
        self.reader = reader
        self.state = state
        self.mapper = mapper
        self.column_cache: dict[str, str] = {}

    def sync_all(self, include_closed: bool = False, dry_run: bool = False) -> dict:
        """Sync all issues from Beads to Fizzy."""
        if not dry_run:
            self._ensure_columns_exist()

        issues = self.reader.all_issues(include_closed=include_closed)
        results = {"created": 0, "updated": 0, "skipped": 0, "errors": []}

        for issue in issues:
            result = self.sync_issue(issue, dry_run=dry_run)
            if result["action"] == "created":
                results["created"] += 1
            elif result["action"] == "updated":
                results["updated"] += 1
            elif result["action"] == "skipped":
                results["skipped"] += 1
            elif result["action"] == "error":
                results["errors"].append(result)

        return results

    def sync_issue(self, issue: dict, dry_run: bool = False) -> dict:
        """Sync a single issue."""
        beads_id = issue["id"]
        checksum = self._calculate_checksum(issue)

        # Skip if unchanged
        if self.state.checksum_for(beads_id) == checksum:
            return {"action": "skipped", "beads_id": beads_id, "reason": "unchanged"}

        if dry_run:
            if self.state.card_number_for(beads_id):
                return {"action": "updated", "beads_id": beads_id, "dry_run": True}
            else:
                return {"action": "created", "beads_id": beads_id, "dry_run": True}

        try:
            card_data = self.mapper.beads_to_fizzy_card(issue)
            column_name = self.mapper.column_for_status(issue["status"])
            column_id = self._get_column_id(column_name)

            if card_number := self.state.card_number_for(beads_id):
                self._update_card(card_number, issue, card_data, column_id)
                self.state.record_sync(beads_id, card_number, checksum)
                return {
                    "action": "updated",
                    "beads_id": beads_id,
                    "card_number": card_number,
                }
            else:
                card_number = self._create_card(issue, card_data, column_id)
                self.state.record_sync(beads_id, card_number, checksum)
                return {
                    "action": "created",
                    "beads_id": beads_id,
                    "card_number": card_number,
                }
        except Exception as e:
            return {"action": "error", "beads_id": beads_id, "error": str(e)}

    def _ensure_columns_exist(self) -> None:
        """Create missing columns (Doing, Blocked).

        Note: We only need columns for active work states:
        - "open" issues stay in Fizzy's built-in Maybe? (the inbox/backlog)
        - "closed" issues go to Fizzy's built-in Done
        """
        if not self.config.sync_options.get("auto_create_columns", True):
            # Just load existing columns
            existing = self.client.list_columns(self.config.board_id)
            self.column_cache = {c["name"]: c["id"] for c in existing}
            return

        existing = self.client.list_columns(self.config.board_id)
        self.column_cache = {c["name"]: c["id"] for c in existing}

        for name in ["Doing", "Blocked"]:
            if name not in self.column_cache:
                color = self.mapper.color_for_column(name)
                console.print(f"  Creating column: [cyan]{name}[/cyan]")
                self.client.create_column(
                    self.config.board_id, name=name, color=color
                )
                # Re-fetch columns to get proper IDs
                existing = self.client.list_columns(self.config.board_id)
                self.column_cache = {c["name"]: c["id"] for c in existing}

    def _create_card(self, issue: dict, card_data: dict, column_id: str) -> int:
        """Create new card and triage to column."""
        # 1. Create card
        response = self.client.create_card(
            board_id=self.config.board_id,
            title=card_data["title"],
            description=card_data["description"],
        )
        card_number = self._extract_card_number(response)

        # 2. Triage to column
        if column_id:
            self.client.triage_card(card_number, column_id)

        # 3. Handle closed status
        if issue["status"] == "closed":
            self.client.close_card(card_number)

        # 4. Add tags
        if self.config.sync_options.get("priority_as_tag", True) or self.config.sync_options.get("type_as_tag", True):
            for tag in self.mapper.tags_for_issue(issue):
                try:
                    self.client.toggle_tag(card_number, tag)
                except Exception:
                    pass  # Tags may not exist, continue

        return card_number

    def _update_card(
        self, card_number: int, issue: dict, card_data: dict, column_id: str
    ) -> None:
        """Update existing card."""
        # 1. Update content
        self.client.update_card(
            card_number,
            title=card_data["title"],
            description=card_data["description"],
        )

        # 2. Move to correct column (or back to Maybe? if open)
        if column_id:
            self.client.triage_card(card_number, column_id)
        elif issue["status"] == "open":
            # "open" status means back to Maybe? (untriage)
            try:
                self.client.untriage_card(card_number)
            except Exception:
                pass  # May not be triaged

        # 3. Handle closed/reopened
        if issue["status"] == "closed":
            try:
                self.client.close_card(card_number)
            except Exception:
                pass  # May already be closed
        else:
            try:
                self.client.reopen_card(card_number)
            except Exception:
                pass  # May not be closed

    def _calculate_checksum(self, issue: dict) -> str:
        """Calculate checksum for change detection."""
        # Include relevant fields that would trigger an update
        data = {
            "id": issue.get("id"),
            "title": issue.get("title"),
            "description": issue.get("description"),
            "status": issue.get("status"),
            "priority": issue.get("priority"),
            "issue_type": issue.get("issue_type"),
            "labels": issue.get("labels"),
        }
        return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()[
            :16
        ]

    def _get_column_id(self, column_name: str) -> str:
        """Get column ID by name."""
        return self.column_cache.get(column_name, "")

    def _extract_card_number(self, response: dict) -> int:
        """Extract card number from API response."""
        if "number" in response:
            return response["number"]
        if "url" in response:
            match = re.search(r"/cards/(\d+)", response["url"])
            if match:
                return int(match.group(1))
        raise ValueError(f"Could not extract card number from response: {response}")


# =============================================================================
# CLI Commands
# =============================================================================

CONFIG_TEMPLATE = """# Fizzy-Beads Sync Configuration
# TIP: Run 'bizzy wizard' for interactive setup that fills these automatically!

fizzy:
  base_url: https://app.fizzy.do  # Or your self-hosted URL
  account_slug: "YOUR_ACCOUNT_SLUG"  # Found in URL: fizzy.do/{slug}/boards/...
  api_token: ${FIZZY_API_TOKEN}  # Set via environment variable

board:
  id: "YOUR_BOARD_ID"  # Found in URL: fizzy.do/.../boards/{id}

# Column mapping (Beads status → Fizzy column name)
# - "open" stays in Maybe? (Fizzy's built-in inbox = your backlog)
# - "closed" goes to Done (Fizzy's built-in)
columns:
  in_progress: Doing
  blocked: Blocked

sync:
  auto_triage: true
  auto_create_columns: true
  include_closed: false
  priority_as_tag: true
  type_as_tag: true

beads:
  path: "."
"""


# =============================================================================
# Wizard Command - Interactive Setup
# =============================================================================


def _wizard_prompt(prompt: str, default: str | None = None) -> str:
    """Prompt user for input with optional default."""
    if default:
        display = f"{prompt} [{default}]: "
    else:
        display = f"{prompt}: "

    response = input(display).strip()
    if not response and default:
        return default
    return response


def _wizard_choice(prompt: str, choices: list[str], default: int = 1) -> int:
    """Present numbered choices and return selection (1-indexed)."""
    console.print(f"\n{prompt}")
    for i, choice in enumerate(choices, 1):
        marker = "→" if i == default else " "
        console.print(f"  {marker} {i}. {choice}")

    while True:
        response = input(f"\nEnter choice [1-{len(choices)}] (default: {default}): ").strip()
        if not response:
            return default
        try:
            choice = int(response)
            if 1 <= choice <= len(choices):
                return choice
        except ValueError:
            pass
        console.print(f"[yellow]Please enter a number between 1 and {len(choices)}[/yellow]")


def cmd_wizard(args: argparse.Namespace) -> None:
    """Interactive setup wizard - guides you through complete Bizzy configuration."""
    console.print("\n[bold cyan]╔══════════════════════════════════════════════════════════════╗[/bold cyan]")
    console.print("[bold cyan]║           🧙 Bizzy Setup Wizard                              ║[/bold cyan]")
    console.print("[bold cyan]║     Connect Beads issues to your Fizzy Kanban board          ║[/bold cyan]")
    console.print("[bold cyan]╚══════════════════════════════════════════════════════════════╝[/bold cyan]")

    # ==========================================================================
    # Pre-check: Is Beads installed and initialized?
    # ==========================================================================
    beads_dir = Path(".beads")
    beads_db = beads_dir / "beads.db"

    # Check if bd command is available
    import shutil
    bd_installed = shutil.which("bd") is not None

    if not beads_dir.exists() or not beads_db.exists():
        console.print("\n[yellow]⚠ Beads is not initialized in this directory![/yellow]")
        console.print("""
[dim]Bizzy syncs issues from Beads to Fizzy. You need Beads set up
first so there's something to sync.[/dim]
""")

        if not bd_installed:
            console.print("""[cyan]Step 1: Install Beads[/cyan]

  pipx install beads-cli

  [dim]Or with pip:[/dim]
  pip install beads-cli

  [dim]Or if you use Claude Code, install the Beads plugin:[/dim]
  claude mcp add beads -- npx -y beads-mcp@latest
""")
            console.print("""[cyan]Step 2: Initialize Beads in your project[/cyan]

  bd init

  [dim]Or in Claude Code:[/dim]
  /beads:init
""")
        else:
            console.print("""[cyan]Initialize Beads in your project:[/cyan]

  bd init

  [dim]Or if you're using Claude Code:[/dim]
  /beads:init
""")

        console.print("[dim]Then run this wizard again.[/dim]\n")
        response = _wizard_prompt("Continue anyway? (y/N)", "n")
        if response.lower() != "y":
            if not bd_installed:
                console.print("Install Beads first, then come back!")
            else:
                console.print("Run [cyan]bd init[/cyan] first, then come back!")
            return
        console.print("\n[dim]Continuing without Beads... (you'll need to set it up before syncing)[/dim]")

    # Check for existing config
    config_path = Path(".fizzy-sync.yml")
    if config_path.exists() and not args.force:
        console.print(f"\n[yellow]Config file already exists: {config_path}[/yellow]")
        response = _wizard_prompt("Overwrite? (y/N)", "n")
        if response.lower() != "y":
            console.print("Wizard cancelled.")
            return

    # ==========================================================================
    # STEP 1: Hosted vs Local
    # ==========================================================================
    console.print("\n[bold]━━━ Step 1: Where is Fizzy running? ━━━[/bold]")

    hosting_choice = _wizard_choice(
        "How are you accessing Fizzy?",
        [
            "Hosted (app.fizzy.do) - the official cloud service",
            "Self-hosted / Local (running on your own server)",
        ],
        default=1
    )

    if hosting_choice == 1:
        base_url = "https://app.fizzy.do"
        console.print(f"\n[green]✓[/green] Using hosted Fizzy at [cyan]{base_url}[/cyan]")
    else:
        console.print("\n[dim]Enter your Fizzy server URL (e.g., http://fizzy.localhost:3006)[/dim]")
        base_url = _wizard_prompt("Fizzy URL", "http://fizzy.localhost:3006")
        console.print(f"[green]✓[/green] Using self-hosted Fizzy at [cyan]{base_url}[/cyan]")

    # ==========================================================================
    # STEP 2: API Token
    # ==========================================================================
    console.print("\n[bold]━━━ Step 2: Get your API Token ━━━[/bold]")

    if hosting_choice == 1:
        console.print("""
[cyan]To get your API token from app.fizzy.do:[/cyan]

  1. Log in to [link=https://app.fizzy.do]app.fizzy.do[/link]
  2. Click [bold]Fizzy[/bold] menu at the top
  3. Select [bold]Personal settings[/bold]
  4. Scroll down to [bold]Access tokens[/bold]
  5. Click [bold]Create access token[/bold]
  6. Give it a name like "Bizzy Sync" and click [bold]Create[/bold]
  7. [yellow]Copy the token immediately[/yellow] - it won't be shown again!

[dim]The token looks like: aB3cD7eF9gH2jK5mN8pQ4rS6[/dim]
""")
    else:
        console.print("""
[cyan]To get your API token from your self-hosted Fizzy:[/cyan]

  1. Log in to your Fizzy instance
  2. Click [bold]Fizzy[/bold] menu at the top
  3. Select [bold]Personal settings[/bold]
  4. Scroll down to [bold]Access tokens[/bold]
  5. Click [bold]Create access token[/bold]
  6. Give it a name like "Bizzy Sync" and click [bold]Create[/bold]
  7. [yellow]Copy the token immediately[/yellow] - it won't be shown again!
""")

    # Get and validate token
    api_token = ""
    while not api_token:
        api_token = _wizard_prompt("Paste your API token here").strip()
        if not api_token:
            console.print("[yellow]Token is required to continue[/yellow]")

    console.print("\n[dim]Validating token...[/dim]")

    # Test the token
    try:
        test_client = FizzyClient(base_url, "", api_token)
        identity = test_client.get_identity()
        accounts = identity.get("accounts", [])
        test_client.close()

        if not accounts:
            console.print("[red]✗ Token valid but no accounts found![/red]")
            console.print("Make sure you have access to at least one Fizzy account.")
            return

        console.print(f"[green]✓[/green] Token valid! Found {len(accounts)} account(s)")

    except httpx.HTTPStatusError as e:
        if e.response.status_code == 401:
            console.print("[red]✗ Invalid token! Please check and try again.[/red]")
        else:
            console.print(f"[red]✗ API error: {e.response.status_code}[/red]")
        return
    except Exception as e:
        console.print(f"[red]✗ Connection failed: {e}[/red]")
        console.print(f"[dim]Could not connect to {base_url}[/dim]")
        return

    # ==========================================================================
    # STEP 3: Select Account
    # ==========================================================================
    console.print("\n[bold]━━━ Step 3: Select Account ━━━[/bold]")

    if len(accounts) == 1:
        account = accounts[0]
        console.print(f"[green]✓[/green] Using account: [cyan]{account['name']}[/cyan] ({account['slug']})")
    else:
        account_names = [f"{a['name']} ({a['slug']})" for a in accounts]
        choice = _wizard_choice("Select an account:", account_names)
        account = accounts[choice - 1]
        console.print(f"[green]✓[/green] Selected: [cyan]{account['name']}[/cyan]")

    account_slug = account["slug"].lstrip("/")

    # ==========================================================================
    # STEP 4: Select or Create Board
    # ==========================================================================
    console.print("\n[bold]━━━ Step 4: Select or Create Board ━━━[/bold]")

    client = FizzyClient(base_url, account_slug, api_token)

    try:
        boards = client.list_boards()
    except Exception as e:
        console.print(f"[red]✗ Could not fetch boards: {e}[/red]")
        client.close()
        return

    board_options = ["[Create a new board]"] + [f"{b['name']}" for b in boards]
    choice = _wizard_choice("Select a board or create new:", board_options)

    if choice == 1:
        # Create new board - default to current folder name
        default_board_name = Path.cwd().name.replace("-", " ").replace("_", " ").title()
        board_name = _wizard_prompt("New board name", default_board_name)
        try:
            new_board = client.create_board(board_name)
            board_id = new_board["id"]
            console.print(f"[green]✓[/green] Created board: [cyan]{board_name}[/cyan]")

            # Set up triage columns for active work states
            # Fizzy has built-in columns that we leverage:
            # - "Maybe?" = inbox/backlog (where "open" issues stay)
            # - "Done" = closed cards
            # We only need custom columns for: Doing, Blocked
            console.print("[dim]Setting up columns (Doing, Blocked)...[/dim]")

            beads_columns = [
                ("Doing", "var(--color-card-4)"),          # Lime
                ("Blocked", "var(--color-card-8)"),        # Pink
            ]

            existing_names = {c["name"].lower() for c in client.list_columns(board_id)}
            for name, color in beads_columns:
                if name.lower() not in existing_names:
                    client.create_column(board_id, name, color)

            console.print("[green]✓[/green] Columns configured")
            console.print("[dim]  'open' issues → Maybe? (backlog)  |  'closed' → Done[/dim]")

        except Exception as e:
            console.print(f"[red]✗ Could not create board: {e}[/red]")
            client.close()
            return
    else:
        board = boards[choice - 2]  # -2 because option 1 is "create new"
        board_id = board["id"]
        console.print(f"[green]✓[/green] Selected board: [cyan]{board['name']}[/cyan]")

        # Check if board has the right columns
        # We only need Doing, Blocked - Fizzy's built-in Maybe?/Done handle open/closed
        columns = client.list_columns(board_id)
        column_names = {c["name"].lower() for c in columns}
        needed = {"doing", "blocked"}
        missing = needed - column_names

        if missing:
            console.print(f"\n[yellow]Board is missing columns: {', '.join(missing)}[/yellow]")
            response = _wizard_prompt("Add missing columns? (Y/n)", "y")
            if response.lower() != "n":
                beads_columns = {
                    "doing": ("Doing", "var(--color-card-4)"),
                    "blocked": ("Blocked", "var(--color-card-8)"),
                }
                for col in missing:
                    name, color = beads_columns[col]
                    client.create_column(board_id, name, color)
                console.print("[green]✓[/green] Added missing columns")

    client.close()

    # ==========================================================================
    # STEP 5: Token Storage Choice
    # ==========================================================================
    console.print("\n[bold]━━━ Step 5: How to store your token? ━━━[/bold]")

    storage_choice = _wizard_choice(
        "How would you like to store your API token?",
        [
            "Environment variable (recommended) - more secure",
            "Directly in config file - simpler but less secure",
        ],
        default=1
    )

    if storage_choice == 1:
        token_value = "${FIZZY_API_TOKEN}"
        console.print(f"""
[green]✓[/green] Token will be read from [cyan]FIZZY_API_TOKEN[/cyan] environment variable

[yellow]Add this to your shell profile (.bashrc, .zshrc, etc.):[/yellow]

  export FIZZY_API_TOKEN="{api_token}"

[dim]Or set it just for this session:[/dim]

  export FIZZY_API_TOKEN="{api_token}"
""")
    else:
        token_value = api_token
        console.print("\n[yellow]⚠ Token will be stored in plain text in .fizzy-sync.yml[/yellow]")
        console.print("[dim]Consider adding .fizzy-sync.yml to .gitignore[/dim]")

    # ==========================================================================
    # STEP 6: Write Config
    # ==========================================================================
    console.print("\n[bold]━━━ Step 6: Save Configuration ━━━[/bold]")

    config_content = f"""# Fizzy-Beads Sync Configuration
# Generated by Bizzy Setup Wizard

fizzy:
  base_url: {base_url}
  account_slug: "{account_slug}"
  api_token: {token_value}

board:
  id: "{board_id}"

# Column mapping (Beads status → Fizzy column name)
# - "open" stays in Maybe? (Fizzy's built-in inbox = your backlog)
# - "closed" goes to Done (Fizzy's built-in)
columns:
  in_progress: Doing
  blocked: Blocked

sync:
  auto_triage: true
  auto_create_columns: true
  include_closed: false
  priority_as_tag: true
  type_as_tag: true

beads:
  path: "."
"""

    config_path.write_text(config_content)
    console.print(f"[green]✓[/green] Configuration saved to [cyan]{config_path}[/cyan]")

    # ==========================================================================
    # DONE! - Ask if they want to start watching
    # ==========================================================================
    console.print("\n[bold green]╔══════════════════════════════════════════════════════════════╗[/bold green]")
    console.print("[bold green]║                    🎉 Setup Complete!                        ║[/bold green]")
    console.print("[bold green]╚══════════════════════════════════════════════════════════════╝[/bold green]")

    # Re-check if Beads is initialized (user might have done it during wizard)
    beads_db = Path(".beads") / "beads.db"
    if beads_db.exists():
        console.print("\n[cyan]Ready to start syncing![/cyan]")
        start_watch = _wizard_prompt("Start watching for changes now? (Y/n)", "y")

        if start_watch.lower() != "n":
            if storage_choice == 1:
                console.print("\n[dim]Tip: Add this to ~/.zshrc for future sessions:[/dim]")
                console.print(f"  export FIZZY_API_TOKEN=\"{api_token}\"\n")

            # Start watcher in background
            _start_background_watcher(api_token if storage_choice == 2 else None)
            return

    # If not starting watch, show next steps
    if storage_choice == 1:
        console.print(f"""
[yellow]Before running Bizzy, set your token:[/yellow]

  export FIZZY_API_TOKEN="{api_token}"
""")

    console.print("""[cyan]Next steps:[/cyan]

  1. [bold]Start watching:[/bold]        bizzy watch

[dim]Bizzy will automatically sync your Beads issues to Fizzy![/dim]
""")


def cmd_init(args: argparse.Namespace) -> None:
    """Create .fizzy-sync.yml config file."""
    config_path = Path(".fizzy-sync.yml")
    result = init_config(config_path, force=args.force)

    if result.already_exists:
        console.print("[yellow]Config file already exists![/yellow]")
        console.print("Use --force to overwrite.")
        return

    if result.success:
        console.print(f"[green]Created {result.config_path}[/green]")
        console.print("\nNext steps:")
        console.print("  1. Set FIZZY_API_TOKEN environment variable")
        console.print("  2. Run: uv run fizzy-sync.py auth")


def cmd_auth(args: argparse.Namespace) -> None:
    """Test API connection."""
    try:
        config = Config.load(args.config)
    except FileNotFoundError as e:
        console.print(f"[red]{e}[/red]")
        return

    if not config.fizzy_api_token:
        console.print("[red]API token not set![/red]")
        console.print("Set FIZZY_API_TOKEN environment variable.")
        return

    client = FizzyClient(
        config.fizzy_base_url, config.fizzy_account_slug, config.fizzy_api_token
    )

    try:
        result = verify_auth(config, client)

        if not result.success:
            if result.error_code:
                console.print(f"[red]Auth failed: {result.error_code}[/red]")
                console.print("Check your API token.")
            else:
                console.print(f"[red]{result.error}[/red]")
            return

        console.print("[green]Connected![/green]")
        if result.user_name:
            console.print(f"  User: {result.user_name} ({result.user_email})")
            console.print(f"  Account: {result.account_name} ({result.account_slug})")

        if result.board_name:
            console.print(f"  Board: {result.board_name} ({result.board_id})")
        elif result.error:
            console.print(f"  [yellow]{result.error}[/yellow]")

    finally:
        client.close()


def cmd_setup(args: argparse.Namespace) -> None:
    """Set up a Fizzy board for Beads sync.

    Creates columns for active work states: Doing, Blocked.
    Fizzy's built-in Maybe? = backlog, Done = closed.
    """
    try:
        config = Config.load(args.config)
    except FileNotFoundError as e:
        console.print(f"[red]{e}[/red]")
        return

    if not config.fizzy_api_token:
        console.print("[red]API token not set![/red]")
        return

    client = FizzyClient(
        config.fizzy_base_url, config.fizzy_account_slug, config.fizzy_api_token
    )
    mapper = Mapper()

    try:
        if args.new_board:
            console.print(f"[cyan]Creating board:[/cyan] {args.new_board}")

        result = setup_board(
            config, client, mapper,
            new_board=args.new_board,
            reset=args.reset,
            force=args.force,
        )

        if not result.success:
            console.print(f"[red]{result.error}[/red]")
            return

        console.print(f"\n[cyan]Board:[/cyan] {result.board_name}")

        if result.columns_deleted:
            console.print("\n[cyan]Removed columns:[/cyan]")
            for name in result.columns_deleted:
                console.print(f"  [dim]Deleted: {name}[/dim]")

        console.print("\n[cyan]Beads columns:[/cyan]")
        for name in result.columns_existing:
            console.print(f"  [dim]{name} (already exists)[/dim]")
        for name in result.columns_created:
            console.print(f"  [green]{name}[/green]")

        console.print("\n[green]Board setup complete![/green]")
        if args.new_board:
            console.print("\n[yellow]Don't forget to update .fizzy-sync.yml:[/yellow]")
            console.print("  board:")
            console.print(f"    id: \"{result.board_id}\"")

    finally:
        client.close()


def cmd_status(args: argparse.Namespace) -> None:
    """Show sync status."""
    try:
        config = Config.load(args.config)
    except FileNotFoundError as e:
        console.print(f"[red]{e}[/red]")
        return

    try:
        reader = BeadsReader(config.beads_path)
    except FileNotFoundError as e:
        console.print(f"[red]{e}[/red]")
        return

    state = SyncState(config.beads_path)
    status = get_status(config, reader, state)

    console.print("\n[bold]Beads Status[/bold]")
    console.print(f"  Open issues: {status.open_issues}")
    console.print(f"  Total issues: {status.total_issues}")

    console.print("\n[bold]Sync Status[/bold]")
    console.print(f"  Synced to Fizzy: {status.synced_count}")
    console.print(f"  Last sync: {status.last_sync or 'Never'}")

    if status.pending_sync:
        console.print(f"  [yellow]Pending sync: {status.pending_sync} issues[/yellow]")


def cmd_sync(args: argparse.Namespace) -> None:
    """Sync issues to Fizzy."""
    try:
        config = Config.load(args.config)
    except FileNotFoundError as e:
        console.print(f"[red]{e}[/red]")
        return

    if not config.fizzy_api_token:
        console.print("[red]API token not set![/red]")
        console.print("Set FIZZY_API_TOKEN environment variable.")
        return

    try:
        reader = BeadsReader(config.beads_path)
    except FileNotFoundError as e:
        console.print(f"[red]{e}[/red]")
        return

    client = FizzyClient(
        config.fizzy_base_url, config.fizzy_account_slug, config.fizzy_api_token
    )
    state = SyncState(config.beads_path)
    mapper = Mapper(config.column_mapping)
    engine = SyncEngine(config, client, reader, state, mapper)

    try:
        if args.issue:
            # Sync specific issue
            issue = reader.get_issue(args.issue)
            if not issue:
                console.print(f"[red]Issue {args.issue} not found[/red]")
                return

            if args.dry_run:
                console.print(f"[yellow]Dry run:[/yellow] Would sync {args.issue}")
                return

            result = engine.sync_issue(issue)
            if result["action"] == "error":
                console.print(f"[red]Error: {result['error']}[/red]")
            else:
                console.print(
                    f"[green]Synced {result['beads_id']} → Card #{result.get('card_number', 'N/A')}[/green]"
                )
        else:
            # Sync all issues
            include_closed = args.include_closed or config.sync_options.get(
                "include_closed", False
            )

            if args.dry_run:
                console.print("[yellow]Dry run mode[/yellow]")

            console.print("Syncing issues to Fizzy...")
            results = engine.sync_all(
                include_closed=include_closed, dry_run=args.dry_run
            )

            # Show results
            total = results["created"] + results["updated"]
            console.print(
                f"\n[green]{'Would sync' if args.dry_run else 'Synced'} {total} issues[/green]"
            )

            table = Table(show_header=False, box=None)
            table.add_row("  Created:", str(results["created"]))
            table.add_row("  Updated:", str(results["updated"]))
            table.add_row("  Skipped:", str(results["skipped"]))
            console.print(table)

            if results["errors"]:
                console.print(f"\n[red]Errors: {len(results['errors'])}[/red]")
                for err in results["errors"][:5]:  # Show first 5 errors
                    console.print(f"  - {err['beads_id']}: {err['error']}")

    except httpx.HTTPStatusError as e:
        console.print(f"[red]API error: {e.response.status_code}[/red]")
        if args.verbose:
            console.print(e.response.text)
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        if args.verbose:
            import traceback

            traceback.print_exc()
    finally:
        client.close()


def _start_background_watcher(api_token: str | None = None) -> None:
    """Start the watcher as a background process."""
    import os
    import shutil
    import subprocess

    # Find bizzy executable
    bizzy_path = shutil.which("bizzy")
    if not bizzy_path:
        console.print("[yellow]Could not find bizzy in PATH, using 'uv run bizzy'[/yellow]")
        cmd = ["uv", "run", "bizzy", "watch"]
    else:
        cmd = [bizzy_path, "watch"]

    # Set up environment
    env = os.environ.copy()
    if api_token:
        env["FIZZY_API_TOKEN"] = api_token

    # Start as detached background process
    try:
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            env=env,
            start_new_session=True,  # Detach from terminal
        )
        console.print(f"[green]✓[/green] Watcher started in background (PID: {process.pid})")
        console.print(f"[dim]  Stop with: kill {process.pid}[/dim]")
        console.print("[dim]  View logs: bizzy watch (foreground)[/dim]")
    except Exception as e:
        console.print(f"[red]Failed to start background watcher: {e}[/red]")
        console.print("[dim]Run manually with: bizzy watch[/dim]")


def _run_watch_loop(config: Config, verbose: bool = False) -> None:
    """Run the watch loop with given config (shared by wizard and cmd_watch)."""
    from watchfiles import watch

    beads_path = config.beads_path
    db_path = beads_path / ".beads" / "beads.db"

    console.print("[cyan]Watching for beads changes...[/cyan]")
    console.print(f"  Database: {db_path}")
    console.print("  Press Ctrl+C to stop\n")

    # Do initial sync
    console.print("[dim]Running initial sync...[/dim]")
    _run_sync(config, quiet=not verbose)

    # Watch for changes
    try:
        for changes in watch(beads_path / ".beads", watch_filter=lambda _, path: path.endswith(('beads.db', 'issues.jsonl'))):
            # Debounce: watchfiles already batches rapid changes
            changed_files = [str(path) for _, path in changes]

            if any('beads.db' in f or 'issues.jsonl' in f for f in changed_files):
                console.print(f"\n[dim]{datetime.now().strftime('%H:%M:%S')}[/dim] Change detected, syncing...")
                _run_sync(config, quiet=not verbose)
    except KeyboardInterrupt:
        console.print("\n[yellow]Watch stopped.[/yellow]")


def cmd_watch(args: argparse.Namespace) -> None:
    """Watch for beads changes and auto-sync."""
    try:
        config = Config.load(args.config)
    except FileNotFoundError as e:
        console.print(f"[red]{e}[/red]")
        return

    if not config.fizzy_api_token:
        console.print("[red]API token not set![/red]")
        console.print("Set FIZZY_API_TOKEN environment variable.")
        return

    beads_path = config.beads_path
    db_path = beads_path / ".beads" / "beads.db"

    if not db_path.exists():
        console.print(f"[red]Beads database not found at {db_path}[/red]")
        return

    _run_watch_loop(config, verbose=args.verbose)


def _run_sync(config: Config, quiet: bool = False, include_closed: bool = True) -> None:
    """Run sync with given config (helper for watch mode)."""
    try:
        reader = BeadsReader(config.beads_path)
        client = FizzyClient(
            config.fizzy_base_url, config.fizzy_account_slug, config.fizzy_api_token
        )
        state = SyncState(config.beads_path)
        mapper = Mapper(config.column_mapping)
        engine = SyncEngine(config, client, reader, state, mapper)

        # Watch mode includes closed issues so they move to Done column
        results = engine.sync_all(include_closed=include_closed)

        total = results["created"] + results["updated"]
        if total > 0 or not quiet:
            console.print(f"  [green]Synced: {results['created']} created, {results['updated']} updated, {results['skipped']} skipped[/green]")

        if results["errors"]:
            console.print(f"  [red]Errors: {len(results['errors'])}[/red]")

        client.close()
    except Exception as e:
        console.print(f"  [red]Sync error: {e}[/red]")


def main() -> None:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        prog="bizzy",
        description="Sync Beads issues to Fizzy Kanban boards",
    )
    parser.add_argument(
        "--config",
        type=Path,
        help="Config file path (default: .fizzy-sync.yml)",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Verbose output",
    )

    subparsers = parser.add_subparsers(dest="command", help="Commands")

    # wizard command (first, as it's the recommended starting point)
    wizard_parser = subparsers.add_parser(
        "wizard", help="Interactive setup wizard (recommended for first-time setup)"
    )
    wizard_parser.add_argument(
        "--force", action="store_true", help="Overwrite existing config"
    )

    # init command
    init_parser = subparsers.add_parser("init", help="Create .fizzy-sync.yml config")
    init_parser.add_argument(
        "--force", action="store_true", help="Overwrite existing config"
    )

    # auth command
    subparsers.add_parser("auth", help="Test API connection")

    # setup command
    setup_parser = subparsers.add_parser(
        "setup", help="Set up Fizzy board for Beads sync"
    )
    setup_parser.add_argument(
        "--new-board",
        type=str,
        metavar="NAME",
        help="Create a new board with this name",
    )
    setup_parser.add_argument(
        "--reset",
        action="store_true",
        help="Reset existing board columns (delete all, create Beads columns)",
    )
    setup_parser.add_argument(
        "--force",
        action="store_true",
        help="Skip confirmation prompt when deleting columns",
    )

    # status command
    subparsers.add_parser("status", help="Show sync status")

    # sync command
    sync_parser = subparsers.add_parser("sync", help="Sync issues to Fizzy")
    sync_parser.add_argument(
        "--all",
        action="store_true",
        help="Full sync (same as default)",
    )
    sync_parser.add_argument(
        "--issue",
        type=str,
        help="Sync specific issue ID",
    )
    sync_parser.add_argument(
        "--include-closed",
        action="store_true",
        help="Include closed issues",
    )
    sync_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would sync without making changes",
    )

    # watch command
    watch_parser = subparsers.add_parser(
        "watch", help="Watch for beads changes and auto-sync"
    )
    watch_parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Show all sync output (not just changes)",
    )

    args = parser.parse_args()

    if args.command == "wizard":
        cmd_wizard(args)
    elif args.command == "init":
        cmd_init(args)
    elif args.command == "auth":
        cmd_auth(args)
    elif args.command == "setup":
        cmd_setup(args)
    elif args.command == "status":
        cmd_status(args)
    elif args.command == "sync":
        cmd_sync(args)
    elif args.command == "watch":
        cmd_watch(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
