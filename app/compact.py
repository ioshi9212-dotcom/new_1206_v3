"""Small standalone storage/runtime base for Akira 1206 v3.

This is intentionally not the old 1206 v2 runtime. It provides the base
FastAPI app, filesystem helpers, and canonical v3 start-session state.
"""
from __future__ import annotations

import json
import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI

APP_NAME = "akira-1206-v3"
APP_VERSION = "0.3.192-v3-runtime-consistency-fix"
BASE_URL = os.getenv("PUBLIC_BASE_URL") or os.getenv("RAILWAY_PUBLIC_DOMAIN") or "http://localhost:8000"
if BASE_URL and not BASE_URL.startswith(("http://", "https://")):
    BASE_URL = "https://" + BASE_URL

REPO_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = Path(os.getenv("DATA_DIR", str(REPO_ROOT / ".data"))).resolve()
SESSIONS_DIR = DATA_DIR / "sessions"

SYNC_FROM_REPO: list[str] = ["api_contracts", "calendar", "canon_lore", "characters", "gpt", "state", "scenes", "schedule", "npcs"]

START_COMMANDS = {"начнем", "начнём", "начинай", "начать", "старт", "start", "begin"}

app = FastAPI(title="Akira 1206 v3 API", version=APP_VERSION)
app.version = APP_VERSION  # type: ignore[attr-defined]


def safe_session_id(session_id: str | None) -> str:
    raw = str(session_id or "default").strip()
    cleaned = "".join(ch for ch in raw if ch.isalnum() or ch in "-_")
    return cleaned or "default"


def new_session_id(prefix: str = "session") -> str:
    return safe_session_id(f"{prefix}_{datetime.utcnow().strftime('%Y%m%d_%H%M%S_%f')}")


def normalize_command(text: Any) -> str:
    return " ".join(str(text or "").strip().lower().replace("ё", "е").split())


def is_start_command(text: Any) -> bool:
    normalized = normalize_command(text)
    return normalized in {cmd.replace("ё", "е") for cmd in START_COMMANDS}


def _repo_path(path: str | Path) -> Path:
    return (REPO_ROOT / str(path).lstrip("/")).resolve()


def _session_root(session_id: str | None) -> Path:
    return SESSIONS_DIR / safe_session_id(session_id)


def _session_path(path: str | Path, session_id: str | None) -> Path:
    return (_session_root(session_id) / str(path).lstrip("/")).resolve()


def seed() -> None:
    """Create DATA_DIR and copy stable repo content into DATA once if absent.

    Session reads can still fall back to repo files, but having a DATA copy makes
    Railway persistence/debugging clearer and keeps old v2 paths out of the app.
    """
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    for name in SYNC_FROM_REPO:
        src = REPO_ROOT / name
        dst = DATA_DIR / name
        if not src.exists() or dst.exists():
            continue
        if src.is_dir():
            shutil.copytree(src, dst)
        else:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)


def ensure_session(session_id: str | None) -> str:
    sid = safe_session_id(session_id)
    seed()
    root = _session_root(sid)
    root.mkdir(parents=True, exist_ok=True)
    meta_path = root / "session_meta.json"
    if not meta_path.exists():
        write_json("session_meta.json", {
            "session_id": sid,
            "created_at": datetime.utcnow().isoformat(),
            "updated_at": datetime.utcnow().isoformat(),
            "runtime": APP_VERSION,
        }, session_id=sid)
    return sid


def read_text(path: str, session_id: str | None = None, default: str = "") -> str:
    candidates: list[Path] = []
    if session_id:
        candidates.append(_session_path(path, session_id))
    # Repository content is the current canon after every deploy.  The seeded
    # DATA_DIR copy is only a fallback; otherwise an old Railway Volume shadows
    # later GitHub updates forever.
    candidates.append(_repo_path(path))
    candidates.append(DATA_DIR / str(path).lstrip("/"))
    for candidate in candidates:
        try:
            if candidate.exists() and candidate.is_file():
                return candidate.read_text(encoding="utf-8")
        except Exception:
            continue
    return default


def write_text(path: str, data: str, session_id: str | None = None) -> None:
    target = _session_path(path, session_id) if session_id else (DATA_DIR / str(path).lstrip("/"))
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(str(data), encoding="utf-8")


def read_json(path: str, session_id: str | None = None, default: Any = None) -> Any:
    text = read_text(path, session_id=session_id, default="")
    if not text.strip():
        return default
    try:
        return json.loads(text)
    except Exception:
        return default


def write_json(path: str, data: Any, session_id: str | None = None) -> None:
    target = _session_path(path, session_id) if session_id else (DATA_DIR / str(path).lstrip("/"))
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def append_scene_history(session_id: str, entry: dict[str, Any]) -> None:
    sid = ensure_session(session_id)
    path = "state/scene_history.json"
    history = read_json(path, session_id=sid, default=[])
    if not isinstance(history, list):
        history = history.get("entries", []) if isinstance(history, dict) else []
    history.append(entry)
    write_json(path, history[-80:], session_id=sid)


def start_calendar_runtime(overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    data: dict[str, Any] = {
        "schema": "calendar_runtime_v3_start_state",
        "project": "akira-1206-v3",
        "current_date": "1206-08-31",
        "current_day_file": "calendar/days/1206-08-31.yaml",
        "current_day_phase": "поздняя ночь",
        "time_of_day": "поздняя ночь",
        "active_window": "start_pressure_node",
        "current_beat_id": "house_pressure_open",
        "completed_beat_ids": [],
        "skipped_beat_ids": [],
        "introduced_character_ids": [],
        "pending_events": [
            "player_reaction_window",
            "raiden_delayed_conditional_arrival",
            "samuel_people_search_and_pursuit_latency",
            "east_sector_contact_if_branch_reaches_it",
        ],
        "rules": [
            "Calendar defines world pressure and consequences, not Akira's scripted actions.",
            "Normal play loads only the current day file.",
            "Conditional arrivals require plausible in-world time and distance.",
            "Sleep/rest/timeskip should jump to the next meaningful beat.",
        ],
    }
    if overrides:
        for key, value in overrides.items():
            if value is not None:
                data[key] = value
    return data


def default_current_state(session_id: str, overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    """Canonical non-empty start state for a new v3 chat/session.

    This mirrors the 1206 v2 first-scene setup but uses the v3 full-card structure.
    """
    now = datetime.utcnow().isoformat()
    data: dict[str, Any] = {
        "session_id": safe_session_id(session_id),
        "project_slug": "akira-1206-v3",
        "story": "akira_1206_v3",
        "current_scene_id": "start_scene",
        "scene_id": "start_scene",
        "current_date": "1206-08-31",
        "date": "1206-08-31",
        "current_day_phase": "поздняя ночь",
        "time_of_day": "поздняя ночь",
        "current_location_id": "jun_house_akira_room",
        "location_id": "jun_house_akira_room",
        "current_location_text": "дом Джуна Картера, комната Акиры",
        "pov_character_id": "akira",
        "active_character_ids": ["akira", "jun", "irey", "emma"],
        "scene_character_ids": ["akira", "jun", "irey", "emma"],
        "present_character_ids": ["akira", "jun", "irey", "emma"],
        "conditional_character_ids": ["raiden", "ray"],
        "relationship_pair_ids": ["akira__jun", "akira__irey", "akira__emma", "jun__irey", "jun__emma"],
        "current_outfit": "серая пижама — футболка и шорты; босиком",
        "visible_inventory": ["записка: Рэй / Восточный сектор"],
        "nearby_items": ["дверь", "окно", "стол", "записка", "документы"],
        "inventory_state": {
            "note_ray_east_sector": "visible_start_scene_item; after exact first output Akira has read/taken it unless player rewrites action through applyTurnResult",
            "cover_documents": "visible_on_table_start_scene",
        },
        "scene_goal": "Стартовая сцена: поздняя ночь. Акира просыпается от голосов Эммы и Ирэя внизу; Джун тянет время; записка ведёт к Рэю / Восточному сектору.",
        "current_scene_goal": "Стартовая сцена: поздняя ночь. Акира просыпается от голосов Эммы и Ирэя внизу; Джун тянет время; записка ведёт к Рэю / Восточному сектору.",
        "last_player_input": "начнем",
        "voice_identity_map_hidden": {
            "Женский голос снизу": "emma",
            "Незнакомый мужской голос": "irey",
        },
        "visible_relationships_start": {
            "jun": {"score": 15, "label": "доверие", "visible_label": "Джун"},
            "irey": {"score": 1, "label": "настороженность", "visible_label": "Незнакомый мужской голос"},
            "emma": {"score": -2, "label": "угроза", "visible_label": "Женский голос снизу"},
        },
        "start_scene_file": "scenes/start_scene.md",
        "start_scene_logic_file": "scenes/start_scene_logic.md",
        "start_scene_exact_text_required": True,
        "start_scene_completed": False,
        "weather": {
            "summary": "прохладная поздняя ночь; воздух неподвижный",
            "temperature_feel": "прохладно",
            "details": [],
        },
        "akira_state": {
            "visible_state": "резко проснулась; внешне собрана",
            "internal_state": "эмоции заблокированы; память держит только последние два года",
            "body_state": "тело собрано раньше памяти",
            "hair_state": "сонные растрёпанные белые волосы",
        },
        "rules": [
            "Use only akira-1206-v3 data.",
            "Start date is 1206-08-31, phase is поздняя ночь.",
            "No Akira fallback is needed because the start scene explicitly selects characters.",
            "Do not reveal hidden lore automatically.",
            "Do not load past.yaml only because of words like записка, документы or Джун.",
            "Ray and Raiden are conditional; do not place them in the first scene without later scene source.",
        ],
        "created_at": now,
        "updated_at": now,
    }
    if overrides:
        for key, value in overrides.items():
            if value is not None:
                data[key] = value
    return data


def initialize_start_session(
    session_id: str | None,
    overrides: dict[str, Any] | None = None,
    *,
    reset_dynamic_state: bool = False,
) -> dict[str, Any]:
    """Write canonical start current_state/calendar into the per-session volume."""
    sid = safe_session_id(session_id)
    if reset_dynamic_state:
        # A fresh start must not retain scene history, character memory,
        # relationships, or maintenance counters from an older playthrough.
        shutil.rmtree(_session_root(sid), ignore_errors=True)
    sid = ensure_session(session_id)
    current = default_current_state(sid, overrides)
    current["updated_at"] = datetime.utcnow().isoformat()
    write_json("state/current_state.json", current, session_id=sid)
    write_json("state/calendar_runtime.json", start_calendar_runtime(), session_id=sid)
    if reset_dynamic_state:
        write_json("state/scene_history.json", [], session_id=sid)
    else:
        existing_history = read_json("state/scene_history.json", session_id=sid, default=None)
        if not isinstance(existing_history, list):
            write_json("state/scene_history.json", [], session_id=sid)
    write_json("state/story_lines.json", {
        "schema": "story_lines_runtime_v3",
        "turn_counter": 0,
        "last_state_recovery_audit_turn": 0,
        "last_compaction_cleanup_turn": 0,
        "maintenance": {
            "state_recovery_audit_every": 10,
            "compaction_cleanup_every": 15,
            "compaction_cleanup_offset": 12
        }
    }, session_id=sid)
    return current
