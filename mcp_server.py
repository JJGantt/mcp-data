#!/usr/bin/env python3
"""
MCP server for personal data — lists, reminders, and notes.

TOOLS
=====
Lists (todo, grocery, or any list in data_dir/lists/):
- list_items(list_name)          — View all active items on a list
- add_item(list_name, text)      — Add an item to a list
- check_item(list_name, id)      — Mark an item as done
- uncheck_item(list_name, id)    — Mark an item as not done
- remove_item(list_name, id)     — Soft-delete an item
- create_list(list_name)         — Create a new empty list

Reminders:
- list_reminders()               — View all active reminders
- add_reminder(text, due?)       — Add a reminder (optional due date)
- complete_reminder(id)          — Mark a reminder as done
- remove_reminder(id)            — Soft-delete a reminder

Notes:
- get_note(name)                 — Read a note
- set_note(name, content)        — Write/update a note

Reviews:
- review_notes()                 — Pull all notes with review instructions
- review_list(list_name?)        — Pull a list with review instructions
- review_reminders()             — Pull reminders with review instructions
"""

import asyncio
import json
import logging
import uuid
from datetime import datetime
from pathlib import Path

from mcp.server import Server
import mcp.server.stdio
import mcp.types as types

from config import LISTS_DIR, NOTES_DIR, REMINDERS_FILE, HEALTH_PROFILE, HEALTH_WORKOUTS

log = logging.getLogger(__name__)
server = Server("data")
_workouts_lock = asyncio.Lock()


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _load_json(path: Path) -> dict:
    if path.exists():
        return json.loads(path.read_text())
    return {}


def _save_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, indent=2) + "\n")


def _load_list(name: str) -> tuple[list, Path]:
    path = LISTS_DIR / f"{name}.json"
    data = _load_json(path)
    return data.get("items", []), path


def _save_list(items: list, path: Path) -> None:
    _save_json(path, {"items": items})


def _active_items(items: list) -> list:
    return [i for i in items if not i.get("deleted", False)]


def _find_item(items: list, item_id: str) -> dict | None:
    for i in items:
        if i["id"] == item_id:
            return i
    return None


def _format_list_items(items: list, list_name: str) -> str:
    active = _active_items(items)
    if not active:
        return f"'{list_name}' is empty."
    lines = []
    for i in active:
        check = "[x]" if i.get("done") else "[ ]"
        due = f" (due: {i['due']})" if "due" in i else ""
        lines.append(f"{check} {i['text']}{due}  [id: {i['id'][:8]}]")
    return f"**{list_name}** ({len(active)} items):\n" + "\n".join(lines)


def _format_reminders(items: list) -> str:
    active = _active_items(items)
    if not active:
        return "No active reminders."
    lines = []
    for i in active:
        check = "[x]" if i.get("done") else "[ ]"
        due = f" (due: {i['due']})" if i.get("due") else ""
        lines.append(f"{check} {i['text']}{due}  [id: {i['id'][:8]}]")
    return f"**Reminders** ({len(active)} items):\n" + "\n".join(lines)


def _available_lists() -> list[str]:
    return sorted(p.stem for p in LISTS_DIR.glob("*.json"))


def _load_profile() -> dict:
    if HEALTH_PROFILE.exists():
        return json.loads(HEALTH_PROFILE.read_text())
    return {"weight_log": []}


def _save_profile(data: dict) -> None:
    data["updated"] = _now()
    _save_json(HEALTH_PROFILE, data)


def _load_workouts() -> dict:
    if HEALTH_WORKOUTS.exists():
        return json.loads(HEALTH_WORKOUTS.read_text())
    return {"sessions": []}


def _save_workouts(data: dict) -> None:
    tmp = HEALTH_WORKOUTS.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n")
    tmp.rename(HEALTH_WORKOUTS)


def _format_session_table(session: dict) -> str:
    lines = [
        f"**{session['type'].title()} session** ({session['date'][:10]})  `{session['id'][:8]}`",
        "",
        "| Exercise | Sets | Reps | Weight |",
        "|---|---|---|---|",
    ]
    for ex in session.get("exercises", []):
        sets = ex.get("sets", [])
        if not sets:
            lines.append(f"| {ex['name']} | 0 | — | — |")
            continue
        n = len(sets)
        weights = [s["weight_lbs"] for s in sets]
        reps_list = [s["reps"] for s in sets]
        w = weights[0]
        weight_str = f"{int(w) if w == int(w) else w} lbs" if len(set(weights)) == 1 else "varies"
        reps_str = str(reps_list[0]) if len(set(reps_list)) == 1 else "varies"
        lines.append(f"| {ex['name']} | {n} | {reps_str} | {weight_str} |")
    return "\n".join(lines)


def _resolve_session(sessions: list, id_prefix: str) -> dict | None:
    for s in sessions:
        if s["id"] == id_prefix:
            return s
    matches = [s for s in sessions if s["id"].startswith(id_prefix)]
    if len(matches) == 1:
        return matches[0]
    return None


@server.list_tools()
async def list_tools() -> list[types.Tool]:
    return [
        # --- Lists ---
        types.Tool(
            name="list_items",
            description=(
                "View all active (non-deleted) items on a list. "
                "Available lists: todo, grocery, or any custom list. "
                "Use this to check what's on a list before adding/removing."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "list_name": {
                        "type": "string",
                        "description": "Name of the list (e.g. 'todo', 'grocery'). Omit to see all lists.",
                    },
                },
            },
        ),
        types.Tool(
            name="add_item",
            description=(
                "Add an item to a list (todo, grocery, or any custom list). "
                "Creates the list if it doesn't exist. "
                "Use for adding to-do items, grocery items, or any list-based data."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "list_name": {
                        "type": "string",
                        "description": "Name of the list (e.g. 'todo', 'grocery').",
                    },
                    "text": {
                        "type": "string",
                        "description": "The item text to add.",
                    },
                },
                "required": ["list_name", "text"],
            },
        ),
        types.Tool(
            name="check_item",
            description="Mark a list item as done/checked. Use the short ID prefix shown in list_items output.",
            inputSchema={
                "type": "object",
                "properties": {
                    "list_name": {
                        "type": "string",
                        "description": "Name of the list.",
                    },
                    "id": {
                        "type": "string",
                        "description": "ID (or prefix) of the item to check.",
                    },
                },
                "required": ["list_name", "id"],
            },
        ),
        types.Tool(
            name="uncheck_item",
            description="Mark a list item as not done/unchecked.",
            inputSchema={
                "type": "object",
                "properties": {
                    "list_name": {
                        "type": "string",
                        "description": "Name of the list.",
                    },
                    "id": {
                        "type": "string",
                        "description": "ID (or prefix) of the item to uncheck.",
                    },
                },
                "required": ["list_name", "id"],
            },
        ),
        types.Tool(
            name="remove_item",
            description="Remove (soft-delete) an item from a list.",
            inputSchema={
                "type": "object",
                "properties": {
                    "list_name": {
                        "type": "string",
                        "description": "Name of the list.",
                    },
                    "id": {
                        "type": "string",
                        "description": "ID (or prefix) of the item to remove.",
                    },
                },
                "required": ["list_name", "id"],
            },
        ),
        types.Tool(
            name="create_list",
            description="Create a new empty list.",
            inputSchema={
                "type": "object",
                "properties": {
                    "list_name": {
                        "type": "string",
                        "description": "Name for the new list (lowercase, no spaces — use hyphens).",
                    },
                },
                "required": ["list_name"],
            },
        ),
        # --- Reminders ---
        types.Tool(
            name="list_reminders",
            description="View all active reminders with their due dates and IDs.",
            inputSchema={"type": "object", "properties": {}},
        ),
        types.Tool(
            name="add_reminder",
            description="Add a new reminder with an optional due date/time.",
            inputSchema={
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": "The reminder text.",
                    },
                    "due": {
                        "type": "string",
                        "description": "Optional due date in ISO format (e.g. '2026-03-01T09:00:00').",
                    },
                },
                "required": ["text"],
            },
        ),
        types.Tool(
            name="complete_reminder",
            description="Mark a reminder as done/completed.",
            inputSchema={
                "type": "object",
                "properties": {
                    "id": {
                        "type": "string",
                        "description": "ID (or prefix) of the reminder to complete.",
                    },
                },
                "required": ["id"],
            },
        ),
        types.Tool(
            name="remove_reminder",
            description="Remove (soft-delete) a reminder.",
            inputSchema={
                "type": "object",
                "properties": {
                    "id": {
                        "type": "string",
                        "description": "ID (or prefix) of the reminder to remove.",
                    },
                },
                "required": ["id"],
            },
        ),
        # --- Notes ---
        types.Tool(
            name="get_note",
            description="Read a note by name. Omit name to list available notes.",
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Note name (e.g. 'general'). Omit to list available notes.",
                    },
                },
            },
        ),
        types.Tool(
            name="set_note",
            description="Write or update a note. Creates the note if it doesn't exist.",
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Note name (e.g. 'general').",
                    },
                    "content": {
                        "type": "string",
                        "description": "The content to write to the note.",
                    },
                },
                "required": ["name", "content"],
            },
        ),
        # --- Reviews ---
        types.Tool(
            name="review_notes",
            description=(
                "Pull all notes for review. Returns every note with built-in "
                "instructions for how to present them to the user. "
                "Use when the user says 'review my notes' or during scheduled reviews."
            ),
            inputSchema={"type": "object", "properties": {}},
        ),
        types.Tool(
            name="review_list",
            description=(
                "Pull a list for review with built-in handling instructions. "
                "Use when the user says 'review my grocery list', 'review my todo list', etc. "
                "Omit list_name to review ALL lists."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "list_name": {
                        "type": "string",
                        "description": "Name of the list to review (e.g. 'todo', 'grocery'). Omit to review all lists.",
                    },
                },
            },
        ),
        types.Tool(
            name="review_reminders",
            description=(
                "Pull all reminders for review with built-in handling instructions. "
                "Use when the user says 'review my reminders' or during scheduled reviews."
            ),
            inputSchema={"type": "object", "properties": {}},
        ),
        # --- Health: Profile ---
        types.Tool(
            name="get_health_profile",
            description="Returns height and recent weight log entries.",
            inputSchema={"type": "object", "properties": {}},
        ),
        types.Tool(
            name="set_height",
            description="Set or update height in inches.",
            inputSchema={
                "type": "object",
                "properties": {
                    "height_in": {
                        "type": "number",
                        "description": "Height in inches (e.g. 72 for 6'0\").",
                    },
                },
                "required": ["height_in"],
            },
        ),
        types.Tool(
            name="log_weight",
            description="Log a body weight entry. Appends to the weight log.",
            inputSchema={
                "type": "object",
                "properties": {
                    "weight_lbs": {
                        "type": "number",
                        "description": "Body weight in pounds.",
                    },
                    "date": {
                        "type": "string",
                        "description": "Date in YYYY-MM-DD format. Defaults to today.",
                    },
                },
                "required": ["weight_lbs"],
            },
        ),
        # --- Health: Catalog ---
        types.Tool(
            name="get_workout_catalog",
            description=(
                "Returns all unique exercise names and session types seen so far. "
                "Call this before start_workout or log_exercise to see existing session types "
                "and exercise names. Always reuse an exact existing name when the exercise is "
                "the same, even if the user phrases it differently."
            ),
            inputSchema={"type": "object", "properties": {}},
        ),
        # --- Health: Workout Sessions ---
        types.Tool(
            name="start_workout",
            description=(
                "Start a new workout session. Returns a session ID. "
                "Call get_workout_catalog first to reuse an existing session type. "
                "Pass date for retroactive logging (e.g. logging a workout that happened yesterday)."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "type": {
                        "type": "string",
                        "description": "Workout type (e.g. 'push', 'pull', 'legs'). Check catalog for existing types.",
                    },
                    "notes": {
                        "type": "string",
                        "description": "Optional notes for this session.",
                    },
                    "date": {
                        "type": "string",
                        "description": "ISO datetime of when the workout happened (e.g. '2026-03-01T19:30:00'). Defaults to now if omitted.",
                    },
                },
                "required": ["type"],
            },
        ),
        types.Tool(
            name="log_exercise",
            description=(
                "Log one or more exercises to a workout session in a single call. "
                "Pass an 'exercises' array (preferred) to log multiple at once. "
                "Check get_workout_catalog first to match existing exercise names for consistency. "
                "Returns the updated session table — display it to the user in your response."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "session_id": {
                        "type": "string",
                        "description": "Session ID or 8-char prefix from start_workout.",
                    },
                    "exercises": {
                        "type": "array",
                        "description": "List of exercises to log: [{name, sets: [{weight_lbs, reps}]}].",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string"},
                                "sets": {
                                    "type": "array",
                                    "items": {
                                        "type": "object",
                                        "properties": {
                                            "weight_lbs": {"type": "number"},
                                            "reps": {"type": "integer"},
                                        },
                                        "required": ["weight_lbs", "reps"],
                                    },
                                },
                            },
                            "required": ["name", "sets"],
                        },
                    },
                },
                "required": ["session_id", "exercises"],
            },
        ),
        types.Tool(
            name="list_workouts",
            description="List recent workout sessions (summarized, no set detail).",
            inputSchema={
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "integer",
                        "description": "Max sessions to return. Defaults to 10.",
                    },
                },
            },
        ),
        types.Tool(
            name="get_workout",
            description="Get full detail of a workout session including all exercises and sets.",
            inputSchema={
                "type": "object",
                "properties": {
                    "session_id": {
                        "type": "string",
                        "description": "Session ID or 8-char prefix.",
                    },
                },
                "required": ["session_id"],
            },
        ),
        types.Tool(
            name="get_exercise_history",
            description="Get progression history for a single exercise across all sessions.",
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Exercise name (exact match, case-insensitive).",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max sessions to return. Defaults to 20.",
                    },
                },
                "required": ["name"],
            },
        ),
        types.Tool(
            name="update_exercise",
            description="Update an exercise's sets in a workout session. Finds the exercise by name (case-insensitive) and replaces its sets. Returns the updated session table — display it to the user in your response.",
            inputSchema={
                "type": "object",
                "properties": {
                    "session_id": {
                        "type": "string",
                        "description": "Session ID or 8-char prefix.",
                    },
                    "name": {
                        "type": "string",
                        "description": "Exercise name (case-insensitive match).",
                    },
                    "sets": {
                        "type": "array",
                        "description": "New sets to replace existing: [{weight_lbs, reps}].",
                        "items": {
                            "type": "object",
                            "properties": {
                                "weight_lbs": {"type": "number"},
                                "reps": {"type": "integer"},
                            },
                            "required": ["weight_lbs", "reps"],
                        },
                    },
                },
                "required": ["session_id", "name", "sets"],
            },
        ),
        types.Tool(
            name="remove_exercise",
            description="Remove an exercise from a workout session by name (case-insensitive). Returns the updated session table — display it to the user in your response.",
            inputSchema={
                "type": "object",
                "properties": {
                    "session_id": {
                        "type": "string",
                        "description": "Session ID or 8-char prefix.",
                    },
                    "name": {
                        "type": "string",
                        "description": "Exercise name (case-insensitive match).",
                    },
                },
                "required": ["session_id", "name"],
            },
        ),
        types.Tool(
            name="delete_workout",
            description="Permanently delete a workout session.",
            inputSchema={
                "type": "object",
                "properties": {
                    "session_id": {
                        "type": "string",
                        "description": "Session ID or 8-char prefix.",
                    },
                },
                "required": ["session_id"],
            },
        ),
    ]


def _resolve_item(items: list, id_prefix: str) -> dict | None:
    """Find an item by full ID or prefix match."""
    item = _find_item(items, id_prefix)
    if item:
        return item
    matches = [i for i in items if i["id"].startswith(id_prefix) and not i.get("deleted")]
    if len(matches) == 1:
        return matches[0]
    return None


def _text(msg: str) -> list[types.TextContent]:
    return [types.TextContent(type="text", text=msg)]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:
    # ---- Lists ----
    if name == "list_items":
        list_name = arguments.get("list_name", "").strip()
        if not list_name:
            available = _available_lists()
            if not available:
                return _text("No lists found.")
            return _text("Available lists: " + ", ".join(available))
        items, _ = _load_list(list_name)
        return _text(_format_list_items(items, list_name))

    if name == "add_item":
        list_name = arguments["list_name"].strip()
        text = arguments["text"].strip()
        items, path = _load_list(list_name)
        now = _now()
        new_item = {
            "id": str(uuid.uuid4()),
            "text": text,
            "done": False,
            "deleted": False,
            "created": now,
            "updated": now,
        }
        items.append(new_item)
        _save_list(items, path)
        return _text(f"Added '{text}' to {list_name}. [id: {new_item['id'][:8]}]")

    if name == "check_item":
        list_name = arguments["list_name"].strip()
        items, path = _load_list(list_name)
        item = _resolve_item(items, arguments["id"].strip())
        if not item:
            return _text(f"Item not found: {arguments['id']}")
        item["done"] = True
        item["updated"] = _now()
        _save_list(items, path)
        return _text(f"Checked: {item['text']}")

    if name == "uncheck_item":
        list_name = arguments["list_name"].strip()
        items, path = _load_list(list_name)
        item = _resolve_item(items, arguments["id"].strip())
        if not item:
            return _text(f"Item not found: {arguments['id']}")
        item["done"] = False
        item["updated"] = _now()
        _save_list(items, path)
        return _text(f"Unchecked: {item['text']}")

    if name == "remove_item":
        list_name = arguments["list_name"].strip()
        items, path = _load_list(list_name)
        item = _resolve_item(items, arguments["id"].strip())
        if not item:
            return _text(f"Item not found: {arguments['id']}")
        item["deleted"] = True
        item["updated"] = _now()
        _save_list(items, path)
        return _text(f"Removed: {item['text']}")

    if name == "create_list":
        list_name = arguments["list_name"].strip().lower().replace(" ", "-")
        path = LISTS_DIR / f"{list_name}.json"
        if path.exists():
            return _text(f"List '{list_name}' already exists.")
        _save_list([], path)
        return _text(f"Created empty list: {list_name}")

    # ---- Reminders ----
    if name == "list_reminders":
        data = _load_json(REMINDERS_FILE)
        items = data.get("items", [])
        return _text(_format_reminders(items))

    if name == "add_reminder":
        data = _load_json(REMINDERS_FILE)
        items = data.get("items", [])
        now = _now()
        new_item = {
            "id": str(uuid.uuid4()),
            "text": arguments["text"].strip(),
            "done": False,
            "deleted": False,
            "created": now,
            "updated": now,
        }
        if arguments.get("due"):
            new_item["due"] = arguments["due"].strip()
        items.append(new_item)
        _save_json(REMINDERS_FILE, {"items": items})
        due_str = f" (due: {new_item.get('due', 'none')})" if new_item.get("due") else ""
        return _text(f"Added reminder: '{new_item['text']}'{due_str} [id: {new_item['id'][:8]}]")

    if name == "complete_reminder":
        data = _load_json(REMINDERS_FILE)
        items = data.get("items", [])
        item = _resolve_item(items, arguments["id"].strip())
        if not item:
            return _text(f"Reminder not found: {arguments['id']}")
        item["done"] = True
        item["updated"] = _now()
        _save_json(REMINDERS_FILE, {"items": items})
        return _text(f"Completed reminder: {item['text']}")

    if name == "remove_reminder":
        data = _load_json(REMINDERS_FILE)
        items = data.get("items", [])
        item = _resolve_item(items, arguments["id"].strip())
        if not item:
            return _text(f"Reminder not found: {arguments['id']}")
        item["deleted"] = True
        item["updated"] = _now()
        _save_json(REMINDERS_FILE, {"items": items})
        return _text(f"Removed reminder: {item['text']}")

    # ---- Notes ----
    if name == "get_note":
        note_name = arguments.get("name", "").strip()
        if not note_name:
            available = sorted(p.stem for p in NOTES_DIR.glob("*.json"))
            if not available:
                return _text("No notes found.")
            return _text("Available notes: " + ", ".join(available))
        path = NOTES_DIR / f"{note_name}.json"
        data = _load_json(path)
        if not data:
            return _text(f"Note '{note_name}' not found.")
        content = data.get("content", "")
        updated = data.get("updated", "unknown")
        if not content:
            return _text(f"Note '{note_name}' is empty. (last updated: {updated})")
        return _text(f"**{note_name}** (updated: {updated}):\n{content}")

    if name == "set_note":
        note_name = arguments["name"].strip().lower().replace(" ", "-")
        content = arguments["content"]
        path = NOTES_DIR / f"{note_name}.json"
        _save_json(path, {"content": content, "updated": _now()})
        return _text(f"Note '{note_name}' saved.")

    # ---- Reviews ----
    if name == "review_notes":
        return _text(_build_notes_review())

    if name == "review_list":
        list_name = arguments.get("list_name", "").strip()
        return _text(_build_list_review(list_name))

    if name == "review_reminders":
        return _text(_build_reminders_review())

    # ---- Health: Profile ----
    if name == "get_health_profile":
        profile = _load_profile()
        height = profile.get("height_in")
        weight_log = profile.get("weight_log", [])
        lines = []
        if height:
            feet, inches = divmod(int(height), 12)
            lines.append(f"Height: {height} in ({feet}'{inches}\")")
        else:
            lines.append("Height: not set")
        if weight_log:
            latest = weight_log[-1]
            lines.append(f"Latest weight: {latest['weight_lbs']} lbs on {latest['date']}")
            recent = weight_log[-5:]
            lines.append(f"\nWeight log (last {len(recent)}):")
            for e in reversed(recent):
                lines.append(f"  {e['date']}: {e['weight_lbs']} lbs")
        else:
            lines.append("Weight log: empty")
        return _text("\n".join(lines))

    if name == "set_height":
        profile = _load_profile()
        profile["height_in"] = float(arguments["height_in"])
        _save_profile(profile)
        h = profile["height_in"]
        feet, inches = divmod(int(h), 12)
        return _text(f"Height set to {h} in ({feet}'{inches}\").")

    if name == "log_weight":
        profile = _load_profile()
        weight_log = profile.get("weight_log", [])
        date = arguments.get("date") or _now()[:10]
        entry = {
            "date": date,
            "weight_lbs": float(arguments["weight_lbs"]),
            "logged_at": _now(),
        }
        weight_log.append(entry)
        profile["weight_log"] = weight_log
        _save_profile(profile)
        return _text(f"Logged {entry['weight_lbs']} lbs on {date}.")

    # ---- Health: Catalog ----
    if name == "get_workout_catalog":
        data = _load_workouts()
        sessions = data.get("sessions", [])
        types_seen = sorted({s["type"] for s in sessions})
        exercises_seen = sorted({
            ex["name"]
            for s in sessions
            for ex in s.get("exercises", [])
        })
        lines = [
            f"Session types ({len(types_seen)}): {', '.join(types_seen) or 'none yet'}",
            f"Exercises ({len(exercises_seen)}): {', '.join(exercises_seen) or 'none yet'}",
        ]
        return _text("\n".join(lines))

    # ---- Health: Workout Sessions ----
    if name == "start_workout":
        async with _workouts_lock:
            data = _load_workouts()
            date = arguments.get("date") or _now()
            session = {
                "id": str(uuid.uuid4()),
                "date": date,
                "type": arguments["type"].strip(),
                "notes": arguments.get("notes", "").strip(),
                "exercises": [],
            }
            data["sessions"].append(session)
            _save_workouts(data)
        return _text(
            f"Started {session['type']} workout ({date}).\n"
            f"Session ID: {session['id'][:8]}\n\n"
            + _format_session_table(session)
        )

    if name == "log_exercise":
        async with _workouts_lock:
            data = _load_workouts()
            session = _resolve_session(data["sessions"], arguments["session_id"].strip())
            if not session:
                return _text(f"Session not found: {arguments['session_id']}")
            exercises_raw = arguments.get("exercises") or [{"name": arguments["name"], "sets": arguments["sets"]}]
            for ex_raw in exercises_raw:
                sets = []
                for i, s in enumerate(ex_raw["sets"], 1):
                    sets.append({
                        "set_num": i,
                        "weight_lbs": float(s["weight_lbs"]),
                        "reps": int(s["reps"]),
                    })
                session["exercises"].append({
                    "id": str(uuid.uuid4()),
                    "name": ex_raw["name"].strip(),
                    "sets": sets,
                })
            _save_workouts(data)
        return _text(_format_session_table(session))

    if name == "list_workouts":
        data = _load_workouts()
        limit = int(arguments.get("limit") or 10)
        sessions = data.get("sessions", [])[-limit:][::-1]
        if not sessions:
            return _text("No workout sessions found.")
        lines = []
        for s in sessions:
            ex_count = len(s.get("exercises", []))
            lines.append(
                f"[{s['id'][:8]}] {s['date'][:10]}  {s['type']}  "
                f"{ex_count} exercises"
            )
        return _text("\n".join(lines))

    if name == "get_workout":
        data = _load_workouts()
        session = _resolve_session(data["sessions"], arguments["session_id"].strip())
        if not session:
            return _text(f"Session not found: {arguments['session_id']}")
        lines = [
            f"Session: {session['id'][:8]}",
            f"Type: {session['type']}",
            f"Date: {session['date']}",
        ]
        if session.get("notes"):
            lines.append(f"Notes: {session['notes']}")
        lines.append("")
        for ex in session.get("exercises", []):
            lines.append(f"  {ex['name']}:")
            for s in ex.get("sets", []):
                lines.append(f"    Set {s['set_num']}: {s['weight_lbs']} lbs x {s['reps']} reps")
        return _text("\n".join(lines))

    if name == "get_exercise_history":
        data = _load_workouts()
        target = arguments["name"].strip().lower()
        limit = int(arguments.get("limit") or 20)
        results = []
        for s in data.get("sessions", []):
            for ex in s.get("exercises", []):
                if ex["name"].lower() == target:
                    results.append({
                        "date": s["date"][:10],
                        "session_id": s["id"][:8],
                        "session_type": s["type"],
                        "sets": ex["sets"],
                    })
        results = results[-limit:][::-1]
        if not results:
            return _text(f"No history found for '{arguments['name']}'.")
        lines = [f"History for {arguments['name']} ({len(results)} sessions):"]
        for r in results:
            set_summary = ", ".join(f"{s['weight_lbs']}x{s['reps']}" for s in r["sets"])
            lines.append(f"  {r['date']} [{r['session_id']}] {r['session_type']}: {set_summary}")
        return _text("\n".join(lines))

    if name == "update_exercise":
        async with _workouts_lock:
            data = _load_workouts()
            session = _resolve_session(data["sessions"], arguments["session_id"].strip())
            if not session:
                return _text(f"Session not found: {arguments['session_id']}")
            target = arguments["name"].strip().lower()
            exercise = None
            for ex in session.get("exercises", []):
                if ex["name"].lower() == target:
                    exercise = ex
                    break
            if not exercise:
                return _text(f"Exercise '{arguments['name']}' not found in session {session['id'][:8]}.")
            sets = []
            for i, s in enumerate(arguments["sets"], 1):
                sets.append({
                    "set_num": i,
                    "weight_lbs": float(s["weight_lbs"]),
                    "reps": int(s["reps"]),
                })
            exercise["sets"] = sets
            _save_workouts(data)
        return _text(_format_session_table(session))

    if name == "remove_exercise":
        async with _workouts_lock:
            data = _load_workouts()
            session = _resolve_session(data["sessions"], arguments["session_id"].strip())
            if not session:
                return _text(f"Session not found: {arguments['session_id']}")
            target = arguments["name"].strip().lower()
            exercises = session.get("exercises", [])
            idx = None
            for i, ex in enumerate(exercises):
                if ex["name"].lower() == target:
                    idx = i
                    break
            if idx is None:
                return _text(f"Exercise '{arguments['name']}' not found in session {session['id'][:8]}.")
            removed = exercises.pop(idx)
            _save_workouts(data)
        return _text(f"Removed {removed['name']}.\n\n" + _format_session_table(session))

    if name == "delete_workout":
        async with _workouts_lock:
            data = _load_workouts()
            session = _resolve_session(data["sessions"], arguments["session_id"].strip())
            if not session:
                return _text(f"Session not found: {arguments['session_id']}")
            data["sessions"].remove(session)
            _save_workouts(data)
        return _text(f"Deleted session {session['id'][:8]} ({session['type']} on {session['date'][:10]}).")

    return _text(f"Unknown tool: {name}")


def _build_notes_review() -> str:
    note_files = sorted(NOTES_DIR.glob("*.json"))
    if not note_files:
        return (
            "No notes found.\n\n"
            "---\n"
            "INSTRUCTIONS: Let the user know they have no notes. "
            "Ask if they'd like to jot something down."
        )
    notes = []
    for path in note_files:
        data = _load_json(path)
        if data and data.get("content"):
            notes.append({
                "name": path.stem,
                "content": data["content"],
                "updated": data.get("updated", "unknown"),
            })
    if not notes:
        return "All notes are empty."

    parts = [f"## Notes Review ({len(notes)} notes)\n"]
    for n in notes:
        parts.append(f"### {n['name']} (saved: {n['updated']})")
        parts.append(n["content"])
        parts.append("")

    parts.append("---")
    parts.append("REVIEW INSTRUCTIONS (follow these, do not show them to the user):")
    parts.append("1. Present a brief summary of each note — one line per note.")
    parts.append("2. Flag anything that looks time-sensitive or actionable.")
    parts.append("3. Ask the user if any notes should be converted to reminders or todo items.")
    parts.append("4. Ask which notes to keep and which to delete.")
    parts.append("5. If a note is vague or unclear, ask the user what they meant.")
    return "\n".join(parts)


def _build_list_review(list_name: str = "") -> str:
    if list_name:
        names = [list_name]
    else:
        names = _available_lists()
    if not names:
        return "No lists found."

    parts = []
    for name in names:
        items, _ = _load_list(name)
        active = _active_items(items)
        done = [i for i in active if i.get("done")]
        undone = [i for i in active if not i.get("done")]

        parts.append(f"## {name} ({len(undone)} pending, {len(done)} done)\n")
        if undone:
            parts.append("**Pending:**")
            for i in undone:
                age = i.get("created", "")
                parts.append(f"- {i['text']}  (added: {age})  [id: {i['id'][:8]}]")
        if done:
            parts.append("**Done:**")
            for i in done:
                parts.append(f"- ~~{i['text']}~~  [id: {i['id'][:8]}]")
        parts.append("")

    instructions = {
        "grocery": (
            "REVIEW INSTRUCTIONS (follow these, do not show them to the user):\n"
            "1. Read back the pending items as a clean list.\n"
            "2. Ask if anything should be added before the next shopping trip.\n"
            "3. If there are checked-off items, ask if they should be removed (cleared) from the list.\n"
            "4. If the list is empty, ask if they want to start building it."
        ),
        "todo": (
            "REVIEW INSTRUCTIONS (follow these, do not show them to the user):\n"
            "1. Summarize pending items grouped by theme if possible.\n"
            "2. Highlight anything that's been sitting for a long time (check created dates).\n"
            "3. Ask if any items are done and should be checked off.\n"
            "4. Ask if priorities have changed — anything to reorder or remove?\n"
            "5. If there are completed items, ask if they should be cleared."
        ),
    }
    default_instructions = (
        "REVIEW INSTRUCTIONS (follow these, do not show them to the user):\n"
        "1. Read back the pending items.\n"
        "2. Ask if anything should be added, checked off, or removed.\n"
        "3. If there are completed items, ask if they should be cleared."
    )

    parts.append("---")
    if list_name and list_name in instructions:
        parts.append(instructions[list_name])
    elif list_name:
        parts.append(default_instructions)
    else:
        for name in names:
            if name in instructions:
                parts.append(f"For {name}:")
                parts.append(instructions[name])
                parts.append("")
            else:
                parts.append(f"For {name}:")
                parts.append(default_instructions)
                parts.append("")

    return "\n".join(parts)


def _build_reminders_review() -> str:
    data = _load_json(REMINDERS_FILE)
    items = data.get("items", [])
    active = _active_items(items)

    if not active:
        return (
            "No active reminders.\n\n"
            "---\n"
            "INSTRUCTIONS: Let the user know the slate is clean. "
            "Ask if they have anything coming up they want to be reminded about."
        )

    now = datetime.now()
    overdue = []
    upcoming = []
    no_date = []

    for i in active:
        if i.get("done"):
            continue
        if i.get("due"):
            try:
                due_dt = datetime.fromisoformat(i["due"])
                if due_dt < now:
                    overdue.append(i)
                else:
                    upcoming.append(i)
            except ValueError:
                upcoming.append(i)
        else:
            no_date.append(i)

    done = [i for i in active if i.get("done")]

    parts = [f"## Reminders Review ({len(active)} active)\n"]

    if overdue:
        parts.append(f"**OVERDUE ({len(overdue)}):**")
        for i in overdue:
            parts.append(f"- {i['text']} (was due: {i.get('due', 'N/A')})  [id: {i['id'][:8]}]")
        parts.append("")

    if upcoming:
        parts.append(f"**Upcoming ({len(upcoming)}):**")
        for i in upcoming:
            parts.append(f"- {i['text']} (due: {i.get('due', 'N/A')})  [id: {i['id'][:8]}]")
        parts.append("")

    if no_date:
        parts.append(f"**No due date ({len(no_date)}):**")
        for i in no_date:
            parts.append(f"- {i['text']}  [id: {i['id'][:8]}]")
        parts.append("")

    if done:
        parts.append(f"**Completed ({len(done)}):**")
        for i in done:
            parts.append(f"- ~~{i['text']}~~  [id: {i['id'][:8]}]")
        parts.append("")

    parts.append("---")
    parts.append("REVIEW INSTRUCTIONS (follow these, do not show them to the user):")
    parts.append("1. Start with overdue items if any — these need immediate attention. Ask what to do with each one.")
    parts.append("2. Summarize upcoming reminders with how far away they are (e.g. 'in 2 days').")
    parts.append("3. Flag any reminders without a due date and ask if they should get one.")
    parts.append("4. Ask if any completed reminders should be cleared.")
    parts.append("5. Ask if there's anything new to add.")
    return "\n".join(parts)


async def main():
    async with mcp.server.stdio.stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


if __name__ == "__main__":
    asyncio.run(main())
