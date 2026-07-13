from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSION = "0.10.0-v3-quarantine-repair"


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def write(path: str, text: str) -> None:
    (ROOT / path).write_text(text, encoding="utf-8")


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one marker, found {count}")
    return text.replace(old, new, 1)


def replace_between(
    text: str,
    start: str,
    end: str,
    replacement: str,
    label: str,
) -> str:
    left = text.find(start)
    if left < 0:
        raise RuntimeError(f"{label}: start marker not found")
    right = text.find(end, left)
    if right < 0:
        raise RuntimeError(f"{label}: end marker not found")
    return text[:left] + replacement + text[right:]


compact = read("app/compact.py")
compact = replace_once(
    compact,
    'APP_VERSION = "0.9.0-v3-session-recovery-rollback"',
    f'APP_VERSION = "{VERSION}"',
    "compact version",
)
compact = replace_once(
    compact,
    '        "last_rollback": None,\n        "updated_at": datetime.utcnow().isoformat(),\n',
    '        "last_rollback": None,\n        "last_repair": None,\n        "updated_at": datetime.utcnow().isoformat(),\n',
    "default last repair",
)
compact = replace_once(
    compact,
    '    runtime.setdefault("last_rollback", None)\n    return runtime\n',
    '    runtime.setdefault("last_rollback", None)\n    runtime.setdefault("last_repair", None)\n    return runtime\n',
    "runtime last repair default",
)
write("app/compact.py", compact)


scene_validation = read("app/scene_validation.py")
scene_validation = replace_once(
    scene_validation,
    'VERSION = "0.9.0-v3-session-recovery-rollback"',
    f'VERSION = "{VERSION}"',
    "scene validation version",
)
write("app/scene_validation.py", scene_validation)


recovery = read("app/session_recovery.py")
recovery = replace_once(
    recovery,
    'VERSION = "0.9.0-v3-session-recovery-rollback"',
    f'VERSION = "{VERSION}"',
    "recovery version",
)
recovery = replace_once(
    recovery,
    'SNAPSHOT_SCHEMA = "turn_revision_snapshot_v1"\n',
    'LEGACY_SNAPSHOT_SCHEMA = "turn_revision_snapshot_v1"\n'
    'SNAPSHOT_SCHEMA = "turn_revision_snapshot_v2"\n'
    'SUPPORTED_SNAPSHOT_SCHEMAS = {LEGACY_SNAPSHOT_SCHEMA, SNAPSHOT_SCHEMA}\n',
    "snapshot schemas",
)

helpers = """def is_canonical_state_path(path: str) -> bool:
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
        relative = str(target.relative_to(session_root)).replace("\\\\", "/")
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


"""
recovery = replace_once(
    recovery,
    'def snapshot_file(state_revision: int, turn_id: str) -> str:\n',
    helpers + 'def snapshot_file(state_revision: int, turn_id: str) -> str:\n',
    "recovery helpers",
)

build_snapshot = """def build_apply_snapshot(
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


"""
recovery = replace_between(
    recovery,
    "def build_apply_snapshot(",
    "def plan_change_journal(",
    build_snapshot,
    "build apply snapshot",
)

mismatch = """def _snapshot_hash_mismatches(
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


"""
recovery = replace_between(
    recovery,
    "def _snapshot_hash_mismatches(",
    "def rollback_last_turn(",
    mismatch,
    "snapshot mismatch checker",
)
recovery = recovery.replace(
    'snapshot.get("schema") != SNAPSHOT_SCHEMA',
    'snapshot.get("schema") not in SUPPORTED_SNAPSHOT_SCHEMAS',
)
recovery = replace_once(
    recovery,
    '        writes[snapshot_path] = snapshot\n\n        plan_change_journal(sid, writes, {\n',
    '        if snapshot.get("schema") == SNAPSHOT_SCHEMA:\n'
    '            snapshot["snapshot_sha256"] = _snapshot_payload_hash(snapshot)\n'
    '        writes[snapshot_path] = snapshot\n\n'
    '        plan_change_journal(sid, writes, {\n',
    "rollback snapshot self hash",
)
write("app/session_recovery.py", recovery)


repair = read("app/session_repair.py")
repair = replace_once(
    repair,
    '        "requires_allow_turn_loss": target_revision < current_revision,\n',
    '        "requires_allow_turn_loss": (\n'
    '            target_revision < current_revision\n'
    '            and transition_kind != "rollback"\n'
    '        ),\n',
    "rollback turn loss rule",
)
normalizer = """def _normalized_engine_hash(
    path: str,
    data: Any,
    *,
    mode: str | None,
) -> str:
    if mode != "rollback" or not isinstance(data, dict):
        return _sha(data)
    normalized = dict(data)
    if path == CURRENT_STATE_FILE:
        for key in (
            "state_revision",
            "last_applied_turn_id",
            "last_rollback_turn_id",
            "updated_at",
        ):
            normalized.pop(key, None)
    elif path == TURN_RUNTIME_FILE:
        for key in (
            "state_revision",
            "pending_turn",
            "next_turn_number",
            "last_rollback",
            "last_state_transition",
            "updated_at",
        ):
            normalized.pop(key, None)
    return _sha(normalized)


"""
repair = replace_once(
    repair,
    'def _target_mismatches(\n',
    normalizer + 'def _target_mismatches(\n',
    "rollback normalizer",
)
repair = replace_once(
    repair,
    '    ignore: set[str] | None = None,\n) -> list[dict[str, Any]]:\n',
    '    ignore: set[str] | None = None,\n'
    '    normalize_mode: str | None = None,\n'
    ') -> list[dict[str, Any]]:\n',
    "target mismatch signature",
)
old_body = """        expected_hash = expected.get(path)
        try:
            image = _file_image(session_id, path)
            actual_hash = image.get("sha256") if image.get("exists") else None
            unreadable = False
        except ValueError:
            actual_hash = None
            unreadable = True
        if actual_hash != expected_hash:
"""
new_body = """        expected_image = target["images"].get(path)
        expected_hash = expected.get(path)
        if (
            normalize_mode
            and isinstance(expected_image, dict)
            and expected_image.get("exists")
        ):
            expected_hash = _normalized_engine_hash(
                path,
                expected_image.get("data"),
                mode=normalize_mode,
            )
        try:
            image = _file_image(session_id, path)
            actual_hash = (
                _normalized_engine_hash(
                    path,
                    image.get("data"),
                    mode=normalize_mode,
                )
                if image.get("exists")
                else None
            )
            unreadable = False
        except ValueError:
            actual_hash = None
            unreadable = True
        if actual_hash != expected_hash:
"""
repair = replace_once(
    repair,
    old_body,
    new_body,
    "normalized target mismatch body",
)
repair = replace_once(
    repair,
    '            ignore = {TURN_RUNTIME_FILE, CONTEXT_SNAPSHOT_FILE} if pending else set()\n'
    '            mismatches = _target_mismatches(sid, candidate, ignore=ignore)\n',
    '            ignore = {TURN_RUNTIME_FILE, CONTEXT_SNAPSHOT_FILE} if pending else set()\n'
    '            normalize_mode = None\n'
    '            if "rollback_before_image" in candidate["mode"]:\n'
    '                ignore.add(CONTEXT_SNAPSHOT_FILE)\n'
    '                normalize_mode = "rollback"\n'
    '            mismatches = _target_mismatches(\n'
    '                sid,\n'
    '                candidate,\n'
    '                ignore=ignore,\n'
    '                normalize_mode=normalize_mode,\n'
    '            )\n',
    "integrity normalized mismatch call",
)
write("app/session_repair.py", repair)


production = read("app/production_runtime_patch.py")
production = replace_once(
    production,
    "from app import session_recovery\n",
    "from app import session_recovery\nfrom app import session_repair\n",
    "production repair import",
)
production = replace_once(
    production,
    '        "automatic_recovery_audit": "prepared_write_and_delete_replay_with_persistent_log",\n',
    '        "automatic_recovery_audit": "prepared_write_and_delete_replay_with_persistent_log",\n'
    '        "quarantine_repair_protocol": "explicit_confirm_revision_guard_full_forensic_quarantine",\n'
    '        "trusted_snapshot_protocol": "full_before_and_after_state_images_with_self_hash",\n',
    "health repair protocol",
)
production = replace_once(
    production,
    "    return session_recovery.integrity_report(session_id)\n",
    "    return session_repair.integrity_report(session_id)\n",
    "integrity route repair wrapper",
)
repair_route = """@app.post("/api/v1/sessions/{session_id}/repair-state", operation_id="repairSessionState")
def repair_session_state(
    session_id: str,
    body: dict[str, Any] | None = Body(default=None),
) -> dict[str, Any]:
    return session_repair.repair_session_state(session_id, body)


"""
production = replace_once(
    production,
    '@app.post("/api/v1/sessions", operation_id="createSession")\n',
    repair_route + '@app.post("/api/v1/sessions", operation_id="createSession")\n',
    "repair route",
)
production = replace_once(
    production,
    '    rollback_body_schema = _object_schema({\n'
    '        "expected_state_revision": {"type": "integer", "description": "Optional optimistic concurrency guard from getSessionIntegrity."},\n'
    '        "reason": {"type": "string", "description": "Short audit reason for undoing the last applied turn."},\n'
    '    })\n',
    '    rollback_body_schema = _object_schema({\n'
    '        "expected_state_revision": {"type": "integer", "description": "Optional optimistic concurrency guard from getSessionIntegrity."},\n'
    '        "reason": {"type": "string", "description": "Short audit reason for undoing the last applied turn."},\n'
    '    })\n'
    '    repair_body_schema = _object_schema({\n'
    '        "expected_state_revision": {"type": "integer", "description": "Required revision copied from getSessionIntegrity."},\n'
    '        "confirm_repair": {"type": "boolean", "description": "Must be true; prevents silent repair."},\n'
    '        "discard_pending_turn": {"type": "boolean", "description": "Explicitly allow repair to clear a pending player turn."},\n'
    '        "allow_turn_loss": {"type": "boolean", "description": "Explicitly allow fallback to an older or legacy snapshot."},\n'
    '        "dry_run": {"type": "boolean", "description": "Validate the repair plan without writing quarantine or state."},\n'
    '        "reason": {"type": "string", "description": "Short audit reason stored with quarantine and repair history."},\n'
    '    }, required=["expected_state_revision", "confirm_repair"])\n',
    "repair body schema",
)
production = replace_once(
    production,
    '            "description": "Transactional API: processTurn creates turn_id; Railway freezes one snapshot with exact world time, NPC activity/location/availability/ETA, evidence-bounded character memory, relevant relationship pairs and player-control boundaries. applyTurnResult validates and atomically commits the scene together with a rollback snapshot and change journal. getSessionIntegrity recovers interrupted transactions and verifies hashes; rollbackLastTurn restores the latest inverse image as a new monotonic revision.",\n',
    '            "description": "Transactional API: applyTurnResult commits the scene with full trusted before/after state images. getSessionIntegrity detects revision, hash and JSON damage. repairSessionState requires explicit confirmation and the exact revision, preserves the damaged state in quarantine, then restores a trusted snapshot under a new monotonic revision. Legacy fallback never loses a turn without allow_turn_loss=true.",\n',
    "openapi repair description",
)
production = replace_once(
    production,
    '            "/api/v1/sessions/{session_id}/rollback-last-turn": {\n'
    '                "post": {"operationId": "rollbackLastTurn", "summary": "Undo the latest applied turn from its before-image as a new monotonic state revision.", "parameters": [_session_path_param()], "requestBody": {"required": False, "content": {"application/json": {"schema": rollback_body_schema}}}, "responses": {"200": _response("Rollback result")}}\n'
    '            },\n',
    '            "/api/v1/sessions/{session_id}/rollback-last-turn": {\n'
    '                "post": {"operationId": "rollbackLastTurn", "summary": "Undo the latest applied turn from its before-image as a new monotonic state revision.", "parameters": [_session_path_param()], "requestBody": {"required": False, "content": {"application/json": {"schema": rollback_body_schema}}}, "responses": {"200": _response("Rollback result")}}\n'
    '            },\n'
    '            "/api/v1/sessions/{session_id}/repair-state": {\n'
    '                "post": {"operationId": "repairSessionState", "summary": "After getSessionIntegrity reports damage, quarantine the current JSON and restore a trusted snapshot with explicit revision/confirmation guards.", "parameters": [_session_path_param()], "requestBody": {"required": True, "content": {"application/json": {"schema": repair_body_schema}}}, "responses": {"200": _response("Quarantine repair result")}}\n'
    '            },\n',
    "openapi repair path",
)
write("app/production_runtime_patch.py", production)


for test_path in (
    "tests/test_scene_validation_runtime.py",
    "tests/test_session_recovery.py",
):
    text = read(test_path)
    text = text.replace(
        '"0.9.0-v3-session-recovery-rollback"',
        f'"{VERSION}"',
    )
    text = text.replace(
        '"turn_revision_snapshot_v1"',
        '"turn_revision_snapshot_v2"',
        1,
    )
    write(test_path, text)


for relative in (
    "tools/install_quarantine_repair.py",
    ".github/workflows/install-quarantine-repair.yml",
):
    path = ROOT / relative
    if path.exists():
        path.unlink()
