from __future__ import annotations

from typing import Any

import app.start_scene_runtime_patch as start_runtime
from app.start_scene_runtime_patch import app
import app.character_registry_runtime_patch as character_registry  # noqa: F401
import app.response_size_guard_runtime_patch as size_guard  # noqa: F401
import app.pov_switch_runtime_patch as pov_switch  # noqa: F401
import app.state_persistence_runtime_patch as state_persistence  # noqa: F401
import app.physical_continuity_runtime_patch as physical_continuity  # noqa: F401
import app.character_entry_runtime_patch as character_entry  # noqa: F401
import app.fast_context_runtime_patch as fast_context  # noqa: F401

try:
    import app.npc_living_runtime_patch as npc_living  # noqa: F401
except Exception:
    npc_living = None  # type: ignore[assignment]

try:
    import app.east_sector_context_runtime_patch as east_sector_context  # noqa: F401
except Exception:
    east_sector_context = None  # type: ignore[assignment]

try:
    import app.story_rules_context_runtime_patch as story_rules_context  # noqa: F401
except Exception:
    story_rules_context = None  # type: ignore[assignment]

try:
    import app.knowledge_state_runtime_patch as knowledge_state_runtime  # noqa: F401
except Exception:
    knowledge_state_runtime = None  # type: ignore[assignment]

try:
    import app.roster_identity_context_guard_runtime_patch as roster_identity_context_guard  # noqa: F401
except Exception:
    roster_identity_context_guard = None  # type: ignore[assignment]

try:
    import app.past_visibility_guard_runtime_patch as past_visibility_guard  # noqa: F401
except Exception:
    past_visibility_guard = None  # type: ignore[assignment]

try:
    import app.character_depth_context_runtime_patch as character_depth_context  # noqa: F401
except Exception:
    character_depth_context = None  # type: ignore[assignment]

try:
    import app.calendar_driven_character_entry_runtime_patch as calendar_driven_character_entry  # noqa: F401
except Exception:
    calendar_driven_character_entry = None  # type: ignore[assignment]

try:
    import app.essential_character_context_runtime_patch as essential_character_context  # noqa: F401
except Exception:
    essential_character_context = None  # type: ignore[assignment]

try:
    import app.compact_fast_context_runtime_patch as compact_fast_context  # noqa: F401
except Exception:
    compact_fast_context = None  # type: ignore[assignment]

try:
    import app.npc_knowledge_visibility_runtime_patch as npc_knowledge_visibility  # noqa: F401
except Exception:
    npc_knowledge_visibility = None  # type: ignore[assignment]

# Keep old route available internally, but override gameplay route with compact Academy-style scene contract.
try:
    import app.section_aware_turn_packet_runtime_patch as section_aware_turn_packet  # noqa: F401
except Exception:
    section_aware_turn_packet = None  # type: ignore[assignment]

try:
    import app.compact_scene_contract_runtime_patch as compact_scene_contract  # noqa: F401
except Exception:
    compact_scene_contract = None  # type: ignore[assignment]

# Final gameplay contract override: compact scene contract + memory-retention slice.
# This keeps Actions response size controlled while preserving recent session facts,
# dynamic NPC knowledge, relationships and scene continuity.
try:
    import app.scene_memory_contract_runtime_patch as scene_memory_contract  # noqa: F401
except Exception:
    scene_memory_contract = None  # type: ignore[assignment]

# Final v3 content-layer overrides. Keep these imports LAST: they replace the old
# runtime-summary scene contract and old state/character_knowledge apply logic.
try:
    import app.v3_full_cards_scene_contract_runtime_patch as v3_full_cards_scene_contract  # noqa: F401
except Exception:
    v3_full_cards_scene_contract = None  # type: ignore[assignment]

try:
    import app.v3_apply_turn_result_runtime_patch as v3_apply_turn_result  # noqa: F401
except Exception:
    v3_apply_turn_result = None  # type: ignore[assignment]

app.version = "0.3.180-v3-full-cards-builder-v1"


def _object_schema(properties: dict | None = None, *, required: list[str] | None = None) -> dict:
    schema = {"type": "object", "properties": properties or {}, "additionalProperties": True}
    if required:
        schema["required"] = required
    return schema


def _array_string() -> dict:
    return {"type": "array", "items": {"type": "string"}}


def _components() -> dict:
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
            "title": {"type": "string"},
            "created_at": {"type": "string"},
            "updated_at": {"type": "string"},
            "start_scene": _object_schema(),
        }, required=["session_id"]),
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
            "energy_loaded_by_character": _object_schema(),
            "runtime_sources": _object_schema(),
            "has_static_knowledge": _object_schema(),
            "contract_chars_estimate": {"type": "integer"},
            "instructions": _array_string(),
        }),
        "ProcessTurnResponse": _object_schema({
            "success": {"type": "boolean"},
            "session_id": {"type": "string"},
            "player_input": {"type": "string"},
            "current_scene_id": {"type": "string"},
            "status": {"type": "string"},
            "scene_text": {"type": "string"},
            "scene_packet": _object_schema(),
        }),
        "ApplyTurnResultResponse": _object_schema({
            "status": {"type": "string"},
            "session_id": {"type": "string"},
            "changed_files": _array_string(),
            "visible_scene_text": {"type": "string"},
            "final_scene_text": {"type": "string"},
        }),
    }


def _ref(name: str) -> dict:
    return {"$ref": f"#/components/schemas/{name}"}


def _response(description: str, name: str) -> dict:
    return {"description": description, "content": {"application/json": {"schema": _ref(name)}}}


def _session_path_param() -> dict:
    return {"name": "session_id", "in": "path", "required": True, "schema": {"type": "string"}}


def _scene_contract_params() -> list[dict]:
    return [
        {"name": "max_total_chars", "in": "query", "required": False, "schema": {"type": "integer", "default": 12000, "minimum": 9000, "maximum": 18000}},
        {"name": "include_debug", "in": "query", "required": False, "schema": {"type": "boolean", "default": False}},
    ]


def _audit_params() -> list[dict]:
    return [
        {"name": "max_total_chars", "in": "query", "required": False, "schema": {"type": "integer", "default": 12000, "minimum": 7000, "maximum": 18000}},
    ]


def _openapi() -> dict[str, Any]:
    return {
        "openapi": "3.1.0",
        "info": {"title": "Akira 1206 v2 Actions", "version": app.version},
        "servers": [{"url": start_runtime.base.BASE_URL}],
        "components": {"schemas": _components()},
        "paths": {
            "/health": {
                "get": {
                    "operationId": "health",
                    "summary": "Check API health and runtime version",
                    "responses": {"200": _response("API health status", "HealthResponse")},
                }
            },
            "/api/v1/sessions": {
                "post": {
                    "operationId": "createSession",
                    "summary": "Create or initialize a gameplay session",
                    "requestBody": {"required": False, "content": {"application/json": {"schema": _object_schema({"session_id": {"type": "string"}, "title": {"type": "string"}, "reset": {"type": "boolean"}})}}},
                    "responses": {"200": _response("Created session", "SessionResponse")},
                }
            },
            "/api/v1/sessions/{session_id}/turn": {
                "post": {
                    "operationId": "processTurn",
                    "summary": "Return exact first start_scene text for start command",
                    "parameters": [_session_path_param()],
                    "requestBody": {"required": True, "content": {"application/json": {"schema": _object_schema({"player_input": {"type": "string"}, "mode": {"type": "string", "default": "play"}, "include_file_contents": {"type": "boolean", "default": False}, "state_patches": _object_schema()}, required=["player_input"])}}},
                    "responses": {"200": _response("Processed turn", "ProcessTurnResponse")},
                }
            },
            "/api/v2/sessions/{session_id}/scene-contract": {
                "get": {
                    "operationId": "getSceneContract",
                    "summary": "Get memory-safe compact scene contract for gameplay",
                    "parameters": [_session_path_param()] + _scene_contract_params(),
                    "responses": {"200": _response("Scene contract", "SceneContractResponse")},
                }
            },
            "/api/v2/sessions/{session_id}/debug/context-audit": {
                "get": {
                    "operationId": "getContextAudit",
                    "summary": "Read-only audit for v3 scene contract: full-card sources, memory and energy availability",
                    "parameters": [_session_path_param()] + _audit_params(),
                    "responses": {"200": _response("Context audit", "ContextAuditResponse")},
                }
            },
            "/api/v1/sessions/{session_id}/apply-turn-result": {
                "post": {
                    "operationId": "applyTurnResult",
                    "summary": "Apply meaningful scene changes",
                    "parameters": [_session_path_param()],
                    "requestBody": {"required": False, "content": {"application/json": {"schema": _object_schema({"turn_file": {"type": "string"}, "data": _object_schema(), "dry_run": {"type": "boolean", "default": False}, "visible_scene_text": {"type": "string"}, "render_packet": _object_schema()})}}},
                    "responses": {"200": _response("Apply result", "ApplyTurnResultResponse")},
                }
            },
        },
    }


def _remove(path: str) -> None:
    for route in list(app.router.routes):
        if getattr(route, "path", None) == path:
            app.router.routes.remove(route)


_remove("/openapi-actions.json")


@app.get("/openapi-actions.json", include_in_schema=False)
def openapi_actions() -> dict[str, Any]:
    return _openapi()


app.openapi_schema = None
app.openapi = _openapi  # type: ignore[method-assign]
# Final v3 content-layer overrides. Keep these imports LAST: they replace the old
# runtime-summary scene contract and old state/character_knowledge apply logic.
try:
    import app.v3_full_cards_scene_contract_runtime_patch as v3_full_cards_scene_contract  # noqa: F401
except Exception:
    v3_full_cards_scene_contract = None  # type: ignore[assignment]

try:
    import app.v3_apply_turn_result_runtime_patch as v3_apply_turn_result  # noqa: F401
except Exception:
    v3_apply_turn_result = None  # type: ignore[assignment]

app.version = "0.3.180-v3-full-cards-builder-v1"
