# Complete Setup Guide

This guide walks you through setting up everything from scratch.

## Quickest Way: Use the Wizard

If you just want to get started quickly:

```bash
# Navigate to your project (must have .beads/ directory)
cd /path/to/your/project

# Run the interactive setup wizard
uv run /path/to/bizzy/fizzy_sync.py wizard
```

The wizard will guide you through:
1. Entering your Fizzy URL and account slug
2. Getting an API token
3. Creating or selecting a board
4. Running your first sync

If you prefer manual setup or the wizard doesn't work for your situation, follow the detailed steps below.

---

## Prerequisites

You'll need:
- **Python 3.11+** - Check with `python3 --version`
- **uv** - Python package runner ([install guide](https://github.com/astral-sh/uv))
- **Git** - For cloning repos

## Step 1: Install Beads

Beads is the issue tracker that AI agents use.

```bash
# Install the beads CLI
pip install beads
# or
uv tool install beads

# Verify installation
bd --version
```

**Alternative:** If you're using Claude Code, Beads may already be available as an MCP plugin.

## Step 2: Initialize Beads in Your Project

```bash
# Navigate to your project
cd /path/to/your/project

# Initialize Beads (creates .beads/ directory)
bd init

# Optionally set a custom prefix for issue IDs
bd init --prefix myproject  # Creates IDs like myproject-1, myproject-2
```

You should now have:
```
your-project/
  .beads/
    beads.db      # SQLite database
    config.json   # Beads configuration
```

## Step 3: Get Fizzy Access

Fizzy is the Kanban board where you'll see the cards.

### Option A: Self-hosted Fizzy
If you're running Fizzy locally or on your own server:
1. Follow the [Fizzy installation guide](https://github.com/basecamp/fizzy)
2. Note your Fizzy URL (e.g., `http://localhost:3000`)

### Option B: Hosted Fizzy
If using a hosted Fizzy instance:
1. Sign up / log in to your Fizzy instance
2. Note the base URL from your browser

## Step 4: Get a Fizzy API Token

1. Log into Fizzy in your browser
2. Go to **Settings** → **API Tokens**
   - Or navigate directly to: `{your-fizzy-url}/settings/tokens`
3. Click **Create Token**
4. Give it a name (e.g., "Bizzy Sync")
5. Copy the token immediately (it won't be shown again!)

Store the token securely:
```bash
# Add to your shell profile (~/.bashrc, ~/.zshrc, etc.)
export FIZZY_API_TOKEN="your-token-here"

# Or for this session only
export FIZZY_API_TOKEN="your-token-here"
```

## Step 5: Get Bizzy

### Option A: Clone the repo
```bash
git clone https://github.com/hugoalves/bizzy.git
cd bizzy
```

### Option B: Download just the script
```bash
# Download the sync script
curl -O https://raw.githubusercontent.com/hugoalves/bizzy/main/fizzy_sync.py
```

## Step 6: Initialize Bizzy Config

Navigate to your project (where `.beads/` exists):

```bash
cd /path/to/your/project

# Create the config file
uv run /path/to/bizzy/fizzy_sync.py init
```

This creates `.fizzy-sync.yml`:

```yaml
fizzy:
  base_url: http://localhost:3000    # ← Change to your Fizzy URL
  account_slug: "your-account"       # ← Change to your account slug
  api_token: ${FIZZY_API_TOKEN}      # ← Uses environment variable

board:
  id: "your-board-id"                # ← We'll get this in the next step

# Column mapping (only active work states need custom columns)
# "open" stays in Maybe? (Fizzy's built-in inbox = backlog)
# "closed" goes to Done (Fizzy's built-in)
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
```

### Finding Your Account Slug

Your account slug is in the Fizzy URL when you're logged in:
```
https://fizzy.example.com/897362094/boards/...
                         ▲
                         └── This is your account slug
```

## Step 7: Test the Connection

```bash
uv run /path/to/bizzy/fizzy_sync.py auth
```

Expected output:
```
✓ Connected!
  User: Your Name (your@email.com)
  Account: Your Account (897362094)
```

If it fails:
- Check your `FIZZY_API_TOKEN` is set: `echo $FIZZY_API_TOKEN`
- Check your `base_url` in the config
- Check your `account_slug` in the config

## Step 8: Create or Set Up a Board

### Option A: Create a New Board (Recommended)

```bash
uv run /path/to/bizzy/fizzy_sync.py setup --new-board "My Project"
```

Output:
```
Creating board: My Project
  ✓ Created! Board ID: 03fciv7v80pmuqn81be8aj8vg
  Update .fizzy-sync.yml with:
    board:
      id: "03fciv7v80pmuqn81be8aj8vg"

Creating custom columns...
  ✓ Doing (Lime)
  ✓ Blocked (Pink)

Board setup complete!
Note: Open issues go to Maybe? (built-in), closed to Done (built-in).
```

**Copy the board ID** and update your `.fizzy-sync.yml`:

```yaml
board:
  id: "03fciv7v80pmuqn81be8aj8vg"  # ← Paste here
```

### Option B: Use an Existing Board

1. Go to the board in Fizzy
2. Copy the board ID from the URL:
   ```
   https://fizzy.example.com/897362094/boards/03fcid9iealth0x5s199b55q4
                                               ▲
                                               └── This is the board ID
   ```
3. Update `.fizzy-sync.yml` with the board ID
4. Run setup to add missing columns:
   ```bash
   uv run /path/to/bizzy/fizzy_sync.py setup
   ```

## Step 9: Run Your First Sync

### Create a test issue in Beads:

```bash
bd create "Test issue from Beads" --type task --priority 2
```

### Sync to Fizzy:

```bash
uv run /path/to/bizzy/fizzy_sync.py sync
```

Output:
```
Syncing issues to Fizzy...
  Board: My Project
  Issues to sync: 1

  ✓ Created: bizzy-1 → Card #1

Sync complete: 1 created, 0 updated, 0 skipped
```

### Check Fizzy

Open your Fizzy board in the browser - you should see the card in the "Maybe?" column (the backlog)!

## Step 10: Enable Watch Mode (Recommended)

For automatic syncing, run watch mode:

```bash
uv run /path/to/bizzy/fizzy_sync.py watch
```

Output:
```
Starting watch mode...
  Watching: /path/to/your/project/.beads
  Press Ctrl+C to stop

Initial sync complete: 1 issues synced
Watching for changes...
```

Now any changes made by AI agents (or you) via `bd` commands will automatically appear on the Fizzy board within seconds.

### Run in Background

To keep watch mode running in the background:

```bash
# Using nohup
nohup uv run /path/to/bizzy/fizzy_sync.py watch > bizzy.log 2>&1 &

# Or using tmux/screen
tmux new-session -d -s bizzy 'uv run /path/to/bizzy/fizzy_sync.py watch'
```

## Quick Reference

Once set up, here are the common commands:

```bash
# Manual sync
uv run fizzy_sync.py sync

# Watch mode (auto-sync)
uv run fizzy_sync.py watch

# Check status
uv run fizzy_sync.py status

# Include closed issues
uv run fizzy_sync.py sync --include-closed

# Dry run (preview without syncing)
uv run fizzy_sync.py sync --dry-run
```

## Troubleshooting

### "Config file not found"
Run `fizzy_sync.py init` in your project directory.

### "API token not set"
```bash
export FIZZY_API_TOKEN="your-token"
```

### "Beads database not found"
Run `bd init` in your project directory.

### Cards not moving to correct columns
Run `fizzy_sync.py setup` to ensure all columns exist.

### Duplicate columns on board
Run `fizzy_sync.py setup --reset --force` to clean up.

## File Locations Summary

```
your-project/
├── .beads/
│   ├── beads.db                 # Beads SQLite database
│   ├── config.json              # Beads config
│   └── .fizzy-sync-state.json   # Bizzy sync state (auto-created)
├── .fizzy-sync.yml              # Bizzy configuration
└── ... your project files ...
```
