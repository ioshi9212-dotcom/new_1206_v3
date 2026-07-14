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
from app import session_recovery
from app import session_repair
from app import start_scene_commit
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
        "current_scene_id", "current_location_id",
        "current_location_text", "pov_character_id", "active_character_ids", "scene_character_ids",
        "relationship_pair_ids", "scene_goal", "last_player_input",
    ]:
        if key in payload:
            overrides[key] = payload[key]
    if player_input:
        overrides["last_player_input"] = player_input
    # A fresh 1206 start has one canonical clock. Resume/custom dates must be
    # reached through applied time_advance, otherwise current_state and calendar
    # would begin the same session on different days.
    for key in (
        "current_datetime", "current_date", "date", "current_time",
        "current_day_phase", "time_of_day", "elapsed_world_minutes", "time_revision",
    ):
        overrides.pop(key, None)
    return overrides


def _current_frame_ack(current: dict[str, Any]) -> dict[str, Any]:
    return {
        "current_scene_id": current.get("current_scene_id") or current.get("scene_id"),
        "current_datetime": current.get("current_datetime"),
        "current_date": current.get("current_date") or current.get("date"),
        "current_time": current.get("current_time"),
        "current_day_phase": current.get("current_day_phase") or current.get("time_of_day"),
        "elapsed_world_minutes": int(current.get("elapsed_world_minutes") or 0),
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
        "remote_contact_character_ids": [],
        "relationship_focus_pair_ids": [],
        "thinking_about_character_ids": [],
    }
    for key in [
        "pov_character_id", "active_character_ids", "scene_character_ids", "present_character_ids",
        "speaking_character_ids", "addressed_character_ids", "observing_character_ids",
        "remote_contact_character_ids", "contacted_character_ids",
        "relationship_pair_ids", "relationship_focus_pair_ids", "thinking_about_character_ids", "scene_goal",
        "current_scene_id", "current_location_id", "current_location_text",
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


def _begin_pending_turn(
    sid: str,
    player_input: str,
    state_patch: dict[str, Any],
    time_intent: dict[str, Any] | None = None,
) -> tuple[str, dict[str, Any], dict[str, Any]]:
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
            "time_intent": time_intent if isinstance(time_intent, dict) else {},
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
        "player_character_protocol": "current_pov_protected_nonpov_akira_low_stakes_micro_agency",
        "scene_validation_protocol": "precommit_scene_gate_frozen_snapshot_rewrite_v1",
        "automatic_scene_rewrite_attempts": 3,
        "world_time_protocol": "monotonic_evidence_backed_clock_and_current_day_only_calendar",
        "npc_autonomy_protocol": "offscreen_activity_location_availability_eta_and_missed_event_consequences",
        "state_storage": "atomic_json_with_recoverable_multi_file_journal",
        "revision_snapshot_protocol": "before_image_per_applied_turn_with_hashes",
        "rollback_protocol": "last_applied_turn_inverse_transaction_monotonic_revision",
        "automatic_recovery_audit": "prepared_write_and_delete_replay_with_persistent_log",
        "quarantine_repair_protocol": "explicit_confirm_revision_guard_full_forensic_quarantine",
        "trusted_snapshot_protocol": "full_before_and_after_state_images_with_self_hash",
        "start_scene_protocol": "canonical_hash_revision_guard_atomic_commit_before_visible_output",
        "large_contract_actions_disabled": True,
    }


@app.get("/api/v1/sessions/{session_id}/integrity", operation_id="getSessionIntegrity")
def get_session_integrity(session_id: str) -> dict[str, Any]:
    """Recover any prepared transaction, then report canonical state consistency."""
    return session_repair.integrity_report(session_id)


@app.post("/api/v1/sessions/{session_id}/rollback-last-turn", operation_id="rollbackLastTurn")
def rollback_last_turn(session_id: str, body: dict[str, Any] | None = Body(default=None)) -> dict[str, Any]:
    """Undo only the latest canonical apply through its captured inverse image."""
    return session_recovery.rollback_last_turn(session_id, body)


@app.post("/api/v1/sessions/{session_id}/repair-state", operation_id="repairSessionState")
def repair_session_state(
    session_id: str,
    body: dict[str, Any] | None = Body(default=None),
) -> dict[str, Any]:
    return session_repair.repair_session_state(session_id, body)


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


@app.post("/api/v1/sessions/{session_id}/commit-start-scene", operation_id="commitStartScene")
def commit_start_scene(
    session_id: str,
    body: dict[str, Any] | None = Body(default=None),
) -> dict[str, Any]:
    return start_scene_commit.commit_start_scene(session_id, body)


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
            "required_sequence": [
                "getPreflight",
                "getStartSceneText",
                "commitStartScene with state_revision and exact_text_sha256",
                "show commitStartScene.visible_scene_text exactly once",
                "waitForPlayerInput",
            ],
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
        payload.get("time_intent") if isinstance(payload.get("time_intent"), dict) else None,
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
    commit_start_scene_body_schema = _object_schema({
        "expected_state_revision": {"type": "integer", "description": "Exact state_revision returned by getStartSceneText."},
        "exact_text_sha256": {"type": "string", "description": "Canonical opening hash returned by getStartSceneText."},
    }, required=["expected_state_revision", "exact_text_sha256"])
    process_turn_body_schema = _object_schema({
        "player_input": {"type": "string", "description": "Exact latest player message/action/reply. Required for normal turns after the start scene."},
        "user_input": {"type": "string", "description": "Alias for player_input."},
        "text": {"type": "string", "description": "Alias for player_input."},
        "message": {"type": "string", "description": "Alias for player_input."},
        "current_location_id": {"type": "string"},
        "current_location_text": {"type": "string"},
        "current_scene_id": {"type": "string"},
        "time_intent": {
            "type": "object",
            "description": "Optional protected player intent for waiting/sleep/travel/timeskip. It never changes the clock by itself; applyTurnResult still needs validated elapsed_minutes and evidence.",
            "properties": {
                "mode": {"type": "string"},
                "explicit": {"type": "boolean"},
                "target_datetime": {"type": "string"},
                "requested_elapsed_minutes": {"type": "integer"},
            },
            "additionalProperties": False,
        },
        "pov_character_id": {"type": "string"},
        "active_character_ids": _array_string(),
        "scene_character_ids": _array_string(),
        "present_character_ids": _array_string(),
        "speaking_character_ids": _array_string(),
        "addressed_character_ids": _array_string(),
        "observing_character_ids": _array_string(),
        "remote_contact_character_ids": _array_string(),
        "contacted_character_ids": _array_string(),
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
        "remote_contact_character_ids": _array_string(),
        "contacted_character_ids": _array_string(),
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
        "proposed_updates": {"type": "object", "description": "Dynamic state only. Time/autonomy changes use time_advance, event_updates and npc_autonomy_updates; character facts require source_type/evidence; relationships are snapshot-scoped."},
        "current_state_patch": object_any,
        "current_state_changes": object_any,
        "current_state": object_any,
        "state_changes": object_any,
        "scene_continuity_patch": object_any,
        "time_advance": _object_schema({
            "elapsed_minutes": {"type": "integer"},
            "mode": {"type": "string", "description": "scene | travel | meal | training | rest | sleep | wait | timeskip"},
            "reason": {"type": "string"},
            "evidence": {"type": "string"},
            "target_datetime": {"type": "string"},
        }),
        "event_updates": {"type": "array", "items": object_any, "description": "Trigger/resolve/cancel a frozen calendar event with scene evidence. Missed deadlines are escalated by Railway."},
        "npc_autonomy_updates": {"type": "array", "items": object_any, "description": "Evidence-backed NPC activity/travel/arrival/delay updates. Akira's permitted non-POV micro-actions stay in scene prose and never use this block; NPC travel must respect ETA."},
        "physical_continuity_patch": object_any,
        "character_memory_updates": {"type": "object", "description": "Evidence-backed events for characters whose dynamic memory was loaded in the snapshot. Never personality/card rewrites."},
        "relationship_updates": object_any,
        "relationship_pair_updates": {"type": "object", "description": "Evidence-backed, bounded deltas only for relationship_pair_ids loaded in the snapshot."},
        "dry_run": {"type": "boolean"},
        "safety_checks": {"type": "object", "properties": {}, "additionalProperties": {"type": "boolean"}},
        "continuity_checks": {"type": "object", "properties": {}, "additionalProperties": {"type": "boolean"}},
        "scene_validation": _object_schema({
            "repair_attempt": {"type": "integer", "minimum": 0},
            "speaker_character_ids": _array_string(),
            "addressed_character_responses": object_any,
        }),
        "change_reason": {"type": "string", "description": "Optional short internal reason stored in the state change journal."},
    }, required=["turn_id", "visible_scene_text"])
    rollback_body_schema = _object_schema({
        "expected_state_revision": {"type": "integer", "description": "Optional optimistic concurrency guard from getSessionIntegrity."},
        "reason": {"type": "string", "description": "Short audit reason for undoing the last applied turn."},
    })
    repair_body_schema = _object_schema({
        "expected_state_revision": {"type": "integer", "description": "Required revision copied from getSessionIntegrity."},
        "confirm_repair": {"type": "boolean", "description": "Must be true; prevents silent repair."},
        "discard_pending_turn": {"type": "boolean", "description": "Explicitly allow repair to clear a pending player turn."},
        "allow_turn_loss": {"type": "boolean", "description": "Explicitly allow fallback to an older or legacy snapshot."},
        "dry_run": {"type": "boolean", "description": "Validate the repair plan without writing quarantine or state."},
        "reason": {"type": "string", "description": "Short audit reason stored with quarantine and repair history."},
    }, required=["expected_state_revision", "confirm_repair"])
    return {
        "openapi": "3.1.0",
        "info": {
            "title": "Akira 1206 v3 Actions",
            "version": RUNTIME_VERSION,
            "description": "Transactional API: getStartSceneText returns the canonical opening and hash; commitStartScene atomically records it before visible output. Normal turns then use one protected turn_id, frozen ordered context chunks, precommit validation and atomic apply. Integrity, rollback and quarantine repair remain revision-guarded.",
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
            "/api/v1/sessions/{session_id}/commit-start-scene": {
                "post": {"operationId": "commitStartScene", "summary": "Atomically commit the exact canonical opening before showing it to the player.", "parameters": [_session_path_param()], "requestBody": {"required": True, "content": {"application/json": {"schema": commit_start_scene_body_schema}}}, "responses": {"200": _response("Committed opening scene")}}
            },
            "/api/v1/sessions/{session_id}/turn": {
                "post": {"operationId": "processTurn", "summary": "Protect one player input and return its turn_id. Never overwrite a different pending turn.", "parameters": [_session_path_param()], "requestBody": {"required": True, "content": {"application/json": {"schema": process_turn_body_schema}}}, "responses": {"200": _response("Transactional turn ack")}}
            },
            "/api/v1/sessions/{session_id}/integrity": {
                "get": {"operationId": "getSessionIntegrity", "summary": "Recover interrupted transactions, verify canonical hashes/revisions, and report rollback availability.", "parameters": [_session_path_param()], "responses": {"200": _response("Session integrity report")}}
            },
            "/api/v1/sessions/{session_id}/rollback-last-turn": {
                "post": {"operationId": "rollbackLastTurn", "summary": "Undo the latest applied turn from its before-image as a new monotonic state revision.", "parameters": [_session_path_param()], "requestBody": {"required": False, "content": {"application/json": {"schema": rollback_body_schema}}}, "responses": {"200": _response("Rollback result")}}
            },
            "/api/v1/sessions/{session_id}/repair-state": {
                "post": {"operationId": "repairSessionState", "summary": "After getSessionIntegrity reports damage, quarantine the current JSON and restore a trusted snapshot with explicit revision/confirmation guards.", "parameters": [_session_path_param()], "requestBody": {"required": True, "content": {"application/json": {"schema": repair_body_schema}}}, "responses": {"200": _response("Quarantine repair result")}}
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
                "post": {"operationId": "applyTurnResult", "summary": "Validate and, if needed, rewrite this exact pending turn_id before atomic apply. Only a successful applied response authorizes showing visible_scene_text.", "parameters": [_session_path_param()], "requestBody": {"required": True, "content": {"application/json": {"schema": apply_body_schema}}}, "responses": {"200": _response("Validated atomic apply result")}}
            },
        },
    }


@app.get("/openapi-actions.json", include_in_schema=False)
def openapi_actions_route() -> dict[str, Any]:
    return openapi_actions()


def custom_openapi() -> dict[str, Any]:
    return openapi_actions()


app.openapi = custom_openapi  # type: ignore[assignment]
