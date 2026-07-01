"""GPT Actions compatibility patch for 1206_v2.

Keeps the current calendar/scene-packet runtime, but makes the main Custom GPT
endpoints tolerant:
- default session ids such as main-1206-v2 are auto-created on read calls;
- GET endpoints also have POST fallbacks for older/stale GPT Action schemas.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import Body

import app.calendar_scene_runtime_patch as runtime
from app.calendar_scene_runtime_patch import app
from app import compact as base

try:
    import app.compact_context_patch as ccp
except Exception:  # pragma: no cover
    ccp = None  # type: ignore[assignment]

app.version = "0.3.64-actions-compat-auto-session"

SESSION_CONTEXT_PATH = "/api/v1/sessions/{session_id}/context"
TURN_CONTRACT_PATH = "/api/v1/sessions/{session_id}/turn-contract"
REQUIRED_FILES_MANIFEST_PATH = "/api/v1/sessions/{session_id}/required-files-manifest"
REQUIRED_FILES_CHUNK_PATH = "/api/v1/sessions/{session_id}/required-files-chunk"
REQUIRED_FILES_BUNDLE_PATH = "/api/v1/sessions/{session_id}/required-files-bundle"


def _safe_session_id(session_id: str | None) -> str:
    raw = session_id or "main-1206-v2"
    try:
        return base.safe_session_id(raw)
    except Exception:
        safe = "".join(ch for ch in str(raw) if ch.isalnum() or ch in "-_")
        return safe or "main-1206-v2"


def _now() -> str:
    return datetime.utcnow().isoformat()


def _ensure_session_exists(session_id: str | None) -> str:
    """Create the requested session if GPT calls read endpoints before createSession."""
    base.seed()
    sid = _safe_session_id(session_id)
    session_root = base.session_dir(sid)
    if not session_root.exists():
        session_root.mkdir(parents=True, exist_ok=True)
        try:
            base.copy_missing(base.DATA / "state", session_root / "state")
        except Exception:
            pass
        meta = {
            "session_id": sid,
            "title": "Akira 1206 v2 Session",
            "created_at": _now(),
            "updated_at": _now(),
            "auto_created_by": "actions_compat_runtime_patch",
        }
        (session_root / "session.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    else:
        meta_path = session_root / "session.json"
        if not meta_path.exists():
            meta = {
                "session_id": sid,
                "title": "Akira 1206 v2 Session",
                "created_at": _now(),
                "updated_at": _now(),
                "auto_created_by": "actions_compat_runtime_patch",
            }
            meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return sid


def _remove_route(path: str, methods: set[str] | None = None) -> None:
    methods_upper = {m.upper() for m in methods} if methods else None
    for route in list(app.router.routes):
        if getattr(route, "path", None) != path:
            continue
        route_methods = set(getattr(route, "methods", set()) or set())
        if methods_upper is None or route_methods & methods_upper:
            app.router.routes.remove(route)


def _query_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except Exception:
        return default


# Remove earlier strict GET routes so these tolerant versions run first.
for _path in [
    SESSION_CONTEXT_PATH,
    TURN_CONTRACT_PATH,
    REQUIRED_FILES_MANIFEST_PATH,
    REQUIRED_FILES_CHUNK_PATH,
    REQUIRED_FILES_BUNDLE_PATH,
]:
    _remove_route(_path)


@app.get(SESSION_CONTEXT_PATH, operation_id="getSessionContext")
def get_session_context_compat(session_id: str) -> Any:
    sid = _ensure_session_exists(session_id)
    return base.context_payload(sid)


@app.post(SESSION_CONTEXT_PATH, include_in_schema=False)
def post_session_context_compat(session_id: str, body: dict[str, Any] | None = Body(default=None)) -> Any:
    sid = _ensure_session_exists(session_id)
    return base.context_payload(sid)


@app.get(TURN_CONTRACT_PATH, operation_id="getSessionTurnContract")
def get_turn_contract_compat(session_id: str) -> Any:
    sid = _ensure_session_exists(session_id)
    if ccp is not None and hasattr(ccp, "session_turn_contract_with_prompt_preview"):
        return ccp.session_turn_contract_with_prompt_preview(sid)  # type: ignore[attr-defined]
    return base.session_turn_contract(sid)


@app.post(TURN_CONTRACT_PATH, include_in_schema=False)
def post_turn_contract_compat(session_id: str, body: dict[str, Any] | None = Body(default=None)) -> Any:
    sid = _ensure_session_exists(session_id)
    if ccp is not None and hasattr(ccp, "session_turn_contract_with_prompt_preview"):
        return ccp.session_turn_contract_with_prompt_preview(sid)  # type: ignore[attr-defined]
    return base.session_turn_contract(sid)


@app.get(REQUIRED_FILES_MANIFEST_PATH, operation_id="getRequiredFilesManifest")
def get_required_files_manifest_compat(session_id: str) -> Any:
    sid = _ensure_session_exists(session_id)
    return {
        "session_id": sid,
        "mode": "diagnostic_disabled_for_normal_gameplay",
        "required_files": [],
        "files": [],
        "missing_files": [],
        "loaded_count": 0,
        "missing_count": 0,
        "chunks_total": 0,
        "usage_note": "Use getFastRenderContext. Full file chunks are disabled for normal gameplay.",
    }


@app.post(REQUIRED_FILES_MANIFEST_PATH, include_in_schema=False)
def post_required_files_manifest_compat(session_id: str, body: dict[str, Any] | None = Body(default=None)) -> Any:
    return get_required_files_manifest_compat(session_id)


@app.get(REQUIRED_FILES_CHUNK_PATH, operation_id="getRequiredFilesChunk")
def get_required_files_chunk_compat(
    session_id: str,
    chunk_index: int = 0,
    max_chars: int = 12000,
    max_items: int = 1,
) -> Any:
    sid = _ensure_session_exists(session_id)
    return {
        "session_id": sid,
        "mode": "full_chunks_disabled_for_normal_gameplay",
        "chunk_index": 0,
        "chunks_total": 0,
        "has_more": False,
        "next_chunk_index": None,
        "required_files": [],
        "loaded_files": [],
        "missing_files": [],
        "loaded_count": 0,
        "missing_count": 0,
        "total_loaded_parts": 0,
        "usage_note": "Use getFastRenderContext. Do not call file chunks during gameplay.",
    }


@app.post(REQUIRED_FILES_CHUNK_PATH, include_in_schema=False)
def post_required_files_chunk_compat(session_id: str, body: dict[str, Any] | None = Body(default=None)) -> Any:
    return get_required_files_chunk_compat(session_id)


@app.get(REQUIRED_FILES_BUNDLE_PATH, operation_id="getRequiredFilesBundle")
def get_required_files_bundle_compat(
    session_id: str,
    chunk_index: int = 0,
    max_chars: int = 12000,
    max_items: int = 1,
) -> Any:
    return get_required_files_chunk_compat(session_id, chunk_index=chunk_index, max_chars=max_chars, max_items=max_items)


@app.post(REQUIRED_FILES_BUNDLE_PATH, include_in_schema=False)
def post_required_files_bundle_compat(session_id: str, body: dict[str, Any] | None = Body(default=None)) -> Any:
    return post_required_files_chunk_compat(session_id, body=body)


_old_openapi = app.openapi


def _openapi_actions_compat() -> dict[str, Any]:
    schema = _old_openapi()
    schema.setdefault("info", {})["version"] = app.version
    return schema


_remove_route("/openapi-actions.json")


@app.get("/openapi-actions.json", include_in_schema=False)
def openapi_actions() -> dict[str, Any]:
    return _openapi_actions_compat()


app.openapi_schema = None
app.openapi = _openapi_actions_compat  # type: ignore[method-assign]
