# Bizzy

Sync issues from [Beads](https://github.com/steveyegge/beads) (git-backed CLI issue tracker) to [Fizzy](https://github.com/basecamp/fizzy) (Kanban board) via REST API.

**Let AI agents visualize their work on a Kanban board.**

📖 **[Complete Setup Guide](SETUP.md)** | 🏗️ **[Architecture Overview](ARCHITECTURE.md)**

## Features

- One-way sync from Beads to Fizzy
- **Self-healing sync** — automatically detects and corrects Fizzy drift
- Automatic column creation for active states (default: Doing, Blocked)
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

## Quick Start (2 minutes)

```bash
git clone https://github.com/hugo-alves/bizzy.git
cd bizzy

# Optional: install deps for local dev
uv sync

# From your Beads project directory:
uv run bizzy wizard
```

That’s it — the wizard sets up config, board, and your first sync.

## Quick Start

### Option 1: Interactive Wizard (Recommended for new users)

```bash
# Navigate to your Beads project
cd /path/to/your/project

# Run the interactive setup wizard
uv run bizzy wizard
```

The wizard guides you through the entire setup process interactively.

### Option 2: Manual Setup

```bash
# Navigate to your Beads project
cd /path/to/your/project

# Initialize config
uv run bizzy init

# Set your API token (see above)
export FIZZY_API_TOKEN="your-token-here"

# Test connection
uv run bizzy auth

# Set up board with correct columns
uv run bizzy setup --new-board "My Project"
# Update .fizzy-sync.yml with the board ID shown

# Sync issues
uv run bizzy sync

# Or run in watch mode (recommended)
uv run bizzy watch
```

## Configuration

The `init` command creates `.fizzy-sync.yml` in the current directory:

```yaml
# Fizzy API connection
fizzy:
  base_url: https://fizzy.example.com
  account_slug: "YOUR_ACCOUNT_SLUG"
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
  self_healing_interval: 300 # Seconds between self-healing checks (0 to disable)

# Beads source
beads:
  path: "."  # Path to repo with .beads/
```

## Commands

### `init`

Create a new `.fizzy-sync.yml` configuration file.

```bash
uv run bizzy init
uv run bizzy init --force  # Overwrite existing config
```

### `auth`

Test the API connection and display account info.

```bash
uv run bizzy auth
```

### `setup`

Set up a Fizzy board for Beads sync. Creates the custom columns (Doing, Blocked) needed for active work states. Fizzy's built-in Maybe? and Done columns handle open and closed issues.

```bash
# Create a new board (recommended for fresh start)
uv run bizzy setup --new-board "My Project"

# Reset existing board columns (removes duplicates)
uv run bizzy setup --reset --force

# Just add missing columns (non-destructive)
uv run bizzy setup
```

**Note:** When creating a new board, update `.fizzy-sync.yml` with the board ID shown in the output.

### `status`

Show sync status (Beads issue count, synced count, pending changes).

```bash
uv run bizzy status
```

### `sync`

Sync issues from Beads to Fizzy.

```bash
# Sync all open issues
uv run bizzy sync

# Sync a specific issue
uv run bizzy sync --issue bizzy-123

# Include closed issues
uv run bizzy sync --include-closed

# Preview changes without syncing
uv run bizzy sync --dry-run
```

### `watch`

**Recommended for continuous sync.** Watches the beads database and automatically syncs when changes are detected.

```bash
# Start watching (runs until Ctrl+C)
uv run bizzy watch

# Verbose mode - show all sync output
uv run bizzy watch -v
```

This is the best way to keep Fizzy in sync with Beads:
- Runs initial sync on startup
- Monitors `.beads/beads.db` for changes
- Auto-syncs within seconds of any beads operation
- Self-healing checks to correct Fizzy drift (every 5 minutes by default)
- No manual intervention needed

```bash
# Custom heal interval (in seconds)
uv run bizzy watch --heal-interval 600

# Disable self-healing
uv run bizzy watch --heal-interval 0
```

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

**Note:** Tags are additive. Bizzy adds missing tags but does not remove existing tags.

### Beads ID Tracking

Each Fizzy card includes a marker in its description to link back to the Beads issue:

```
Issue description here.

[beads:bizzy-123]
```

## Self-Healing Sync

Fizzy is a **visibility tool**, not the source of truth. Beads is the authoritative source for all issue data. The self-healing feature ensures Fizzy always reflects the true state in Beads, even when cards are manually modified in Fizzy.

### How It Works

1. **Drift Detection** — Periodically compares each synced Fizzy card against the corresponding Beads issue
2. **Auto-Correction** — When drift is detected (wrong column, outdated title/description, missing tags), the card is automatically updated to match Beads
3. **Non-Destructive** — Only updates cards that have drifted; cards already in sync are untouched

### What Gets Corrected

| Drift Type | Example | Correction |
|------------|---------|------------|
| Column mismatch | Card moved manually in Fizzy | Moved back to correct column based on Beads status |
| Title changed | Card renamed in Fizzy | Title restored from Beads |
| Description edited | Notes added in Fizzy | Description restored (with `[beads:id]` marker) |
| Tags modified | Priority tag removed | Tags restored from Beads priority/type/labels |

### Configuration

```yaml
# In .fizzy-sync.yml
sync:
  self_healing_interval: 300  # Check every 5 minutes (default)
```

Or via command line:

```bash
# Custom interval
uv run bizzy watch --heal-interval 600  # Every 10 minutes

# Disable self-healing
uv run bizzy watch --heal-interval 0
```

### Why Self-Healing?

- **Fizzy is for visibility** — Drag cards around to explore, but changes don't stick
- **Beads is the source of truth** — All real changes happen via `bd` commands
- **No surprises** — The board always reflects actual issue state
- **Team-friendly** — Multiple people can view Fizzy without corrupting data

> **Tip:** If you need to change an issue's status, use `bd start`, `bd block`, or `bd close` in the terminal. The change will sync to Fizzy automatically.

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
alias bizzy="uv run bizzy"
bizzy sync
```

### Automate with git hooks

Add to `.git/hooks/post-commit`:

```bash
#!/bin/bash
export FIZZY_API_TOKEN="your-token"
uv run bizzy sync --quiet
```

## Troubleshooting

### "Config file not found"

Run `bizzy init` to create the config file.

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

Use `bizzy setup --reset --force` to clean up and recreate the standard Beads columns.

## Development

### Running Tests

```bash
# Install dev dependencies and run tests
uv run --with pytest --with pytest-httpx pytest tests/ -v

# Or with the dev dependencies from pyproject.toml
uv sync --extra dev
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
