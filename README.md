# mcp-data

MCP server for personal data — lists, reminders, and notes. Syncs across Mac and Pi via bidirectional rsync.

## Overview

**mcp-data** provides tools for managing your to-do lists, grocery lists, reminders, and notes. All data is stored as JSON in a shared directory that syncs between machines.

Perfect for keeping personal state synchronized with Claude across sessions and channels.

## MCP Tools

### Lists

| Tool | Purpose |
|------|---------|
| `list_items(list_name)` | View all active items on a list |
| `add_item(list_name, text)` | Add an item to a list |
| `check_item(list_name, id)` | Mark an item as done |
| `uncheck_item(list_name, id)` | Mark an item as not done |
| `remove_item(list_name, id)` | Soft-delete an item (not permanent) |
| `create_list(list_name)` | Create a new empty list |
| `review_list(list_name?)` | Pull a list with review instructions |

### Reminders

| Tool | Purpose |
|------|---------|
| `list_reminders()` | View all active reminders |
| `add_reminder(text, due?)` | Add a reminder (optional due date in ISO format) |
| `complete_reminder(id)` | Mark a reminder as done |
| `remove_reminder(id)` | Soft-delete a reminder |
| `review_reminders()` | Pull reminders with review instructions |

### Notes

| Tool | Purpose |
|------|---------|
| `get_note(name)` | Read a note by name |
| `set_note(name, content)` | Write or update a note |
| `review_notes()` | Pull all notes with review instructions |

## Data Format

Lists and reminders use soft deletion (items stay in the file with `deleted: true`):

```json
{
  "items": [
    {
      "id": "abc123",
      "text": "Buy milk",
      "done": false,
      "deleted": false,
      "created": "2026-03-01T10:00:00",
      "updated": "2026-03-01T10:05:00"
    }
  ]
}
```

Notes are simple:

```json
{
  "content": "Personal thoughts...",
  "updated": "2026-03-01T10:05:00"
}
```

## Storage

- Lists: `~/pi-data/lists/` (e.g., `todo.json`, `grocery.json`)
- Reminders: `~/pi-data/reminders/reminders.json`
- Notes: `~/pi-data/notes/` (e.g., `general.json`)

## Sync

Mac (`~/pi-data/`) ↔ Pi (`/home/jaredgantt/data/`) sync every 30 minutes via `sync_mac_to_pi.sh` and `sync_pi_data.sh`.

Merge logic in `merge.py` handles conflicting edits (keeps newest).

## Related

- **History:** [mcp-history](https://github.com/JJGantt/mcp-history) — Conversation history & context
