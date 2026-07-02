"""Production entrypoint for Akira 1206 v3 standalone API.

V3 is a standalone Railway app. It does not import the old 1206 v2 runtime
patch stack. It uses full character cards from characters/<id>/ plus dynamic
state/character_memory and state/relationship_pairs.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import Body

from app import compact as base
from app.compact import app

# Register v3 routes on the standalone app.
import app.v3_full_cards_scene_contract_runtime_patch as v3_scene_contract  # noqa: F401,E402
import app.v3_apply_turn_result_runtime_patch as v3_apply_turn_result  # noqa: F401,E402

RUNTIME_VERSION = base.APP_VERSION


def _object_schema(properties: dict | None = None, *, required: list[str] | None = None) -> dict:
    schema = {"type": "object", "properties": properties or {}, "additionalProperties": True}
    if required:
        schema["required"] = required
    return schema


def _array_string() -> dict:
    return {"type": "array", "items": {"type": "string"}}


def _ref(name: str) -> dict:
    return {"$ref": f"#/components/schemas/{name}"}


def _response(description: str, name: str) -> dict:
    return {"description": description, "content": {"application/json": {"schema": _ref(name)}}}


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


def _components() -> dict[str, Any]:
    return {
        "HealthResponse": _object_schema({
            "status": {"type": "string"},
            "app": {"type": "string"},
            "version": {"type": "string"},
            "public_base_url": {"type": "string"},
            "standalone_v3": {"type": "boolean"},
        }),
        "SessionResponse": _object_schema({
            "success": {"type": "boolean"},
            "session_id": {"type": "string"},
            "created_at": {"type": "string"},
            "current_state": _object_schema(),
            "scene_contract_response": _object_schema(),
            "start_scene_ready": {"type": "boolean"},
        }, required=["success", "session_id"]),
        "SceneContractResponse": _object_schema({
            "success": {"type": "boolean"},
            "session_id": {"type": "string"},
            "runtime_version": {"type": "string"},
            "mode": {"type": "string"},
            "active_character_ids": _array_string(),
            "scene_character_ids": _array_string(),
            "scene_contract": _object_schema(),
            "context_audit": _object_schema(),
        }, required=["success", "session_id", "scene_contract"]),
        "ContextAuditResponse": _object_schema({
            "success": {"type": "boolean"},
            "session_id": {"type": "string"},
            "runtime_version": {"type": "string"},
            "mode": {"type": "string"},
            "active_character_ids": _array_string(),
            "scene_character_ids": _array_string(),
            "short_character_summaries_used": {"type": "boolean"},
            "legacy_character_memory_path_used": {"type": "boolean"},
            "source_files_by_character": _object_schema(),
            "relationship_pair_files": _object_schema(),
            "contract_chars_estimate": {"type": "integer"},
            "instructions": _array_string(),
        }),
        "ProcessTurnResponse": _object_schema({
            "success": {"type": "boolean"},
            "session_id": {"type": "string"},
            "player_input": {"type": "string"},
            "current_state": _object_schema(),
            "scene_contract_response": _object_schema(),
            "start_command_detected": {"type": "boolean"},
        }),
        "ApplyTurnResultResponse": _object_schema({
            "status": {"type": "string"},
            "session_id": {"type": "string"},
            "changed_files": _array_string(),
            "visible_scene_text": {"type": "string"},
            "final_scene_text": {"type": "string"},
        }),
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
    }


@app.post("/api/v1/sessions", operation_id="createSession")
def create_session(body: dict[str, Any] | None = Body(default=None)) -> dict[str, Any]:
    payload = _payload(body)
    # For a new Custom GPT chat, omit session_id: the API creates a fresh session.
    raw_sid = payload.get("session_id")
    sid = base.ensure_session(raw_sid or base.new_session_id())
    current_state = base.read_json("state/current_state.json", session_id=sid, default=None)
    player_input = _startish_input(payload)
    start_command = base.is_start_command(player_input)
    reset = bool(payload.get("reset"))

    if reset or start_command or not isinstance(current_state, dict):
        current_state = base.initialize_start_session(sid, _merge_start_overrides(payload, player_input=player_input or "начнем"))
    scene_contract_response = v3_scene_contract.build_v3_scene_contract_response(sid, max_total_chars=30000, include_debug=False)
    return {
        "success": True,
        "session_id": sid,
        "created_at": datetime.utcnow().isoformat(),
        "current_state": current_state,
        "scene_contract_response": scene_contract_response,
        "start_scene_ready": bool(current_state.get("start_scene_exact_text_required")),
    }


@app.post("/api/v1/start", operation_id="startSession")
def start_session(body: dict[str, Any] | None = Body(default=None)) -> dict[str, Any]:
    """Convenience action for Custom GPT: new chat + `начнем` -> fresh start scene."""
    payload = _payload(body)
    payload.setdefault("reset", True)
    payload.setdefault("player_input", "начнем")
    return create_session(payload)


@app.post("/api/v1/sessions/{session_id}/turn", operation_id="processTurn")
def process_turn(session_id: str, body: dict[str, Any] | None = Body(default=None)) -> dict[str, Any]:
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

    contract_response = v3_scene_contract.build_v3_scene_contract_response(sid, max_total_chars=30000, include_debug=bool(payload.get("include_debug")))
    return {
        "success": True,
        "session_id": sid,
        "player_input": player_input,
        "current_state": current_state,
        "scene_contract_response": contract_response,
        "start_command_detected": start_command,
    }


@app.get("/openapi-actions.json", include_in_schema=False)
def openapi_actions() -> dict[str, Any]:
    session_request_schema = _object_schema({
        "session_id": {"type": "string", "description": "Optional. Omit in a new Custom GPT chat to create a fresh session."},
        "reset": {"type": "boolean"},
        "player_input": {"type": "string", "description": "Use `начнем` / `начнём` to initialize the canonical first scene."},
        "current_state": _object_schema(),
    })
    return {
        "openapi": "3.1.0",
        "info": {"title": "Akira 1206 v3 Actions", "version": RUNTIME_VERSION},
        "servers": [{"url": base.BASE_URL}],
        "components": {"schemas": _components()},
        "paths": {
            "/health": {
                "get": {
                    "operationId": "health",
                    "summary": "Check v3 API health and runtime version",
                    "responses": {"200": _response("API health status", "HealthResponse")},
                }
            },
            "/api/v1/start": {
                "post": {
                    "operationId": "startSession",
                    "summary": "Create a fresh v3 session and load the canonical first scene for `начнем`.",
                    "requestBody": {"required": False, "content": {"application/json": {"schema": session_request_schema}}},
                    "responses": {"200": _response("Started session", "SessionResponse")},
                }
            },
            "/api/v1/sessions": {
                "post": {
                    "operationId": "createSession",
                    "summary": "Create or initialize a v3 gameplay session. Omit session_id for a new chat.",
                    "requestBody": {"required": False, "content": {"application/json": {"schema": session_request_schema}}},
                    "responses": {"200": _response("Created session", "SessionResponse")},
                }
            },
            "/api/v1/sessions/{session_id}/turn": {
                "post": {
                    "operationId": "processTurn",
                    "summary": "Store player input/current frame hints and return a v3 scene contract. `начнем` resets to canonical start scene.",
                    "parameters": [_session_path_param()],
                    "requestBody": {"required": False, "content": {"application/json": {"schema": _object_schema({
                        "player_input": {"type": "string"},
                        "pov_character_id": {"type": "string"},
                        "active_character_ids": _array_string(),
                        "scene_character_ids": _array_string(),
                        "relationship_pair_ids": _array_string(),
                        "scene_goal": {"type": "string"},
                        "include_debug": {"type": "boolean"},
                    })}}},
                    "responses": {"200": _response("Stored turn and scene contract", "ProcessTurnResponse")},
                }
            },
            "/api/v2/sessions/{session_id}/scene-contract": {
                "get": {
                    "operationId": "getSceneContract",
                    "summary": "Get v3 full-card scene contract",
                    "parameters": [
                        _session_path_param(),
                        {"name": "max_total_chars", "in": "query", "required": False, "schema": {"type": "integer", "default": 18000, "minimum": 12000, "maximum": 30000}},
                        {"name": "include_debug", "in": "query", "required": False, "schema": {"type": "boolean", "default": False}},
                    ],
                    "responses": {"200": _response("Scene contract", "SceneContractResponse")},
                }
            },
            "/api/v2/sessions/{session_id}/debug/context-audit": {
                "get": {
                    "operationId": "getContextAudit",
                    "summary": "Audit which v3 files were loaded into the scene contract",
                    "parameters": [_session_path_param()],
                    "responses": {"200": _response("Context audit", "ContextAuditResponse")},
                }
            },
            "/api/v1/sessions/{session_id}/apply-turn-result": {
                "post": {
                    "operationId": "applyTurnResult",
                    "summary": "Apply validated proposed updates after ChatGPT writes a scene",
                    "parameters": [_session_path_param()],
                    "requestBody": {"required": False, "content": {"application/json": {"schema": _object_schema()}}},
                    "responses": {"200": _response("Apply result", "ApplyTurnResultResponse")},
                }
            },
        },
    }


# Keep Swagger in sync with Actions schema.
def custom_openapi() -> dict[str, Any]:
    return openapi_actions()


app.openapi = custom_openapi  # type: ignore[assignment]
