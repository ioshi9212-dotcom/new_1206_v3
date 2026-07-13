"""Transactional v3 apply writer for all dynamic session state.

One protected ``turn_id`` is validated, planned in memory, journaled, and then
committed across current state, history, memory, relationships and maintenance.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any

from fastapi import Body

from app import compact as base

app = base.app
RUNTIME_VERSION = base.APP_VERSION

APPLY_PATH = "/api/v1/sessions/{session_id}/apply-turn-result"
LAST_APPLY_RESULT_FILE = "state/last_apply_result.json"
SCENE_HISTORY_FILE = "state/scene_history.json"
CURRENT_STATE_FILE = "state/current_state.json"
SCENE_CONTINUITY_FILE = "state/scene_continuity_state.json"
CALENDAR_RUNTIME_FILE = "state/calendar_runtime.json"
PHYSICAL_CONTINUITY_FILE = "state/physical_continuity_state.json"
STORY_LINES_FILE = "state/story_lines.json"
MAINTENANCE_RULES_FILE = "state/maintenance_rules_1206.json"

ID_ALIASES = {
    "Акира": "akira", "акира": "akira", "akira": "akira",
    "Алекс": "alex", "алекс": "alex", "alex": "alex",
    "Эмма": "emma", "эмма": "emma", "emma": "emma",
    "Ирэй": "irey", "ирэй": "irey", "ирей": "irey", "irey": "irey",
    "Джун": "jun", "джун": "jun", "jun": "jun", "jun_carter": "jun",
    "Кай": "kai", "кай": "kai", "kai": "kai",
    "Мики": "miki", "мики": "miki", "miki": "miki",
    "Райден": "raiden", "рейден": "raiden", "рейдон": "raiden", "raiden": "raiden", "raiden_sterling": "raiden",
    "Рэй": "ray", "рэй": "ray", "рей": "ray", "ray": "ray", "ray_carter": "ray",
    "Хару": "haru", "хару": "haru", "haru": "haru", "haru_foster": "haru",
    "Широ": "shiro", "широ": "shiro", "shiro": "shiro",
    "Юна": "yuna", "юна": "yuna", "yuna": "yuna",
}


def _remove_route(path: str, method: str | None = None) -> None:
    method_upper = method.upper() if method else None
    for route in list(app.router.routes):
        if getattr(route, "path", None) != path:
            continue
        methods = set(getattr(route, "methods", set()) or set())
        if method_upper is None or method_upper in methods:
            app.router.routes.remove(route)


def _safe_session_id(session_id: str) -> str:
    try:
        return base.safe_session_id(session_id)
    except Exception:
        cleaned = "".join(ch for ch in str(session_id or "") if ch.isalnum() or ch in "-_")
        return cleaned or "default"


def _cid(value: Any) -> str:
    raw = str(value or "").strip()
    return ID_ALIASES.get(raw, ID_ALIASES.get(raw.lower(), raw.lower()))


def _pair_id(value: Any) -> str:
    if isinstance(value, list) and len(value) == 2:
        left, right = _cid(value[0]), _cid(value[1])
    else:
        parts = [part for part in str(value or "").split("__") if part]
        if len(parts) != 2:
            return ""
        left, right = _cid(parts[0]), _cid(parts[1])
    return f"{left}__{right}" if left and right else ""


def _read_json(path: str, sid: str, default: Any) -> Any:
    try:
        value = base.read_json(path, session_id=sid, default=default)
        return default if value is None else value
    except Exception:
        return default


def _story_lines_state(sid: str) -> dict[str, Any]:
    state = _read_json(STORY_LINES_FILE, sid, {})
    if not isinstance(state, dict) or not state:
        state = {
            "schema": "story_lines_runtime_v3",
            "turn_counter": 0,
            "last_state_recovery_audit_turn": 0,
            "last_compaction_cleanup_turn": 0,
            "maintenance": {
                "state_recovery_audit_every": 10,
                "compaction_cleanup_every": 15,
                "compaction_cleanup_offset": 12
            }
        }
    return state


def _plan_turn_counter(sid: str, writes: dict[str, Any]) -> dict[str, Any]:
    state = writes.get(STORY_LINES_FILE)
    if not isinstance(state, dict):
        state = _story_lines_state(sid)
    else:
        state = dict(state)
    state["turn_counter"] = int(state.get("turn_counter") or 0) + 1
    turn = int(state.get("turn_counter") or 0)
    audit_due = bool(turn and turn % 10 == 0 and state.get("last_state_recovery_audit_turn") != turn)
    cleanup_due = bool(turn and turn % 15 == 12 and state.get("last_compaction_cleanup_turn") != turn)
    if audit_due and cleanup_due:
        cleanup_due = False
    state["last_checked_at"] = datetime.utcnow().isoformat()
    state["maintenance_due"] = {
        "state_recovery_audit_due": audit_due,
        "state_recovery_audit_rule": "turn_counter % 10 == 0",
        "state_compaction_cleanup_due": cleanup_due,
        "state_compaction_cleanup_rule": "turn_counter % 15 == 12",
        "instruction": "If a due flag is true, the next context_slice/preflight will include deeper recent history. Write missed facts only through applyTurnResult; compact noise only, never hidden lore.",
    }
    writes[STORY_LINES_FILE] = state
    return {
        "turn_counter": turn,
        **state.get("maintenance_due", {}),
        "story_lines_file": STORY_LINES_FILE,
    }

def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _dedupe_list(values: Any) -> list[Any]:
    result: list[Any] = []
    seen: set[str] = set()
    for item in _as_list(values):
        key = json.dumps(item, ensure_ascii=False, sort_keys=True) if isinstance(item, (dict, list)) else str(item)
        if key and key not in seen:
            result.append(item)
            seen.add(key)
    return result


def _deep_merge(base_value: Any, patch_value: Any) -> Any:
    if isinstance(base_value, dict) and isinstance(patch_value, dict):
        merged = dict(base_value)
        for key, value in patch_value.items():
            if key in merged:
                merged[key] = _deep_merge(merged[key], value)
            else:
                merged[key] = value
        return merged
    if isinstance(base_value, list) and isinstance(patch_value, list):
        return _dedupe_list(base_value + patch_value)
    return patch_value


def _find(payload: dict[str, Any], *names: str) -> Any:
    for name in names:
        if name in payload:
            return payload[name]
    data = payload.get("data")
    if isinstance(data, dict):
        for name in names:
            if name in data:
                return data[name]
        proposed = data.get("proposed_updates")
        if isinstance(proposed, dict):
            for name in names:
                if name in proposed:
                    return proposed[name]
    proposed = payload.get("proposed_updates")
    if isinstance(proposed, dict):
        for name in names:
            if name in proposed:
                return proposed[name]
    return None


def _payload(body: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(body, dict):
        return {}
    data = body.get("data")
    if isinstance(data, dict):
        merged = dict(data)
        for key in ["turn_id", "visible_scene_text", "scene_text", "final_scene_text", "render_packet", "scene_response", "metadata", "dry_run"]:
            if key in body and key not in merged:
                merged[key] = body[key]
        return merged
    return body


def _turn_id(body: dict[str, Any], payload: dict[str, Any]) -> str:
    candidates: list[Any] = [body.get("turn_id"), payload.get("turn_id")]
    for container in (body, payload):
        for key in ("scene_response", "metadata"):
            nested = container.get(key)
            if isinstance(nested, dict):
                candidates.append(nested.get("turn_id"))
                metadata = nested.get("metadata")
                if isinstance(metadata, dict):
                    candidates.append(metadata.get("turn_id"))
    for value in candidates:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _apply_digest(turn_id: str, payload: dict[str, Any], scene_text: str) -> str:
    logical = dict(payload)
    logical.pop("dry_run", None)
    logical["turn_id"] = turn_id
    logical["visible_scene_text"] = scene_text
    logical.pop("final_scene_text", None)
    logical.pop("scene_text", None)
    return hashlib.sha256(
        json.dumps(logical, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()


def _rejected(sid: str, error: str, *, turn_id: str = "", expected_turn_id: str = "", next_action: str = "getPreflight") -> dict[str, Any]:
    runtime = base.read_turn_runtime(sid)
    return {
        "success": False,
        "status": "rejected",
        "session_id": sid,
        "runtime_version": RUNTIME_VERSION,
        "turn_id": turn_id or None,
        "expected_turn_id": expected_turn_id or None,
        "state_revision": int(runtime.get("state_revision") or 0),
        "error": error,
        "next_action": next_action,
        "visible_scene_output_allowed": False,
    }


def _scene_text(body: dict[str, Any], payload: dict[str, Any]) -> str:
    for value in [
        body.get("visible_scene_text"), body.get("final_scene_text"), body.get("scene_text"),
        payload.get("visible_scene_text"), payload.get("final_scene_text"), payload.get("scene_text"),
    ]:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _section_items(section: Any) -> list[dict[str, Any]]:
    if isinstance(section, list):
        return [item for item in section if isinstance(item, dict)]
    if isinstance(section, dict):
        if isinstance(section.get("items"), list):
            return [item for item in section["items"] if isinstance(item, dict)]
        if isinstance(section.get("changes"), list):
            return [item for item in section["changes"] if isinstance(item, dict)]
        # dict keyed by id
        if not any(k in section for k in ["character_id", "pair_id", "id", "patch", "append", "set", "add"]):
            return [{"id": key, "patch": value} for key, value in section.items() if isinstance(value, dict)]
        return [section]
    return []


def _plan_json_patch_file(sid: str, path: str, section: Any, writes: dict[str, Any]) -> bool:
    if not isinstance(section, dict) or not section:
        return False
    old = writes.get(path, _read_json(path, sid, {}))
    if not isinstance(old, dict):
        old = {}
    new = _deep_merge(old, section)
    if json.dumps(old, ensure_ascii=False, sort_keys=True) == json.dumps(new, ensure_ascii=False, sort_keys=True):
        return False
    writes[path] = new
    return True


def _plan_character_memory(sid: str, payload: dict[str, Any], writes: dict[str, Any]) -> list[str]:
    section = _find(payload, "character_memory_updates", "character_memory_changes", "character_memory_patch", "memory_changes", "knowledge_changes", "knowledge_state_changes")
    changed: list[str] = []
    for item in _section_items(section):
        cid = _cid(item.get("character_id") or item.get("id") or item.get("персонаж") or item.get("имя"))
        if not cid:
            continue
        path = f"state/character_memory/{cid}.json"
        state = writes.get(path, _read_json(path, sid, {}))
        if not isinstance(state, dict):
            state = {"character_id": cid}
        patch = item.get("patch") if isinstance(item.get("patch"), dict) else {}
        for key in ["set", "add", "append", "memory", "notes", "knows", "knows_as_fact", "знает_как_факт", "beliefs", "believes", "assumes", "предполагает", "does_not_know", "не_знает", "wrong_beliefs", "misbelieves", "ошибочно_считает", "observed", "seen", "видела", "видел", "heard", "слышала", "слышал", "events_witnessed", "conclusions", "learned_this_scene", "forbidden_as_fact"]:
            value = item.get(key)
            if value is not None:
                if key in {"set", "add", "append"} and isinstance(value, dict):
                    patch = _deep_merge(patch, value)
                else:
                    patch[key] = value
        # Russian compact update shape.
        fact = item.get("факт") or item.get("знание") or item.get("текст") or item.get("событие")
        if fact:
            field = item.get("field") or item.get("поле") or "memory"
            patch.setdefault(str(field), [])
            if isinstance(patch[str(field)], list):
                patch[str(field)].append(str(fact))
        if not patch:
            continue
        new = _deep_merge(state, patch)
        new.setdefault("character_id", cid)
        new["last_updated_at"] = datetime.utcnow().isoformat()
        if json.dumps(state, ensure_ascii=False, sort_keys=True) != json.dumps(new, ensure_ascii=False, sort_keys=True):
            writes[path] = new
            changed.append(path)
    return sorted(set(changed))


def _plan_relationship_pairs(sid: str, payload: dict[str, Any], writes: dict[str, Any]) -> list[str]:
    section = _find(payload, "relationship_pair_updates", "relationship_updates", "relationship_pair_changes", "relationship_changes", "relationships_changes", "relationship_deltas", "relationships")
    changed: list[str] = []
    for item in _section_items(section):
        pair = _pair_id(item.get("pair_id") or item.get("pair") or item.get("id"))
        if not pair:
            continue
        path = f"state/relationship_pairs/{pair}.json"
        state = writes.get(path, _read_json(path, sid, {}))
        if not isinstance(state, dict):
            state = {"pair_id": pair}
        patch = item.get("patch") if isinstance(item.get("patch"), dict) else {}
        for key in ["set", "add", "append", "status", "surface_dynamic", "hidden_dynamic", "memory", "notes", "trust", "tension", "respect", "attachment", "jealousy"]:
            value = item.get(key)
            if value is not None:
                if key in {"set", "add", "append"} and isinstance(value, dict):
                    patch = _deep_merge(patch, value)
                else:
                    patch[key] = value
        note = item.get("note") or item.get("заметка")
        if note:
            patch.setdefault("memory", [])
            if isinstance(patch["memory"], list):
                patch["memory"].append(str(note))
        if not patch:
            continue
        new = _deep_merge(state, patch)
        new.setdefault("pair_id", pair)
        new["last_updated_at"] = datetime.utcnow().isoformat()
        if json.dumps(state, ensure_ascii=False, sort_keys=True) != json.dumps(new, ensure_ascii=False, sort_keys=True):
            writes[path] = new
            changed.append(path)
    return sorted(set(changed))


def _plan_scene_history(
    sid: str,
    turn_id: str,
    player_input: str,
    scene_text: str,
    current: dict[str, Any],
    changed: list[str],
    state_revision: int,
    writes: dict[str, Any],
) -> bool:
    if not scene_text:
        return False
    history = writes.get(SCENE_HISTORY_FILE, _read_json(SCENE_HISTORY_FILE, sid, {"schema": "scene_history_v3", "entries": []}))
    if isinstance(history, list):
        root: Any = list(history)
        entries = root
    else:
        if not isinstance(history, dict):
            history = {"schema": "scene_history_v3", "entries": []}
        root = dict(history)
        entries = history.setdefault("entries", [])
        if not isinstance(entries, list):
            entries = []
        else:
            entries = list(entries)
        root["entries"] = entries
    if any(isinstance(entry, dict) and entry.get("turn_id") == turn_id for entry in entries):
        return False
    entry = {
        "id": f"scene_{turn_id}",
        "turn_id": turn_id,
        "state_revision": state_revision,
        "kind": "gameplay",
        "created_at": datetime.utcnow().isoformat(),
        "current_date": current.get("current_date") if isinstance(current, dict) else None,
        "current_time": current.get("current_time") if isinstance(current, dict) else None,
        "location_id": current.get("current_location_id") if isinstance(current, dict) else None,
        "location_text": current.get("current_location_text") if isinstance(current, dict) else None,
        "active_characters": current.get("active_character_ids") or current.get("active_characters", []) if isinstance(current, dict) else [],
        "player_input": player_input,
        "visible_scene_text": scene_text,
        "changed_files_snapshot": list(changed),
    }
    entries.append(entry)
    if isinstance(root, dict):
        root["schema"] = root.get("schema") or "scene_history_v3"
        root["total_entries"] = len(entries)
        root["last_updated_at"] = datetime.utcnow().isoformat()
    writes[SCENE_HISTORY_FILE] = root
    return True


_remove_route(APPLY_PATH, "POST")


@app.post(APPLY_PATH, operation_id="applyTurnResult")
def apply_turn_result_v3(session_id: str, body: dict[str, Any] | None = Body(default=None)) -> dict[str, Any]:
    sid = _safe_session_id(session_id)
    base.ensure_session(sid)
    body = body if isinstance(body, dict) else {}
    payload = _payload(body)
    dry_run = bool(body.get("dry_run") or payload.get("dry_run"))
    turn_id = _turn_id(body, payload)
    if not turn_id:
        pending = base.get_pending_turn(sid)
        return _rejected(
            sid,
            "turn_id is required. Use processTurn.turn_id; scene_response.turn_id or metadata.turn_id are fallback locations only.",
            expected_turn_id=str(pending.get("turn_id") or "") if pending else "",
            next_action="applyTurnResult" if pending else "waitForPlayerInput",
        )

    text = _scene_text(body, payload)
    digest = _apply_digest(turn_id, payload, text)

    with base.session_guard(sid):
        runtime = base.read_turn_runtime(sid)
        last_applied = runtime.get("last_applied_turn")
        if isinstance(last_applied, dict) and last_applied.get("turn_id") == turn_id:
            if last_applied.get("payload_sha256") != digest:
                return _rejected(
                    sid,
                    "This turn_id was already applied with a different result payload; history cannot be rewritten by retry.",
                    turn_id=turn_id,
                    next_action="waitForPlayerInput",
                )
            stored = last_applied.get("result")
            if isinstance(stored, dict):
                replay = dict(stored)
                replay["idempotent_replay"] = True
                replay["recovered_or_replayed"] = True
                return replay

        pending = runtime.get("pending_turn")
        if not isinstance(pending, dict) or not pending.get("turn_id"):
            return _rejected(
                sid,
                "No pending turn exists. The result is stale, already applied, or the session was reset.",
                turn_id=turn_id,
                next_action="waitForPlayerInput",
            )
        expected_turn_id = str(pending.get("turn_id") or "")
        if turn_id != expected_turn_id:
            return _rejected(
                sid,
                "turn_id does not match the protected pending turn. No state was changed.",
                turn_id=turn_id,
                expected_turn_id=expected_turn_id,
                next_action="applyTurnResult",
            )
        current_revision = int(runtime.get("state_revision") or 0)
        if int(pending.get("base_revision") or 0) != current_revision:
            return _rejected(
                sid,
                "Pending turn base_revision is stale. Run preflight/recovery before generating anything else.",
                turn_id=turn_id,
                expected_turn_id=expected_turn_id,
                next_action="getPreflight",
            )
        if not text:
            return _rejected(
                sid,
                "visible_scene_text is empty. A gameplay turn cannot commit without the final player-visible scene.",
                turn_id=turn_id,
                expected_turn_id=expected_turn_id,
                next_action="applyTurnResult",
            )

        new_revision = current_revision + 1
        writes: dict[str, Any] = {}
        changed: list[str] = []

        # Commit the protected player input and its overrides together with the
        # generated result; processTurn never mutates canonical current_state.
        current = base.effective_current_state(sid)
        current_section = _find(payload, "current_state_patch", "current_state_changes", "current_state", "state_changes")
        if isinstance(current_section, dict) and current_section:
            current = _deep_merge(current, current_section)
        current["session_id"] = sid
        current["last_player_input"] = str(pending.get("player_input") or "")
        current["state_revision"] = new_revision
        current["last_applied_turn_id"] = turn_id
        current["updated_at"] = datetime.utcnow().isoformat()
        writes[CURRENT_STATE_FILE] = current
        changed.append(CURRENT_STATE_FILE)

        # Safe whole-file merges for the remaining non-character dynamic state.
        json_sections = [
            (SCENE_CONTINUITY_FILE, ["scene_continuity_patch", "scene_continuity_changes", "scene_continuity_state"]),
            (CALENDAR_RUNTIME_FILE, ["calendar_runtime_patch", "calendar_runtime_changes", "calendar_runtime", "calendar_changes"]),
            (PHYSICAL_CONTINUITY_FILE, ["physical_continuity_patch", "physical_continuity_changes", "physical_continuity_state"]),
        ]
        for path, names in json_sections:
            if _plan_json_patch_file(sid, path, _find(payload, *names), writes):
                changed.append(path)

        changed.extend(_plan_character_memory(sid, payload, writes))
        changed.extend(_plan_relationship_pairs(sid, payload, writes))
        changed = list(dict.fromkeys(changed))

        if _plan_scene_history(
            sid,
            turn_id,
            str(pending.get("player_input") or ""),
            text,
            current,
            changed,
            new_revision,
            writes,
        ):
            changed.append(SCENE_HISTORY_FILE)

        maintenance = _plan_turn_counter(sid, writes)
        changed.append(STORY_LINES_FILE)
        changed.extend([base.TURN_RUNTIME_FILE, LAST_APPLY_RESULT_FILE])
        changed = list(dict.fromkeys(changed))

        if dry_run:
            return {
                "success": True,
                "status": "validated_dry_run",
                "session_id": sid,
                "runtime_version": RUNTIME_VERSION,
                "turn_id": turn_id,
                "base_revision": current_revision,
                "would_be_state_revision": new_revision,
                "dry_run": True,
                "would_change_files": changed,
                "maintenance_preview": maintenance,
                "visible_scene_output_allowed": False,
                "next_action": "applyTurnResult",
            }

        applied_at = datetime.utcnow().isoformat()
        result = {
            "success": True,
            "status": "applied",
            "session_id": sid,
            "runtime_version": RUNTIME_VERSION,
            "turn_id": turn_id,
            "turn_number": pending.get("turn_number"),
            "base_revision": current_revision,
            "state_revision": new_revision,
            "dry_run": False,
            "visible_scene_text": text,
            "final_scene_text": text,
            "visible_scene_output_allowed": True,
            "display_instruction": "State is committed. Show visible_scene_text now; do not expose proposed_updates or internal JSON.",
            "next_action": "waitForPlayerInput",
        }
        audit_result = {
            **result,
            "changed_files": changed,
            "maintenance": maintenance,
            "blocked_paths": ["characters/<id>/*.yaml", "legacy monolithic dynamic memory files"],
            "audit_note": "Internal audit record. Never render this file or its metadata as scene output.",
        }
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
            "result": result,
        }
        final_runtime["updated_at"] = applied_at
        writes[base.TURN_RUNTIME_FILE] = final_runtime
        writes[LAST_APPLY_RESULT_FILE] = audit_result

        # If the process stops after any target replacement, the prepared journal
        # rolls all files forward on the next request. A retry then returns the
        # stored result without applying the turn twice.
        base.commit_json_transaction(sid, f"apply_{turn_id}", writes)
        return result


try:
    app.version = RUNTIME_VERSION
except Exception:
    pass
