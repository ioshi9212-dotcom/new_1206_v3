"""Action-safe character context pipeline for Akira 1206 v3.

Single replacement patch. It does not add extra runtime layers.
The point is to send the writer the right character material:
POV voice/limits, active NPC goals, behavior, knowledge, unknowns and reaction hooks.
"""
from __future__ import annotations

import json
import re
from typing import Any

from fastapi import Body

from app import compact as base

app = base.app
RUNTIME_VERSION = base.APP_VERSION

CURRENT_STATE_FILE = "state/current_state.json"
SCENE_HISTORY_FILE = "state/scene_history.json"
CALENDAR_RUNTIME_FILE = "state/calendar_runtime.json"
STORY_LINES_FILE = "state/story_lines.json"
MAINTENANCE_RULES_FILE = "state/maintenance_rules_1206.json"
START_SCENE_PATH = "scenes/start_scene.md"
RENDER_CONTRACT_PATH = "gpt/scene_output_contract_1206.json"

ID_ALIASES = {
    "акира": "akira", "akira": "akira", "кира": "akira",
    "алекс": "alex", "alex": "alex",
    "эмма": "emma", "emma": "emma", "беловолосая девушка": "emma",
    "ирэй": "irey", "ирей": "irey", "irey": "irey", "беловолосый парень": "irey",
    "джун": "jun", "jun": "jun", "jun_carter": "jun",
    "кай": "kai", "kai": "kai",
    "мики": "miki", "miki": "miki",
    "рейден": "raiden", "рейдон": "raiden", "raiden": "raiden", "sterling": "raiden", "стерлинг": "raiden", "парень с пирсингом": "raiden",
    "рэй": "ray", "рей": "ray", "ray": "ray", "ray_carter": "ray", "мужчина в форме": "ray",
    "хару": "haru", "haru": "haru", "haru_foster": "haru",
    "широ": "shiro", "shiro": "shiro",
    "юна": "yuna", "yuna": "yuna",
}

VISIBLE_LABELS = {
    "emma": "беловолосая девушка",
    "irey": "беловолосый парень",
    "raiden": "парень с пирсингом",
    "ray": "мужчина в форме",
    "jun": "Джун",
    "akira": "Акира",
}

DIALOGUE_WORDS = (
    "объяс", "говор", "скажи", "спрос", "ответ", "почему", "зачем", "кто", "что ",
    "молчи", "треб", "слуш", "реплик", "назов", "давит", "угрож", "вопрос", "разговор",
    "сказал", "сказала", "посмотр", "обрат", "имя", "цель", "памят", "помн", "узна",
)


def _remove_route(path: str, method: str | None = None) -> None:
    method_upper = method.upper() if method else None
    for route in list(app.router.routes):
        if getattr(route, "path", None) != path:
            continue
        methods = set(getattr(route, "methods", set()) or set())
        if method_upper is None or method_upper in methods:
            app.router.routes.remove(route)


def _sid(session_id: str | None) -> str:
    return base.safe_session_id(session_id or "default")


def _payload(body: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(body, dict):
        return {}
    data = body.get("data")
    if isinstance(data, dict):
        merged = dict(data)
        for key, value in body.items():
            merged.setdefault(key, value)
        return merged
    return body


def _trim(value: Any, max_chars: int = 600) -> str:
    text = str(value or "")
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1].rstrip() + "…"


def _compact(value: Any, *, max_chars: int = 800, max_items: int = 8, depth: int = 2) -> Any:
    if depth <= 0:
        if isinstance(value, str):
            return _trim(value, max_chars)
        if isinstance(value, (int, float, bool)) or value is None:
            return value
        return _trim(json.dumps(value, ensure_ascii=False, sort_keys=True), max_chars)
    if isinstance(value, dict):
        preferred = [
            "id", "character_id", "display_name", "name", "role", "status", "summary",
            "current_status", "current_goal", "memory", "knows_as_fact", "знает_как_факт", "знает",
            "believes", "assumes", "предполагает", "suspects", "does_not_know", "не_знает",
            "wrong_beliefs", "ошибочно_считает", "seen", "saw", "видел", "видела", "heard",
            "слышал", "слышала", "future_hooks", "protected_moments", "rules",
        ]
        keys = [k for k in preferred if k in value]
        keys += [k for k in value.keys() if k not in keys]
        return {str(key): _compact(value[key], max_chars=max_chars, max_items=max_items, depth=depth - 1) for key in keys[:max_items]}
    if isinstance(value, list):
        return [_compact(item, max_chars=max_chars, max_items=max_items, depth=depth - 1) for item in value[:max_items]]
    if isinstance(value, str):
        return _trim(value, max_chars)
    return value


def _read_json(path: str, sid: str, default: Any) -> Any:
    try:
        value = base.read_json(path, session_id=sid, default=default)
        return default if value is None else value
    except Exception:
        return default


def _read_text(path: str, sid: str | None = None) -> str:
    try:
        return base.read_text(path, session_id=sid, default="") or ""
    except Exception:
        return ""


def _ensure_current(sid: str) -> dict[str, Any]:
    base.ensure_session(sid)
    current = _read_json(CURRENT_STATE_FILE, sid, {})
    if not isinstance(current, dict) or not current:
        current = base.initialize_start_session(sid, {"last_player_input": "начнем"})
    return current


def _canonical_id(value: Any) -> str:
    raw = str(value or "").strip()
    key = raw.lower().replace("ё", "е")
    return ID_ALIASES.get(key, key)


def _add_id(ids: list[str], value: Any) -> None:
    if isinstance(value, str):
        cid = _canonical_id(value)
        if cid and cid not in ids and cid not in {"none", "null", "unknown", "false"}:
            ids.append(cid)
    elif isinstance(value, list):
        for item in value:
            _add_id(ids, item)
    elif isinstance(value, dict):
        for key, item in value.items():
            if isinstance(item, dict):
                _add_id(ids, item.get("id") or item.get("character_id") or item.get("slug") or key)
            else:
                _add_id(ids, key)


def _scene_is_dialogue_or_pressure(player_input: str, scene_plan: dict[str, Any]) -> bool:
    text = (player_input or "").lower().replace("ё", "е")
    scene_type = str(scene_plan.get("scene_type") or scene_plan.get("type") or "").lower()
    if any(x in scene_type for x in ["dialog", "conversation", "talk", "разговор", "conflict", "pressure", "interrogation", "question"]):
        return True
    return any(word in text for word in DIALOGUE_WORDS)


def _yaml_section(text: str, key: str, *, max_chars: int = 1100) -> str:
    lines = text.splitlines()
    key_norm = key.strip().lower().replace("ё", "е")
    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or not stripped.lower().replace("ё", "е").startswith(f"{key_norm}:"):
            continue
        indent = len(line) - len(line.lstrip(" "))
        block = [line]
        for next_line in lines[index + 1:]:
            if not next_line.strip():
                block.append(next_line)
                continue
            next_indent = len(next_line) - len(next_line.lstrip(" "))
            next_stripped = next_line.strip()
            if next_indent <= indent and not next_stripped.startswith("-") and re.match(r"^[\wА-Яа-я_\-]+\s*:", next_stripped):
                break
            block.append(next_line)
        return _trim("\n".join(block), max_chars)
    return ""


def _sections(text: str, keys: list[str], *, max_chars: int = 2200, section_chars: int = 1000) -> str:
    blocks: list[str] = []
    seen: set[str] = set()
    for key in keys:
        block = _yaml_section(text, key, max_chars=section_chars)
        if block and block not in seen:
            seen.add(block)
            blocks.append(block)
    return _trim("\n\n".join(blocks), max_chars)


def _grep_lines(text: str, patterns: list[str], *, max_lines: int = 10, max_chars: int = 900) -> str:
    result: list[str] = []
    for line in text.splitlines():
        low = line.lower().replace("ё", "е")
        if any(pattern in low for pattern in patterns):
            clean = line.strip()
            if clean and clean not in result:
                result.append(clean)
        if len(result) >= max_lines:
            break
    return _trim("\n".join(result), max_chars)


def _list_from_memory(memory: dict[str, Any], *keys: str) -> list[Any]:
    result: list[Any] = []

    def collect_from(obj: Any) -> None:
        if not isinstance(obj, dict):
            return
        for key in keys:
            value = obj.get(key)
            if isinstance(value, list):
                result.extend(value)
            elif isinstance(value, dict):
                result.append(value)
            elif value:
                result.append(value)
        for nested_key in ["memory", "temporary_knowledge_state", "current_status"]:
            nested = obj.get(nested_key)
            if isinstance(nested, dict):
                collect_from(nested)

    collect_from(memory)
    out: list[Any] = []
    seen: set[str] = set()
    for item in result:
        marker = json.dumps(item, ensure_ascii=False, sort_keys=True) if isinstance(item, (dict, list)) else str(item)
        if marker and marker not in seen:
            seen.add(marker)
            out.append(item)
    return out


def _story_lines(sid: str) -> dict[str, Any]:
    data = _read_json(STORY_LINES_FILE, sid, {})
    if not isinstance(data, dict) or not data:
        data = {
            "schema": "story_lines_runtime_v3",
            "turn_counter": 0,
            "last_state_recovery_audit_turn": 0,
            "last_compaction_cleanup_turn": 0,
            "maintenance": {"state_recovery_audit_every": 10, "compaction_cleanup_every": 15, "compaction_cleanup_offset": 12},
        }
    return data


def _maintenance_slice(sid: str) -> dict[str, Any]:
    story = _story_lines(sid)
    turn = int(story.get("turn_counter") or 0)
    rules = _read_json(MAINTENANCE_RULES_FILE, sid, {})
    if not isinstance(rules, dict):
        rules = {}
    audit_due = bool(turn and turn % 10 == 0 and story.get("last_state_recovery_audit_turn") != turn)
    compact_due = bool(turn and turn % 15 == 12 and story.get("last_compaction_cleanup_turn") != turn)
    if audit_due and compact_due:
        compact_due = False
    return {
        "turn_counter": turn,
        "state_recovery_audit_due": audit_due,
        "state_recovery_audit_rule": "every 10 turns: check recent scene history for missed seen/heard/learned facts; do not invent hidden lore",
        "state_compaction_cleanup_due": compact_due,
        "state_compaction_cleanup_rule": "every 15 turns with offset 12: compact noise only; keep knowledge sources, emotional hooks and open promises",
        "recent_history_depth": 12 if audit_due or compact_due else 4,
        "source_file": MAINTENANCE_RULES_FILE if rules else None,
    }


def _current_state_slice(current: dict[str, Any]) -> dict[str, Any]:
    return {
        "current_scene_id": current.get("current_scene_id") or current.get("scene_id"),
        "current_date": current.get("current_date") or current.get("date"),
        "current_day_phase": current.get("current_day_phase") or current.get("time_of_day"),
        "current_location_id": current.get("current_location_id") or current.get("location_id"),
        "current_location_text": current.get("current_location_text") or current.get("location_text"),
        "pov_character_id": current.get("pov_character_id"),
        "active_character_ids": current.get("active_character_ids", []),
        "scene_character_ids": current.get("scene_character_ids", []),
        "present_character_ids": current.get("present_character_ids", []),
        "speaking_character_ids": current.get("speaking_character_ids", []),
        "addressed_character_ids": current.get("addressed_character_ids", []),
        "conditional_character_ids": current.get("conditional_character_ids", []),
        "relationship_pair_ids": current.get("relationship_pair_ids", []),
        "current_outfit": _trim(current.get("current_outfit"), 300),
        "engine_visible_inventory": _compact(current.get("visible_inventory", []), max_chars=420, max_items=8, depth=2),
        "engine_nearby_items": _compact(current.get("nearby_items", []), max_chars=420, max_items=8, depth=2),
        "scene_goal": _trim(current.get("scene_goal") or current.get("current_scene_goal"), 450),
        "last_player_input": _trim(current.get("last_player_input"), 320),
        "start_scene_exact_text_required": bool(current.get("start_scene_exact_text_required")),
        "start_scene_completed": bool(current.get("start_scene_completed")),
        "pov_context_rule": "POV character card must load for voice, inner limits, microreactions and player-control boundaries.",
        "boundary_note": "Engine inventory/nearby items are not NPC knowledge unless that NPC saw/heard them or got a source.",
    }


def _calendar_slice(sid: str, current: dict[str, Any]) -> dict[str, Any]:
    runtime = _read_json(CALENDAR_RUNTIME_FILE, sid, {})
    if not isinstance(runtime, dict):
        runtime = {}
    return {
        "current_date": current.get("current_date") or runtime.get("current_date"),
        "current_day_phase": current.get("current_day_phase") or runtime.get("current_day_phase"),
        "current_beat_id": runtime.get("current_beat_id") or current.get("current_beat_id"),
        "pending_events": _compact(runtime.get("pending_events", []), max_chars=550, max_items=6, depth=2),
        "rules": _compact(runtime.get("rules", []), max_chars=550, max_items=4, depth=2),
        "detail_level": "current_beat_plus_pending_guards",
    }


def _history_slice(sid: str, depth: int = 4) -> list[dict[str, Any]]:
    history = _read_json(SCENE_HISTORY_FILE, sid, [])
    if isinstance(history, dict):
        history = history.get("entries", [])
    if not isinstance(history, list):
        return []
    result = []
    for item in history[-depth:]:
        if isinstance(item, dict):
            result.append({
                "scene_id": item.get("scene_id") or item.get("id"),
                "player_input": _trim(item.get("player_input"), 180),
                "summary": _trim(item.get("summary") or item.get("visible_scene_text") or item.get("scene_text"), 520),
                "changed_files_snapshot": _compact(item.get("changed_files_snapshot", []), max_chars=320, max_items=5, depth=2),
            })
    return result


def _schedule_status() -> dict[str, Any]:
    files = [
        "schedule/base_weekly_schedule.yaml",
        "schedule/base_daily_routine.yaml",
        "schedule/squad_weekly_raids.yaml",
        "schedule/lessons_and_training_schedule.yaml",
        "schedule/staff_shifts.yaml",
        "schedule/location_activity_windows.yaml",
    ]
    empty = True
    present = []
    for path in files:
        text = _read_text(path)
        if text.strip():
            present.append(path)
        if "placeholder_empty" not in text and re.search(r"\n\s*[-\w]+:\s*(?!\[\]|placeholder_empty)", text):
            empty = False
    return {
        "status": "placeholder_empty" if empty else "configured_partial",
        "source_files": present,
        "rule": "If schedule files are placeholder_empty, do not invent raids, lessons, staff shifts or exact NPC locations.",
    }


def _location_slice(current: dict[str, Any], scene_plan: dict[str, Any]) -> dict[str, Any]:
    location_id = scene_plan.get("location_id") or scene_plan.get("target_location_id") or current.get("current_location_id") or current.get("location_id")
    text = current.get("current_location_text") or current.get("location_text") or str(location_id or "")
    lid = str(location_id or "").lower()
    if "room" in lid or "комнат" in text.lower():
        privacy = "private"
        default_activity = "quiet/private; background NPCs are not expected without a scene source"
    elif "cafeteria" in lid or "столов" in text.lower():
        privacy = "public"
        default_activity = "food service, background conversations, staff and passing raiders if schedule allows"
    elif "corridor" in lid or "корид" in text.lower():
        privacy = "semi_public_transition"
        default_activity = "movement, overheard voices, possible passersby depending on time"
    elif "training" in lid or "зал" in text.lower() or "корт" in text.lower():
        privacy = "functional_public"
        default_activity = "training only if schedule/location state allows"
    else:
        privacy = "unknown_or_custom"
        default_activity = "use current_state and explicit scene source only"
    return {
        "location_id": location_id,
        "location_text": text,
        "privacy_level": privacy,
        "detail_level": scene_plan.get("location_detail") or scene_plan.get("needs", {}).get("location", "basic"),
        "default_activity": default_activity,
        "sound_rule": "Do not add a click/creak/knock if the current scene says the door/floor opens silently.",
    }


def _collect_requested_character_ids(payload: dict[str, Any], current: dict[str, Any], player_input: str, scene_plan: dict[str, Any], expanded: bool) -> list[str]:
    ids: list[str] = []
    _add_id(ids, current.get("pov_character_id"))
    for key in ["speaking_character_ids", "addressed_character_ids", "looked_at_character_ids"]:
        _add_id(ids, current.get(key))
    for key in ["character_requests", "characters", "requested_characters", "character_ids", "speaking_character_ids", "addressed_character_ids", "present_character_ids"]:
        _add_id(ids, payload.get(key))
        _add_id(ids, scene_plan.get(key))
    _add_id(ids, current.get("scene_character_ids") or current.get("active_character_ids") or [])
    if _scene_is_dialogue_or_pressure(player_input, scene_plan):
        _add_id(ids, current.get("present_character_ids") or current.get("active_character_ids") or [])
    return ids[:8 if expanded else 6]


def _role_in_context(cid: str, current: dict[str, Any], player_input: str, scene_plan: dict[str, Any], request: dict[str, Any]) -> str:
    pov_id = _canonical_id(current.get("pov_character_id"))
    if cid == pov_id:
        return "pov"
    current_speaking = [_canonical_id(x) for x in (current.get("speaking_character_ids") or [])]
    current_addressed = [_canonical_id(x) for x in (current.get("addressed_character_ids") or [])]
    if cid in current_speaking or request.get("speech") or request.get("voice"):
        return "speaking"
    if cid in current_addressed:
        return "addressed"
    if _scene_is_dialogue_or_pressure(player_input, scene_plan) and cid in [_canonical_id(x) for x in (current.get("scene_character_ids") or [])]:
        return "reaction_relevant"
    if cid in [_canonical_id(x) for x in (current.get("present_character_ids") or current.get("active_character_ids") or [])]:
        return "present_active"
    return "referenced"


def _knowledge_boundary(cid: str, knowledge_text: str, memory: dict[str, Any], role: str, expanded: bool) -> dict[str, Any]:
    knows = _list_from_memory(memory, "knows_as_fact", "знает_как_факт", "knows", "known", "known_facts", "знает")
    believes = _list_from_memory(memory, "beliefs", "believes", "assumes", "предполагает", "suspects")
    wrong = _list_from_memory(memory, "wrong_beliefs", "misbelieves", "mistaken_beliefs", "ошибочно_считает", "wrongly_believes")
    does_not = _list_from_memory(memory, "does_not_know", "не_знает", "unknown", "knowledge_limits", "не знает")
    seen = _list_from_memory(memory, "seen", "saw", "видела", "видел", "observed")
    heard = _list_from_memory(memory, "heard", "слышала", "слышал")
    return {
        "source_files": [f"characters/{cid}/knowledge.yaml", f"state/character_memory/{cid}.json"],
        "role_in_context": role,
        "memory_facts": _compact(knows, max_chars=700 if not expanded else 950, max_items=8 if not expanded else 12, depth=2),
        "static_knowledge": _sections(
            knowledge_text,
            ["known_at_start", "stable_knows", "starting_knowledge", "about_akira", "about_raiden", "about_jun", "about_family_and_jun", "about_east_sector", "self", "akira", "emma", "samuel"],
            max_chars=1700 if not expanded else 2400,
            section_chars=900 if not expanded else 1300,
        ),
        "beliefs_or_assumptions": _compact(believes, max_chars=700, max_items=8, depth=2),
        "wrong_beliefs": _compact(wrong, max_chars=520, max_items=6, depth=2),
        "memory_unknowns": _compact(does_not, max_chars=800 if not expanded else 1100, max_items=9 if not expanded else 12, depth=2),
        "static_unknowns_and_withholds": _sections(
            knowledge_text,
            ["unknown_at_start", "stable_does_not_know", "does_not_know_at_start", "strict_unknowns", "withholds_from_akira", "withholds_from", "stable_hides_from", "known_name_rules"],
            max_chars=1500 if not expanded else 2200,
            section_chars=850 if not expanded else 1200,
        ),
        "reaction_and_disclosure_rules": _sections(
            knowledge_text,
            ["disclosure_rules", "question_response_rules", "inference_rules", "ability_detection_rules", "start_assumptions", "wrong_beliefs", "pov_rules"],
            max_chars=1100 if not expanded else 1700,
            section_chars=750 if not expanded else 1000,
        ),
        "seen_by_this_character": _compact(seen, max_chars=500, max_items=6, depth=2),
        "heard_by_this_character": _compact(heard, max_chars=500, max_items=6, depth=2),
        "rule": "Facts only from own static knowledge, own memory, seen/heard, scene source. Unknowns must produce checks/questions/evasions, not omniscience or flat passivity.",
    }


def _character_slice(sid: str, cid: str, request: Any, current: dict[str, Any], player_input: str, scene_plan: dict[str, Any], expanded: bool) -> dict[str, Any]:
    req = request if isinstance(request, dict) else {}
    needs = req.get("needs") if isinstance(req.get("needs"), dict) else {}
    main_path = f"characters/{cid}/main.yaml"
    char_path = f"characters/{cid}/character.yaml"
    know_path = f"characters/{cid}/knowledge.yaml"
    mem_path = f"state/character_memory/{cid}.json"
    main = _read_text(main_path, sid)
    character = _read_text(char_path, sid)
    knowledge = _read_text(know_path, sid)
    memory = _read_json(mem_path, sid, {})
    if not isinstance(memory, dict):
        memory = {}
    role = _role_in_context(cid, current, player_input, scene_plan, {**req, **needs})
    detail = expanded or role in {"pov", "speaking", "addressed", "reaction_relevant"} or bool(needs)
    result: dict[str, Any] = {
        "id": cid,
        "visible_label_default": VISIBLE_LABELS.get(cid, cid),
        "role_in_context": role,
        "source_files": {"main": main_path, "character": char_path, "knowledge": know_path, "memory": mem_path},
        "identity_basic": _sections(main, ["identity", "name", "role", "appearance", "name_use_rule"], max_chars=800, section_chars=500)
            or _grep_lines(main, ["имя", "name", "роль", "role", "возраст", "age", "рост", "height", "внеш", "appearance"], max_lines=8, max_chars=750),
    }
    if detail:
        result["character_core_behavior"] = _sections(
            character,
            ["core", "core_character", "character_goal", "role", "goals", "primary_goal", "personality", "voice", "speech", "voice_and_dialogue", "body_language", "behavior", "behavior_with_akira", "relationship_behavior", "arrival_reaction_rule", "inner_conflicts", "weaknesses", "command_style", "care_and_softness", "perception", "body_memory"],
            max_chars=2200 if expanded else 1600,
            section_chars=900 if expanded else 700,
        )
        result["reaction_triggers"] = _sections(
            character,
            ["relationship_behavior", "behavior_with_akira", "arrival_reaction_rule", "pain_rule", "weaknesses", "inner_conflicts", "relationship_rules", "player_control_rule", "empty_face_humor_and_poison", "forbidden"],
            max_chars=1400 if expanded else 1000,
            section_chars=700 if expanded else 550,
        )
        result["dynamic_memory_relevant"] = _compact(memory, max_chars=1200 if expanded else 850, max_items=10, depth=3)
    if role == "pov":
        result["pov_voice_and_player_control"] = _sections(
            character,
            ["voice", "empty_face_humor_and_poison", "body_language", "body_memory", "perception", "care_and_softness"],
            max_chars=2200 if expanded else 1700,
            section_chars=850 if expanded else 700,
        )
        result["pov_knowledge_limits"] = _knowledge_boundary(cid, knowledge, memory, role, expanded)
    elif detail or needs.get("knowledge"):
        result["knowledge_boundary"] = _knowledge_boundary(cid, knowledge, memory, role, expanded)
    if role in {"pov", "speaking", "addressed", "reaction_relevant"} or needs.get("energy") or needs.get("combat"):
        result["ability_or_combat_if_relevant"] = _sections(
            character,
            ["energy", "energy_behavior", "abilities", "combat_style", "combat_behavior"],
            max_chars=1100 if not expanded else 1600,
            section_chars=700 if not expanded else 900,
        )
    return result


def _relationship_slices(sid: str, payload: dict[str, Any], current: dict[str, Any], expanded: bool) -> dict[str, Any]:
    scene_plan = payload.get("scene_plan") if isinstance(payload.get("scene_plan"), dict) else {}
    rel_need = scene_plan.get("relationships") or scene_plan.get("needs", {}).get("relationships") or payload.get("relationships")
    if rel_need in [False, "false", "none", "no"]:
        return {}
    pair_ids = payload.get("relationship_pair_ids") or scene_plan.get("relationship_pair_ids") or current.get("relationship_pair_ids", [])
    result: dict[str, Any] = {}
    for pair in list(pair_ids)[:7 if expanded else 5]:
        pid = str(pair or "").strip()
        if "__" not in pid:
            continue
        path = f"state/relationship_pairs/{pid}.json"
        data = _read_json(path, sid, {})
        if isinstance(data, dict) and data:
            result[pid] = {
                "source_file": path,
                "surface_and_reaction_slice": _compact(data, max_chars=1600 if expanded else 1100, max_items=12, depth=3),
            }
    return result


def _extract_start_scene_text() -> str:
    text = _read_text(START_SCENE_PATH)
    match = re.search(r"```text\s*(.*?)```", text, flags=re.S)
    return match.group(1).strip() if match else text.strip()


def _render_contract() -> dict[str, Any]:
    data = _read_json(RENDER_CONTRACT_PATH, "default", {})
    if not isinstance(data, dict) or not data:
        return {
            "source_file": RENDER_CONTRACT_PATH,
            "must_be_last_writer_instruction": True,
            "dialogue_format_required": "**Имя** — реплика.",
            "bottom_blocks": ["Что можно сделать", "Что Акира могла бы сказать", "Мысли Акиры", "Состояние", "Риск/Отношения по факту"],
        }
    data.setdefault("source_file", RENDER_CONTRACT_PATH)
    data["must_be_last_writer_instruction"] = True
    return data


def _context_rules() -> dict[str, Any]:
    return {
        "pov": "POV character is always loaded for voice, microreactions, knowledge limits and player-control boundaries.",
        "characters": "Active/speaking/reaction-relevant characters receive behavior + knowledge + memory slices, not full YAML dumps.",
        "npc_knowledge": "NPC dialogue facts come only from own static knowledge, own memory, seen/heard facts or scene source.",
        "unknowns": "Unknowns do not make NPC passive; they cause questions, pauses, checks, lies, evasions or wrong assumptions according to character behavior.",
        "relationships": "Relationship pair slices drive visible tension/control/trust changes.",
        "past_energy_lore": "Past/deep lore remains blocked unless the scene explicitly triggers it.",
    }


_remove_route("/api/v3/sessions/{session_id}/preflight", "GET")


@app.get("/api/v3/sessions/{session_id}/preflight", operation_id="getPreflight")
def get_preflight(session_id: str) -> dict[str, Any]:
    sid = _sid(session_id)
    current = _ensure_current(sid)
    maintenance = _maintenance_slice(sid)
    return {
        "success": True,
        "session_id": sid,
        "runtime_version": RUNTIME_VERSION,
        "mode": "v3_preflight_character_balanced",
        "current_state": _current_state_slice(current),
        "calendar": _calendar_slice(sid, current),
        "recent_scene_history": _history_slice(sid, int(maintenance.get("recent_history_depth") or 4)),
        "schedule_status": _schedule_status(),
        "maintenance": maintenance,
        "next_action": "requestContextSlice",
        "planner_checklist": [
            "POV card must be present in context_slice.characters.",
            "If an NPC may speak or react, keep them in scene_character_ids/speaking/addressed so behavior and knowledge limits load.",
            "If a character does not know something, play a character-specific reaction instead of silence or accidental omniscience.",
        ],
    }


_remove_route("/api/v3/sessions/{session_id}/context-request", "POST")
_remove_route("/api/v3/sessions/{session_id}/context-more", "POST")


@app.post("/api/v3/sessions/{session_id}/context-request", operation_id="requestContextSlice")
def request_context_slice(session_id: str, body: dict[str, Any] | None = Body(default=None)) -> dict[str, Any]:
    sid = _sid(session_id)
    current = _ensure_current(sid)
    payload = _payload(body)
    scene_plan = payload.get("scene_plan") if isinstance(payload.get("scene_plan"), dict) else {}
    explicit_input = payload.get("player_input") or payload.get("user_input") or scene_plan.get("player_input") or scene_plan.get("user_input")
    player_input = _trim(explicit_input or current.get("last_player_input"), 400)
    expanded = bool(payload.get("expanded_context") or scene_plan.get("expanded_context") or payload.get("requested_blocks"))
    dialogue_scene = _scene_is_dialogue_or_pressure(player_input, scene_plan)
    char_requests = payload.get("character_requests") or scene_plan.get("character_requests") or scene_plan.get("characters") or payload.get("characters") or {}
    if not isinstance(char_requests, dict):
        char_requests = {}
    cids = _collect_requested_character_ids(payload, current, player_input, scene_plan, expanded)
    characters = {
        cid: _character_slice(sid, cid, char_requests.get(cid, {}), current, player_input, scene_plan, expanded)
        for cid in cids
    }
    maintenance = _maintenance_slice(sid)
    history_depth = int(maintenance.get("recent_history_depth") or (6 if expanded else 4 if dialogue_scene else 3))
    start_scene_available = bool(current.get("start_scene_exact_text_required") and not current.get("start_scene_completed"))
    input_warning = None
    if not explicit_input and not start_scene_available:
        input_warning = "No explicit player_input/user_input; used current_state.last_player_input. Ordinary turns must pass exact input."
    return {
        "success": True,
        "session_id": sid,
        "runtime_version": RUNTIME_VERSION,
        "mode": "v3_context_slice_character_balanced_expanded" if expanded else "v3_context_slice_character_balanced",
        "player_input": player_input,
        "input_consistency": {"explicit_input_received": bool(explicit_input), "warning": input_warning},
        "scene_plan_echo": _compact(scene_plan, max_chars=800, max_items=8, depth=3),
        "context_slice": {
            "current_state": _current_state_slice(current),
            "calendar": _calendar_slice(sid, current),
            "location": _location_slice(current, scene_plan),
            "schedule": _schedule_status(),
            "maintenance": maintenance,
            "recent_scene_history": _history_slice(sid, history_depth),
            "characters": characters,
            "relationships": _relationship_slices(sid, payload, current, expanded),
            "ambient_npcs": {
                "status": "placeholder_empty",
                "rule": "Ambient NPCs exist only after npcs/ambient is populated or current_state explicitly creates them.",
            },
            "start_scene": {
                "exact_text_required": start_scene_available,
                "text_endpoint": f"/api/v3/sessions/{sid}/start-scene-text" if start_scene_available else None,
                "rule": "If exact_text_required=true, call getStartSceneText and output that text as the first scene before normal play.",
            },
            "blocked_blocks": {
                "past": "blocked unless hard past trigger is present and requested",
                "energy": "loaded as hint for relevant characters; full lore blocked unless combat/training/overload/energy lesson/application is present",
                "deep_lore": "blocked unless explicit question, explanation, or story-critical trigger",
                "full_character_yaml": "blocked for Actions; semantic sections are loaded instead",
            },
            "rules": _context_rules(),
            "knowledge_boundary_contract": {
                "rule": "Before each NPC line/reaction, check that character's knowledge_boundary and reaction_triggers. Unknowns should produce questions/checks/evasions, not flat silence.",
                "engine_state_is_not_npc_knowledge": True,
                "unknown_names_use_visible_labels": VISIBLE_LABELS,
            },
            "final_render_contract": _render_contract(),
        },
        "size_guard": "character-balanced slice; larger than v3 slim but still sectioned and bounded",
        "next_action": "writeScene" if not start_scene_available else "getStartSceneText",
    }


@app.post("/api/v3/sessions/{session_id}/context-more", operation_id="requestMoreContext")
def request_more_context(session_id: str, body: dict[str, Any] | None = Body(default=None)) -> dict[str, Any]:
    payload = _payload(body)
    payload["expanded_context"] = True
    return request_context_slice(session_id, payload)


_remove_route("/api/v3/sessions/{session_id}/start-scene-text", "GET")


@app.get("/api/v3/sessions/{session_id}/start-scene-text", operation_id="getStartSceneText")
def get_start_scene_text(session_id: str) -> dict[str, Any]:
    sid = _sid(session_id)
    current = _ensure_current(sid)
    required = bool(current.get("start_scene_exact_text_required") and not current.get("start_scene_completed"))
    return {
        "success": True,
        "session_id": sid,
        "runtime_version": RUNTIME_VERSION,
        "mode": "v3_start_scene_text",
        "exact_text_required": required,
        "exact_text": _extract_start_scene_text() if required else "",
        "after_output_instruction": "After outputting exact_text, wait for the player. On the next player input, call processTurn; processTurn will mark start_scene_completed.",
    }


_remove_route("/api/v2/sessions/{session_id}/scene-contract", "GET")
_remove_route("/api/v2/sessions/{session_id}/turn-packet", "GET")
_remove_route("/api/v2/sessions/{session_id}/debug/context-audit", "GET")


@app.get("/api/v2/sessions/{session_id}/scene-contract", operation_id="getSceneContract")
def get_scene_contract_action_safe(session_id: str) -> dict[str, Any]:
    sid = _sid(session_id)
    current = _ensure_current(sid)
    return {
        "success": True,
        "session_id": sid,
        "runtime_version": RUNTIME_VERSION,
        "mode": "deprecated_scene_contract_action_safe_pointer",
        "current_state": _current_state_slice(current),
        "message": "Full scene_contract is disabled for Actions. Use getPreflight then requestContextSlice.",
        "next_action": "getPreflight",
    }


@app.get("/api/v2/sessions/{session_id}/turn-packet", operation_id="getTurnPacket")
def get_turn_packet_action_safe(session_id: str) -> dict[str, Any]:
    return get_scene_contract_action_safe(session_id)


@app.get("/api/v2/sessions/{session_id}/debug/context-audit", operation_id="getContextAudit")
def get_context_audit_action_safe(session_id: str) -> dict[str, Any]:
    sid = _sid(session_id)
    current = _ensure_current(sid)
    return {
        "success": True,
        "session_id": sid,
        "runtime_version": RUNTIME_VERSION,
        "mode": "v3_action_safe_context_audit_character_balanced",
        "current_state": _current_state_slice(current),
        "short_character_summaries_used": False,
        "legacy_character_memory_path_used": False,
        "full_scene_contract_actions_disabled": True,
        "pov_character_must_load": True,
        "preflight_endpoint": f"/api/v3/sessions/{sid}/preflight",
        "context_request_endpoint": f"/api/v3/sessions/{sid}/context-request",
        "schedule_status": _schedule_status(),
        "maintenance": _maintenance_slice(sid),
    }


try:
    app.version = RUNTIME_VERSION
except Exception:
    pass
