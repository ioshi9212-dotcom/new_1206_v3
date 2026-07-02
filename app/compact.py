"""Small standalone storage/runtime base for Akira 1206 v3.

This is intentionally not the old 1206 v2 runtime. It only provides the base
FastAPI app and filesystem helpers needed by the v3 full-card scene-contract
and apply-turn-result modules.
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
APP_VERSION = "0.3.181-v3-standalone-full-cards"
BASE_URL = os.getenv("PUBLIC_BASE_URL") or os.getenv("RAILWAY_PUBLIC_DOMAIN") or "http://localhost:8000"
if BASE_URL and not BASE_URL.startswith(("http://", "https://")):
    BASE_URL = "https://" + BASE_URL

REPO_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = Path(os.getenv("DATA_DIR", str(REPO_ROOT / ".data"))).resolve()
SESSIONS_DIR = DATA_DIR / "sessions"

SYNC_FROM_REPO: list[str] = ["api_contracts", "calendar", "canon_lore", "characters", "gpt", "state"]

app = FastAPI(title="Akira 1206 v3 API", version=APP_VERSION)
app.version = APP_VERSION  # type: ignore[attr-defined]


def safe_session_id(session_id: str | None) -> str:
    raw = str(session_id or "default").strip()
    cleaned = "".join(ch for ch in raw if ch.isalnum() or ch in "-_")
    return cleaned or "default"


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
    candidates.append(DATA_DIR / str(path).lstrip("/"))
    candidates.append(_repo_path(path))
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


def default_current_state(session_id: str, overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    data: dict[str, Any] = {
        "session_id": safe_session_id(session_id),
        "current_scene_id": "1206_start",
        "current_date": "1206-08-31",
        "current_day_phase": "night",
        "current_location_id": "unknown_start",
        "current_location_text": "стартовая сцена ещё не уточнена",
        "pov_character_id": "akira",
        "active_character_ids": ["akira"],
        "scene_character_ids": ["akira"],
        "scene_goal": "Начать сцену только из явно заданных вводных. Не добавлять отсутствующих персонажей.",
        "last_player_input": "",
        "created_at": datetime.utcnow().isoformat(),
        "updated_at": datetime.utcnow().isoformat(),
    }
    if overrides:
        for key, value in overrides.items():
            if value is not None:
                data[key] = value
    return data
