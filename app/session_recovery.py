"""Revision snapshots, rollback, integrity diagnostics and recovery audit helpers."""
from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app import compact as base

VERSION = "0.10.0-v3-quarantine-repair"
LEGACY_SNAPSHOT_SCHEMA = "turn_revision_snapshot_v1"
SNAPSHOT_SCHEMA = "turn_revision_snapshot_v2"
SUPPORTED_SNAPSHOT_SCHEMAS = {LEGACY_SNAPSHOT_SCHEMA, SNAPSHOT_SCHEMA}
CHANGE_JOURNAL_SCHEMA = "state_change_journal_v1"
CHANGE_JOURNAL_FILE = "state/change_journal.json"
RECOVERY_AUDIT_FILE = "state/recovery_audit.json"
SNAPSHOT_DIR = "state/revision_snapshots"
MAX_FULL_SNAPSHOTS = 20

TURN_RUNTIME_FILE = base.TURN_RUNTIME_FILE
CONTEXT_SNAPSHOT_FILE = base.CONTEXT_SNAPSHOT_FILE
CURRENT_STATE_FILE = "state/current_state.json"
LAST_APPLY_RESULT_FILE = "state/last_apply_result.json"
SCENE_HISTORY_FILE = "state/scene_history.json"


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _sha(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()


def _safe_text(value: Any, maximum: int = 500) -> str:
    return " ".join(str(value or "").split())[:maximum]


def is_canonical_state_path(path: str) -> bool:
    normalized = str(path).lstrip("/")
    if not normalized.startswith("state/") or not normalized.endswith(".json"):
        return False
    if normalized in {
        CHANGE_JOURNAL_FILE,
        RECOVERY_AUDIT_FILE,
        "state/repair_history.json",
    }:
        return False
    return not (
        normalized.startswith(f"{base.TRANSACTIONS_DIR}/")
        or normalized.startswith(f"{SNAPSHOT_DIR}/")
        or normalized.startswith("state/quarantine/")
    )


def canonical_state_paths(session_id: str) -> list[str]:
    root = base._safe_session_target(session_id, "state")
    session_root = base._safe_session_target(session_id, ".")
    if not root.is_dir():
        return []
    paths: list[str] = []
    for target in root.rglob("*.json"):
        relative = str(target.relative_to(session_root)).replace("\\", "/")
        if target.is_file() and is_canonical_state_path(relative):
            paths.append(relative)
    return sorted(set(paths))


def _full_state_images(session_id: str) -> dict[str, dict[str, Any]]:
    return {
        path: _session_file_image(session_id, path)
        for path in canonical_state_paths(session_id)
    }


def _image_from_data(data: Any) -> dict[str, Any]:
    return {"exists": True, "sha256": _sha(data), "data": data}


def _snapshot_payload_hash(snapshot: dict[str, Any]) -> str:
    return _sha({
        key: value
        for key, value in snapshot.items()
        if key != "snapshot_sha256"
    })


def snapshot_file(state_revision: int, turn_id: str) -> str:
    safe_turn = base.safe_session_id(turn_id)
    return f"{SNAPSHOT_DIR}/revision_{int(state_revision):06d}_{safe_turn}.json"


def _session_file_image(session_id: str, path: str) -> dict[str, Any]:
    target = base._safe_session_target(session_id, path)
    if not target.is_file():
        return {"exists": False, "sha256": None, "data": None}
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ValueError(f"Cannot create rollback snapshot: session JSON is unreadable at {path}") from exc
    return {"exists": True, "sha256": _sha(data), "data": data}


def build_apply_snapshot(
    session_id: str,
    *,
    turn_id: str,
    turn_number: Any,
    base_revision: int,
    state_revision: int,
    paths: list[str],
    after_writes: dict[str, Any],
    reason: str,
) -> tuple[str, dict[str, Any]]:
    changed_paths = sorted(
        {
            str(path).lstrip("/")
            for path in paths
            if is_canonical_state_path(str(path))
        }
        | {
            str(path).lstrip("/")
            for path in after_writes
            if is_canonical_state_path(str(path))
        }
    )
    path = snapshot_file(state_revision, turn_id)
    before = _full_state_images(session_id)
    for changed_path in changed_paths:
        before.setdefault(
            changed_path,
            {"exists": False, "sha256": None, "data": None},
        )
    after = {
        item: dict(image)
        for item, image in before.items()
        if image.get("exists")
    }
    for changed_path, data in after_writes.items():
        normalized = str(changed_path).lstrip("/")
        if is_canonical_state_path(normalized):
            after[normalized] = _image_from_data(data)
    snapshot = {
        "schema": SNAPSHOT_SCHEMA,
        "kind": "apply",
        "status": "active",
        "snapshot_file": path,
        "turn_id": turn_id,
        "turn_number": turn_number,
        "base_revision": int(base_revision),
        "state_revision": int(state_revision),
        "created_at": _now(),
        "reason": _safe_text(reason) or "applied gameplay turn",
        "changed_paths": changed_paths,
        "before": dict(sorted(before.items())),
        "after": dict(sorted(after.items())),
        "after_sha256": {
            item: image.get("sha256")
            for item, image in sorted(after.items())
        },
        "rollback_count": 0,
    }
    snapshot["snapshot_sha256"] = _snapshot_payload_hash(snapshot)
    return path, snapshot


def plan_change_journal(session_id: str, writes: dict[str, Any], entry: dict[str, Any]) -> None:
    current = writes.get(CHANGE_JOURNAL_FILE)
    if not isinstance(current, dict):
        current = base.read_session_json(CHANGE_JOURNAL_FILE, session_id, default={})
    if not isinstance(current, dict):
        current = {}
    entries = current.get("entries") if isinstance(current.get("entries"), list) else []
    entries = [item for item in entries if isinstance(item, dict)]
    normalized = dict(entry)
    normalized.setdefault("entry_id", f"change_{len(entries) + 1:06d}_{hashlib.sha256(_now().encode()).hexdigest()[:8]}")
    normalized.setdefault("created_at", _now())
    entries.append(normalized)
    entries = entries[-500:]
    writes[CHANGE_JOURNAL_FILE] = {
        "schema": CHANGE_JOURNAL_SCHEMA,
        "entries": entries,
        "total_entries_retained": len(entries),
        "last_entry_id": normalized["entry_id"],
        "last_updated_at": normalized["created_at"],
    }


def snapshot_prune_candidates(session_id: str, *, preserve: set[str] | None = None) -> list[str]:
    preserve = {str(item).lstrip("/") for item in (preserve or set())}
    directory = base._safe_session_target(session_id, SNAPSHOT_DIR)
    if not directory.is_dir():
        return []
    files = sorted(
        f"{SNAPSHOT_DIR}/{path.name}"
        for path in directory.glob("revision_*.json")
        if path.is_file()
    )
    removable = [path for path in files if path not in preserve]
    excess = max(0, len(removable) - (MAX_FULL_SNAPSHOTS - len(preserve)))
    return removable[:excess]


def _snapshot_hash_mismatches(
    session_id: str,
    snapshot: dict[str, Any],
    *,
    ignore: set[str] | None = None,
) -> list[dict[str, Any]]:
    ignore = ignore or set()
    if (
        snapshot.get("schema") == SNAPSHOT_SCHEMA
        and isinstance(snapshot.get("after"), dict)
    ):
        expected = {
            path: image.get("sha256")
            if isinstance(image, dict) and image.get("exists")
            else None
            for path, image in snapshot["after"].items()
        }
        all_paths = set(expected) | set(canonical_state_paths(session_id))
    else:
        hashes = snapshot.get("after_sha256")
        expected = dict(hashes) if isinstance(hashes, dict) else {}
        all_paths = set(expected)
    mismatches: list[dict[str, Any]] = []
    for path in sorted(all_paths):
        if path in ignore:
            continue
        expected_hash = expected.get(path)
        try:
            image = _session_file_image(session_id, path)
            actual_hash = image.get("sha256") if image.get("exists") else None
            unreadable = False
        except ValueError:
            actual_hash = None
            unreadable = True
        if actual_hash != expected_hash:
            mismatches.append({
                "path": path,
                "expected_sha256": expected_hash,
                "actual_sha256": actual_hash,
                "unreadable_json": unreadable,
            })
    return mismatches


def rollback_last_turn(session_id: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
    sid = base.ensure_session(session_id)
    payload = body if isinstance(body, dict) else {}
    with base.session_guard(sid):
        runtime = base.read_turn_runtime(sid)
        current_revision = int(runtime.get("state_revision") or 0)
        expected = payload.get("expected_state_revision")
        if expected is not None:
            try:
                expected_revision = int(expected)
            except Exception:
                expected_revision = -1
            if expected_revision != current_revision:
                return {
                    "success": False,
                    "status": "rollback_rejected_stale_revision",
                    "session_id": sid,
                    "state_revision": current_revision,
                    "expected_state_revision": expected,
                    "error": "State revision changed before rollback. Run getSessionIntegrity and retry with the current revision.",
                    "next_action": "getSessionIntegrity",
                }
        pending = runtime.get("pending_turn")
        if isinstance(pending, dict) and pending.get("turn_id"):
            return {
                "success": False,
                "status": "rollback_rejected_pending_turn",
                "session_id": sid,
                "state_revision": current_revision,
                "pending_turn_id": pending.get("turn_id"),
                "error": "A generated turn is still pending. Finish or explicitly reset that turn before rolling back canonical state.",
                "next_action": "applyTurnResult",
            }
        transition = runtime.get("last_state_transition")
        if isinstance(transition, dict) and transition.get("kind") == "rollback":
            return {
                "success": False,
                "status": "rollback_rejected_already_rolled_back",
                "session_id": sid,
                "state_revision": current_revision,
                "error": "The latest state transition is already a rollback; repeated undo is blocked.",
                "next_action": "waitForPlayerInput",
            }
        last_applied = runtime.get("last_applied_turn")
        if not isinstance(last_applied, dict) or not last_applied.get("turn_id"):
            return {
                "success": False,
                "status": "rollback_unavailable",
                "session_id": sid,
                "state_revision": current_revision,
                "error": "No rollback-capable applied turn exists in this session.",
                "next_action": "waitForPlayerInput",
            }
        snapshot_path = str(last_applied.get("rollback_snapshot_file") or "")
        snapshot = base.read_session_json(snapshot_path, sid, default={}) if snapshot_path else {}
        if not isinstance(snapshot, dict) or snapshot.get("schema") not in SUPPORTED_SNAPSHOT_SCHEMAS:
            return {
                "success": False,
                "status": "rollback_snapshot_missing",
                "session_id": sid,
                "state_revision": current_revision,
                "turn_id": last_applied.get("turn_id"),
                "error": "The last turn predates rollback snapshots or its snapshot is missing.",
                "next_action": "getSessionIntegrity",
            }
        if snapshot.get("status") != "active" or int(snapshot.get("state_revision") or -1) != current_revision:
            return {
                "success": False,
                "status": "rollback_snapshot_not_current",
                "session_id": sid,
                "state_revision": current_revision,
                "snapshot_status": snapshot.get("status"),
                "snapshot_state_revision": snapshot.get("state_revision"),
                "error": "Rollback snapshot is not the active image for the current revision.",
                "next_action": "getSessionIntegrity",
            }
        mismatches = _snapshot_hash_mismatches(sid, snapshot)
        if mismatches:
            return {
                "success": False,
                "status": "rollback_rejected_integrity_mismatch",
                "session_id": sid,
                "state_revision": current_revision,
                "turn_id": last_applied.get("turn_id"),
                "integrity_mismatches": mismatches,
                "error": "Canonical files changed outside the recorded apply transaction; automatic rollback was blocked.",
                "next_action": "getSessionIntegrity",
            }

        before = snapshot.get("before") if isinstance(snapshot.get("before"), dict) else {}
        writes: dict[str, Any] = {}
        deletes: list[str] = []
        for path, image in before.items():
            if path in {TURN_RUNTIME_FILE, CONTEXT_SNAPSHOT_FILE, CHANGE_JOURNAL_FILE}:
                continue
            if isinstance(image, dict) and image.get("exists"):
                writes[path] = image.get("data")
            else:
                deletes.append(path)

        rollback_revision = current_revision + 1
        before_runtime_image = before.get(TURN_RUNTIME_FILE) if isinstance(before.get(TURN_RUNTIME_FILE), dict) else {}
        before_runtime = before_runtime_image.get("data") if before_runtime_image.get("exists") else {}
        restored_runtime = dict(before_runtime) if isinstance(before_runtime, dict) else base.default_turn_runtime()
        restored_runtime["state_revision"] = rollback_revision
        restored_runtime["pending_turn"] = None
        restored_runtime["next_turn_number"] = max(
            int(runtime.get("next_turn_number") or 1), int(restored_runtime.get("next_turn_number") or 1)
        )
        rolled_back_at = _now()
        restored_runtime["last_rollback"] = {
            "rolled_back_turn_id": snapshot.get("turn_id"),
            "rolled_back_state_revision": current_revision,
            "restored_from_base_revision": int(snapshot.get("base_revision") or 0),
            "rollback_state_revision": rollback_revision,
            "reason": _safe_text(payload.get("reason")) or "user requested rollback of last applied turn",
            "rolled_back_at": rolled_back_at,
            "snapshot_file": snapshot_path,
        }
        restored_runtime["last_state_transition"] = {
            "kind": "rollback",
            "state_revision": rollback_revision,
            "turn_id": snapshot.get("turn_id"),
            "created_at": rolled_back_at,
        }
        restored_runtime["updated_at"] = rolled_back_at
        writes[TURN_RUNTIME_FILE] = restored_runtime

        current = writes.get(CURRENT_STATE_FILE)
        if isinstance(current, dict):
            current = dict(current)
            previous = restored_runtime.get("last_applied_turn")
            current["state_revision"] = rollback_revision
            current["last_applied_turn_id"] = previous.get("turn_id") if isinstance(previous, dict) else None
            current["last_rollback_turn_id"] = snapshot.get("turn_id")
            current["updated_at"] = rolled_back_at
            writes[CURRENT_STATE_FILE] = current

        writes[CONTEXT_SNAPSHOT_FILE] = {
            "schema": "context_snapshot_v1",
            "status": "rolled_back",
            "runtime_version": VERSION,
            "turn_id": snapshot.get("turn_id"),
            "rolled_back_state_revision": current_revision,
            "state_revision": rollback_revision,
            "rolled_back_at": rolled_back_at,
            "all_required_chunks_served": False,
        }
        snapshot = dict(snapshot)
        snapshot["status"] = "rolled_back"
        snapshot["rolled_back_at"] = rolled_back_at
        snapshot["rollback_state_revision"] = rollback_revision
        snapshot["rollback_count"] = int(snapshot.get("rollback_count") or 0) + 1
        if snapshot.get("schema") == SNAPSHOT_SCHEMA:
            snapshot["snapshot_sha256"] = _snapshot_payload_hash(snapshot)
        writes[snapshot_path] = snapshot

        plan_change_journal(sid, writes, {
            "kind": "rollback",
            "state_revision": rollback_revision,
            "rolled_back_turn_id": snapshot.get("turn_id"),
            "rolled_back_state_revision": current_revision,
            "restored_from_base_revision": snapshot.get("base_revision"),
            "snapshot_file": snapshot_path,
            "reason": restored_runtime["last_rollback"]["reason"],
            "restored_paths": sorted(writes),
            "deleted_paths": sorted(set(deletes)),
        })
        transaction_id = f"rollback_{base.safe_session_id(str(snapshot.get('turn_id') or 'turn'))}_{rollback_revision:06d}"
        base.commit_json_transaction(sid, transaction_id, writes, deletes=sorted(set(deletes)))
        return {
            "success": True,
            "status": "rolled_back",
            "session_id": sid,
            "runtime_version": VERSION,
            "rolled_back_turn_id": snapshot.get("turn_id"),
            "rolled_back_state_revision": current_revision,
            "state_revision": rollback_revision,
            "restored_from_base_revision": snapshot.get("base_revision"),
            "restored_paths": sorted(writes),
            "deleted_paths": sorted(set(deletes)),
            "pending_turn_id": None,
            "visible_scene_output_allowed": False,
            "next_action": "waitForPlayerInput",
        }


def integrity_report(session_id: str) -> dict[str, Any]:
    sid = base.ensure_session(session_id)
    with base.session_guard(sid):
        runtime = base.read_turn_runtime(sid)
        current = base.read_session_json(CURRENT_STATE_FILE, sid, default={})
        context = base.read_session_json(CONTEXT_SNAPSHOT_FILE, sid, default={})
        pending = runtime.get("pending_turn") if isinstance(runtime.get("pending_turn"), dict) else None
        revision = int(runtime.get("state_revision") or 0)
        errors: list[dict[str, Any]] = []
        warnings: list[dict[str, Any]] = []

        if not isinstance(current, dict):
            errors.append({"code": "current_state_missing", "message": "Canonical current_state is missing or invalid."})
            current = {}
        current_revision = int(current.get("state_revision") or 0)
        if current_revision != revision:
            errors.append({
                "code": "state_revision_mismatch",
                "runtime_revision": revision,
                "current_state_revision": current_revision,
            })
        if pending and int(pending.get("base_revision") or -1) != revision:
            errors.append({
                "code": "pending_base_revision_mismatch",
                "turn_id": pending.get("turn_id"),
                "pending_base_revision": pending.get("base_revision"),
                "state_revision": revision,
            })

        transaction_dir = base._safe_session_target(sid, base.TRANSACTIONS_DIR)
        remaining_transactions = sorted(path.name for path in transaction_dir.glob("*.json")) if transaction_dir.is_dir() else []
        if remaining_transactions:
            errors.append({"code": "unrecovered_transaction_journals", "files": remaining_transactions})

        transition = runtime.get("last_state_transition") if isinstance(runtime.get("last_state_transition"), dict) else {}
        last_applied = runtime.get("last_applied_turn") if isinstance(runtime.get("last_applied_turn"), dict) else {}
        snapshot_path = str(last_applied.get("rollback_snapshot_file") or "")
        snapshot = base.read_session_json(snapshot_path, sid, default={}) if snapshot_path else {}
        rollback_available = bool(
            not pending
            and transition.get("kind") == "apply"
            and isinstance(snapshot, dict)
            and snapshot.get("status") == "active"
            and int(snapshot.get("state_revision") or -1) == revision
        )
        if transition.get("kind") == "apply":
            if not isinstance(snapshot, dict) or snapshot.get("schema") not in SUPPORTED_SNAPSHOT_SCHEMAS:
                warnings.append({"code": "rollback_snapshot_missing", "turn_id": last_applied.get("turn_id")})
            elif snapshot.get("status") != "active":
                errors.append({"code": "active_apply_snapshot_not_active", "snapshot_status": snapshot.get("status")})
            else:
                ignore = {TURN_RUNTIME_FILE, CONTEXT_SNAPSHOT_FILE} if pending else set()
                mismatches = _snapshot_hash_mismatches(sid, snapshot, ignore=ignore)
                if mismatches:
                    errors.append({"code": "canonical_hash_mismatch", "files": mismatches})
        elif transition.get("kind") == "rollback":
            if isinstance(snapshot, dict) and snapshot and snapshot.get("status") != "rolled_back":
                warnings.append({"code": "rollback_snapshot_status_unexpected", "snapshot_status": snapshot.get("status")})

        if pending:
            if not isinstance(context, dict) or context.get("turn_id") != pending.get("turn_id"):
                warnings.append({
                    "code": "pending_context_not_built_or_not_current",
                    "pending_turn_id": pending.get("turn_id"),
                    "context_turn_id": context.get("turn_id") if isinstance(context, dict) else None,
                })

        recovery_audit = base.read_session_json(RECOVERY_AUDIT_FILE, sid, default={})
        recovery_entries = recovery_audit.get("entries") if isinstance(recovery_audit, dict) and isinstance(recovery_audit.get("entries"), list) else []
        change_journal = base.read_session_json(CHANGE_JOURNAL_FILE, sid, default={})
        change_entries = change_journal.get("entries") if isinstance(change_journal, dict) and isinstance(change_journal.get("entries"), list) else []
        return {
            "success": not errors,
            "status": "healthy" if not errors else "integrity_errors",
            "session_id": sid,
            "runtime_version": VERSION,
            "state_revision": revision,
            "current_state_revision": current_revision,
            "pending_turn_id": pending.get("turn_id") if pending else None,
            "last_transition": transition or None,
            "last_applied_turn_id": last_applied.get("turn_id"),
            "rollback_available": rollback_available,
            "rollback_snapshot_file": snapshot_path or None,
            "remaining_transaction_journals": remaining_transactions,
            "change_journal_entries": len(change_entries),
            "automatic_recovery_events": len(recovery_entries),
            "last_recovery_event": recovery_entries[-1] if recovery_entries else None,
            "errors": errors,
            "warnings": warnings,
            "next_action": "waitForPlayerInput" if not errors else "repairSessionState",
        }
