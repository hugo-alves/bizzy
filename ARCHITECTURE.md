# Bizzy Architecture

## The Big Picture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                            YOUR PROJECT                                      │
│                                                                             │
│   ┌──────────────────┐                           ┌──────────────────┐       │
│   │    AI AGENT      │                           │     YOU          │       │
│   │  (Claude Code)   │                           │   (Human)        │       │
│   └────────┬─────────┘                           └────────┬─────────┘       │
│            │                                              │                 │
│            │ creates/updates issues                       │ watches board   │
│            ▼                                              ▼                 │
│   ┌──────────────────┐         BIZZY            ┌──────────────────┐       │
│   │      BEADS       │ ───────────────────────▶ │      FIZZY       │       │
│   │   (Text/SQLite)  │        syncs             │  (Kanban Board)  │       │
│   │                  │                          │                  │       │
│   │  .beads/         │                          │  ┌─────┬────┬────┐│       │
│   │   └─beads.db     │                          │  │Maybe│Doing│Done││       │
│   │                  │                          │  │  ?  │    │    ││       │
│   └──────────────────┘                          │  │ □  │ □  │ □  ││       │
│                                                 │  │ □  │    │ □  ││       │
│                                                 │  └─────┴────┴────┘│       │
│                                                 └──────────────────┘       │
└─────────────────────────────────────────────────────────────────────────────┘
```

## What Are These Tools?

### Beads (The Source)

A **command-line issue tracker** that stores data in your git repo. AI agents (like Claude Code) use it to track their work without leaving the terminal.

```bash
$ bd create "Fix login bug"               # Agent creates task
$ bd update bizzy-1 --status in_progress  # Agent starts work
$ bd close bizzy-1                        # Agent finishes

# Data lives in: .beads/beads.db (SQLite)
```

**Why Beads?** It's designed for AI agents - simple CLI commands, git-friendly storage, no GUI needed.

### Fizzy (The Display)

A **visual Kanban board** (like Trello). Cards represent tasks and move between columns as work progresses.

```
┌─────────────┬─────────────┬─────────────┬─────────────┐
│   Maybe?    │    Doing    │   Blocked   │    Done     │
│  (backlog)  │             │             │             │
├─────────────┼─────────────┼─────────────┼─────────────┤
│ ┌─────────┐ │ ┌─────────┐ │             │ ┌─────────┐ │
│ │ Task 3  │ │ │ Task 1  │ │             │ │ Task 2  │ │
│ │ P2 task │ │ │ P0 bug  │ │             │ │ feature │ │
│ └─────────┘ │ └─────────┘ │             │ └─────────┘ │
│ ┌─────────┐ │             │             │             │
│ │ Task 4  │ │             │             │             │
│ └─────────┘ │             │             │             │
└─────────────┴─────────────┴─────────────┴─────────────┘
```

> **Note:** Fizzy has built-in columns: "Maybe?" (inbox/backlog), "Not Now" (postponed), and "Done" (closed). We use Maybe? as the backlog and only create 2 custom columns: Doing and Blocked.

**Why Fizzy?** It provides a human-friendly view of what's happening - you can see progress at a glance.

### Bizzy (The Bridge)

Watches Beads for changes and syncs them to Fizzy. That's it!

---

## The Sync Flow

```
 BEADS DATABASE                    BIZZY                         FIZZY API
 ─────────────                    ─────                         ─────────

 ┌─────────────┐
 │ beads.db    │
 │ ┌─────────┐ │    1. READ
 │ │ bizzy-1 │─┼──────────────▶ ┌─────────────────┐
 │ │ open    │ │                │                 │
 │ │ P0 bug  │ │                │  BeadsReader    │
 │ └─────────┘ │                │  - queries DB   │
 │ ┌─────────┐ │                │  - gets issues  │
 │ │ bizzy-2 │─┼──────────────▶ │                 │
 │ │ closed  │ │                └────────┬────────┘
 └─────────────┘                         │
                                         │ 2. TRANSFORM
                                         ▼
                                ┌─────────────────┐
                                │     Mapper      │
                                │                 │
                                │ status → column │
                                │ open → Maybe?   │
                                │   (untriaged)   │
                                │ in_progress →   │
                                │         Doing   │
                                │ blocked →       │
                                │         Blocked │
                                │ closed → Done   │
                                │   (built-in)    │
                                │                 │
                                │ priority → tag  │
                                │ 0→P0, 1→P1...   │
                                └────────┬────────┘
                                         │
                                         │ 3. SYNC
                                         ▼
                                ┌─────────────────┐          ┌──────────────┐
                                │   SyncEngine    │          │  Fizzy API   │
                                │                 │  HTTP    │              │
                                │ - check changes ├─────────▶│ POST /cards  │
                                │ - create cards  │          │ PUT /cards/1 │
                                │ - update cards  │◀─────────┤ POST /triage │
                                │ - move columns  │          │              │
                                └────────┬────────┘          └──────────────┘
                                         │
                                         │ 4. SAVE STATE
                                         ▼
                                ┌─────────────────┐
                                │   SyncState     │
                                │                 │
                                │ .fizzy-sync-    │
                                │  state.json     │
                                │                 │
                                │ {bizzy-1: {     │
                                │   card: 42,     │
                                │   checksum: x   │
                                │ }}              │
                                └─────────────────┘
```

---

## Key Components

### 1. BeadsReader

Reads issues from the SQLite database:

```python
# Queries .beads/beads.db
SELECT id, title, description, status, priority, issue_type
FROM issues
WHERE status != 'closed'  # or include closed if configured
```

**Blocked Status Detection:** BeadsReader also queries the `blocked_issues_cache` table
to determine which issues are blocked by dependencies. An issue is blocked if:
- It has a `blocks` dependency on an issue that is still open/in_progress/blocked
- When the blocker is closed, the dependent issue becomes unblocked

This means the `status` shown to Fizzy may differ from the raw database status:
- Issue with `status=open` but in `blocked_issues_cache` → synced as `blocked`
- Issue with `status=blocked` but NOT in cache (blocker was closed) → synced as `open`

### 2. Mapper

Transforms data between Beads and Fizzy formats:

```
Beads Issue                      Fizzy Card
───────────                      ──────────
id: "bizzy-1"            →       (stored in description)
title: "Fix login bug"   →       title: "Fix login bug"
description: "Details"   →       description: "Details\n[beads:bizzy-1]"
status: "in_progress"    →       column: "Doing"
priority: 0              →       tags: ["P0"]
issue_type: "bug"        →       tags: ["bug"]
```

The `[beads:bizzy-1]` marker in the description links the card back to its source issue.

### 3. SyncState

Remembers what's been synced to avoid duplicates:

```json
{
  "synced_issues": {
    "bizzy-1": {
      "card_number": 42,
      "checksum": "a1b2c3d4e5f6...",
      "synced_at": "2024-01-05T10:30:00"
    }
  },
  "last_sync": "2024-01-05T10:30:00"
}
```

The **checksum** is a hash of the issue's key fields. If it changes, we know the issue was updated.

### 4. SyncEngine

The brain - orchestrates the sync:

```
For each Beads issue:
  1. Calculate checksum of issue data
  2. Look up in SyncState
  3. If not synced      → CREATE card
     If checksum changed → UPDATE card
     If unchanged       → SKIP (no API call)
  4. Save new state
```

### 5. FizzyClient

HTTP client that talks to Fizzy's REST API:

```
POST /boards/{id}/cards      → Create card
PUT /cards/{number}          → Update card
POST /cards/{number}/triage  → Move to column
POST /cards/{number}/closure → Close card
DELETE /cards/{number}/closure → Reopen card
```

Includes retry logic with exponential backoff for reliability.

---

## Watch Mode

The recommended way to run Bizzy:

```
┌──────────────────────────────────────────────────────────────┐
│                                                              │
│   $ bizzy watch                                              │
│                                                              │
│   ┌─────────────┐      file change      ┌─────────────┐     │
│   │ beads.db    │ ───────────────────▶  │  watchfiles │     │
│   │ (modified)  │      detected         │  library    │     │
│   └─────────────┘                       └──────┬──────┘     │
│                                                │             │
│                                                │ triggers    │
│                                                ▼             │
│                                         ┌─────────────┐     │
│                                         │ run sync()  │     │
│                                         └─────────────┘     │
│                                                              │
│   Loops forever until Ctrl+C                                 │
│                                                              │
└──────────────────────────────────────────────────────────────┘
```

1. Monitors `.beads/beads.db` for file changes
2. When an agent runs `bd create`, `bd update`, `bd close`...
3. The database file changes → Bizzy detects it → Runs sync
4. Card appears/moves on the Fizzy board within seconds

---

## Why This Exists

```
PROBLEM:
─────────────────────────────────────────────────────────
AI agents work in terminals. Humans can't easily see
what they're doing or how much progress they've made.

SOLUTION:
─────────────────────────────────────────────────────────

  Agent works          Beads tracks         Fizzy shows
  ───────────          ────────────         ───────────
       │                    │                    │
       │ "fixing bug"       │                    │
       ├───────────────────▶│                    │
       │                    │  bizzy syncs       │
       │                    ├───────────────────▶│  Card appears
       │                    │                    │  in "Doing"
       │ "done!"            │                    │
       ├───────────────────▶│                    │
       │                    ├───────────────────▶│  Card moves
       │                    │                    │  to "Done"
       │                    │                    │
       ▼                    ▼                    ▼
   Agent keeps          Everything          Human sees
   working              logged              progress!
```

---

## Data Flow Summary

```
1. Agent creates issue     →  bd create "Fix bug"
2. Beads writes to DB      →  .beads/beads.db updated
3. Bizzy detects change    →  watchfiles notices file change
4. Bizzy reads issue       →  SELECT * FROM issues
5. Bizzy maps to card      →  status→column, priority→tag
6. Bizzy calls Fizzy API   →  POST /cards, POST /triage
7. Card appears on board   →  You see it in the browser!
```

---

## Status Mapping

| Beads Status | Fizzy Location | Type |
|--------------|----------------|------|
| `open` | Maybe? | Built-in (inbox/backlog) |
| `in_progress` | Doing | Custom column (Lime) |
| `blocked` | Blocked | Custom column (Pink) |
| `closed` | Done | Built-in |

> **Note:** Maybe? and Done are Fizzy's built-in card states, not database columns. We only create 2 custom columns: Doing and Blocked.

### Blocked Status & Dependencies

The `blocked` status is **derived from dependencies**, not just the raw status field:

```
┌─────────────────┐         blocks          ┌─────────────────┐
│  Task A (open)  │ ◄─────────────────────  │  Task B (open)  │
│                 │                          │                 │
│  No blockers    │                          │  Blocked by A   │
│  → Maybe?       │                          │  → Blocked      │
└─────────────────┘                          └─────────────────┘

When Task A is closed:

┌─────────────────┐                          ┌─────────────────┐
│ Task A (closed) │                          │  Task B (open)  │
│                 │                          │                 │
│  → Done         │                          │  No blockers    │
└─────────────────┘                          │  → Maybe?       │
                                             └─────────────────┘
```

Beads maintains a `blocked_issues_cache` table that tracks which issues are blocked.
Bizzy reads this cache to determine the effective status for sync.

## Tag Mapping

| Beads Field | Fizzy Tag |
|-------------|-----------|
| `priority: 0` | `P0` |
| `priority: 1` | `P1` |
| `priority: 2` | `P2` |
| `issue_type: bug` | `bug` |
| `issue_type: feature` | `feature` |
| `labels: ["urgent"]` | `urgent` |
