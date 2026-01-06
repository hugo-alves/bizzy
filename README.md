# Bizzy

Sync issues from [Beads](https://github.com/steveyegge/beads) (git-backed CLI issue tracker) to [Fizzy](https://github.com/basecamp/fizzy) (Kanban board) via REST API.

**Let AI agents visualize their work on a Kanban board.**

📖 **[Complete Setup Guide](SETUP.md)** | 🏗️ **[Architecture Overview](ARCHITECTURE.md)**

## Features

- One-way sync from Beads to Fizzy
- Automatic column creation (Doing, Blocked)
- Smart status mapping using Fizzy's built-in columns:
  - `open` → Maybe? (Fizzy's built-in inbox/backlog)
  - `in_progress` → Doing (custom column)
  - `blocked` → Blocked (custom column)
  - `closed` → Done (Fizzy's built-in)
- Priority and issue type as tags (P0-P4, bug, feature, task, epic, chore)
- Change detection via checksums (only syncs modified issues)
- State persistence (tracks which Beads issues map to which Fizzy cards)

## Requirements

- Python 3.11+
- [uv](https://github.com/astral-sh/uv) (handles dependencies automatically)
- A Beads project (`.beads/` directory)
- A Fizzy instance with API access

## Getting a Fizzy API Token

1. Log into your Fizzy account
2. Go to **Settings > API Tokens** (or navigate to `/settings/tokens`)
3. Click **Create Token**
4. Copy the generated token (it won't be shown again)

Store the token securely:
```bash
export FIZZY_API_TOKEN="your-token-here"
```

## Quick Start

### Option 1: Interactive Wizard (Recommended for new users)

```bash
# Navigate to your Beads project
cd /path/to/your/project

# Run the interactive setup wizard
uv run /path/to/fizzy_sync.py wizard
```

The wizard guides you through the entire setup process interactively.

### Option 2: Manual Setup

```bash
# Navigate to your Beads project
cd /path/to/your/project

# Initialize config
uv run /path/to/fizzy_sync.py init

# Set your API token (see above)
export FIZZY_API_TOKEN="your-token-here"

# Test connection
uv run /path/to/fizzy_sync.py auth

# Set up board with correct columns
uv run /path/to/fizzy_sync.py setup --new-board "My Project"
# Update .fizzy-sync.yml with the board ID shown

# Sync issues
uv run /path/to/fizzy_sync.py sync

# Or run in watch mode (recommended)
uv run /path/to/fizzy_sync.py watch
```

## Configuration

The `init` command creates `.fizzy-sync.yml` in the current directory:

```yaml
# Fizzy API connection
fizzy:
  base_url: http://fizzy.localhost:3006
  account_slug: "897362094"
  api_token: ${FIZZY_API_TOKEN}  # Use environment variable

# Target board
board:
  id: "your-board-id"

# Status -> Column mapping (only active work states need custom columns)
# "open" stays in Maybe? (Fizzy's built-in inbox = backlog)
# "closed" goes to Done (Fizzy's built-in)
columns:
  in_progress: Doing
  blocked: Blocked

# Sync options
sync:
  auto_triage: true          # Move cards to columns automatically
  auto_create_columns: true  # Create missing columns
  include_closed: false      # Skip closed issues by default
  priority_as_tag: true      # Add P0-P4 tags
  type_as_tag: true          # Add bug/feature/task tags

# Beads source
beads:
  path: "."  # Path to repo with .beads/
```

## Commands

### `init`

Create a new `.fizzy-sync.yml` configuration file.

```bash
uv run fizzy_sync.py init
uv run fizzy_sync.py init --force  # Overwrite existing config
```

### `auth`

Test the API connection and display account info.

```bash
uv run fizzy_sync.py auth
```

### `setup`

Set up a Fizzy board for Beads sync. Creates the custom columns (Doing, Blocked) needed for active work states. Fizzy's built-in Maybe? and Done columns handle open and closed issues.

```bash
# Create a new board (recommended for fresh start)
uv run fizzy_sync.py setup --new-board "My Project"

# Reset existing board columns (removes duplicates)
uv run fizzy_sync.py setup --reset --force

# Just add missing columns (non-destructive)
uv run fizzy_sync.py setup
```

**Note:** When creating a new board, update `.fizzy-sync.yml` with the board ID shown in the output.

### `status`

Show sync status (Beads issue count, synced count, pending changes).

```bash
uv run fizzy_sync.py status
```

### `sync`

Sync issues from Beads to Fizzy.

```bash
# Sync all open issues
uv run fizzy_sync.py sync

# Sync a specific issue
uv run fizzy_sync.py sync --issue bizzy-123

# Include closed issues
uv run fizzy_sync.py sync --include-closed

# Preview changes without syncing
uv run fizzy_sync.py sync --dry-run
```

### `watch`

**Recommended for continuous sync.** Watches the beads database and automatically syncs when changes are detected.

```bash
# Start watching (runs until Ctrl+C)
uv run fizzy_sync.py watch

# Verbose mode - show all sync output
uv run fizzy_sync.py watch -v
```

This is the best way to keep Fizzy in sync with Beads:
- Runs initial sync on startup
- Monitors `.beads/beads.db` for changes
- Auto-syncs within seconds of any beads operation
- No manual intervention needed

## Data Mapping

### Status to Column

| Beads Status | Fizzy Location | Type |
|--------------|----------------|------|
| `open` | Maybe? | Built-in (inbox/backlog) |
| `in_progress` | Doing | Custom column (Lime) |
| `blocked` | Blocked | Custom column (Pink) |
| `closed` | Done | Built-in |

> **Note:** Fizzy has built-in columns (Not Now, Maybe?, Done) that cannot be removed. We use Maybe? as the backlog and Done for closed issues, so we only create 2 custom columns: Doing and Blocked.

**Blocked status from dependencies:** Issues with `blocks` dependencies are automatically shown as blocked when their blocker is open, and return to Maybe? when the blocker is closed. This is derived from Beads' `blocked_issues_cache` table.

### Tags

- Priority: `P0`, `P1`, `P2`, `P3`, `P4`
- Issue Type: `bug`, `feature`, `task`, `epic`, `chore`
- Labels: Any custom labels from Beads

### Beads ID Tracking

Each Fizzy card includes a marker in its description to link back to the Beads issue:

```
Issue description here.

[beads:bizzy-123]
```

## State File

Sync state is stored in `.beads/.fizzy-sync-state.json`:

```json
{
  "synced_issues": {
    "bizzy-123": {
      "card_number": 42,
      "checksum": "abc123...",
      "synced_at": "2026-01-05T10:30:00"
    }
  },
  "last_sync": "2026-01-05T10:30:00"
}
```

This file tracks:
- Which Beads issues have been synced
- The corresponding Fizzy card number
- A checksum to detect changes

## Tips

### Create an alias

```bash
alias fizzy-sync="uv run /path/to/fizzy_sync.py"
fizzy-sync sync
```

### Automate with git hooks

Add to `.git/hooks/post-commit`:

```bash
#!/bin/bash
export FIZZY_API_TOKEN="your-token"
uv run /path/to/fizzy_sync.py sync --quiet
```

## Troubleshooting

### "Config file not found"

Run `fizzy_sync.py init` to create the config file.

### "API token not set"

Set the `FIZZY_API_TOKEN` environment variable:

```bash
export FIZZY_API_TOKEN="your-token-here"
```

### "Beads database not found"

Make sure you're in a directory with a `.beads/` folder, or run `bd init` to initialize Beads.

### Cards created but not in correct column

Check that the columns exist in Fizzy. If `auto_create_columns: true`, they should be created automatically.

### Duplicate columns on board

Use `fizzy_sync.py setup --reset --force` to clean up and recreate the standard Beads columns.

## Development

### Running Tests

```bash
# Install dev dependencies and run tests
uv run --with pytest --with pytest-httpx pytest tests/ -v

# Or with the dev dependencies from pyproject.toml
uv sync --group dev
uv run pytest tests/ -v
```

### Project Structure

```
fizzy-beads-sync/
  fizzy_sync.py      # Main script (runnable with uv run)
  pyproject.toml     # Package configuration
  tests/             # Test suite
    test_mapper.py   # Tests for data mapping
    test_config.py   # Tests for configuration
    test_client.py   # Tests for API client
```

## License

MIT
