"""Tests for the Config class."""

import sys
from pathlib import Path

import pytest

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from fizzy_sync import Config


class TestConfig:
    """Tests for Config class."""

    def test_load_basic_config(self, tmp_path):
        """Test loading a basic config file."""
        config_content = """
fizzy:
  base_url: http://localhost:3000
  account_slug: "12345"
  api_token: test-token

board:
  id: board-123

columns:
  open: Backlog
  in_progress: Doing
  blocked: Blocked
  closed: Done
"""
        config_file = tmp_path / ".fizzy-sync.yml"
        config_file.write_text(config_content)

        config = Config.load(config_file)

        assert config.fizzy_base_url == "http://localhost:3000"
        assert config.fizzy_account_slug == "12345"
        assert config.fizzy_api_token == "test-token"
        assert config.board_id == "board-123"

    def test_load_expands_env_vars(self, tmp_path, monkeypatch):
        """Test that environment variables are expanded."""
        monkeypatch.setenv("TEST_API_TOKEN", "secret-token-123")

        config_content = """
fizzy:
  base_url: http://localhost:3000
  account_slug: "12345"
  api_token: ${TEST_API_TOKEN}

board:
  id: board-123
"""
        config_file = tmp_path / ".fizzy-sync.yml"
        config_file.write_text(config_content)

        config = Config.load(config_file)

        assert config.fizzy_api_token == "secret-token-123"

    def test_load_missing_env_var_becomes_empty(self, tmp_path, monkeypatch):
        """Test that missing env vars become empty/None."""
        # Ensure the env var doesn't exist
        monkeypatch.delenv("NONEXISTENT_VAR", raising=False)

        config_content = """
fizzy:
  base_url: http://localhost:3000
  account_slug: "12345"
  api_token: ${NONEXISTENT_VAR}

board:
  id: board-123
"""
        config_file = tmp_path / ".fizzy-sync.yml"
        config_file.write_text(config_content)

        config = Config.load(config_file)

        # Missing env var results in empty string in YAML, which becomes None
        assert config.fizzy_api_token in ("", None)

    def test_load_file_not_found(self, tmp_path):
        """Test that missing config file raises FileNotFoundError."""
        nonexistent = tmp_path / "nonexistent.yml"

        with pytest.raises(FileNotFoundError):
            Config.load(nonexistent)

    def test_load_custom_column_mapping(self, tmp_path):
        """Test loading custom column mapping."""
        config_content = """
fizzy:
  base_url: http://localhost:3000
  account_slug: "12345"
  api_token: test

board:
  id: board-123

columns:
  open: Todo
  in_progress: Working
  blocked: Stuck
  closed: Finished
"""
        config_file = tmp_path / ".fizzy-sync.yml"
        config_file.write_text(config_content)

        config = Config.load(config_file)

        assert config.column_mapping["open"] == "Todo"
        assert config.column_mapping["in_progress"] == "Working"
        assert config.column_mapping["blocked"] == "Stuck"
        assert config.column_mapping["closed"] == "Finished"

    def test_load_sync_options(self, tmp_path):
        """Test loading sync options."""
        config_content = """
fizzy:
  base_url: http://localhost:3000
  account_slug: "12345"
  api_token: test

board:
  id: board-123

sync:
  auto_triage: false
  include_closed: true
  priority_as_tag: false
"""
        config_file = tmp_path / ".fizzy-sync.yml"
        config_file.write_text(config_content)

        config = Config.load(config_file)

        assert config.sync_options.get("auto_triage") is False
        assert config.sync_options.get("include_closed") is True
        assert config.sync_options.get("priority_as_tag") is False

    def test_load_beads_path(self, tmp_path):
        """Test loading custom beads path."""
        config_content = """
fizzy:
  base_url: http://localhost:3000
  account_slug: "12345"
  api_token: test

board:
  id: board-123

beads:
  path: "../other-project"
"""
        config_file = tmp_path / ".fizzy-sync.yml"
        config_file.write_text(config_content)

        config = Config.load(config_file)

        assert config.beads_path == Path("../other-project")

    def test_find_config_file_in_current_dir(self, tmp_path, monkeypatch):
        """Test finding config in current directory."""
        config_file = tmp_path / ".fizzy-sync.yml"
        config_file.write_text("fizzy:\n  base_url: http://test")

        monkeypatch.chdir(tmp_path)

        found = Config.find_config_file()
        assert found == config_file

    def test_find_config_file_in_parent_dir(self, tmp_path, monkeypatch):
        """Test finding config in parent directory."""
        config_file = tmp_path / ".fizzy-sync.yml"
        config_file.write_text("fizzy:\n  base_url: http://test")

        subdir = tmp_path / "subdir"
        subdir.mkdir()
        monkeypatch.chdir(subdir)

        found = Config.find_config_file()
        assert found == config_file

    def test_find_config_file_not_found(self, tmp_path, monkeypatch):
        """Test when no config file exists."""
        monkeypatch.chdir(tmp_path)

        found = Config.find_config_file()
        assert found is None
