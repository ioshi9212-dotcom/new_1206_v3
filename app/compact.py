"""Small standalone storage/runtime base for Akira 1206 v3.

This is intentionally not the old 1206 v2 runtime. It provides the base
FastAPI app, filesystem helpers, and canonical v3 start-session state.
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from threading import RLock
from typing import Any

from fastapi import FastAPI

APP_NAME = "akira-1206-v3"
APP_VERSION = "0.4.0-v3-transactional-turns"
BASE_URL = os.getenv("PUBLIC_BASE_URL") or os.getenv("RAILWAY_PUBLIC_DOMAIN") or "http://localhost:8000"
if BASE_URL and not BASE_URL.startswith(("http://", "https://")):
    BASE_URL = "https://" + BASE_URL

REPO_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = Path(os.getenv("DATA_DIR", str(REPO_ROOT / ".data"))).resolve()
SESSIONS_DIR = DATA_DIR / "sessions"
TURN_RUNTIME_FILE = "state/turn_runtime.json"
TRANSACTIONS_DIR = "state/transactions"

_SESSION_LOCKS: dict[str, RLock] = {}
_SESSION_LOCKS_GUARD = RLock()

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


def _session_lock(session_id: str | None) -> RLock:
    sid = safe_session_id(session_id)
    with _SESSION_LOCKS_GUARD:
        return _SESSION_LOCKS.setdefault(sid, RLock())


@contextmanager
def session_guard(session_id: str | None):
    """Serialize multi-file session operations inside this runtime process."""
    with _session_lock(session_id):
        yield


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
    recover_json_transactions(sid)
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
    _atomic_write_text_target(target, str(data))


def read_json(path: str, session_id: str | None = None, default: Any = None) -> Any:
    text = read_text(path, session_id=session_id, default="")
    if not text.strip():
        return default
    try:
        return json.loads(text)
    except Exception:
        return default


def read_session_json(path: str, session_id: str, default: Any = None) -> Any:
    """Read only a session-owned file, without falling back to repo templates."""
    target = _session_path(path, safe_session_id(session_id))
    try:
        if not target.is_file():
            return default
        return json.loads(target.read_text(encoding="utf-8"))
    except Exception:
        return default


def write_json(path: str, data: Any, session_id: str | None = None) -> None:
    target = _session_path(path, session_id) if session_id else (DATA_DIR / str(path).lstrip("/"))
    _atomic_write_json_target(target, data)


def _atomic_write_text_target(target: Path, data: str) -> None:
    """Replace one file atomically so readers never see half-written JSON/text."""
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=str(target.parent))
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, target)
        try:
            dir_fd = os.open(target.parent, os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        except OSError:
            # Directory fsync is not available on every local/test filesystem.
            pass
    finally:
        if temp_path.exists():
            temp_path.unlink()


def _atomic_write_json_target(target: Path, data: Any) -> None:
    _atomic_write_text_target(target, json.dumps(data, ensure_ascii=False, indent=2))


def _safe_session_target(session_id: str, relative_path: str | Path) -> Path:
    raw = str(relative_path).lstrip("/")
    if not raw or ".." in Path(raw).parts:
        raise ValueError(f"Unsafe session path: {relative_path!r}")
    root = _session_root(session_id).resolve()
    target = (root / raw).resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"Session path escapes root: {relative_path!r}") from exc
    return target


def commit_json_transaction(session_id: str, transaction_id: str, writes: dict[str, Any]) -> None:
    """Durably roll a set of JSON files forward as one recoverable transaction.

    A prepared journal is written before any target. If the process dies between
    file replacements, ``ensure_session`` replays the complete target set before
    the next request is served.
    """
    sid = safe_session_id(session_id)
    safe_tid = safe_session_id(transaction_id)
    if not writes:
        return
    with session_guard(sid):
        root = _session_root(sid)
        root.mkdir(parents=True, exist_ok=True)
        ordered = [
            {"path": str(path).lstrip("/"), "data": data}
            for path, data in sorted(writes.items(), key=lambda item: item[0])
        ]
        for item in ordered:
            _safe_session_target(sid, item["path"])
        journal_path = _safe_session_target(sid, f"{TRANSACTIONS_DIR}/{safe_tid}.json")
        journal = {
            "schema": "json_transaction_v1",
            "transaction_id": safe_tid,
            "status": "prepared",
            "prepared_at": datetime.utcnow().isoformat(),
            "writes": ordered,
        }
        _atomic_write_json_target(journal_path, journal)
        for item in ordered:
            _atomic_write_json_target(_safe_session_target(sid, item["path"]), item["data"])
        journal["status"] = "committed"
        journal["committed_at"] = datetime.utcnow().isoformat()
        _atomic_write_json_target(journal_path, journal)
        try:
            journal_path.unlink()
        except FileNotFoundError:
            pass


def recover_json_transactions(session_id: str) -> list[str]:
    """Finish prepared multi-file writes left behind by an interrupted request."""
    sid = safe_session_id(session_id)
    recovered: list[str] = []
    with session_guard(sid):
        journal_dir = _safe_session_target(sid, TRANSACTIONS_DIR)
        if not journal_dir.is_dir():
            return recovered
        for journal_path in sorted(journal_dir.glob("*.json")):
            try:
                journal = json.loads(journal_path.read_text(encoding="utf-8"))
            except Exception:
                continue
            status = str(journal.get("status") or "") if isinstance(journal, dict) else ""
            writes = journal.get("writes") if isinstance(journal, dict) else None
            if status == "committed":
                journal_path.unlink(missing_ok=True)
                continue
            if status != "prepared" or not isinstance(writes, list):
                continue
            for item in writes:
                if not isinstance(item, dict) or "path" not in item:
                    raise ValueError(f"Invalid transaction journal: {journal_path}")
                _atomic_write_json_target(_safe_session_target(sid, item["path"]), item.get("data"))
            journal["status"] = "committed"
            journal["recovered_at"] = datetime.utcnow().isoformat()
            _atomic_write_json_target(journal_path, journal)
            recovered.append(str(journal.get("transaction_id") or journal_path.stem))
            journal_path.unlink(missing_ok=True)
    return recovered


def default_turn_runtime() -> dict[str, Any]:
    return {
        "schema": "turn_runtime_v1",
        "state_revision": 0,
        "next_turn_number": 1,
        "pending_turn": None,
        "last_applied_turn": None,
        "updated_at": datetime.utcnow().isoformat(),
    }


def read_turn_runtime(session_id: str) -> dict[str, Any]:
    runtime = read_session_json(TURN_RUNTIME_FILE, safe_session_id(session_id), default={})
    if not isinstance(runtime, dict) or runtime.get("schema") != "turn_runtime_v1":
        runtime = default_turn_runtime()
    runtime.setdefault("state_revision", 0)
    runtime.setdefault("next_turn_number", 1)
    runtime.setdefault("pending_turn", None)
    runtime.setdefault("last_applied_turn", None)
    return runtime


def get_pending_turn(session_id: str) -> dict[str, Any] | None:
    pending = read_turn_runtime(session_id).get("pending_turn")
    return pending if isinstance(pending, dict) and pending.get("turn_id") else None


def effective_current_state(session_id: str) -> dict[str, Any]:
    """Return committed state overlaid with the one protected pending input."""
    sid = safe_session_id(session_id)
    current = read_session_json("state/current_state.json", sid, default={})
    if not isinstance(current, dict):
        current = {}
    effective = dict(current)
    pending = get_pending_turn(sid)
    if pending:
        patch = pending.get("current_state_patch")
        if isinstance(patch, dict):
            effective.update(patch)
        effective["last_player_input"] = str(pending.get("player_input") or "")
    return effective


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
    """Write a fresh canonical start snapshot and clear the turn transaction."""
    sid = safe_session_id(session_id)
    if reset_dynamic_state:
        # A fresh start must not retain scene history, character memory,
        # relationships, or maintenance counters from an older playthrough.
        shutil.rmtree(_session_root(sid), ignore_errors=True)
    sid = ensure_session(session_id)
    current = default_current_state(sid, overrides)
    current["updated_at"] = datetime.utcnow().isoformat()
    current["state_revision"] = 0
    writes: dict[str, Any] = {
        "state/current_state.json": current,
        "state/calendar_runtime.json": start_calendar_runtime(),
        TURN_RUNTIME_FILE: default_turn_runtime(),
    }
    if reset_dynamic_state:
        writes["state/scene_history.json"] = []
    else:
        existing_history = read_json("state/scene_history.json", session_id=sid, default=None)
        if not isinstance(existing_history, list):
            writes["state/scene_history.json"] = []
    writes["state/story_lines.json"] = {
        "schema": "story_lines_runtime_v3",
        "turn_counter": 0,
        "last_state_recovery_audit_turn": 0,
        "last_compaction_cleanup_turn": 0,
        "maintenance": {
            "state_recovery_audit_every": 10,
            "compaction_cleanup_every": 15,
            "compaction_cleanup_offset": 12
        }
    }
    commit_json_transaction(sid, f"start_{new_session_id('snapshot')}", writes)
    return current
