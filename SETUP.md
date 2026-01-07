# Complete Setup Guide

This guide walks you through setting up everything from scratch.

## Quickest Way: Use the Wizard

If you just want to get started quickly:

```bash
# Navigate to your project (must have .beads/ directory)
cd /path/to/your/project

# Run the interactive setup wizard
uv run bizzy wizard
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
# Install bd (macOS/Linux)
curl -fsSL https://raw.githubusercontent.com/steveyegge/beads/main/scripts/install.sh | bash

# Or Homebrew
brew install steveyegge/beads/bd

# Or npm
npm install -g @beads/bd

# Windows (PowerShell)
irm https://raw.githubusercontent.com/steveyegge/beads/main/install.ps1 | iex

# Verify installation
bd --version
```

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
git clone https://github.com/hugo-alves/bizzy.git
cd bizzy
```

### Option B: Download just the script
```bash
# Download the sync script
curl -O https://raw.githubusercontent.com/hugo-alves/bizzy/main/fizzy_sync.py
```

If you use the single-file script, run it like:
```bash
uv run fizzy_sync.py wizard
```

## Step 6: Initialize Bizzy Config

Navigate to your project (where `.beads/` exists):

```bash
cd /path/to/your/project

# Create the config file
uv run bizzy init
```

This creates `.fizzy-sync.yml`:

```yaml
fizzy:
  base_url: https://fizzy.example.com  # ← Change to your Fizzy URL
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
https://fizzy.example.com/YOUR_ACCOUNT_SLUG/boards/...
                         ▲
                         └── This is your account slug
```

## Step 7: Test the Connection

```bash
uv run bizzy auth
```

Expected output:
```
✓ Connected!
  User: Your Name (your@email.com)
  Account: Your Account (YOUR_ACCOUNT_SLUG)
```

If it fails:
- Check your `FIZZY_API_TOKEN` is set: `echo $FIZZY_API_TOKEN`
- Check your `base_url` in the config
- Check your `account_slug` in the config

## Step 8: Create or Set Up a Board

### Option A: Create a New Board (Recommended)

```bash
uv run bizzy setup --new-board "My Project"
```

Output:
```
Creating board: My Project
  ✓ Created! Board ID: YOUR_BOARD_ID
  Update .fizzy-sync.yml with:
    board:
      id: "YOUR_BOARD_ID"

Creating custom columns...
  ✓ Doing (Lime)
  ✓ Blocked (Pink)

Board setup complete!
Note: Open issues go to Maybe? (built-in), closed to Done (built-in).
```

**Copy the board ID** and update your `.fizzy-sync.yml`:

```yaml
board:
  id: "YOUR_BOARD_ID"  # ← Paste here
```

### Option B: Use an Existing Board

1. Go to the board in Fizzy
2. Copy the board ID from the URL:
   ```
   https://fizzy.example.com/YOUR_ACCOUNT_SLUG/boards/YOUR_BOARD_ID
                                               ▲
                                               └── This is the board ID
   ```
3. Update `.fizzy-sync.yml` with the board ID
4. Run setup to add missing columns:
   ```bash
   uv run bizzy setup
   ```

## Step 9: Run Your First Sync

### Create a test issue in Beads:

```bash
bd create "Test issue from Beads" --type task --priority 2
```

### Sync to Fizzy:

```bash
uv run bizzy sync
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
uv run bizzy watch
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
nohup uv run bizzy watch > bizzy.log 2>&1 &

# Or using tmux/screen
tmux new-session -d -s bizzy 'uv run bizzy watch'
```

## Quick Reference

Once set up, here are the common commands:

```bash
# Manual sync
uv run bizzy sync

# Watch mode (auto-sync)
uv run bizzy watch

# Check status
uv run bizzy status

# Include closed issues
uv run bizzy sync --include-closed

# Dry run (preview without syncing)
uv run bizzy sync --dry-run
```

## Troubleshooting

### "Config file not found"
Run `bizzy init` in your project directory.

### "API token not set"
```bash
export FIZZY_API_TOKEN="your-token"
```

### "Beads database not found"
Run `bd init` in your project directory.

### Cards not moving to correct columns
Run `bizzy setup` to ensure all columns exist.

### Duplicate columns on board
Run `bizzy setup --reset --force` to clean up.

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
