"""Atomic commit and compatibility backfill for the canonical opening scene."""
from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime
from typing import Any

from app import compact as base
from app import session_recovery

VERSION = "0.11.1-v3-pending-validation-diagnostics"
START_SCENE_FILE = "scenes/start_scene.md"
START_SCENE_ID = "start_scene"
START_TRANSITION_ID = "start_scene_opening"
CURRENT_STATE_FILE = "state/current_state.json"
SCENE_HISTORY_FILE = "state/scene_history.json"
LAST_APPLY_RESULT_FILE = "state/last_apply_result.json"


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _text_sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def exact_start_scene_text() -> str:
    raw = base.read_text(START_SCENE_FILE, default="") or ""
    fenced = re.search(r"```(?:text)?\s*\n(.*?)\n```", raw, flags=re.S | re.I)
    text = fenced.group(1) if fenced else raw
    return text.strip()


def exact_start_scene_sha256() -> str:
    return _text_sha256(exact_start_scene_text())


def _history_root(session_id: str, writes: dict[str, Any] | None = None) -> tuple[Any, list[Any]]:
    supplied = writes.get(SCENE_HISTORY_FILE) if isinstance(writes, dict) else None
    history = supplied if supplied is not None else base.read_session_json(SCENE_HISTORY_FILE, session_id, default=[])
    if isinstance(history, list):
        root: Any = list(history)
        return root, root
    if not isinstance(history, dict):
        history = {"schema": "scene_history_v3", "entries": []}
    root = dict(history)
    entries = root.get("entries") if isinstance(root.get("entries"), list) else []
    root["entries"] = list(entries)
    return root, root["entries"]


def _opening_exists(entries: list[Any]) -> bool:
    return any(
        isinstance(entry, dict)
        and (
            entry.get("kind") == "opening"
            or entry.get("turn_id") == START_TRANSITION_ID
            or entry.get("id") == f"scene_{START_TRANSITION_ID}"
        )
        for entry in entries
    )


def _opening_entry(
    current: dict[str, Any],
    *,
    state_revision: int,
    committed_at: str,
    commit_mode: str,
) -> dict[str, Any]:
    text = exact_start_scene_text()
    return {
        "id": f"scene_{START_TRANSITION_ID}",
        "turn_id": START_TRANSITION_ID,
        "turn_number": 0,
        "state_revision": int(state_revision),
        "kind": "opening",
        "scene_id": START_SCENE_ID,
        "source_file": START_SCENE_FILE,
        "commit_mode": commit_mode,
        "created_at": committed_at,
        "current_date": current.get("current_date") or current.get("date"),
        "current_time": current.get("current_time"),
        "location_id": current.get("current_location_id") or current.get("location_id"),
        "location_text": current.get("current_location_text") or current.get("location_text"),
        "pov_character_id": current.get("pov_character_id"),
        "active_characters": current.get("active_character_ids") or current.get("active_characters", []),
        "player_input": "начнем",
        "visible_scene_text": text,
        "exact_text_sha256": _text_sha256(text),
        "changed_files_snapshot": [CURRENT_STATE_FILE, SCENE_HISTORY_FILE],
    }


def plan_opening_history(
    session_id: str,
    writes: dict[str, Any],
    current: dict[str, Any],
    *,
    state_revision: int,
    committed_at: str,
    commit_mode: str,
) -> bool:
    root, entries = _history_root(session_id, writes)
    if _opening_exists(entries):
        return False
    entries.append(_opening_entry(
        current,
        state_revision=state_revision,
        committed_at=committed_at,
        commit_mode=commit_mode,
    ))
    if isinstance(root, dict):
        root["schema"] = root.get("schema") or "scene_history_v3"
        root["total_entries"] = len(entries)
        root["last_updated_at"] = committed_at
    writes[SCENE_HISTORY_FILE] = root
    return True


def plan_compatibility_backfill(
    session_id: str,
    writes: dict[str, Any],
    before_current: dict[str, Any],
    after_current: dict[str, Any],
    *,
    state_revision: int,
    committed_at: str,
) -> bool:
    """Backfill opening history for old clients that skipped commitStartScene.

    This does not create a separate revision. It is only a compatibility bridge;
    the public GPT flow must use commitStartScene before the first player turn.
    """
    transitioned = bool(
        after_current.get("start_scene_completed")
        and not before_current.get("start_scene_completed")
    )
    if not transitioned:
        return False
    return plan_opening_history(
        session_id,
        writes,
        after_current,
        state_revision=state_revision,
        committed_at=committed_at,
        commit_mode="compatibility_backfill_with_first_gameplay_apply",
    )


def _rejected(
    session_id: str,
    status: str,
    error: str,
    *,
    state_revision: int,
    next_action: str,
    **extra: Any,
) -> dict[str, Any]:
    return {
        "success": False,
        "status": status,
        "session_id": session_id,
        "runtime_version": VERSION,
        "state_revision": state_revision,
        "error": error,
        "visible_scene_output_allowed": False,
        "next_action": next_action,
        **extra,
    }


def commit_start_scene(
    session_id: str,
    body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    sid = base.ensure_session(session_id)
    payload = body if isinstance(body, dict) else {}
    with base.session_guard(sid):
        runtime = base.read_turn_runtime(sid)
        current_revision = int(runtime.get("state_revision") or 0)
        current = base.read_session_json(CURRENT_STATE_FILE, sid, default={})
        if not isinstance(current, dict):
            return _rejected(
                sid,
                "start_scene_commit_missing_state",
                "Canonical current_state is missing or invalid.",
                state_revision=current_revision,
                next_action="startSession",
            )

        previous = runtime.get("last_start_scene_commit")
        if (
            isinstance(previous, dict)
            and previous.get("exact_text_sha256") == exact_start_scene_sha256()
            and current.get("start_scene_completed")
        ):
            stored = previous.get("result")
            if isinstance(stored, dict):
                replay = dict(stored)
                replay["idempotent_replay"] = True
                replay["display_instruction"] = (
                    "The opening is already committed. Show visible_scene_text only if it was not already displayed to the player."
                )
                return replay

        pending = runtime.get("pending_turn")
        if isinstance(pending, dict) and pending.get("turn_id"):
            return _rejected(
                sid,
                "start_scene_commit_rejected_pending_turn",
                "A gameplay turn is already pending. The opening cannot be committed out of order.",
                state_revision=current_revision,
                next_action="getTurnContract",
                pending_turn_id=pending.get("turn_id"),
            )

        expected = payload.get("expected_state_revision")
        if expected is None:
            return _rejected(
                sid,
                "start_scene_expected_revision_required",
                "expected_state_revision is required from getStartSceneText.",
                state_revision=current_revision,
                next_action="getStartSceneText",
            )
        try:
            expected_revision = int(expected)
        except Exception:
            expected_revision = -1
        if expected_revision != current_revision:
            return _rejected(
                sid,
                "start_scene_commit_rejected_stale_revision",
                "State changed after getStartSceneText. Fetch the canonical opening again.",
                state_revision=current_revision,
                next_action="getStartSceneText",
                expected_state_revision=expected,
            )

        exact_text = exact_start_scene_text()
        exact_hash = _text_sha256(exact_text)
        if not exact_text:
            return _rejected(
                sid,
                "start_scene_text_missing",
                "The canonical start scene file is empty.",
                state_revision=current_revision,
                next_action="getSessionIntegrity",
            )
        supplied_hash = str(payload.get("exact_text_sha256") or "").strip()
        if supplied_hash != exact_hash:
            return _rejected(
                sid,
                "start_scene_hash_mismatch",
                "The opening text hash does not match the current repository canon.",
                state_revision=current_revision,
                next_action="getStartSceneText",
                expected_exact_text_sha256=exact_hash,
                supplied_exact_text_sha256=supplied_hash or None,
            )

        required = bool(
            current.get("start_scene_exact_text_required")
            and not current.get("start_scene_completed")
        )
        if not required:
            return _rejected(
                sid,
                "start_scene_commit_not_required",
                "The session is not waiting for the canonical opening scene.",
                state_revision=current_revision,
                next_action="getPreflight",
            )

        committed_at = _now()
        new_revision = current_revision + 1
        writes: dict[str, Any] = {}
        committed_current = dict(current)
        committed_current["start_scene_completed"] = True
        committed_current["start_scene_exact_text_required"] = False
        committed_current["start_scene_committed_revision"] = new_revision
        committed_current["start_scene_exact_text_sha256"] = exact_hash
        committed_current["state_revision"] = new_revision
        committed_current["updated_at"] = committed_at
        writes[CURRENT_STATE_FILE] = committed_current
        plan_opening_history(
            sid,
            writes,
            committed_current,
            state_revision=new_revision,
            committed_at=committed_at,
            commit_mode="dedicated_start_scene_commit",
        )

        rollback_snapshot_file = session_recovery.snapshot_file(
            new_revision,
            START_TRANSITION_ID,
        )
        result = {
            "success": True,
            "status": "start_scene_committed",
            "session_id": sid,
            "runtime_version": VERSION,
            "turn_id": START_TRANSITION_ID,
            "turn_number": 0,
            "base_revision": current_revision,
            "state_revision": new_revision,
            "exact_text_sha256": exact_hash,
            "visible_scene_text": exact_text,
            "final_scene_text": exact_text,
            "visible_scene_output_allowed": True,
            "display_instruction": (
                "Opening state is committed. Show visible_scene_text exactly once, then stop and wait for the player's first non-empty action."
            ),
            "rollback_available": True,
            "rollback_snapshot_file": rollback_snapshot_file,
            "next_action": "waitForPlayerInput",
        }

        final_runtime = dict(runtime)
        final_runtime["state_revision"] = new_revision
        final_runtime["pending_turn"] = None
        final_runtime["last_applied_turn"] = {
            "kind": "start_scene",
            "turn_id": START_TRANSITION_ID,
            "turn_number": 0,
            "base_revision": current_revision,
            "state_revision": new_revision,
            "payload_sha256": exact_hash,
            "applied_at": committed_at,
            "rollback_snapshot_file": rollback_snapshot_file,
            "result": result,
        }
        final_runtime["last_start_scene_commit"] = {
            "turn_id": START_TRANSITION_ID,
            "base_revision": current_revision,
            "state_revision": new_revision,
            "exact_text_sha256": exact_hash,
            "committed_at": committed_at,
            "rollback_snapshot_file": rollback_snapshot_file,
            "result": result,
        }
        final_runtime["last_state_transition"] = {
            "kind": "start_scene",
            "turn_id": START_TRANSITION_ID,
            "base_revision": current_revision,
            "state_revision": new_revision,
            "created_at": committed_at,
            "snapshot_file": rollback_snapshot_file,
        }
        final_runtime["updated_at"] = committed_at
        writes[base.TURN_RUNTIME_FILE] = final_runtime
        writes[base.CONTEXT_SNAPSHOT_FILE] = {
            "schema": "context_snapshot_v1",
            "status": "start_scene_committed",
            "runtime_version": VERSION,
            "turn_id": START_TRANSITION_ID,
            "base_revision": current_revision,
            "state_revision": new_revision,
            "exact_text_sha256": exact_hash,
            "committed_at": committed_at,
            "all_required_chunks_served": False,
        }
        writes[LAST_APPLY_RESULT_FILE] = {
            **result,
            "kind": "opening",
            "source_file": START_SCENE_FILE,
            "changed_files": sorted(writes),
            "audit_note": "Internal start-scene commit record. Render only visible_scene_text.",
        }
        session_recovery.plan_change_journal(sid, writes, {
            "kind": "start_scene",
            "turn_id": START_TRANSITION_ID,
            "turn_number": 0,
            "base_revision": current_revision,
            "state_revision": new_revision,
            "snapshot_file": rollback_snapshot_file,
            "reason": "committed canonical exact opening scene",
            "changed_files": sorted(writes),
            "scene_sha256": exact_hash,
        })

        tracked_paths = sorted(
            path
            for path in writes
            if path != session_recovery.CHANGE_JOURNAL_FILE
        )
        built_path, snapshot = session_recovery.build_apply_snapshot(
            sid,
            turn_id=START_TRANSITION_ID,
            turn_number=0,
            base_revision=current_revision,
            state_revision=new_revision,
            paths=tracked_paths,
            after_writes=writes,
            reason="committed canonical exact opening scene",
            kind="start_scene",
        )
        if built_path != rollback_snapshot_file:
            raise RuntimeError("Start scene rollback snapshot path changed during planning.")
        writes[rollback_snapshot_file] = snapshot
        pruned = session_recovery.snapshot_prune_candidates(
            sid,
            preserve={rollback_snapshot_file},
        )
        base.commit_json_transaction(
            sid,
            f"commit_{START_TRANSITION_ID}_{new_revision:06d}",
            writes,
            deletes=pruned,
        )
        return result
