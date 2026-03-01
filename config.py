"""
Configuration loader for mcp-data.

Loads config.json from the repo directory, expands ~ in path fields,
and provides a singleton CONFIG dict used by all other modules.
"""

import json
from pathlib import Path

_CONFIG_PATH = Path(__file__).parent / "config.json"


def _expand_paths(cfg: dict) -> dict:
    """Expand ~ in known path fields."""
    for key in ("data_dir",):
        if key in cfg:
            cfg[key] = str(Path(cfg[key]).expanduser())
    peer = cfg.get("peer", {})
    if "data_dir" in peer:
        peer["data_dir"] = str(Path(peer["data_dir"]).expanduser())
    if "ssh_key" in peer:
        peer["ssh_key"] = str(Path(peer["ssh_key"]).expanduser())
    return cfg


def load_config() -> dict:
    if not _CONFIG_PATH.exists():
        raise FileNotFoundError(
            f"No config.json found at {_CONFIG_PATH}. "
            f"Copy config.example.json to config.json and fill in your values."
        )
    with open(_CONFIG_PATH) as f:
        cfg = json.load(f)
    return _expand_paths(cfg)


CONFIG = load_config()

# Derived paths
DATA_DIR = Path(CONFIG["data_dir"])
LISTS_DIR = DATA_DIR / "lists"
NOTES_DIR = DATA_DIR / "notes"
REMINDERS_DIR = DATA_DIR / "reminders"
REMINDERS_FILE = REMINDERS_DIR / "reminders.json"
HEALTH_DIR = DATA_DIR / "health"
HEALTH_PROFILE = HEALTH_DIR / "profile.json"
HEALTH_WORKOUTS = HEALTH_DIR / "workouts.json"

# Ensure directories exist
LISTS_DIR.mkdir(parents=True, exist_ok=True)
NOTES_DIR.mkdir(parents=True, exist_ok=True)
REMINDERS_DIR.mkdir(parents=True, exist_ok=True)
HEALTH_DIR.mkdir(parents=True, exist_ok=True)
