"""Quarantine-first repair for session integrity failures."""
from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

from app import compact as base
from app import session_recovery as recovery

VERSION = "0.10.0-v3-quarantine-repair"
QUARANTINE_SCHEMA = "session_quarantine_v1"
REPAIR_HISTORY_SCHEMA = "session_repair_history_v1"
QUARANTINE_DIR = "state/quarantine"
REPAIR_HISTORY_FILE = "state/repair_history.json"
MAX_QUARANTINES = 10

TURN_RUNTIME_FILE = base.TURN_RUNTIME_FILE
CONTEXT_SNAPSHOT_FILE = base.CONTEXT_SNAPSHOT_FILE
CURRENT_STATE_FILE = "state/current_state.json"

_EXCLUDED_AUDIT_FILES = {
    recovery.CHANGE_JOURNAL_FILE,
    recovery.RECOVERY_AUDIT_FILE,
    REPAIR_HISTORY_FILE,
}


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _stamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")


def _sha(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    ).hexdigest()


def _raw_sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _safe_text(value: Any, maximum: int = 500) -> str:
    return " ".join(str(value or "").split())[:maximum]


def _is_canonical(path: str) -> bool:
    normalized = str(path).lstrip("/")
    if normalized in _EXCLUDED_AUDIT_FILES:
        return False
    return recovery.is_canonical_state_path(normalized)


def _canonical_paths(session_id: str) -> list[str]:
    return recovery.canonical_state_paths(session_id)


def _file_image(session_id: str, path: str) -> dict[str, Any]:
    target = base._safe_session_target(session_id, path)
    if not target.is_file():
        return {"exists": False, "sha256": None, "data": None}
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ValueError(f"Unreadable session JSON: {path}") from exc
    return {"exists": True, "sha256": _sha(data), "data": data}


def _forensic_image(session_id: str, path: str) -> dict[str, Any]:
    target = base._safe_session_target(session_id, path)
    if not target.is_file():
        return {"exists": False, "json_valid": False, "sha256": None}
    raw = target.read_text(encoding="utf-8", errors="replace")
    try:
        data = json.loads(raw)
    except Exception:
        return {
            "exists": True,
            "json_valid": False,
            "sha256": _raw_sha(raw),
            "raw_text": raw,
        }
    return {
        "exists": True,
        "json_valid": True,
        "sha256": _sha(data),
        "data": data,
    }


def _forensic_state(session_id: str) -> dict[str, dict[str, Any]]:
    return {
        path: _forensic_image(session_id, path)
        for path in _canonical_paths(session_id)
    }


def _image_from_data(data: Any) -> dict[str, Any]:
    return {"exists": True, "sha256": _sha(data), "data": data}


def _overlay_images(
    before: dict[str, dict[str, Any]],
    writes: dict[str, Any],
    deletes: list[str],
) -> dict[str, dict[str, Any]]:
    after = {path: dict(image) for path, image in before.items()}
    for path, data in writes.items():
        normalized = str(path).lstrip("/")
        if _is_canonical(normalized):
            after[normalized] = _image_from_data(data)
    for path in deletes:
        normalized = str(path).lstrip("/")
        if _is_canonical(normalized):
            after.pop(normalized, None)
    return dict(sorted(after.items()))


def _snapshot_hash(snapshot: dict[str, Any]) -> str:
    return _sha({
        key: value
        for key, value in snapshot.items()
        if key != "snapshot_sha256"
    })


def _images_valid(images: Any) -> bool:
    if not isinstance(images, dict):
        return False
    for image in images.values():
        if not isinstance(image, dict):
            return False
        if image.get("exists"):
            if _sha(image.get("data")) != image.get("sha256"):
                return False
        elif image.get("sha256") is not None or image.get("data") is not None:
            return False
    return True


def _snapshot_validation(snapshot: Any) -> dict[str, Any]:
    if not isinstance(snapshot, dict):
        return {"valid": False, "exact": False, "errors": ["snapshot_not_object"]}
    schema = snapshot.get("schema")
    errors: list[str] = []
    if schema not in recovery.SUPPORTED_SNAPSHOT_SCHEMAS:
        errors.append("unsupported_schema")
    if not _images_valid(snapshot.get("before")):
        errors.append("invalid_before_images")
    exact = schema == recovery.SNAPSHOT_SCHEMA and isinstance(snapshot.get("after"), dict)
    if exact:
        if not _images_valid(snapshot.get("after")):
            errors.append("invalid_after_images")
        if snapshot.get("snapshot_sha256") != _snapshot_hash(snapshot):
            errors.append("snapshot_self_hash_mismatch")
    elif not isinstance(snapshot.get("after_sha256"), dict):
        errors.append("legacy_after_hashes_missing")
    return {"valid": not errors, "exact": exact, "errors": errors}


def _load_snapshot(session_id: str, path: str) -> dict[str, Any]:
    value = base.read_session_json(path, session_id, default={})
    return value if isinstance(value, dict) else {}


def _snapshot_paths(session_id: str) -> list[str]:
    directory = base._safe_session_target(session_id, recovery.SNAPSHOT_DIR)
    if not directory.is_dir():
        return []
    return sorted(
        (
            f"{recovery.SNAPSHOT_DIR}/{target.name}"
            for target in directory.glob("revision_*.json")
            if target.is_file()
        ),
        reverse=True,
    )


def _target_from_snapshot(
    path: str,
    snapshot: dict[str, Any],
    *,
    transition_kind: str | None,
    current_revision: int,
) -> dict[str, Any] | None:
    validation = _snapshot_validation(snapshot)
    if not validation["valid"]:
        return None
    schema = snapshot.get("schema")
    state_revision = int(snapshot.get("state_revision") or 0)
    base_revision = int(snapshot.get("base_revision") or 0)

    if transition_kind == "rollback":
        images = snapshot.get("before")
        target_revision = base_revision
        mode = "exact_rollback_before_image" if schema == recovery.SNAPSHOT_SCHEMA else "legacy_rollback_before_image"
        full_image = schema == recovery.SNAPSHOT_SCHEMA
    elif validation["exact"]:
        images = snapshot.get("after")
        target_revision = state_revision
        mode = "exact_after_snapshot"
        full_image = True
    else:
        images = snapshot.get("before")
        target_revision = base_revision
        mode = "legacy_before_snapshot"
        full_image = False

    if not isinstance(images, dict):
        return None
    return {
        "snapshot_file": path,
        "snapshot": snapshot,
        "images": images,
        "mode": mode,
        "full_image": full_image,
        "target_revision": target_revision,
        "requires_allow_turn_loss": target_revision < current_revision,
    }


def _preferred_snapshot(runtime: dict[str, Any]) -> tuple[str | None, str | None]:
    transition = (
        runtime.get("last_state_transition")
        if isinstance(runtime.get("last_state_transition"), dict)
        else {}
    )
    kind = str(transition.get("kind") or "") or None
    if kind == "repair":
        last_repair = (
            runtime.get("last_repair")
            if isinstance(runtime.get("last_repair"), dict)
            else {}
        )
        return str(last_repair.get("snapshot_file") or transition.get("snapshot_file") or "") or None, kind
    if kind == "rollback":
        last_rollback = (
            runtime.get("last_rollback")
            if isinstance(runtime.get("last_rollback"), dict)
            else {}
        )
        return str(last_rollback.get("snapshot_file") or "") or None, kind
    last_applied = (
        runtime.get("last_applied_turn")
        if isinstance(runtime.get("last_applied_turn"), dict)
        else {}
    )
    return str(last_applied.get("rollback_snapshot_file") or "") or None, kind or "apply"


def select_repair_candidate(
    session_id: str,
    *,
    runtime: dict[str, Any],
    current_revision: int,
) -> dict[str, Any] | None:
    preferred_path, transition_kind = _preferred_snapshot(runtime)
    if preferred_path:
        preferred = _load_snapshot(session_id, preferred_path)
        target = _target_from_snapshot(
            preferred_path,
            preferred,
            transition_kind=transition_kind,
            current_revision=current_revision,
        )
        if target:
            return target

    candidates: list[dict[str, Any]] = []
    for path in _snapshot_paths(session_id):
        snapshot = _load_snapshot(session_id, path)
        target = _target_from_snapshot(
            path,
            snapshot,
            transition_kind=None,
            current_revision=current_revision,
        )
        if not target:
            continue
        if int(target["target_revision"]) <= current_revision:
            candidates.append(target)
    candidates.sort(
        key=lambda item: (
            not item["full_image"],
            -int(item["target_revision"]),
        )
    )
    return candidates[0] if candidates else None


def _expected_hashes(target: dict[str, Any]) -> dict[str, Any]:
    return {
        path: image.get("sha256") if isinstance(image, dict) and image.get("exists") else None
        for path, image in target["images"].items()
        if _is_canonical(path)
    }


def _target_mismatches(
    session_id: str,
    target: dict[str, Any],
    *,
    ignore: set[str] | None = None,
) -> list[dict[str, Any]]:
    ignore = ignore or set()
    expected = _expected_hashes(target)
    current_paths = set(_canonical_paths(session_id))
    paths = set(expected)
    if target["full_image"]:
        paths |= current_paths
    mismatches: list[dict[str, Any]] = []
    for path in sorted(paths):
        if path in ignore:
            continue
        expected_hash = expected.get(path)
        try:
            image = _file_image(session_id, path)
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


def _append_unique_error(errors: list[dict[str, Any]], error: dict[str, Any]) -> None:
    code = error.get("code")
    if not any(item.get("code") == code for item in errors if isinstance(item, dict)):
        errors.append(error)


def integrity_report(session_id: str) -> dict[str, Any]:
    sid = base.ensure_session(session_id)
    with base.session_guard(sid):
        base_report = recovery.integrity_report(sid)
        runtime = base.read_turn_runtime(sid)
        current = base.read_session_json(CURRENT_STATE_FILE, sid, default=None)
        current_revision = max(
            int(runtime.get("state_revision") or 0),
            int(current.get("state_revision") or 0) if isinstance(current, dict) else 0,
        )
        errors = [
            dict(item)
            for item in base_report.get("errors", [])
            if isinstance(item, dict)
        ]
        warnings = [
            dict(item)
            for item in base_report.get("warnings", [])
            if isinstance(item, dict)
        ]
        pending = (
            runtime.get("pending_turn")
            if isinstance(runtime.get("pending_turn"), dict)
            else None
        )
        candidate = select_repair_candidate(
            sid,
            runtime=runtime,
            current_revision=current_revision,
        )
        if candidate:
            ignore = {TURN_RUNTIME_FILE, CONTEXT_SNAPSHOT_FILE} if pending else set()
            mismatches = _target_mismatches(sid, candidate, ignore=ignore)
            if mismatches:
                _append_unique_error(errors, {
                    "code": "canonical_hash_mismatch",
                    "files": mismatches,
                })

        repair_history = base.read_session_json(REPAIR_HISTORY_FILE, sid, default={})
        repair_entries = (
            repair_history.get("entries")
            if isinstance(repair_history, dict)
            and isinstance(repair_history.get("entries"), list)
            else []
        )
        quarantine_dir = base._safe_session_target(sid, QUARANTINE_DIR)
        quarantine_files = (
            sorted(
                f"{QUARANTINE_DIR}/{path.name}"
                for path in quarantine_dir.glob("quarantine_*.json")
            )
            if quarantine_dir.is_dir()
            else []
        )
        healthy = not errors
        result = dict(base_report)
        result.update({
            "success": healthy,
            "status": "healthy" if healthy else "integrity_errors",
            "runtime_version": VERSION,
            "state_revision": current_revision,
            "errors": errors,
            "warnings": warnings,
            "repair_available": bool(errors and candidate),
            "repair_plan": (
                {
                    "snapshot_file": candidate["snapshot_file"],
                    "mode": candidate["mode"],
                    "target_revision": candidate["target_revision"],
                    "requires_allow_turn_loss": candidate["requires_allow_turn_loss"],
                    "requires_discard_pending_turn": bool(pending),
                    "quarantine_required": True,
                }
                if errors and candidate
                else None
            ),
            "repair_events": len(repair_entries),
            "last_repair_event": repair_entries[-1] if repair_entries else None,
            "quarantine_files": len(quarantine_files),
            "latest_quarantine_file": quarantine_files[-1] if quarantine_files else None,
            "next_action": "waitForPlayerInput" if healthy else "repairSessionState",
        })
        return result


def _images_to_writes(images: dict[str, Any]) -> dict[str, Any]:
    return {
        path: image.get("data")
        for path, image in images.items()
        if _is_canonical(path)
        and isinstance(image, dict)
        and image.get("exists")
    }


def _restore_deletes(
    session_id: str,
    images: dict[str, Any],
    *,
    full_image: bool,
) -> list[str]:
    explicit_missing = {
        path
        for path, image in images.items()
        if _is_canonical(path)
        and isinstance(image, dict)
        and not image.get("exists")
    }
    if not full_image:
        return sorted(explicit_missing)
    expected = {
        path
        for path, image in images.items()
        if _is_canonical(path)
        and isinstance(image, dict)
        and image.get("exists")
    }
    return sorted((set(_canonical_paths(session_id)) - expected) | explicit_missing)


def _plan_repair_history(
    session_id: str,
    writes: dict[str, Any],
    entry: dict[str, Any],
) -> None:
    current = base.read_session_json(REPAIR_HISTORY_FILE, session_id, default={})
    if not isinstance(current, dict):
        current = {}
    entries = (
        current.get("entries")
        if isinstance(current.get("entries"), list)
        else []
    )
    entries = [item for item in entries if isinstance(item, dict)]
    entries.append(dict(entry))
    entries = entries[-200:]
    writes[REPAIR_HISTORY_FILE] = {
        "schema": REPAIR_HISTORY_SCHEMA,
        "entries": entries,
        "total_entries_retained": len(entries),
        "last_repair_at": entry.get("repaired_at"),
    }


def _quarantine_path(state_revision: int) -> str:
    return (
        f"{QUARANTINE_DIR}/quarantine_revision_"
        f"{int(state_revision):06d}_{_stamp()}.json"
    )


def _repair_snapshot_path(state_revision: int) -> str:
    return (
        f"{recovery.SNAPSHOT_DIR}/revision_"
        f"{int(state_revision):06d}_repair_{_stamp()}.json"
    )


def _repair_snapshot(
    *,
    path: str,
    base_revision: int,
    state_revision: int,
    before: dict[str, dict[str, Any]],
    after: dict[str, dict[str, Any]],
    source_snapshot_file: str,
    quarantine_file: str,
    reason: str,
) -> dict[str, Any]:
    snapshot = {
        "schema": recovery.SNAPSHOT_SCHEMA,
        "kind": "repair",
        "status": "repair_baseline",
        "snapshot_file": path,
        "base_revision": int(base_revision),
        "state_revision": int(state_revision),
        "created_at": _now(),
        "reason": reason,
        "source_snapshot_file": source_snapshot_file,
        "quarantine_file": quarantine_file,
        "changed_paths": sorted(set(before) | set(after)),
        "before": before,
        "after": after,
        "after_sha256": {
            item: image.get("sha256") if image.get("exists") else None
            for item, image in after.items()
        },
        "rollback_count": 0,
    }
    snapshot["snapshot_sha256"] = _snapshot_hash(snapshot)
    return snapshot


def _quarantine_prune_candidates(
    session_id: str,
    *,
    preserve: set[str],
) -> list[str]:
    directory = base._safe_session_target(session_id, QUARANTINE_DIR)
    if not directory.is_dir():
        return []
    files = sorted(
        (
            f"{QUARANTINE_DIR}/{path.name}"
            for path in directory.glob("quarantine_*.json")
            if path.is_file()
        )
    )
    removable = [path for path in files if path not in preserve]
    excess = max(0, len(removable) - (MAX_QUARANTINES - len(preserve)))
    return removable[:excess]


def repair_session_state(
    session_id: str,
    body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    sid = base.ensure_session(session_id)
    payload = body if isinstance(body, dict) else {}
    with base.session_guard(sid):
        report = integrity_report(sid)
        if report["status"] == "healthy":
            return {
                "success": False,
                "status": "repair_not_required",
                "session_id": sid,
                "state_revision": report["state_revision"],
                "error": "Session integrity is healthy; repair is blocked.",
                "next_action": "waitForPlayerInput",
            }
        if payload.get("confirm_repair") is not True:
            return {
                "success": False,
                "status": "repair_confirmation_required",
                "session_id": sid,
                "state_revision": report["state_revision"],
                "repair_plan": report.get("repair_plan"),
                "error": "Set confirm_repair=true after reviewing getSessionIntegrity.",
                "next_action": "repairSessionState",
            }
        expected = payload.get("expected_state_revision")
        if expected is None:
            return {
                "success": False,
                "status": "repair_expected_revision_required",
                "session_id": sid,
                "state_revision": report["state_revision"],
                "error": "expected_state_revision is required.",
                "next_action": "getSessionIntegrity",
            }
        try:
            expected_revision = int(expected)
        except Exception:
            expected_revision = -1
        if expected_revision != int(report["state_revision"]):
            return {
                "success": False,
                "status": "repair_rejected_stale_revision",
                "session_id": sid,
                "state_revision": report["state_revision"],
                "expected_state_revision": expected,
                "error": "State changed after the integrity report.",
                "next_action": "getSessionIntegrity",
            }

        runtime = base.read_turn_runtime(sid)
        pending = (
            runtime.get("pending_turn")
            if isinstance(runtime.get("pending_turn"), dict)
            else None
        )
        if pending and payload.get("discard_pending_turn") is not True:
            return {
                "success": False,
                "status": "repair_pending_turn_confirmation_required",
                "session_id": sid,
                "state_revision": report["state_revision"],
                "pending_turn_id": pending.get("turn_id"),
                "error": "Set discard_pending_turn=true explicitly.",
                "next_action": "repairSessionState",
            }

        candidate = select_repair_candidate(
            sid,
            runtime=runtime,
            current_revision=int(report["state_revision"]),
        )
        if not candidate:
            return {
                "success": False,
                "status": "repair_snapshot_unavailable",
                "session_id": sid,
                "state_revision": report["state_revision"],
                "error": "No valid trusted snapshot exists. Automatic reset is forbidden.",
                "next_action": "manualRecoveryRequired",
            }
        if (
            candidate["requires_allow_turn_loss"]
            and payload.get("allow_turn_loss") is not True
        ):
            return {
                "success": False,
                "status": "repair_turn_loss_confirmation_required",
                "session_id": sid,
                "state_revision": report["state_revision"],
                "repair_plan": report.get("repair_plan"),
                "error": "This repair loses one or more applied turns. Set allow_turn_loss=true explicitly.",
                "next_action": "repairSessionState",
            }

        if payload.get("dry_run") is True:
            return {
                "success": True,
                "status": "repair_validated_dry_run",
                "session_id": sid,
                "state_revision": report["state_revision"],
                "repair_mode": candidate["mode"],
                "source_snapshot_file": candidate["snapshot_file"],
                "target_revision": candidate["target_revision"],
                "would_quarantine": True,
                "would_discard_pending_turn_id": (
                    pending.get("turn_id") if pending else None
                ),
                "visible_scene_output_allowed": False,
                "next_action": "repairSessionState",
            }

        damaged_revision = int(report["state_revision"])
        repaired_revision = max(
            damaged_revision,
            int(candidate["snapshot"].get("state_revision") or 0),
            int(candidate["snapshot"].get("base_revision") or 0),
        ) + 1
        repaired_at = _now()
        reason = (
            _safe_text(payload.get("reason"))
            or "emergency repair after integrity failure"
        )
        quarantine_path = _quarantine_path(damaged_revision)
        forensic = _forensic_state(sid)
        quarantine = {
            "schema": QUARANTINE_SCHEMA,
            "quarantine_file": quarantine_path,
            "session_id": sid,
            "damaged_state_revision": damaged_revision,
            "created_at": repaired_at,
            "reason": reason,
            "integrity_errors": report.get("errors", []),
            "integrity_warnings": report.get("warnings", []),
            "pending_turn": pending,
            "selected_repair_mode": candidate["mode"],
            "selected_snapshot_file": candidate["snapshot_file"],
            "canonical_state_images": forensic,
        }
        quarantine["quarantine_sha256"] = _sha({
            key: value
            for key, value in quarantine.items()
            if key != "quarantine_sha256"
        })

        target_images = {
            path: image
            for path, image in candidate["images"].items()
            if _is_canonical(path)
        }
        writes = _images_to_writes(target_images)
        deletes = _restore_deletes(
            sid,
            target_images,
            full_image=bool(candidate["full_image"]),
        )

        target_runtime_image = target_images.get(TURN_RUNTIME_FILE)
        target_runtime = (
            target_runtime_image.get("data")
            if isinstance(target_runtime_image, dict)
            and target_runtime_image.get("exists")
            else {}
        )
        repaired_runtime = (
            dict(target_runtime)
            if isinstance(target_runtime, dict)
            else base.default_turn_runtime()
        )
        repaired_runtime["state_revision"] = repaired_revision
        repaired_runtime["pending_turn"] = None
        repaired_runtime["next_turn_number"] = max(
            int(runtime.get("next_turn_number") or 1),
            int(repaired_runtime.get("next_turn_number") or 1),
        )
        repair_snapshot_path = _repair_snapshot_path(repaired_revision)
        repaired_runtime["last_repair"] = {
            "damaged_state_revision": damaged_revision,
            "repaired_state_revision": repaired_revision,
            "restored_snapshot_revision": candidate["target_revision"],
            "mode": candidate["mode"],
            "source_snapshot_file": candidate["snapshot_file"],
            "snapshot_file": repair_snapshot_path,
            "quarantine_file": quarantine_path,
            "reason": reason,
            "repaired_at": repaired_at,
            "discarded_pending_turn_id": (
                pending.get("turn_id") if pending else None
            ),
        }
        repaired_runtime["last_state_transition"] = {
            "kind": "repair",
            "state_revision": repaired_revision,
            "created_at": repaired_at,
            "snapshot_file": repair_snapshot_path,
            "source_snapshot_file": candidate["snapshot_file"],
        }
        repaired_runtime["updated_at"] = repaired_at
        writes[TURN_RUNTIME_FILE] = repaired_runtime

        current = writes.get(CURRENT_STATE_FILE)
        if not isinstance(current, dict):
            return {
                "success": False,
                "status": "repair_snapshot_missing_current_state",
                "session_id": sid,
                "state_revision": damaged_revision,
                "error": "Trusted snapshot has no valid current_state.",
                "next_action": "manualRecoveryRequired",
            }
        current = dict(current)
        current["state_revision"] = repaired_revision
        current["last_repair_revision"] = repaired_revision
        current["updated_at"] = repaired_at
        writes[CURRENT_STATE_FILE] = current
        writes[CONTEXT_SNAPSHOT_FILE] = {
            "schema": "context_snapshot_v1",
            "status": "repaired",
            "runtime_version": VERSION,
            "state_revision": repaired_revision,
            "damaged_state_revision": damaged_revision,
            "source_snapshot_file": candidate["snapshot_file"],
            "quarantine_file": quarantine_path,
            "repaired_at": repaired_at,
            "all_required_chunks_served": False,
        }

        before_valid = {
            path: {
                "exists": bool(image.get("exists")),
                "sha256": image.get("sha256"),
                "data": image.get("data"),
            }
            for path, image in forensic.items()
            if image.get("json_valid")
        }
        after = _overlay_images(before_valid, writes, deletes)
        repair_snapshot = _repair_snapshot(
            path=repair_snapshot_path,
            base_revision=damaged_revision,
            state_revision=repaired_revision,
            before=before_valid,
            after=after,
            source_snapshot_file=candidate["snapshot_file"],
            quarantine_file=quarantine_path,
            reason=reason,
        )
        writes[repair_snapshot_path] = repair_snapshot
        writes[quarantine_path] = quarantine

        repair_entry = {
            "kind": "repair",
            "repaired_at": repaired_at,
            "damaged_state_revision": damaged_revision,
            "state_revision": repaired_revision,
            "restored_snapshot_revision": candidate["target_revision"],
            "mode": candidate["mode"],
            "source_snapshot_file": candidate["snapshot_file"],
            "snapshot_file": repair_snapshot_path,
            "quarantine_file": quarantine_path,
            "reason": reason,
            "discarded_pending_turn_id": (
                pending.get("turn_id") if pending else None
            ),
            "integrity_error_codes": [
                item.get("code")
                for item in report.get("errors", [])
                if isinstance(item, dict)
            ],
        }
        _plan_repair_history(sid, writes, repair_entry)
        recovery.plan_change_journal(sid, writes, repair_entry)

        prune_snapshots = recovery.snapshot_prune_candidates(
            sid,
            preserve={candidate["snapshot_file"], repair_snapshot_path},
        )
        prune_quarantines = _quarantine_prune_candidates(
            sid,
            preserve={quarantine_path},
        )
        delete_set = (
            set(deletes)
            | set(prune_snapshots)
            | set(prune_quarantines)
        ) - set(writes)
        base.commit_json_transaction(
            sid,
            f"repair_revision_{damaged_revision:06d}_to_{repaired_revision:06d}",
            writes,
            deletes=sorted(delete_set),
        )

        final_report = integrity_report(sid)
        healthy = final_report["status"] == "healthy"
        return {
            "success": healthy,
            "status": (
                "repaired"
                if healthy
                else "repair_committed_but_recheck_failed"
            ),
            "session_id": sid,
            "runtime_version": VERSION,
            "damaged_state_revision": damaged_revision,
            "state_revision": repaired_revision,
            "restored_snapshot_revision": candidate["target_revision"],
            "repair_mode": candidate["mode"],
            "source_snapshot_file": candidate["snapshot_file"],
            "repair_snapshot_file": repair_snapshot_path,
            "quarantine_file": quarantine_path,
            "discarded_pending_turn_id": (
                pending.get("turn_id") if pending else None
            ),
            "integrity_after_repair": {
                "status": final_report["status"],
                "errors": final_report["errors"],
                "warnings": final_report["warnings"],
            },
            "visible_scene_output_allowed": False,
            "next_action": (
                "waitForPlayerInput"
                if healthy
                else "getSessionIntegrity"
            ),
        }
