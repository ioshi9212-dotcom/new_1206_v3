from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSION = "0.9.0-v3-session-recovery-rollback"


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def write(path: str, text: str) -> None:
    (ROOT / path).write_text(text, encoding="utf-8")


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one marker, found {count}")
    return text.replace(old, new, 1)


def replace_between(text: str, start: str, end: str, replacement: str, label: str) -> str:
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
    'APP_VERSION = "0.8.0-v3-scene-validation-rewrite-gate"',
    f'APP_VERSION = "{VERSION}"',
    "compact version",
)
compact = replace_once(
    compact,
    'TRANSACTIONS_DIR = "state/transactions"\n',
    'TRANSACTIONS_DIR = "state/transactions"\nRECOVERY_AUDIT_FILE = "state/recovery_audit.json"\n',
    "recovery audit constant",
)
transaction_code = '''def commit_json_transaction(
    session_id: str,
    transaction_id: str,
    writes: dict[str, Any],
    deletes: list[str] | None = None,
) -> None:
    """Durably roll JSON writes and deletions forward as one recoverable transaction."""
    sid = safe_session_id(session_id)
    safe_tid = safe_session_id(transaction_id)
    delete_paths = sorted({str(path).lstrip("/") for path in (deletes or []) if str(path).strip()})
    if not writes and not delete_paths:
        return
    with session_guard(sid):
        root = _session_root(sid)
        root.mkdir(parents=True, exist_ok=True)
        ordered = [
            {"path": str(path).lstrip("/"), "data": data}
            for path, data in sorted(writes.items(), key=lambda item: item[0])
        ]
        write_paths = {item["path"] for item in ordered}
        overlap = write_paths.intersection(delete_paths)
        if overlap:
            raise ValueError(f"Transaction cannot write and delete the same path: {sorted(overlap)}")
        for item in ordered:
            _safe_session_target(sid, item["path"])
        for path in delete_paths:
            _safe_session_target(sid, path)
        journal_path = _safe_session_target(sid, f"{TRANSACTIONS_DIR}/{safe_tid}.json")
        journal = {
            "schema": "json_transaction_v2",
            "transaction_id": safe_tid,
            "status": "prepared",
            "prepared_at": datetime.utcnow().isoformat(),
            "writes": ordered,
            "deletes": delete_paths,
        }
        _atomic_write_json_target(journal_path, journal)
        for item in ordered:
            _atomic_write_json_target(_safe_session_target(sid, item["path"]), item["data"])
        for path in delete_paths:
            _safe_session_target(sid, path).unlink(missing_ok=True)
        journal["status"] = "committed"
        journal["committed_at"] = datetime.utcnow().isoformat()
        _atomic_write_json_target(journal_path, journal)
        journal_path.unlink(missing_ok=True)


def _append_recovery_audit(session_id: str, entry: dict[str, Any]) -> None:
    target = _safe_session_target(session_id, RECOVERY_AUDIT_FILE)
    current: dict[str, Any] = {}
    try:
        if target.is_file():
            loaded = json.loads(target.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                current = loaded
    except Exception:
        current = {}
    entries = current.get("entries") if isinstance(current.get("entries"), list) else []
    entries = [item for item in entries if isinstance(item, dict)]
    entries.append(dict(entry))
    entries = entries[-300:]
    _atomic_write_json_target(target, {
        "schema": "transaction_recovery_audit_v1",
        "entries": entries,
        "total_entries_retained": len(entries),
        "last_recovered_at": entry.get("recovered_at"),
    })


def recover_json_transactions(session_id: str) -> list[str]:
    """Finish prepared multi-file writes/deletes left by an interrupted request."""
    sid = safe_session_id(session_id)
    recovered: list[str] = []
    with session_guard(sid):
        journal_dir = _safe_session_target(sid, TRANSACTIONS_DIR)
        if not journal_dir.is_dir():
            return recovered
        for journal_path in sorted(journal_dir.glob("*.json")):
            try:
                journal = json.loads(journal_path.read_text(encoding="utf-8"))
            except Exception:
                continue
            status = str(journal.get("status") or "") if isinstance(journal, dict) else ""
            writes = journal.get("writes") if isinstance(journal, dict) else None
            deletes = journal.get("deletes") if isinstance(journal, dict) else []
            if status == "committed":
                journal_path.unlink(missing_ok=True)
                continue
            if status != "prepared" or not isinstance(writes, list) or not isinstance(deletes, list):
                continue
            recovered_writes: list[str] = []
            recovered_deletes: list[str] = []
            for item in writes:
                if not isinstance(item, dict) or "path" not in item:
                    raise ValueError(f"Invalid transaction journal: {journal_path}")
                path = str(item["path"]).lstrip("/")
                _atomic_write_json_target(_safe_session_target(sid, path), item.get("data"))
                recovered_writes.append(path)
            for raw_path in deletes:
                path = str(raw_path).lstrip("/")
                _safe_session_target(sid, path).unlink(missing_ok=True)
                recovered_deletes.append(path)
            recovered_at = datetime.utcnow().isoformat()
            journal["status"] = "committed"
            journal["recovered_at"] = recovered_at
            _atomic_write_json_target(journal_path, journal)
            transaction_id = str(journal.get("transaction_id") or journal_path.stem)
            _append_recovery_audit(sid, {
                "transaction_id": transaction_id,
                "recovered_at": recovered_at,
                "prepared_at": journal.get("prepared_at"),
                "recovered_writes": recovered_writes,
                "recovered_deletes": recovered_deletes,
                "reason": "Prepared transaction was replayed automatically before serving the next request.",
            })
            recovered.append(transaction_id)
            journal_path.unlink(missing_ok=True)
    return recovered


'''
compact = replace_between(
    compact,
    "def commit_json_transaction(",
    "def default_turn_runtime()",
    transaction_code,
    "transaction storage block",
)
compact = replace_once(
    compact,
    '        "last_applied_turn": None,\n        "updated_at": datetime.utcnow().isoformat(),\n',
    '        "last_applied_turn": None,\n        "last_state_transition": None,\n        "last_rollback": None,\n        "updated_at": datetime.utcnow().isoformat(),\n',
    "default runtime recovery fields",
)
compact = replace_once(
    compact,
    '    runtime.setdefault("last_applied_turn", None)\n    return runtime\n',
    '    runtime.setdefault("last_applied_turn", None)\n    runtime.setdefault("last_state_transition", None)\n    runtime.setdefault("last_rollback", None)\n    return runtime\n',
    "runtime recovery defaults",
)
write("app/compact.py", compact)

scene_validation = read("app/scene_validation.py")
scene_validation = replace_once(
    scene_validation,
    'VERSION = "0.8.0-v3-scene-validation-rewrite-gate"',
    f'VERSION = "{VERSION}"',
    "scene validation version",
)
write("app/scene_validation.py", scene_validation)

writer = read("app/v3_apply_turn_result_runtime_patch.py")
writer = replace_once(
    writer,
    "from app import scene_validation\n",
    "from app import scene_validation\nfrom app import session_recovery\n",
    "writer recovery import",
)
final_apply_block = '''        applied_at = datetime.utcnow().isoformat()
        rollback_snapshot_file = session_recovery.snapshot_file(new_revision, turn_id)
        result = {
            "success": True,
            "status": "applied",
            "session_id": sid,
            "runtime_version": RUNTIME_VERSION,
            "turn_id": turn_id,
            "turn_number": pending.get("turn_number"),
            "base_revision": current_revision,
            "state_revision": new_revision,
            "context_snapshot_sha256": context_snapshot.get("context_snapshot_sha256"),
            "dry_run": False,
            "visible_scene_text": text,
            "final_scene_text": text,
            "visible_scene_output_allowed": True,
            "display_instruction": "State is committed. Show visible_scene_text now; do not expose proposed_updates or internal JSON.",
            "next_action": "waitForPlayerInput",
            "rollback_available": True,
            "rollback_snapshot_file": rollback_snapshot_file,
            "state_update_summary": {
                "character_memory_files": len(memory_changed),
                "relationship_pair_files": len(relationship_changed),
            },
            "world_update_summary": {
                "elapsed_world_minutes": int(time_autonomy_audit.get("elapsed_minutes") or 0),
                "npc_autonomy_updates": len(time_autonomy_audit.get("autonomy_updates") or []),
                "missed_event_consequences": len(time_autonomy_audit.get("missed_event_consequences") or []),
            },
        }
        result["scene_validation"] = {
            "passed": True,
            "protocol": scene_validation.PROTOCOL,
            "warnings": scene_gate["warnings"],
            "checks": scene_gate["checks"],
        }
        result["display_instruction"] = (
            "State and scene validation passed. Show visible_scene_text only; "
            "hide validation and internal JSON."
        )
        final_runtime = dict(runtime)
        final_runtime["state_revision"] = new_revision
        final_runtime["pending_turn"] = None
        final_runtime["last_applied_turn"] = {
            "turn_id": turn_id,
            "turn_number": pending.get("turn_number"),
            "base_revision": current_revision,
            "state_revision": new_revision,
            "payload_sha256": digest,
            "applied_at": applied_at,
            "rollback_snapshot_file": rollback_snapshot_file,
            "result": result,
        }
        final_runtime["last_state_transition"] = {
            "kind": "apply",
            "turn_id": turn_id,
            "base_revision": current_revision,
            "state_revision": new_revision,
            "created_at": applied_at,
            "snapshot_file": rollback_snapshot_file,
        }
        final_runtime["updated_at"] = applied_at
        writes[base.CONTEXT_SNAPSHOT_FILE] = {
            "schema": "context_snapshot_v1",
            "status": "applied",
            "runtime_version": RUNTIME_VERSION,
            "context_snapshot_id": context_snapshot.get("context_snapshot_id"),
            "context_snapshot_sha256": context_snapshot.get("context_snapshot_sha256"),
            "turn_id": turn_id,
            "turn_number": pending.get("turn_number"),
            "base_revision": current_revision,
            "state_revision": new_revision,
            "served_chunk_indices": context_snapshot.get("served_chunk_indices", []),
            "all_required_chunks_served": True,
            "built_at": context_snapshot.get("built_at"),
            "applied_at": applied_at,
        }
        writes[base.TURN_RUNTIME_FILE] = final_runtime
        changed.extend([
            base.CONTEXT_SNAPSHOT_FILE,
            base.TURN_RUNTIME_FILE,
            LAST_APPLY_RESULT_FILE,
            session_recovery.CHANGE_JOURNAL_FILE,
            rollback_snapshot_file,
        ])
        changed = list(dict.fromkeys(changed))
        audit_result = {
            **result,
            "changed_files": changed,
            "maintenance": maintenance,
            "character_state_audit": {
                "memory_events": memory_audit,
                "relationship_events": relationship_audit,
                "source_rule": "Facts require in-world evidence; calendar/prompt/runtime/hidden lore are blocked; writes are limited to snapshot-loaded characters and pairs.",
            },
            "time_and_autonomy_audit": time_autonomy_audit,
            "blocked_paths": ["characters/<id>/*.yaml", "legacy monolithic dynamic memory files"],
            "rollback_snapshot_file": rollback_snapshot_file,
            "audit_note": "Internal audit record. Never render this file or its metadata as scene output.",
        }
        writes[LAST_APPLY_RESULT_FILE] = audit_result
        change_reason = _find(payload, "change_reason", "apply_reason", "reason")
        session_recovery.plan_change_journal(sid, writes, {
            "kind": "apply",
            "turn_id": turn_id,
            "turn_number": pending.get("turn_number"),
            "base_revision": current_revision,
            "state_revision": new_revision,
            "snapshot_file": rollback_snapshot_file,
            "reason": str(change_reason or "applied gameplay turn")[:500],
            "changed_files": changed,
            "player_input_sha256": pending.get("player_input_sha256"),
            "scene_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        })
        tracked_paths = sorted(path for path in writes if path != session_recovery.CHANGE_JOURNAL_FILE)
        try:
            built_snapshot_path, rollback_snapshot = session_recovery.build_apply_snapshot(
                sid,
                turn_id=turn_id,
                turn_number=pending.get("turn_number"),
                base_revision=current_revision,
                state_revision=new_revision,
                paths=tracked_paths,
                after_writes=writes,
                reason=str(change_reason or "applied gameplay turn"),
            )
        except ValueError as exc:
            return _rejected(
                sid,
                str(exc),
                turn_id=turn_id,
                expected_turn_id=expected_turn_id,
                next_action="getSessionIntegrity",
                validation_errors=[{"code": "rollback_snapshot_capture_failed", "message": str(exc)}],
            )
        if built_snapshot_path != rollback_snapshot_file:
            raise RuntimeError("Rollback snapshot path changed during apply planning.")
        writes[rollback_snapshot_file] = rollback_snapshot
        pruned_snapshots = session_recovery.snapshot_prune_candidates(
            sid, preserve={rollback_snapshot_file}
        )

        # The prepared transaction now contains the canonical writes, the inverse
        # image needed for undo, and any safe retention deletions. Recovery replays
        # the same complete set after an interrupted process.
        base.commit_json_transaction(
            sid,
            f"apply_{turn_id}",
            writes,
            deletes=pruned_snapshots,
        )
        return result
'''
writer = replace_between(
    writer,
    "        applied_at = datetime.utcnow().isoformat()",
    "\n\n\ntry:\n",
    final_apply_block,
    "writer final apply block",
)
write("app/v3_apply_turn_result_runtime_patch.py", writer)

production = read("app/production_runtime_patch.py")
production = replace_once(
    production,
    "from app import compact as base\n",
    "from app import compact as base\nfrom app import session_recovery\n",
    "production recovery import",
)
production = replace_once(
    production,
    '        "state_storage": "atomic_json_with_recoverable_multi_file_journal",\n',
    '        "state_storage": "atomic_json_with_recoverable_multi_file_journal",\n'
    '        "revision_snapshot_protocol": "before_image_per_applied_turn_with_hashes",\n'
    '        "rollback_protocol": "last_applied_turn_inverse_transaction_monotonic_revision",\n'
    '        "automatic_recovery_audit": "prepared_write_and_delete_replay_with_persistent_log",\n',
    "health recovery protocols",
)
route_code = '''@app.get("/api/v1/sessions/{session_id}/integrity", operation_id="getSessionIntegrity")
def get_session_integrity(session_id: str) -> dict[str, Any]:
    """Recover any prepared transaction, then report canonical state consistency."""
    return session_recovery.integrity_report(session_id)


@app.post("/api/v1/sessions/{session_id}/rollback-last-turn", operation_id="rollbackLastTurn")
def rollback_last_turn(session_id: str, body: dict[str, Any] | None = Body(default=None)) -> dict[str, Any]:
    """Undo only the latest canonical apply through its captured inverse image."""
    return session_recovery.rollback_last_turn(session_id, body)


'''
production = replace_once(
    production,
    '@app.post("/api/v1/sessions", operation_id="createSession")\n',
    route_code + '@app.post("/api/v1/sessions", operation_id="createSession")\n',
    "recovery routes",
)
production = replace_once(
    production,
    '        "scene_validation": _object_schema({\n            "repair_attempt": {"type": "integer", "minimum": 0},\n            "speaker_character_ids": _array_string(),\n            "addressed_character_responses": object_any,\n        }),\n',
    '        "scene_validation": _object_schema({\n            "repair_attempt": {"type": "integer", "minimum": 0},\n            "speaker_character_ids": _array_string(),\n            "addressed_character_responses": object_any,\n        }),\n        "change_reason": {"type": "string", "description": "Optional short internal reason stored in the state change journal."},\n',
    "apply change reason schema",
)
production = replace_once(
    production,
    '    }, required=["turn_id", "visible_scene_text"])\n    return {\n',
    '    }, required=["turn_id", "visible_scene_text"])\n    rollback_body_schema = _object_schema({\n        "expected_state_revision": {"type": "integer", "description": "Optional optimistic concurrency guard from getSessionIntegrity."},\n        "reason": {"type": "string", "description": "Short audit reason for undoing the last applied turn."},\n    })\n    return {\n',
    "rollback openapi schema",
)
production = replace_once(
    production,
    '            "description": "Transactional API: processTurn creates turn_id; Railway freezes one snapshot with exact world time, NPC activity/location/availability/ETA, evidence-bounded character memory, relevant relationship pairs and player-control boundaries. The current POV keeps normal player-choice protection; a present non-POV Akira receives only low-stakes scene continuity. applyTurnResult validates the draft, requests a same-turn rewrite when needed, then atomically validates time, routes, events and state before scene text is shown.",\n',
    '            "description": "Transactional API: processTurn creates turn_id; Railway freezes one snapshot with exact world time, NPC activity/location/availability/ETA, evidence-bounded character memory, relevant relationship pairs and player-control boundaries. applyTurnResult validates and atomically commits the scene together with a rollback snapshot and change journal. getSessionIntegrity recovers interrupted transactions and verifies hashes; rollbackLastTurn restores the latest inverse image as a new monotonic revision.",\n',
    "openapi recovery description",
)
production = replace_once(
    production,
    '            "/api/v1/sessions/{session_id}/turn": {\n                "post": {"operationId": "processTurn", "summary": "Protect one player input and return its turn_id. Never overwrite a different pending turn.", "parameters": [_session_path_param()], "requestBody": {"required": True, "content": {"application/json": {"schema": process_turn_body_schema}}}, "responses": {"200": _response("Transactional turn ack")}}\n            },\n',
    '            "/api/v1/sessions/{session_id}/turn": {\n                "post": {"operationId": "processTurn", "summary": "Protect one player input and return its turn_id. Never overwrite a different pending turn.", "parameters": [_session_path_param()], "requestBody": {"required": True, "content": {"application/json": {"schema": process_turn_body_schema}}}, "responses": {"200": _response("Transactional turn ack")}}\n            },\n            "/api/v1/sessions/{session_id}/integrity": {\n                "get": {"operationId": "getSessionIntegrity", "summary": "Recover interrupted transactions, verify canonical hashes/revisions, and report rollback availability.", "parameters": [_session_path_param()], "responses": {"200": _response("Session integrity report")}}\n            },\n            "/api/v1/sessions/{session_id}/rollback-last-turn": {\n                "post": {"operationId": "rollbackLastTurn", "summary": "Undo the latest applied turn from its before-image as a new monotonic state revision.", "parameters": [_session_path_param()], "requestBody": {"required": False, "content": {"application/json": {"schema": rollback_body_schema}}}, "responses": {"200": _response("Rollback result")}}\n            },\n',
    "openapi recovery paths",
)
write("app/production_runtime_patch.py", production)

for relative in ("tools/install_session_recovery.py", ".github/workflows/install-session-recovery.yml"):
    path = ROOT / relative
    if path.exists():
        path.unlink()
