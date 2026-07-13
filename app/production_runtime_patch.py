"""Production entrypoint for Akira 1206 v3 standalone API.

Transactional action-safe schema:
- processTurn protects one player input and returns its turn_id.
- getTurnContract lets Railway decide what this scene needs.
- getRequiredContextManifest returns the chunk plan.
- getRequiredContextChunk returns small, ordered context chunks until has_more=false.
- context cards keep facts, observations, reports and beliefs separate and load only relevant pairs.
- applyTurnResult validates evidence/snapshot scope and atomically commits that same turn_id before text is shown.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any

from fastapi import Body

from app import compact as base
from app.compact import app

# Register the transactional writer, then the one active context builder.
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
    for key in ("player_input", "user_input", "last_player_input", "command", "text", "message"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    current = payload.get("current_state")
    if isinstance(current, dict):
        for key in ("last_player_input", "user_input", "command", "text", "message"):
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
        "state_revision": int(current.get("state_revision") or 0),
    }


def _pending_state_patch(payload: dict[str, Any], current: dict[str, Any], player_input: str) -> dict[str, Any]:
    patch: dict[str, Any] = {
        "last_player_input": player_input,
        # Turn-local roles must never leak into the next scene merely because
        # the caller omitted them on a later request.
        "speaking_character_ids": [],
        "addressed_character_ids": [],
        "observing_character_ids": [],
        "relationship_focus_pair_ids": [],
        "thinking_about_character_ids": [],
    }
    for key in [
        "pov_character_id", "active_character_ids", "scene_character_ids", "present_character_ids",
        "speaking_character_ids", "addressed_character_ids", "observing_character_ids",
        "relationship_pair_ids", "relationship_focus_pair_ids", "thinking_about_character_ids", "scene_goal",
        "current_scene_id", "current_location_id", "current_location_text", "current_date", "current_day_phase",
        "past_trigger_character_ids", "load_past", "past_triggered",
    ]:
        if key in payload:
            patch[key] = payload[key]
    if current.get("start_scene_exact_text_required") and not current.get("start_scene_completed"):
        # The exact start scene was already shown before this first player reply.
        # Keep this change pending until the generated reply is successfully applied.
        patch["start_scene_completed"] = True
        patch["start_scene_exact_text_required"] = False
    return patch


def _begin_pending_turn(sid: str, player_input: str, state_patch: dict[str, Any]) -> tuple[str, dict[str, Any], dict[str, Any]]:
    """Create one protected pending turn, or return the existing idempotent one."""
    with base.session_guard(sid):
        runtime = base.read_turn_runtime(sid)
        pending = runtime.get("pending_turn")
        if isinstance(pending, dict) and pending.get("turn_id"):
            if str(pending.get("player_input") or "") == player_input:
                return "reused", pending, runtime
            return "conflict", pending, runtime

        turn_number = max(1, int(runtime.get("next_turn_number") or 1))
        created_at = datetime.utcnow().isoformat()
        fingerprint = hashlib.sha256(
            json.dumps(
                {"session_id": sid, "turn_number": turn_number, "player_input": player_input, "created_at": created_at},
                ensure_ascii=False,
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()[:10]
        pending = {
            "turn_id": f"turn_{turn_number:06d}_{fingerprint}",
            "turn_number": turn_number,
            "status": "pending",
            "base_revision": int(runtime.get("state_revision") or 0),
            "player_input": player_input,
            "player_input_sha256": hashlib.sha256(player_input.encode("utf-8")).hexdigest(),
            "current_state_patch": state_patch,
            "created_at": created_at,
        }
        runtime["pending_turn"] = pending
        runtime["next_turn_number"] = turn_number + 1
        runtime["updated_at"] = created_at
        base.write_json(base.TURN_RUNTIME_FILE, runtime, session_id=sid)
        return "created", pending, runtime


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
        "context_pipeline": "single_server_snapshot_bounded_character_groups_ordered_chunks",
        "knowledge_boundary": "evidence_buckets_visible_source_name_permission",
        "maintenance_runtime": "turn10_recovery_turn15_cleanup",
        "final_render_contract": "last_required_context_chunk",
        "turn_protocol": "pending_turn_turn_id_atomic_apply_v1",
        "context_snapshot_protocol": "one_immutable_snapshot_per_turn_with_chunk_progress",
        "character_state_protocol": "evidence_sourced_memory_and_snapshot_scoped_relationships",
        "state_storage": "atomic_json_with_recoverable_multi_file_journal",
        "large_contract_actions_disabled": True,
    }


@app.post("/api/v1/sessions", operation_id="createSession")
def create_session(body: dict[str, Any] | None = Body(default=None)) -> dict[str, Any]:
    """Create/ensure a session and return only a small ack for Custom GPT."""
    payload = _payload(body)
    raw_sid = payload.get("session_id")
    sid = base.ensure_session(raw_sid or base.new_session_id())
    current_state = base.read_session_json("state/current_state.json", session_id=sid, default=None)
    player_input = _startish_input(payload)
    start_command = base.is_start_command(player_input)
    reset = bool(payload.get("reset"))

    if reset or start_command or not isinstance(current_state, dict):
        current_state = base.initialize_start_session(
            sid,
            _merge_start_overrides(payload, player_input=player_input or "начнем"),
            reset_dynamic_state=bool(reset or start_command),
        )

    runtime = base.read_turn_runtime(sid)
    pending = runtime.get("pending_turn") if isinstance(runtime.get("pending_turn"), dict) else None
    effective = base.effective_current_state(sid)
    return {
        "success": True,
        "session_id": sid,
        "created_at": datetime.utcnow().isoformat(),
        "runtime_version": RUNTIME_VERSION,
        "mode": "session_ack_light",
        "current_frame": _current_frame_ack(effective or current_state),
        "state_revision": int(runtime.get("state_revision") or 0),
        "pending_turn_id": pending.get("turn_id") if pending else None,
        "next_action": "getPreflight",
        "note": "Session is ready. getPreflight decides between exact start scene, resuming a pending turn, or waiting for player input.",
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
    """Protect one player input as a pending transaction and return its id.

    The GPT should then call getTurnContract, getRequiredContextManifest and all
    getRequiredContextChunk calls, draft internally, and apply that same turn_id
    before showing the scene.
    """
    payload = _payload(body)
    sid = base.ensure_session(session_id)
    current_state = base.read_session_json("state/current_state.json", session_id=sid, default={})
    if not isinstance(current_state, dict):
        current_state = {}
    player_input = str(payload.get("player_input") or payload.get("user_input") or payload.get("text") or payload.get("message") or "").strip()
    start_command = base.is_start_command(player_input)

    if start_command:
        current_state = base.initialize_start_session(
            sid,
            _merge_start_overrides(payload, player_input=player_input),
            reset_dynamic_state=True,
        )
        return {
            "success": True,
            "session_id": sid,
            "runtime_version": RUNTIME_VERSION,
            "mode": "start_reset_ack",
            "turn_id": None,
            "state_revision": 0,
            "start_command_detected": True,
            "current_frame": _current_frame_ack(current_state),
            "next_action": "getPreflight",
            "required_sequence": ["getPreflight", "getStartSceneText", "show exact_text", "waitForPlayerInput"],
        }

    if not player_input:
        return {
            "success": False,
            "session_id": sid,
            "runtime_version": RUNTIME_VERSION,
            "mode": "turn_rejected_empty_input",
            "error": "player_input is empty; do not write a scene from stale context.",
            "current_frame": _current_frame_ack(current_state if isinstance(current_state, dict) else {}),
            "next_action": "waitForPlayerInput",
        }
    if not current_state:
        current_state = base.initialize_start_session(sid, _merge_start_overrides(payload, player_input="начнем"))

    pending_status, pending, runtime = _begin_pending_turn(
        sid,
        player_input,
        _pending_state_patch(payload, current_state, player_input),
    )
    effective = base.effective_current_state(sid)
    if pending_status == "conflict":
        return {
            "success": False,
            "session_id": sid,
            "runtime_version": RUNTIME_VERSION,
            "mode": "turn_rejected_pending_turn",
            "error": "A different player turn is still pending. Resume/apply it or reset the session; it was not overwritten.",
            "pending_turn_id": pending.get("turn_id"),
            "pending_player_input": pending.get("player_input"),
            "state_revision": int(runtime.get("state_revision") or 0),
            "current_frame": _current_frame_ack(effective),
            "next_action": "getTurnContract",
            "required_turn_id": pending.get("turn_id"),
        }

    return {
        "success": True,
        "session_id": sid,
        "runtime_version": RUNTIME_VERSION,
        "mode": "turn_ack_transactional",
        "turn_id": pending.get("turn_id"),
        "turn_number": pending.get("turn_number"),
        "state_revision": int(runtime.get("state_revision") or 0),
        "base_revision": pending.get("base_revision"),
        "pending_turn_reused": pending_status == "reused",
        "player_input": player_input,
        "start_command_detected": False,
        "current_frame": _current_frame_ack(effective),
        "next_action": "getTurnContract",
        "required_sequence": [
            "getTurnContract with this turn_id",
            "getRequiredContextManifest from the same server snapshot",
            "getRequiredContextChunk in numeric order until all_required_chunks_served=true",
            "draft scene internally",
            "applyTurnResult with this same turn_id",
            "show visible_scene_text only after apply status=applied",
        ],
    }


def openapi_actions() -> dict[str, Any]:
    object_any = {"type": "object", "properties": {}, "additionalProperties": True}

    start_body_schema = _object_schema({
        "player_input": {"type": "string", "description": "Start command, usually 'начнем'/'запускай'."},
        "user_input": {"type": "string", "description": "Alias for player_input."},
        "reset": {"type": "boolean", "description": "When true, reset/start a fresh session state."},
        "current_state": object_any,
    })
    create_session_body_schema = _object_schema({
        "session_id": {"type": "string", "description": "Optional preferred session id."},
        "player_input": {"type": "string", "description": "Optional start command or initial text."},
        "user_input": {"type": "string", "description": "Alias for player_input."},
        "reset": {"type": "boolean"},
        "current_state": object_any,
    })
    process_turn_body_schema = _object_schema({
        "player_input": {"type": "string", "description": "Exact latest player message/action/reply. Required for normal turns after the start scene."},
        "user_input": {"type": "string", "description": "Alias for player_input."},
        "text": {"type": "string", "description": "Alias for player_input."},
        "message": {"type": "string", "description": "Alias for player_input."},
        "current_location_id": {"type": "string"},
        "current_location_text": {"type": "string"},
        "current_scene_id": {"type": "string"},
        "current_date": {"type": "string"},
        "current_day_phase": {"type": "string"},
        "pov_character_id": {"type": "string"},
        "active_character_ids": _array_string(),
        "scene_character_ids": _array_string(),
        "present_character_ids": _array_string(),
        "speaking_character_ids": _array_string(),
        "addressed_character_ids": _array_string(),
        "observing_character_ids": _array_string(),
        "relationship_pair_ids": _array_string(),
        "relationship_focus_pair_ids": _array_string(),
        "thinking_about_character_ids": _array_string(),
        "scene_goal": {"type": "string"},
    }, required=["player_input"])
    scene_plan_schema = _object_schema({
        "scene_type": {"type": "string"},
        "player_input": {"type": "string"},
        "user_input": {"type": "string"},
        "location_depth": _array_string(),
        "speaking_characters": _array_string(),
        "addressed_characters": _array_string(),
        "present_characters": _array_string(),
        "observing_characters": _array_string(),
        "relationship_pair_ids": _array_string(),
        "relationship_focus_pair_ids": _array_string(),
        "thinking_about_character_ids": _array_string(),
        "required_blocks": object_any,
        "character_requests": object_any,
        "knowledge_boundary_required": {"type": "boolean"},
        "needs": object_any,
    })
    turn_contract_body_schema = _object_schema({
        "turn_id": {"type": "string", "description": "Exact turn_id returned by processTurn."},
        "player_input": {"type": "string", "description": "Exact latest player action/reply."},
        "user_input": {"type": "string", "description": "Alias for player_input."},
        "scene_plan": scene_plan_schema,
        "character_requests": object_any,
        "characters": object_any,
        "needs": object_any,
    }, required=["turn_id"])
    manifest_body_schema = _object_schema({
        "turn_id": {"type": "string", "description": "Same protected turn_id."},
        "player_input": {"type": "string"},
        "user_input": {"type": "string"},
        "scene_plan": scene_plan_schema,
        "turn_contract": object_any,
        "needs": object_any,
    }, required=["turn_id"])
    chunk_body_schema = _object_schema({
        "turn_id": {"type": "string", "description": "Same protected turn_id."},
        "chunk_index": {"type": "integer", "description": "Start with 0 and continue until has_more=false."},
        "player_input": {"type": "string"},
        "user_input": {"type": "string"},
        "scene_plan": scene_plan_schema,
        "turn_contract": object_any,
        "needs": object_any,
    }, required=["turn_id", "chunk_index"])
    context_request_body_schema = _object_schema({
        "turn_id": {"type": "string"},
        "player_input": {"type": "string", "description": "Legacy. Prefer getTurnContract + manifest/chunks."},
        "user_input": {"type": "string", "description": "Alias for player_input."},
        "scene_plan": scene_plan_schema,
        "character_requests": object_any,
        "characters": object_any,
        "needs": object_any,
    })
    context_more_body_schema = _object_schema({
        "turn_id": {"type": "string"},
        "chunk_index": {"type": "integer"},
        "player_input": {"type": "string"},
        "user_input": {"type": "string"},
        "scene_plan": scene_plan_schema,
        "turn_contract": object_any,
        "requested_blocks": object_any,
        "reason": {"type": "string"},
    })
    apply_body_schema = _object_schema({
        "turn_id": {"type": "string", "description": "Exact pending turn_id from processTurn. Nested scene_response.turn_id/metadata.turn_id are accepted only as recovery fallbacks."},
        "visible_scene_text": {"type": "string", "description": "Final scene text shown to the user."},
        "final_scene_text": {"type": "string", "description": "Alias/final scene text."},
        "scene_text": {"type": "string", "description": "Alias/final scene text."},
        "proposed_updates": {"type": "object", "description": "Dynamic state only. Character facts require source_type/evidence; relationship deltas are allowed only for pairs loaded in this turn snapshot."},
        "current_state_patch": object_any,
        "current_state_changes": object_any,
        "current_state": object_any,
        "state_changes": object_any,
        "scene_continuity_patch": object_any,
        "calendar_runtime_patch": object_any,
        "physical_continuity_patch": object_any,
        "character_memory_updates": {"type": "object", "description": "Evidence-backed events for characters whose dynamic memory was loaded in the snapshot. Never personality/card rewrites."},
        "relationship_updates": object_any,
        "relationship_pair_updates": {"type": "object", "description": "Evidence-backed, bounded deltas only for relationship_pair_ids loaded in the snapshot."},
        "dry_run": {"type": "boolean"},
    }, required=["turn_id", "visible_scene_text"])
    return {
        "openapi": "3.1.0",
        "info": {
            "title": "Akira 1206 v3 Actions",
            "version": RUNTIME_VERSION,
            "description": "Transactional API: processTurn creates turn_id; Railway freezes one context snapshot with evidence-bounded character memory and only relevant relationship pairs; ordered chunks and applyTurnResult use it; scene text is shown only after a successful atomic apply.",
        },
        "servers": [{"url": base.BASE_URL.rstrip("/")}],
        "paths": {
            "/health": {
                "get": {"operationId": "health", "summary": "Health check", "responses": {"200": _response("OK")}}
            },
            "/api/v1/start": {
                "post": {"operationId": "startSession", "summary": "Start a fresh 1206 v3 session; returns only a light ack.", "requestBody": {"required": False, "content": {"application/json": {"schema": start_body_schema}}}, "responses": {"200": _response("Light session ack")}}
            },
            "/api/v1/sessions": {
                "post": {"operationId": "createSession", "summary": "Create or ensure a session; returns only a light ack.", "requestBody": {"required": False, "content": {"application/json": {"schema": create_session_body_schema}}}, "responses": {"200": _response("Light session ack")}}
            },
            "/api/v1/sessions/{session_id}/turn": {
                "post": {"operationId": "processTurn", "summary": "Protect one player input and return its turn_id. Never overwrite a different pending turn.", "parameters": [_session_path_param()], "requestBody": {"required": True, "content": {"application/json": {"schema": process_turn_body_schema}}}, "responses": {"200": _response("Transactional turn ack")}}
            },
            "/api/v3/sessions/{session_id}/preflight": {
                "get": {"operationId": "getPreflight", "summary": "Get small current frame only; do not write scene from this.", "parameters": [_session_path_param()], "responses": {"200": _response("Preflight slice")}}
            },
            "/api/v3/sessions/{session_id}/turn-contract": {
                "post": {"operationId": "getTurnContract", "summary": "Build or resume the one immutable Railway context snapshot for this turn_id.", "parameters": [_session_path_param()], "requestBody": {"required": True, "content": {"application/json": {"schema": turn_contract_body_schema}}}, "responses": {"200": _response("Snapshot-backed turn contract")}}
            },
            "/api/v3/sessions/{session_id}/required-context/manifest": {
                "post": {"operationId": "getRequiredContextManifest", "summary": "Return the frozen snapshot manifest and the next unserved chunk index.", "parameters": [_session_path_param()], "requestBody": {"required": True, "content": {"application/json": {"schema": manifest_body_schema}}}, "responses": {"200": _response("Snapshot context manifest")}}
            },
            "/api/v3/sessions/{session_id}/required-context/chunk": {
                "post": {"operationId": "getRequiredContextChunk", "summary": "Load the next frozen chunk in numeric order. applyTurnResult stays blocked until every required chunk was served.", "parameters": [_session_path_param()], "requestBody": {"required": True, "content": {"application/json": {"schema": chunk_body_schema}}}, "responses": {"200": _response("Frozen required context chunk")}}
            },
            "/api/v3/sessions/{session_id}/context-request": {
                "post": {"operationId": "requestContextSlice", "summary": "Legacy compatibility endpoint. Prefer getTurnContract + manifest/chunks.", "parameters": [_session_path_param()], "requestBody": {"required": False, "content": {"application/json": {"schema": context_request_body_schema}}}, "responses": {"200": _response("Context manifest pointer")}}
            },
            "/api/v3/sessions/{session_id}/context-more": {
                "post": {"operationId": "requestMoreContext", "summary": "Legacy compatibility endpoint for one additional chunk.", "parameters": [_session_path_param()], "requestBody": {"required": False, "content": {"application/json": {"schema": context_more_body_schema}}}, "responses": {"200": _response("Additional context chunk")}}
            },
            "/api/v3/sessions/{session_id}/start-scene-text": {
                "get": {"operationId": "getStartSceneText", "summary": "Get the exact first-scene text only when preflight/chunk says exact_text_required.", "parameters": [_session_path_param()], "responses": {"200": _response("Exact start scene text")}}
            },
            "/api/v1/sessions/{session_id}/apply-turn-result": {
                "post": {"operationId": "applyTurnResult", "summary": "Atomically apply this exact pending turn_id. Only its successful response authorizes showing visible_scene_text.", "parameters": [_session_path_param()], "requestBody": {"required": True, "content": {"application/json": {"schema": apply_body_schema}}}, "responses": {"200": _response("Atomic apply result")}}
            },
        },
    }


@app.get("/openapi-actions.json", include_in_schema=False)
def openapi_actions_route() -> dict[str, Any]:
    return openapi_actions()


def custom_openapi() -> dict[str, Any]:
    return openapi_actions()


app.openapi = custom_openapi  # type: ignore[assignment]
