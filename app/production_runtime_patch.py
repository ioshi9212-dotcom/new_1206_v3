"""Production entrypoint for Akira 1206 v3 standalone API.

This file does not import the old 1206 v2 runtime patch stack. The v3 project is
standalone: it uses full character cards from characters/<id>/ plus dynamic
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


def _components() -> dict[str, Any]:
    return {
        "HealthResponse": _object_schema({
            "status": {"type": "string"},
            "app": {"type": "string"},
            "version": {"type": "string"},
            "public_base_url": {"type": "string"},
        }),
        "SessionResponse": _object_schema({
            "success": {"type": "boolean"},
            "session_id": {"type": "string"},
            "created_at": {"type": "string"},
            "current_state": _object_schema(),
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
    payload = body if isinstance(body, dict) else {}
    sid = base.ensure_session(payload.get("session_id") or "default")
    current_state = base.read_json("state/current_state.json", session_id=sid, default=None)
    reset = bool(payload.get("reset"))
    if reset or not isinstance(current_state, dict):
        overrides = payload.get("current_state") if isinstance(payload.get("current_state"), dict) else {}
        for key in [
            "current_scene_id", "current_date", "current_day_phase", "current_location_id",
            "current_location_text", "pov_character_id", "active_character_ids", "scene_character_ids",
            "scene_goal", "last_player_input",
        ]:
            if key in payload:
                overrides[key] = payload[key]
        current_state = base.default_current_state(sid, overrides)
        base.write_json("state/current_state.json", current_state, session_id=sid)
    return {"success": True, "session_id": sid, "created_at": datetime.utcnow().isoformat(), "current_state": current_state}


@app.post("/api/v1/sessions/{session_id}/turn", operation_id="processTurn")
def process_turn(session_id: str, body: dict[str, Any] | None = Body(default=None)) -> dict[str, Any]:
    payload = body if isinstance(body, dict) else {}
    sid = base.ensure_session(session_id)
    current_state = base.read_json("state/current_state.json", session_id=sid, default={})
    if not isinstance(current_state, dict):
        current_state = base.default_current_state(sid)
    player_input = str(payload.get("player_input") or payload.get("text") or payload.get("message") or "").strip()
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
    contract_response = v3_scene_contract.build_v3_scene_contract_response(sid, include_debug=bool(payload.get("include_debug")))
    return {
        "success": True,
        "session_id": sid,
        "player_input": player_input,
        "current_state": current_state,
        "scene_contract_response": contract_response,
    }


@app.get("/openapi-actions.json", include_in_schema=False)
def openapi_actions() -> dict[str, Any]:
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
            "/api/v1/sessions": {
                "post": {
                    "operationId": "createSession",
                    "summary": "Create or initialize a v3 gameplay session",
                    "requestBody": {"required": False, "content": {"application/json": {"schema": _object_schema({
                        "session_id": {"type": "string"},
                        "reset": {"type": "boolean"},
                        "current_state": _object_schema(),
                    })}}},
                    "responses": {"200": _response("Created session", "SessionResponse")},
                }
            },
            "/api/v1/sessions/{session_id}/turn": {
                "post": {
                    "operationId": "processTurn",
                    "summary": "Store player input/current frame hints and return a v3 scene contract",
                    "parameters": [_session_path_param()],
                    "requestBody": {"required": False, "content": {"application/json": {"schema": _object_schema({
                        "player_input": {"type": "string"},
                        "pov_character_id": {"type": "string"},
                        "active_character_ids": _array_string(),
                        "scene_character_ids": _array_string(),
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


try:
    app.version = RUNTIME_VERSION  # type: ignore[attr-defined]
except Exception:
    pass
