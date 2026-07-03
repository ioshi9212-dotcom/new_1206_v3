"""Production entrypoint for Akira 1206 v3 standalone API.

This version is action-safe: Custom GPT Actions must not receive huge full-card
scene contracts. The API now exposes a small preflight -> context_request ->
context_slice pipeline. Full cards remain on Railway and are sliced by blocks.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import Body

from app import compact as base
from app.compact import app

# Register legacy apply writer and internal helpers first.
import app.v3_full_cards_scene_contract_runtime_patch as v3_scene_contract  # noqa: F401,E402
import app.v3_apply_turn_result_runtime_patch as v3_apply_turn_result  # noqa: F401,E402
import app.context_request_runtime_patch as v3_context_request  # noqa: F401,E402

RUNTIME_VERSION = base.APP_VERSION


def _object_schema(properties: dict | None = None, *, required: list[str] | None = None) -> dict:
    schema = {"type": "object", "properties": properties or {}, "additionalProperties": True}
    if required:
        schema["required"] = required
    return schema


def _array_string() -> dict:
    return {"type": "array", "items": {"type": "string"}}


def _response(description: str, schema: dict | None = None) -> dict:
    return {"description": description, "content": {"application/json": {"schema": schema or {"type": "object", "properties": {}, "additionalProperties": True}}}}


def _session_path_param() -> dict:
    return {"name": "session_id", "in": "path", "required": True, "schema": {"type": "string"}}


def _payload(body: dict[str, Any] | None) -> dict[str, Any]:
    return body if isinstance(body, dict) else {}


def _startish_input(payload: dict[str, Any]) -> str:
    for key in ("player_input", "last_player_input", "command", "text", "message"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    current = payload.get("current_state")
    if isinstance(current, dict):
        for key in ("last_player_input", "command", "text", "message"):
            value = current.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return ""


def _merge_start_overrides(payload: dict[str, Any], *, player_input: str = "") -> dict[str, Any]:
    overrides: dict[str, Any] = {}
    current_state = payload.get("current_state")
    if isinstance(current_state, dict):
        overrides.update(current_state)
    for key in [
        "current_scene_id", "current_date", "current_day_phase", "current_location_id",
        "current_location_text", "pov_character_id", "active_character_ids", "scene_character_ids",
        "relationship_pair_ids", "scene_goal", "last_player_input",
    ]:
        if key in payload:
            overrides[key] = payload[key]
    if player_input:
        overrides["last_player_input"] = player_input
    return overrides


def _current_frame_ack(current: dict[str, Any]) -> dict[str, Any]:
    return {
        "current_scene_id": current.get("current_scene_id") or current.get("scene_id"),
        "current_date": current.get("current_date") or current.get("date"),
        "current_day_phase": current.get("current_day_phase") or current.get("time_of_day"),
        "current_location_id": current.get("current_location_id") or current.get("location_id"),
        "current_location_text": current.get("current_location_text") or current.get("location_text"),
        "pov_character_id": current.get("pov_character_id"),
        "active_character_ids": current.get("active_character_ids", []),
        "scene_character_ids": current.get("scene_character_ids", []),
        "relationship_pair_ids": current.get("relationship_pair_ids", []),
        "start_scene_exact_text_required": bool(current.get("start_scene_exact_text_required")),
        "start_scene_completed": bool(current.get("start_scene_completed")),
    }


@app.get("/", include_in_schema=False)
def root() -> dict[str, Any]:
    return {"status": "ok", "app": base.APP_NAME, "version": RUNTIME_VERSION}


@app.get("/health", operation_id="health")
def health() -> dict[str, Any]:
    base.seed()
    return {
        "status": "ok",
        "app": base.APP_NAME,
        "version": RUNTIME_VERSION,
        "public_base_url": base.BASE_URL,
        "standalone_v3": True,
        "context_pipeline": "preflight_context_request_context_slice",
        "large_contract_actions_disabled": True,
    }


@app.post("/api/v1/sessions", operation_id="createSession")
def create_session(body: dict[str, Any] | None = Body(default=None)) -> dict[str, Any]:
    """Create/ensure a session and return only a small ack for Custom GPT."""
    payload = _payload(body)
    raw_sid = payload.get("session_id")
    sid = base.ensure_session(raw_sid or base.new_session_id())
    current_state = base.read_json("state/current_state.json", session_id=sid, default=None)
    player_input = _startish_input(payload)
    start_command = base.is_start_command(player_input)
    reset = bool(payload.get("reset"))

    if reset or start_command or not isinstance(current_state, dict):
        current_state = base.initialize_start_session(sid, _merge_start_overrides(payload, player_input=player_input or "начнем"))

    return {
        "success": True,
        "session_id": sid,
        "created_at": datetime.utcnow().isoformat(),
        "runtime_version": RUNTIME_VERSION,
        "mode": "session_ack_light",
        "current_frame": _current_frame_ack(current_state),
        "next_action": "getPreflight",
        "note": "Session is ready. Do not request full scene_contract; call getPreflight, then requestContextSlice.",
    }


@app.post("/api/v1/start", operation_id="startSession")
def start_session(body: dict[str, Any] | None = Body(default=None)) -> dict[str, Any]:
    """New chat + `начнем`: create a fresh start state and return a tiny ack."""
    payload = _payload(body)
    payload.setdefault("reset", True)
    payload.setdefault("player_input", "начнем")
    return create_session(payload)


@app.post("/api/v1/sessions/{session_id}/turn", operation_id="processTurn")
def process_turn(session_id: str, body: dict[str, Any] | None = Body(default=None)) -> dict[str, Any]:
    """Store player input/current-state overrides and return a light ack.

    The GPT should then call requestContextSlice instead of receiving a full
    contract from this endpoint.
    """
    payload = _payload(body)
    sid = base.ensure_session(session_id)
    current_state = base.read_json("state/current_state.json", session_id=sid, default={})
    if not isinstance(current_state, dict):
        current_state = {}
    player_input = str(payload.get("player_input") or payload.get("text") or payload.get("message") or "").strip()
    start_command = base.is_start_command(player_input)

    if start_command:
        current_state = base.initialize_start_session(sid, _merge_start_overrides(payload, player_input=player_input))
    else:
        if not current_state:
            current_state = base.initialize_start_session(sid, _merge_start_overrides(payload, player_input=player_input))
        if player_input:
            current_state["last_player_input"] = player_input
        for key in [
            "pov_character_id", "active_character_ids", "scene_character_ids", "present_character_ids",
            "speaking_character_ids", "addressed_character_ids", "relationship_pair_ids", "scene_goal",
            "current_location_id", "current_location_text", "current_date", "current_day_phase",
            "past_trigger_character_ids", "load_past", "past_triggered",
        ]:
            if key in payload:
                current_state[key] = payload[key]
        current_state["updated_at"] = datetime.utcnow().isoformat()
        base.write_json("state/current_state.json", current_state, session_id=sid)

    return {
        "success": True,
        "session_id": sid,
        "runtime_version": RUNTIME_VERSION,
        "mode": "turn_ack_light",
        "player_input": player_input,
        "start_command_detected": start_command,
        "current_frame": _current_frame_ack(current_state),
        "next_action": "requestContextSlice",
    }


def openapi_actions() -> dict[str, Any]:
    object_any = {"type": "object", "properties": {}, "additionalProperties": True}
    return {
        "openapi": "3.1.0",
        "info": {
            "title": "Akira 1206 v3 Actions",
            "version": RUNTIME_VERSION,
            "description": "Action-safe API: start -> preflight -> context_request/context_slice -> apply_turn_result.",
        },
        "servers": [{"url": base.BASE_URL.rstrip("/")}],
        "paths": {
            "/health": {
                "get": {
                    "operationId": "health",
                    "summary": "Health check",
                    "responses": {"200": _response("OK")},
                }
            },
            "/api/v1/start": {
                "post": {
                    "operationId": "startSession",
                    "summary": "Start a fresh 1206 v3 session; returns only a light ack.",
                    "requestBody": {"required": False, "content": {"application/json": {"schema": object_any}}},
                    "responses": {"200": _response("Light session ack")},
                }
            },
            "/api/v1/sessions": {
                "post": {
                    "operationId": "createSession",
                    "summary": "Create or ensure a session; returns only a light ack.",
                    "requestBody": {"required": False, "content": {"application/json": {"schema": object_any}}},
                    "responses": {"200": _response("Light session ack")},
                }
            },
            "/api/v3/sessions/{session_id}/preflight": {
                "get": {
                    "operationId": "getPreflight",
                    "summary": "Get small current-state/calendar/recent-events preflight before planning a scene.",
                    "parameters": [_session_path_param()],
                    "responses": {"200": _response("Preflight slice")},
                }
            },
            "/api/v3/sessions/{session_id}/context-request": {
                "post": {
                    "operationId": "requestContextSlice",
                    "summary": "Ask Railway for only the semantic blocks needed for the next scene.",
                    "parameters": [_session_path_param()],
                    "requestBody": {"required": False, "content": {"application/json": {"schema": object_any}}},
                    "responses": {"200": _response("Context slice")},
                }
            },
            "/api/v3/sessions/{session_id}/context-more": {
                "post": {
                    "operationId": "requestMoreContext",
                    "summary": "Ask for one additional approved context block if the scene deepens.",
                    "parameters": [_session_path_param()],
                    "requestBody": {"required": False, "content": {"application/json": {"schema": object_any}}},
                    "responses": {"200": _response("Additional context slice")},
                }
            },
            "/api/v3/sessions/{session_id}/start-scene-text": {
                "get": {
                    "operationId": "getStartSceneText",
                    "summary": "Get the exact first-scene text only when preflight says exact_text_required.",
                    "parameters": [_session_path_param()],
                    "responses": {"200": _response("Exact start scene text")},
                }
            },
            "/api/v1/sessions/{session_id}/turn": {
                "post": {
                    "operationId": "processTurn",
                    "summary": "Store player input/current-state overrides; returns a light ack, not a full contract.",
                    "parameters": [_session_path_param()],
                    "requestBody": {"required": False, "content": {"application/json": {"schema": object_any}}},
                    "responses": {"200": _response("Light turn ack")},
                }
            },
            "/api/v1/sessions/{session_id}/apply-turn-result": {
                "post": {
                    "operationId": "applyTurnResult",
                    "summary": "Apply proposed_updates after a scene.",
                    "parameters": [_session_path_param()],
                    "requestBody": {"required": False, "content": {"application/json": {"schema": object_any}}},
                    "responses": {"200": _response("Apply result")},
                }
            },
        },
    }


@app.get("/openapi-actions.json", include_in_schema=False)
def openapi_actions_route() -> dict[str, Any]:
    return openapi_actions()


def custom_openapi() -> dict[str, Any]:
    return openapi_actions()


app.openapi = custom_openapi  # type: ignore[assignment]
