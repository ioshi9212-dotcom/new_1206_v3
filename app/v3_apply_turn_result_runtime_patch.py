"""Transactional v3 apply writer for all dynamic session state.

One protected ``turn_id`` is validated, planned in memory, journaled, and then
committed across current state, history, evidence-backed memory, snapshot-scoped
relationships and maintenance.
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

MEMORY_SOURCE_TYPES = {
    "direct_observation", "direct_hearing", "direct_conversation", "told_by",
    "document_seen", "order_received", "scene_event", "scene_inference",
    "prior_memory", "physical_evidence", "intentional_touch_sensory", "self_knowledge",
}
FACT_SOURCE_TYPES = {
    "direct_observation", "direct_hearing", "direct_conversation", "told_by",
    "document_seen", "order_received", "prior_memory", "physical_evidence", "self_knowledge",
}
BLOCKED_KNOWLEDGE_SOURCES = {
    "calendar", "calendar_rule", "prompt", "runtime", "runtime_state", "scene_rule",
    "hidden_lore", "author_lore", "narrator", "engine", "loaded_file",
}
RELATIONSHIP_METRICS = {"trust", "tension", "respect", "attachment", "jealousy", "conflict"}
FORBIDDEN_CHARACTER_STATE_KEYS = {
    "personality", "character", "character_card", "voice", "appearance", "static_card",
    "характер", "голос", "внешность", "личность",
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


def _rejected(
    sid: str,
    error: str,
    *,
    turn_id: str = "",
    expected_turn_id: str = "",
    next_action: str = "getPreflight",
    validation_errors: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    runtime = base.read_turn_runtime(sid)
    result = {
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
    if validation_errors:
        result["validation_errors"] = validation_errors
    return result


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


def _normalized_key(value: Any) -> str:
    return str(value or "").strip().lower().replace("ё", "е").replace(" ", "_")


def _bounded_text(value: Any, max_chars: int = 600) -> str:
    if isinstance(value, dict):
        for key in ("text", "fact", "summary", "note", "event", "событие", "факт", "знание"):
            if value.get(key):
                value = value[key]
                break
        else:
            value = json.dumps(value, ensure_ascii=False, sort_keys=True)
    text = " ".join(str(value or "").strip().split())
    return text[:max_chars]


def _stable_event_id(turn_id: str, owner_id: str, kind: str, text: str, source_type: str) -> str:
    digest = hashlib.sha256(
        json.dumps(
            {"turn_id": turn_id, "owner_id": owner_id, "kind": kind, "text": text, "source_type": source_type},
            ensure_ascii=False,
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()[:16]
    return f"{turn_id}:{digest}"


def _contains_forbidden_character_state(value: Any, *, top_level: bool = True) -> str:
    if not isinstance(value, dict):
        return ""
    for key, nested in value.items():
        normalized = _normalized_key(key)
        if normalized in FORBIDDEN_CHARACTER_STATE_KEYS and not (top_level and normalized == "character"):
            return str(key)
        if normalized in {"patch", "set", "add", "append"} and isinstance(nested, dict):
            found = _contains_forbidden_character_state(nested, top_level=False)
            if found:
                return found
    return ""


def _memory_event_specs(item: dict[str, Any]) -> list[dict[str, Any]]:
    kind_aliases = {
        "memory": "scene_memory", "notes": "scene_memory", "note": "scene_memory",
        "last_scene_notes": "scene_memory", "recent_scene_notes": "scene_memory", "learned_this_scene": "scene_memory",
        "seen": "observation", "saw": "observation", "observed": "observation", "events_witnessed": "observation",
        "видел": "observation", "видела": "observation", "видели": "observation",
        "heard": "heard", "слышал": "heard", "слышала": "heard", "слышали": "heard",
        "knows": "fact", "knows_as_fact": "fact", "known_facts": "fact", "знает_как_факт": "fact", "факт": "fact", "знание": "fact",
        "beliefs": "belief", "believes": "belief", "assumes": "belief", "suspects": "belief", "conclusions": "belief", "предполагает": "belief", "подозревает": "belief",
        "wrong_beliefs": "mistaken_belief", "misbelieves": "mistaken_belief", "mistaken_beliefs": "mistaken_belief", "ошибочно_считает": "mistaken_belief",
        "does_not_know": "unknown_boundary", "forbidden_as_fact": "unknown_boundary", "не_знает": "unknown_boundary",
        "hides": "hidden_intent", "is_hiding": "hidden_intent", "скрывает": "hidden_intent",
        "agreements": "agreement", "orders_or_duties": "order", "договоренности": "agreement",
        "current_goal": "goal", "goals": "goal", "active_goals": "goal",
        "current_limitations": "limitation", "limitations": "limitation",
        "future_hooks": "future_hook", "open_threads": "future_hook",
    }
    metadata = item.get("patch") if isinstance(item.get("patch"), dict) else {}
    common = {
        "source_type": item.get("source_type") or metadata.get("source_type"),
        "source_character_id": item.get("source_character_id") or item.get("source_character") or metadata.get("source_character_id") or metadata.get("source_character"),
        "evidence": item.get("evidence") or item.get("source_evidence") or metadata.get("evidence") or metadata.get("source_evidence"),
        "status": item.get("status") or metadata.get("status"),
    }
    specs: list[dict[str, Any]] = []

    def add_values(value: Any, default_kind: str) -> None:
        values = value if isinstance(value, list) else [value]
        for raw in values:
            if raw is None:
                continue
            spec = dict(common)
            if isinstance(raw, dict):
                spec.update(raw)
                spec["kind"] = raw.get("kind") or raw.get("type") or default_kind
                spec["text"] = _bounded_text(raw)
            else:
                spec["kind"] = default_kind
                spec["text"] = _bounded_text(raw)
            if spec.get("text"):
                specs.append(spec)

    for key in ("events", "memory_events", "knowledge_events"):
        if item.get(key) is not None:
            add_values(item.get(key), "scene_memory")

    def scan(container: dict[str, Any]) -> None:
        for key, value in container.items():
            normalized = _normalized_key(key)
            if normalized in {"patch", "set", "add", "append"} and isinstance(value, dict):
                scan(value)
                continue
            kind = kind_aliases.get(normalized)
            if kind:
                add_values(value, kind)

    scan(item)
    compact_fact = item.get("текст") or item.get("событие")
    if compact_fact:
        add_values(compact_fact, kind_aliases.get(_normalized_key(item.get("field") or item.get("поле")), "scene_memory"))
    return specs


def _memory_target_field(kind: str) -> str:
    return {
        "observation": "seen", "heard": "heard", "fact": "knows_as_fact",
        "belief": "assumes", "mistaken_belief": "mistaken_beliefs",
        "unknown_boundary": "does_not_know", "hidden_intent": "hides",
        "agreement": "agreements", "order": "agreements", "goal": "future_hooks",
        "limitation": "last_scene_notes", "future_hook": "future_hooks", "scene_memory": "last_scene_notes",
    }.get(kind, "last_scene_notes")


def _append_memory_text(state: dict[str, Any], kind: str, text: str) -> None:
    if kind in {"goal", "limitation", "future_hook", "hidden_intent"}:
        # These are lifecycle events. Their active/resolved status lives in the
        # evidence ledger; copying them into an append-only legacy list would
        # leave stale goals or secrets active forever.
        return
    target = _memory_target_field(kind)
    aliases = {
        "seen": ["seen", "saw", "видел", "видела"],
        "heard": ["heard", "слышал", "слышала"],
        "knows_as_fact": ["knows_as_fact", "знает_как_факт"],
        "assumes": ["assumes", "believes", "предполагает"],
        "mistaken_beliefs": ["mistaken_beliefs", "misbelieves", "wrongly_believes", "ошибочно_считает"],
        "does_not_know": ["does_not_know", "does_not_know_active", "не_знает"],
        "hides": ["hides", "is_hiding", "hides_currently", "скрывает"],
        "agreements": ["agreements", "договоренности"],
        "future_hooks": ["future_hooks", "open_threads", "зацепки_на_будущее"],
        "last_scene_notes": ["last_scene_notes", "recent_scene_notes", "последние_сцены"],
    }
    for key in aliases.get(target, [target]):
        if isinstance(state.get(key), list):
            if text not in state[key]:
                state[key].append(text)
            return
    nested = state.get("memory")
    nested_targets = {
        "seen": "saw", "heard": "heard", "knows_as_fact": "knows_as_fact",
        "assumes": "suspects", "mistaken_beliefs": "wrongly_believes",
        "does_not_know": "does_not_know", "hides": "is_hiding",
        "agreements": "agreements", "future_hooks": "future_hooks", "last_scene_notes": "recent_scene_notes",
    }
    if isinstance(nested, dict):
        nested_key = nested_targets.get(target, target)
        values = nested.setdefault(nested_key, [])
        if isinstance(values, list) and text not in values:
            values.append(text)
        return
    state.setdefault(target, [])
    if isinstance(state[target], list) and text not in state[target]:
        state[target].append(text)


def _plan_character_memory(
    sid: str,
    payload: dict[str, Any],
    writes: dict[str, Any],
    *,
    turn_id: str,
    state_revision: int,
    context_snapshot: dict[str, Any],
    scene_text: str,
) -> tuple[list[str], list[dict[str, Any]], list[dict[str, Any]]]:
    section = _find(payload, "character_memory_updates", "character_memory_changes", "character_memory_patch", "memory_changes", "knowledge_changes", "knowledge_state_changes")
    contract = context_snapshot.get("contract") if isinstance(context_snapshot.get("contract"), dict) else {}
    allowed = {_cid(value) for value in contract.get("memory_character_ids", []) if _cid(value)}
    all_loaded = {_cid(value) for value in contract.get("character_ids", []) if _cid(value)}
    changed: list[str] = []
    audit: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    total_events = 0
    for item in _section_items(section):
        cid = _cid(item.get("character_id") or item.get("id") or item.get("персонаж") or item.get("имя"))
        if not cid:
            errors.append({"scope": "character_memory", "code": "missing_character_id", "message": "Every character memory update needs character_id."})
            continue
        if cid not in allowed:
            errors.append({
                "scope": "character_memory", "character_id": cid, "code": "character_memory_not_loaded",
                "message": "Dynamic memory may be written only for a scene-active character whose memory was loaded in this snapshot.",
            })
            continue
        forbidden = _contains_forbidden_character_state(item)
        if forbidden:
            errors.append({
                "scope": "character_memory", "character_id": cid, "code": "static_character_rewrite_blocked",
                "field": forbidden, "message": "Personality, voice, appearance and static character cards cannot be rewritten through dynamic memory.",
            })
            continue
        specs = _memory_event_specs(item)
        if not specs:
            errors.append({
                "scope": "character_memory", "character_id": cid, "code": "no_supported_memory_events",
                "message": "Use events or evidence fields such as seen/heard/knows_as_fact/assumes/memory.",
            })
            continue
        if len(specs) > 12 or total_events + len(specs) > 40:
            errors.append({
                "scope": "character_memory", "character_id": cid, "code": "too_many_memory_events",
                "message": "At most 12 events per character and 40 total events may be written in one turn.",
            })
            continue
        total_events += len(specs)
        path = f"state/character_memory/{cid}.json"
        old_state = writes.get(path, _read_json(path, sid, {}))
        state = dict(old_state) if isinstance(old_state, dict) else {"character_id": cid}
        ledger = list(state.get("memory_events")) if isinstance(state.get("memory_events"), list) else []
        existing_ids = {entry.get("event_id") for entry in ledger if isinstance(entry, dict)}
        accepted: list[dict[str, Any]] = []
        item_errors: list[dict[str, Any]] = []
        for spec in specs:
            raw_kind = _normalized_key(spec.get("kind") or "scene_memory")
            kind = {
                "seen": "observation", "observed": "observation", "saw": "observation",
                "reported": "heard", "learned_fact": "fact", "knowledge": "fact",
                "suspicion": "belief", "inference": "belief", "wrong_belief": "mistaken_belief",
                "note": "scene_memory", "memory": "scene_memory",
            }.get(raw_kind, raw_kind)
            if kind not in {"observation", "heard", "fact", "belief", "mistaken_belief", "unknown_boundary", "hidden_intent", "agreement", "order", "goal", "limitation", "future_hook", "scene_memory"}:
                item_errors.append({"scope": "character_memory", "character_id": cid, "code": "unsupported_memory_kind", "kind": kind})
                continue
            text = _bounded_text(spec.get("text"))
            source_type = _normalized_key(spec.get("source_type"))
            if not source_type:
                source_type = {
                    "observation": "direct_observation", "heard": "direct_hearing",
                    "belief": "scene_inference", "mistaken_belief": "scene_inference",
                }.get(kind, "scene_event" if kind != "fact" else "")
            if source_type in BLOCKED_KNOWLEDGE_SOURCES or any(blocked in source_type for blocked in BLOCKED_KNOWLEDGE_SOURCES):
                item_errors.append({
                    "scope": "character_memory", "character_id": cid, "code": "forbidden_knowledge_source",
                    "source_type": source_type, "message": "Calendar/prompt/runtime/hidden lore cannot become character knowledge.",
                })
                continue
            if source_type not in MEMORY_SOURCE_TYPES:
                item_errors.append({
                    "scope": "character_memory", "character_id": cid, "code": "invalid_or_missing_source_type",
                    "kind": kind, "message": "Use an in-world source_type such as direct_observation, direct_hearing, told_by or scene_inference.",
                })
                continue
            if kind == "fact" and source_type not in FACT_SOURCE_TYPES:
                item_errors.append({
                    "scope": "character_memory", "character_id": cid, "code": "fact_without_confirming_source",
                    "message": "A confirmed fact needs a confirming in-world source; otherwise store it as belief/suspicion.",
                })
                continue
            source_character_id = _cid(spec.get("source_character_id")) if spec.get("source_character_id") else ""
            if source_type == "told_by" and (not source_character_id or source_character_id not in all_loaded):
                item_errors.append({
                    "scope": "character_memory", "character_id": cid, "code": "unverified_told_by_source",
                    "message": "told_by requires source_character_id loaded in the same snapshot.",
                })
                continue
            evidence = _bounded_text(spec.get("evidence") or scene_text, 500)
            event_id = _stable_event_id(turn_id, cid, kind, text, source_type)
            event = {
                "event_id": event_id,
                "turn_id": turn_id,
                "state_revision": state_revision,
                "kind": kind,
                "text": text,
                "source_type": source_type,
                "source_character_id": source_character_id or None,
                "evidence": evidence,
                "confidence": "confirmed" if kind in {"fact", "observation", "heard"} else "mistaken" if kind == "mistaken_belief" else "unconfirmed",
                "status": _bounded_text(spec.get("status"), 40) or "active",
                "created_at": datetime.utcnow().isoformat(),
            }
            if event_id not in existing_ids:
                ledger.append(event)
                existing_ids.add(event_id)
                _append_memory_text(state, kind, text)
                accepted.append(event)
        if item_errors:
            errors.extend(item_errors)
            continue
        if accepted:
            state["memory_events"] = ledger
            applied_turn_ids = list(state.get("applied_turn_ids")) if isinstance(state.get("applied_turn_ids"), list) else []
            if turn_id not in applied_turn_ids:
                applied_turn_ids.append(turn_id)
            state["applied_turn_ids"] = applied_turn_ids[-80:]
            state.setdefault("character_id", cid)
            state["last_updated_turn_id"] = turn_id
            state["last_updated_state_revision"] = state_revision
            state["last_updated_at"] = datetime.utcnow().isoformat()
            writes[path] = state
            changed.append(path)
            audit.append({"character_id": cid, "path": path, "accepted_event_ids": [event["event_id"] for event in accepted]})
    return sorted(set(changed)), audit, errors


def _resolve_loaded_pair(value: Any, loaded: set[str]) -> str:
    pair = _pair_id(value)
    if not pair:
        return ""
    if pair in loaded:
        return pair
    left, right = pair.split("__", 1)
    reverse = f"{right}__{left}"
    return reverse if reverse in loaded else ""


def _relationship_event_specs(item: dict[str, Any]) -> list[dict[str, Any]]:
    specs: list[dict[str, Any]] = []
    metadata = item.get("patch") if isinstance(item.get("patch"), dict) else {}
    common = {
        "source_type": item.get("source_type") or metadata.get("source_type") or "scene_event",
        "evidence": item.get("evidence") or item.get("source_evidence") or metadata.get("evidence") or metadata.get("source_evidence"),
        "direction": item.get("direction") or metadata.get("direction") or "shared",
        "protected": bool(item.get("protected") or metadata.get("protected")),
    }
    for key in ("events", "relationship_events"):
        values = item.get(key)
        if values is None:
            continue
        for raw in values if isinstance(values, list) else [values]:
            if isinstance(raw, dict):
                spec = dict(common)
                spec.update(raw)
                specs.append(spec)
    deltas: dict[str, float] = {}

    def collect_metrics(container: Any) -> None:
        if not isinstance(container, dict):
            return
        for key, value in container.items():
            normalized = _normalized_key(key)
            if normalized in RELATIONSHIP_METRICS and isinstance(value, (int, float)) and not isinstance(value, bool):
                deltas[normalized] = deltas.get(normalized, 0.0) + float(value)
            elif normalized in {"deltas", "metrics", "surface_dynamic", "patch", "add", "append", "set"} and isinstance(value, dict):
                collect_metrics(value)

    collect_metrics(item)
    note = (
        item.get("note") or item.get("notes") or item.get("memory") or item.get("заметка") or item.get("summary")
        or metadata.get("note") or metadata.get("notes") or metadata.get("memory") or metadata.get("summary")
    )
    if deltas or note:
        spec = dict(common)
        spec["summary"] = _bounded_text(note) or "Relationship pressure changed during this played scene."
        spec["deltas"] = deltas
        specs.append(spec)
    return specs


def _plan_relationship_pairs(
    sid: str,
    payload: dict[str, Any],
    writes: dict[str, Any],
    *,
    turn_id: str,
    state_revision: int,
    context_snapshot: dict[str, Any],
    scene_text: str,
    current: dict[str, Any],
) -> tuple[list[str], list[dict[str, Any]], list[dict[str, Any]]]:
    section = _find(payload, "relationship_pair_updates", "relationship_updates", "relationship_pair_changes", "relationship_changes", "relationships_changes", "relationship_deltas", "relationships")
    contract = context_snapshot.get("contract") if isinstance(context_snapshot.get("contract"), dict) else {}
    loaded = {str(value) for value in contract.get("relationship_pair_ids", []) if "__" in str(value)}
    changed: list[str] = []
    audit: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for item in _section_items(section):
        requested_pair = item.get("pair_id") or item.get("pair") or item.get("id")
        pair = _resolve_loaded_pair(requested_pair, loaded)
        if not pair:
            errors.append({
                "scope": "relationship", "pair_id": _pair_id(requested_pair) or str(requested_pair or ""),
                "code": "relationship_pair_not_loaded",
                "message": "A relationship may change only if that exact pair was selected and loaded in this turn snapshot.",
            })
            continue
        forbidden_keys = {
            _normalized_key(key) for key in item
            if _normalized_key(key) in {"hidden_dynamic", "personality", "voice", "character_card", "status"}
        }
        patch = item.get("patch")
        if isinstance(patch, dict):
            forbidden_keys.update(
                _normalized_key(key) for key in patch
                if _normalized_key(key) in {"hidden_dynamic", "personality", "voice", "character_card", "status"}
            )
        if forbidden_keys:
            errors.append({
                "scope": "relationship", "pair_id": pair, "code": "relationship_overwrite_blocked",
                "fields": sorted(forbidden_keys), "message": "Use evidence-backed events and bounded deltas; do not overwrite hidden state, status or personality.",
            })
            continue
        specs = _relationship_event_specs(item)
        if not specs:
            errors.append({
                "scope": "relationship", "pair_id": pair, "code": "no_supported_relationship_events",
                "message": "Use relationship_events, a note, or bounded trust/tension/respect/attachment/jealousy/conflict deltas.",
            })
            continue
        if len(specs) > 4:
            errors.append({"scope": "relationship", "pair_id": pair, "code": "too_many_relationship_events", "message": "At most four relationship events may be written for one pair in a turn."})
            continue
        path = f"state/relationship_pairs/{pair}.json"
        old_state = writes.get(path, _read_json(path, sid, {}))
        state = dict(old_state) if isinstance(old_state, dict) else {"pair_id": pair, "participants": pair.split("__")}
        ledger = list(state.get("relationship_events")) if isinstance(state.get("relationship_events"), list) else []
        existing_ids = {entry.get("event_id") for entry in ledger if isinstance(entry, dict)}
        metrics = dict(state.get("metrics")) if isinstance(state.get("metrics"), dict) else {}
        surface = state.get("surface_dynamic") if isinstance(state.get("surface_dynamic"), dict) else {}
        for metric in RELATIONSHIP_METRICS:
            if metric not in metrics and isinstance(surface.get(metric), (int, float)) and not isinstance(surface.get(metric), bool):
                metrics[metric] = float(surface[metric])
            metrics.setdefault(metric, 0.0)
        accepted: list[dict[str, Any]] = []
        item_errors: list[dict[str, Any]] = []
        per_turn_delta = {metric: 0.0 for metric in RELATIONSHIP_METRICS}
        left, right = pair.split("__", 1)
        for spec in specs:
            source_type = _normalized_key(spec.get("source_type") or "scene_event")
            if source_type in BLOCKED_KNOWLEDGE_SOURCES or source_type not in MEMORY_SOURCE_TYPES:
                item_errors.append({"scope": "relationship", "pair_id": pair, "code": "invalid_relationship_source", "source_type": source_type})
                continue
            summary = _bounded_text(spec.get("summary") or spec.get("text") or spec.get("note"))
            if not summary:
                item_errors.append({"scope": "relationship", "pair_id": pair, "code": "missing_relationship_event_summary"})
                continue
            raw_deltas = spec.get("deltas") if isinstance(spec.get("deltas"), dict) else {}
            deltas: dict[str, float] = {}
            for metric, value in raw_deltas.items():
                metric_key = _normalized_key(metric)
                if metric_key not in RELATIONSHIP_METRICS or not isinstance(value, (int, float)) or isinstance(value, bool):
                    item_errors.append({"scope": "relationship", "pair_id": pair, "code": "invalid_relationship_delta", "metric": metric_key})
                    continue
                number = float(value)
                per_turn_delta[metric_key] += number
                if abs(per_turn_delta[metric_key]) > 10:
                    item_errors.append({
                        "scope": "relationship", "pair_id": pair, "code": "relationship_delta_too_large",
                        "metric": metric_key, "message": "One turn may change a relationship metric by at most 10 points in either direction.",
                    })
                    continue
                deltas[metric_key] = number
            direction = _bounded_text(spec.get("direction") or "shared", 80)
            allowed_directions = {"shared", left, right, f"{left}_to_{right}", f"{right}_to_{left}"}
            if direction not in allowed_directions:
                item_errors.append({"scope": "relationship", "pair_id": pair, "code": "invalid_relationship_direction", "direction": direction})
                continue
            event_id = _stable_event_id(
                turn_id,
                pair,
                f"{direction}:{json.dumps(deltas, sort_keys=True)}",
                summary,
                source_type,
            )
            event = {
                "event_id": event_id,
                "turn_id": turn_id,
                "state_revision": state_revision,
                "summary": summary,
                "direction": direction,
                "deltas": deltas,
                "source_type": source_type,
                "evidence": _bounded_text(spec.get("evidence") or scene_text, 500),
                "protected": bool(spec.get("protected")),
                "created_at": datetime.utcnow().isoformat(),
            }
            if event_id not in existing_ids:
                ledger.append(event)
                existing_ids.add(event_id)
                accepted.append(event)
        if item_errors:
            errors.extend(item_errors)
            continue
        if accepted:
            for event in accepted:
                for metric, delta in event["deltas"].items():
                    metrics[metric] = max(-100.0, min(100.0, float(metrics.get(metric) or 0.0) + float(delta)))
                    if isinstance(surface.get(metric), (int, float)) and not isinstance(surface.get(metric), bool):
                        surface[metric] = metrics[metric]
            state["relationship_events"] = ledger
            state["metrics"] = metrics
            if surface:
                state["surface_dynamic"] = surface
            shared_key = "shared_memory" if isinstance(state.get("shared_memory"), list) else "memory"
            shared = list(state.get(shared_key)) if isinstance(state.get(shared_key), list) else []
            for event in accepted:
                if event["summary"] not in shared:
                    shared.append(event["summary"])
            state[shared_key] = shared[-80:]
            last = accepted[-1]
            state["last_interaction"] = {
                "date": current.get("current_date") or current.get("date") or "unknown",
                "scene": current.get("current_scene_id") or current.get("scene_id") or "unknown",
                "summary": last["summary"],
                "result": last["deltas"],
                "turn_id": turn_id,
            }
            protected = list(state.get("protected_moments")) if isinstance(state.get("protected_moments"), list) else []
            for event in accepted:
                if event["protected"]:
                    protected.append({
                        "event_id": event["event_id"], "turn_id": turn_id,
                        "date": current.get("current_date") or "unknown",
                        "scene": current.get("current_scene_id") or "unknown",
                        "trigger": event["summary"], "why_it_matters": event["evidence"],
                        "status": "active",
                    })
            if protected:
                state["protected_moments"] = protected
            applied_turn_ids = list(state.get("applied_turn_ids")) if isinstance(state.get("applied_turn_ids"), list) else []
            if turn_id not in applied_turn_ids:
                applied_turn_ids.append(turn_id)
            state["applied_turn_ids"] = applied_turn_ids[-80:]
            state.setdefault("pair_id", pair)
            state.setdefault("participants", [left, right])
            state["last_updated_turn_id"] = turn_id
            state["last_updated_state_revision"] = state_revision
            state["last_updated_at"] = datetime.utcnow().isoformat()
            writes[path] = state
            changed.append(path)
            audit.append({"pair_id": pair, "path": path, "accepted_event_ids": [event["event_id"] for event in accepted], "metrics": metrics})
    return sorted(set(changed)), audit, errors


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

        context_snapshot = base.read_session_json(base.CONTEXT_SNAPSHOT_FILE, sid, default={})
        snapshot_matches = bool(
            isinstance(context_snapshot, dict)
            and context_snapshot.get("schema") == "context_snapshot_v1"
            and context_snapshot.get("status") == "ready"
            and context_snapshot.get("runtime_version") == RUNTIME_VERSION
            and context_snapshot.get("turn_id") == turn_id
            and int(context_snapshot.get("base_revision") or 0) == current_revision
            and context_snapshot.get("player_input_sha256") == pending.get("player_input_sha256")
        )
        if not snapshot_matches:
            return _rejected(
                sid,
                "No valid server-side context snapshot exists for this turn. Build it through getTurnContract.",
                turn_id=turn_id,
                expected_turn_id=expected_turn_id,
                next_action="getTurnContract",
            )
        if not context_snapshot.get("all_required_chunks_served"):
            return _rejected(
                sid,
                "Not all required context chunks were loaded in order. Scene apply is blocked.",
                turn_id=turn_id,
                expected_turn_id=expected_turn_id,
                next_action="getRequiredContextChunk",
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

        memory_changed, memory_audit, memory_errors = _plan_character_memory(
            sid,
            payload,
            writes,
            turn_id=turn_id,
            state_revision=new_revision,
            context_snapshot=context_snapshot,
            scene_text=text,
        )
        relationship_changed, relationship_audit, relationship_errors = _plan_relationship_pairs(
            sid,
            payload,
            writes,
            turn_id=turn_id,
            state_revision=new_revision,
            context_snapshot=context_snapshot,
            scene_text=text,
            current=current,
        )
        validation_errors = memory_errors + relationship_errors
        if validation_errors:
            return _rejected(
                sid,
                "Dynamic character state was rejected. Correct the evidence/source or loaded-character/pair scope, then retry the same turn_id.",
                turn_id=turn_id,
                expected_turn_id=expected_turn_id,
                next_action="applyTurnResult",
                validation_errors=validation_errors,
            )
        changed.extend(memory_changed)
        changed.extend(relationship_changed)
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
        changed.extend([base.CONTEXT_SNAPSHOT_FILE, base.TURN_RUNTIME_FILE, LAST_APPLY_RESULT_FILE])
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
                "character_state_preview": {
                    "memory_events": memory_audit,
                    "relationship_events": relationship_audit,
                },
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
            "context_snapshot_sha256": context_snapshot.get("context_snapshot_sha256"),
            "dry_run": False,
            "visible_scene_text": text,
            "final_scene_text": text,
            "visible_scene_output_allowed": True,
            "display_instruction": "State is committed. Show visible_scene_text now; do not expose proposed_updates or internal JSON.",
            "next_action": "waitForPlayerInput",
            "state_update_summary": {
                "character_memory_files": len(memory_changed),
                "relationship_pair_files": len(relationship_changed),
            },
        }
        audit_result = {
            **result,
            "changed_files": changed,
            "maintenance": maintenance,
            "character_state_audit": {
                "memory_events": memory_audit,
                "relationship_events": relationship_audit,
                "source_rule": "Facts require in-world evidence; calendar/prompt/runtime/hidden lore are blocked; writes are limited to snapshot-loaded characters and pairs.",
            },
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
