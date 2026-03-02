#!/usr/bin/env python3
"""
Bidirectional merge of lists, notes, and reminders between peers.

Usage: merge.py

Reads peer connection info from config.json. Merges all managed files:
  lists/*.json           — item lists (todo, grocery, custom)
  notes/*.json           — notes
  reminders/reminders.json — reminders

Merge strategy:
  - Lists/reminders: dedup by id, latest "updated" timestamp wins per item
  - Notes: latest "updated" timestamp wins (last writer wins)
  - Soft deletes: items with "deleted": true are kept so they don't resurrect
"""

import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from config import CONFIG, DATA_DIR, LISTS_DIR, NOTES_DIR, REMINDERS_DIR, HEALTH_DIR

PEER = CONFIG.get("peer", {})
SSH_USER = PEER.get("ssh_user", "")
SSH_KEY = str(Path(PEER.get("ssh_key", "~/.ssh/id_ed25519")).expanduser())
LOCAL_IP = PEER.get("local_ip", "")
TAILSCALE_IP = PEER.get("tailscale_ip", "")
REMOTE_DATA_DIR = PEER.get("data_dir", "")


def _find_host() -> str | None:
    """Try local IP first, then Tailscale."""
    for ip in (LOCAL_IP, TAILSCALE_IP):
        if not ip:
            continue
        result = subprocess.run(
            ["ssh", "-i", SSH_KEY, "-o", "BatchMode=yes", "-o", "ConnectTimeout=3",
             "-o", "StrictHostKeyChecking=no", f"{SSH_USER}@{ip}", "true"],
            capture_output=True, timeout=10,
        )
        if result.returncode == 0:
            return ip
    return None


def _ssh_cmd(host: str, cmd: str) -> str:
    result = subprocess.run(
        ["ssh", "-i", SSH_KEY, "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=no",
         f"{SSH_USER}@{host}", cmd],
        capture_output=True, text=True, timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError(f"SSH failed: {cmd!r}\n{result.stderr.strip()}")
    return result.stdout


def _ssh_read(host: str, remote_path: str) -> str:
    try:
        return _ssh_cmd(host, f"cat {remote_path} 2>/dev/null || true")
    except RuntimeError:
        return ""


def _ssh_write(host: str, remote_path: str, content: str) -> None:
    proc = subprocess.run(
        ["ssh", "-i", SSH_KEY, "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=no",
         f"{SSH_USER}@{host}",
         f"mkdir -p $(dirname {remote_path}) && "
         f"cat > {remote_path}.tmp && mv {remote_path}.tmp {remote_path}"],
        input=content, capture_output=True, text=True, timeout=30,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"SSH write failed: {remote_path}\n{proc.stderr.strip()}")


# ── JSON I/O ─────────────────────────────────────────────────────────────────

def _read_local_json(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


def _write_local_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    tmp.rename(path)


def _read_remote_json(host: str, rel_path: str) -> dict | None:
    text = _ssh_read(host, f"{REMOTE_DATA_DIR}/{rel_path}")
    if not text.strip():
        return None
    try:
        return json.loads(text)
    except Exception:
        return None


def _write_remote_json(host: str, rel_path: str, data: dict) -> None:
    _ssh_write(host, f"{REMOTE_DATA_DIR}/{rel_path}",
               json.dumps(data, indent=2, ensure_ascii=False) + "\n")


# ── Merge logic ──────────────────────────────────────────────────────────────

def _merge_list_data(local: dict | None, remote: dict | None) -> dict:
    """Merge two list/reminder JSON objects. Dedup by id, latest updated wins."""
    by_id = {}
    for item in (local or {}).get("items", []):
        by_id[item["id"]] = item
    for item in (remote or {}).get("items", []):
        iid = item["id"]
        if iid not in by_id:
            by_id[iid] = item
        else:
            if item.get("updated", "") > by_id[iid].get("updated", ""):
                by_id[iid] = item
    items = sorted(by_id.values(), key=lambda x: x.get("created", ""))
    return {"items": items}


def _merge_note_data(local: dict | None, remote: dict | None) -> dict | None:
    """Latest updated timestamp wins."""
    if not local:
        return remote
    if not remote:
        return local
    return local if local.get("updated", "") >= remote.get("updated", "") else remote


def _merge_workouts_data(local: dict | None, remote: dict | None) -> dict:
    """Merge workout sessions by session ID, exercises by exercise ID."""
    by_session = {}
    for session in (local or {}).get("sessions", []):
        by_session[session["id"]] = {**session, "exercises": {ex["id"]: ex for ex in session.get("exercises", [])}}
    for session in (remote or {}).get("sessions", []):
        sid = session["id"]
        if sid not in by_session:
            by_session[sid] = {**session, "exercises": {ex["id"]: ex for ex in session.get("exercises", [])}}
        else:
            # Session exists on both sides — merge exercises by ID
            for ex in session.get("exercises", []):
                by_session[sid]["exercises"].setdefault(ex["id"], ex)
    # Rebuild sessions with exercises as list, sorted by date
    sessions = []
    for s in by_session.values():
        sessions.append({**s, "exercises": list(s["exercises"].values())})
    sessions.sort(key=lambda s: s.get("date", ""))
    return {"sessions": sessions}


def _changed(a: dict | None, b: dict | None) -> bool:
    return json.dumps(a, sort_keys=True) != json.dumps(b, sort_keys=True)


# ── Per-file sync ────────────────────────────────────────────────────────────

def _sync_list(host: str, rel_path: str, label: str) -> None:
    local_path = DATA_DIR / rel_path
    local = _read_local_json(local_path)
    remote = _read_remote_json(host, rel_path)
    merged = _merge_list_data(local, remote)

    local_changed = _changed(merged, local)
    remote_changed = _changed(merged, remote)

    if local_changed:
        _write_local_json(local_path, merged)
    if remote_changed:
        _write_remote_json(host, rel_path, merged)

    if local_changed or remote_changed:
        print(f"  {label}: merged ({len(merged['items'])} items)")


def _sync_note(host: str, rel_path: str, label: str) -> None:
    local_path = DATA_DIR / rel_path
    local = _read_local_json(local_path)
    remote = _read_remote_json(host, rel_path)
    merged = _merge_note_data(local, remote)

    if merged is None:
        return

    local_changed = _changed(merged, local)
    remote_changed = _changed(merged, remote)

    if local_changed:
        _write_local_json(local_path, merged)
    if remote_changed:
        _write_remote_json(host, rel_path, merged)

    if local_changed or remote_changed:
        print(f"  {label}: merged")


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    host = _find_host()
    if not host:
        print("Peer unreachable, skipping merge.", file=sys.stderr)
        sys.exit(1)

    print(f"Merging with peer at {host}...")

    # Merge all list files
    for list_file in sorted(LISTS_DIR.glob("*.json")):
        rel = f"lists/{list_file.name}"
        _sync_list(host, rel, list_file.stem)

    # Also check for lists that exist on remote but not locally
    try:
        remote_lists = _ssh_cmd(host, f"ls {REMOTE_DATA_DIR}/lists/*.json 2>/dev/null || true")
        for line in remote_lists.strip().splitlines():
            name = Path(line).name
            if not (LISTS_DIR / name).exists():
                rel = f"lists/{name}"
                _sync_list(host, rel, Path(name).stem)
    except RuntimeError:
        pass

    # Merge all note files
    for note_file in sorted(NOTES_DIR.glob("*.json")):
        rel = f"notes/{note_file.name}"
        _sync_note(host, rel, note_file.stem)

    # Check for notes that exist on remote but not locally
    try:
        remote_notes = _ssh_cmd(host, f"ls {REMOTE_DATA_DIR}/notes/*.json 2>/dev/null || true")
        for line in remote_notes.strip().splitlines():
            name = Path(line).name
            if not (NOTES_DIR / name).exists():
                rel = f"notes/{name}"
                _sync_note(host, rel, Path(name).stem)
    except RuntimeError:
        pass

    # Merge reminders
    _sync_list(host, "reminders/reminders.json", "reminders")

    # Merge health files
    all_health = set(f.name for f in sorted(HEALTH_DIR.glob("*.json")))
    try:
        remote_health = _ssh_cmd(host, f"ls {REMOTE_DATA_DIR}/health/*.json 2>/dev/null || true")
        for line in remote_health.strip().splitlines():
            if line.strip():
                all_health.add(Path(line).name)
    except RuntimeError:
        pass
    for name in sorted(all_health):
        rel = f"health/{name}"
        local_path = DATA_DIR / rel
        if name == "workouts.json":
            local = _read_local_json(local_path)
            remote = _read_remote_json(host, rel)
            merged = _merge_workouts_data(local, remote)
            local_changed = _changed(merged, local)
            remote_changed = _changed(merged, remote)
            if local_changed:
                _write_local_json(local_path, merged)
            if remote_changed:
                _write_remote_json(host, rel, merged)
            if local_changed or remote_changed:
                print(f"  health/workouts: merged ({len(merged['sessions'])} sessions)")
        else:
            _sync_note(host, rel, f"health/{Path(name).stem}")

    print("Done.")


if __name__ == "__main__":
    main()
