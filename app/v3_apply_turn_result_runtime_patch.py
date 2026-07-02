"""V3 apply-turn-result override for character_memory and relationship_pairs.

This writer routes dynamic updates only into v3 session state files: state/character_memory and state/relationship_pairs.
"""
from __future__ import annotations

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


def _write_json(path: str, data: Any, sid: str, dry_run: bool) -> bool:
    if not dry_run:
        base.write_json(path, data, session_id=sid)
    return True


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
        for key in ["visible_scene_text", "scene_text", "final_scene_text", "render_packet", "dry_run"]:
            if key in body and key not in merged:
                merged[key] = body[key]
        return merged
    return body


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


def _apply_json_patch_file(sid: str, path: str, section: Any, dry_run: bool) -> bool:
    if not isinstance(section, dict) or not section:
        return False
    old = _read_json(path, sid, {})
    if not isinstance(old, dict):
        old = {}
    new = _deep_merge(old, section)
    if json.dumps(old, ensure_ascii=False, sort_keys=True) == json.dumps(new, ensure_ascii=False, sort_keys=True):
        return False
    _write_json(path, new, sid, dry_run)
    return True


def _apply_character_memory(sid: str, payload: dict[str, Any], dry_run: bool) -> list[str]:
    section = _find(payload, "character_memory_changes", "character_memory_patch", "memory_changes", "knowledge_changes", "knowledge_state_changes")
    changed: list[str] = []
    for item in _section_items(section):
        cid = _cid(item.get("character_id") or item.get("id") or item.get("персонаж") or item.get("имя"))
        if not cid:
            continue
        path = f"state/character_memory/{cid}.json"
        state = _read_json(path, sid, {})
        if not isinstance(state, dict):
            state = {"character_id": cid}
        patch = item.get("patch") if isinstance(item.get("patch"), dict) else {}
        for key in ["set", "add", "append", "memory", "notes", "knows", "does_not_know", "wrong_beliefs", "observed", "heard", "events_witnessed", "conclusions"]:
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
            _write_json(path, new, sid, dry_run)
            changed.append(path)
    return sorted(set(changed))


def _apply_relationship_pairs(sid: str, payload: dict[str, Any], dry_run: bool) -> list[str]:
    section = _find(payload, "relationship_pair_changes", "relationship_changes", "relationships_changes", "relationship_deltas", "relationships")
    changed: list[str] = []
    for item in _section_items(section):
        pair = _pair_id(item.get("pair_id") or item.get("pair") or item.get("id"))
        if not pair:
            continue
        path = f"state/relationship_pairs/{pair}.json"
        state = _read_json(path, sid, {})
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
            _write_json(path, new, sid, dry_run)
            changed.append(path)
    return sorted(set(changed))


def _append_scene_history(sid: str, payload: dict[str, Any], scene_text: str, changed: list[str], dry_run: bool) -> bool:
    if dry_run or not scene_text:
        return False
    current = _read_json(CURRENT_STATE_FILE, sid, {})
    history = _read_json(SCENE_HISTORY_FILE, sid, {"schema": "scene_history_v3", "entries": []})
    if isinstance(history, list):
        root: Any = history
        entries = history
    else:
        if not isinstance(history, dict):
            history = {"schema": "scene_history_v3", "entries": []}
        root = history
        entries = history.setdefault("entries", [])
        if not isinstance(entries, list):
            entries = []
            history["entries"] = entries
    if entries and isinstance(entries[-1], dict) and entries[-1].get("visible_scene_text") == scene_text:
        return False
    entry = {
        "id": f"scene_{datetime.utcnow().strftime('%Y%m%d%H%M%S')}",
        "kind": "gameplay",
        "created_at": datetime.utcnow().isoformat(),
        "current_date": current.get("current_date") if isinstance(current, dict) else None,
        "current_time": current.get("current_time") if isinstance(current, dict) else None,
        "location_id": current.get("current_location_id") if isinstance(current, dict) else None,
        "location_text": current.get("current_location_text") if isinstance(current, dict) else None,
        "active_characters": current.get("active_character_ids") or current.get("active_characters", []) if isinstance(current, dict) else [],
        "player_input": payload.get("player_input") or (current.get("last_player_input") if isinstance(current, dict) else ""),
        "visible_scene_text": scene_text,
        "changed_files_snapshot": list(changed),
    }
    entries.append(entry)
    if isinstance(root, dict):
        root["schema"] = root.get("schema") or "scene_history_v3"
        root["total_entries"] = len(entries)
        root["last_updated_at"] = datetime.utcnow().isoformat()
    _write_json(SCENE_HISTORY_FILE, root, sid, dry_run)
    return True


_remove_route(APPLY_PATH, "POST")


@app.post(APPLY_PATH, operation_id="applyTurnResult")
def apply_turn_result_v3(session_id: str, body: dict[str, Any] | None = Body(default=None)) -> dict[str, Any]:
    sid = _safe_session_id(session_id)
    base.ensure_session(sid)
    body = body if isinstance(body, dict) else {}
    payload = _payload(body)
    dry_run = bool(body.get("dry_run") or payload.get("dry_run"))
    changed: list[str] = []

    # Safe whole-file JSON merges for non-character dynamic state only.
    json_sections = [
        (CURRENT_STATE_FILE, ["current_state_patch", "current_state_changes", "current_state", "state_changes"]),
        (SCENE_CONTINUITY_FILE, ["scene_continuity_patch", "scene_continuity_changes", "scene_continuity_state"]),
        (CALENDAR_RUNTIME_FILE, ["calendar_runtime_patch", "calendar_runtime_changes", "calendar_runtime", "calendar_changes"]),
        (PHYSICAL_CONTINUITY_FILE, ["physical_continuity_patch", "physical_continuity_changes", "physical_continuity_state"]),
    ]
    for path, names in json_sections:
        section = _find(payload, *names)
        if _apply_json_patch_file(sid, path, section, dry_run):
            changed.append(path)

    changed.extend(_apply_character_memory(sid, payload, dry_run))
    changed.extend(_apply_relationship_pairs(sid, payload, dry_run))
    changed = list(dict.fromkeys(changed))

    text = _scene_text(body, payload)
    if _append_scene_history(sid, payload, text, changed, dry_run):
        changed.append(SCENE_HISTORY_FILE)

    status = "applied" if changed else "no_changes_detected"
    result = {
        "status": status,
        "session_id": sid,
        "runtime_version": RUNTIME_VERSION,
        "dry_run": dry_run,
        "changed_files": changed,
        "visible_scene_text": text,
        "final_scene_text": text,
        "blocked_paths": ["characters/<id>/*.yaml", "legacy monolithic dynamic memory files"],
        "notes": [
            "Dynamic character updates are written to state/character_memory/<id>.json.",
            "Relationship updates are written to state/relationship_pairs/<pair>.json.",
            "Static character cards are never modified by applyTurnResult.",
        ],
    }
    if not dry_run:
        _write_json(LAST_APPLY_RESULT_FILE, result, sid, False)
        if LAST_APPLY_RESULT_FILE not in changed:
            changed.append(LAST_APPLY_RESULT_FILE)
            result["changed_files"] = changed
    return result


try:
    app.version = RUNTIME_VERSION
except Exception:
    pass
