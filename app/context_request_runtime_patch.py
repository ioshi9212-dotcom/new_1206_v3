"""Action-safe preflight/context-request pipeline for Akira 1206 v3.

The full cards stay on Railway. Custom GPT receives small semantic slices only.
This version restores the v2 principle of permanent + temporary knowledge:
if an NPC can speak/answer/pressure in the scene, their knowledge boundary is
loaded automatically. The final render contract is returned last in context_slice.
"""
from __future__ import annotations

import json
import re
from datetime import datetime
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
    return body if isinstance(body, dict) else {}


def _trim(value: Any, max_chars: int = 600) -> str:
    text = str(value or "")
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1].rstrip() + "…"


def _compact(value: Any, *, max_chars: int = 700, max_items: int = 6, depth: int = 2) -> Any:
    if depth <= 0:
        return _trim(value, max_chars)
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        preferred = [
            "id", "character_id", "display_name", "name", "role", "status", "current_date", "current_day_phase",
            "current_location_id", "pov_character_id", "active_character_ids", "scene_character_ids",
            "relationship_pair_ids", "summary", "current_beat_id", "pending_events", "rules",
            "last_player_input", "scene_goal", "knows_as_fact", "знает_как_факт", "believes", "assumes",
            "предполагает", "does_not_know", "не_знает", "wrong_beliefs", "ошибочно_считает",
        ]
        keys = [k for k in preferred if k in value] + [k for k in value.keys() if k not in preferred]
        for key in keys[:max_items]:
            result[str(key)] = _compact(value[key], max_chars=max_chars, max_items=max_items, depth=depth - 1)
        return result
    if isinstance(value, list):
        return [_compact(item, max_chars=max_chars, max_items=max_items, depth=depth - 1) for item in value[:max_items]]
    if isinstance(value, str):
        return _trim(value, max_chars)
    return value


def _read_json(path: str, sid: str, default: Any) -> Any:
    value = base.read_json(path, session_id=sid, default=default)
    return default if value is None else value


def _read_text(path: str, sid: str | None = None) -> str:
    return base.read_text(path, session_id=sid, default="") or ""


def _ensure_current(sid: str) -> dict[str, Any]:
    base.ensure_session(sid)
    current = _read_json(CURRENT_STATE_FILE, sid, {})
    if not isinstance(current, dict) or not current:
        current = base.initialize_start_session(sid, {"last_player_input": "начнем"})
    return current


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
        "recent_history_depth": 15 if audit_due or compact_due else 3,
        "source_file": MAINTENANCE_RULES_FILE if rules else None,
    }


def _current_state_slice(current: dict[str, Any]) -> dict[str, Any]:
    # Keep engine facts compact. NPC knowledge is supplied separately in per-character knowledge_boundary.
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
        "engine_visible_inventory": _compact(current.get("visible_inventory", []), max_chars=400, max_items=8, depth=2),
        "engine_nearby_items": _compact(current.get("nearby_items", []), max_chars=400, max_items=8, depth=2),
        "scene_goal": _trim(current.get("scene_goal") or current.get("current_scene_goal"), 450),
        "last_player_input": _trim(current.get("last_player_input"), 320),
        "start_scene_exact_text_required": bool(current.get("start_scene_exact_text_required")),
        "start_scene_completed": bool(current.get("start_scene_completed")),
        "boundary_note": "engine_visible_inventory/nearby_items are scene/POV/engine facts; NPCs may not know them unless their knowledge_boundary says so or they saw/heard them in-scene.",
    }


def _calendar_slice(sid: str, current: dict[str, Any]) -> dict[str, Any]:
    runtime = _read_json(CALENDAR_RUNTIME_FILE, sid, {})
    if not isinstance(runtime, dict):
        runtime = {}
    return {
        "current_date": current.get("current_date") or runtime.get("current_date"),
        "current_day_phase": current.get("current_day_phase") or runtime.get("current_day_phase"),
        "current_beat_id": runtime.get("current_beat_id") or current.get("current_beat_id"),
        "pending_events": _compact(runtime.get("pending_events", []), max_chars=500, max_items=6, depth=2),
        "rules": _compact(runtime.get("rules", []), max_chars=500, max_items=4, depth=2),
        "detail_level": "current_beat_only",
    }


def _history_slice(sid: str, depth: int = 3) -> list[dict[str, Any]]:
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
                "summary": _trim(item.get("summary") or item.get("visible_scene_text") or item.get("scene_text"), 450),
                "changed_files_snapshot": _compact(item.get("changed_files_snapshot", []), max_chars=300, max_items=4, depth=2),
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
        "rules": [
            "Load visual details when POV enters, observes, or the location changes.",
            "Load location rules only if conflict/noise/security/access matters.",
            "Load location lore only if explicitly asked or scene-critical.",
        ],
    }


def _canonical_id(value: Any) -> str:
    raw = str(value or "").strip()
    key = raw.lower().replace("ё", "е")
    return ID_ALIASES.get(key, key)


def _add_id(ids: list[str], value: Any) -> None:
    if isinstance(value, str):
        cid = _canonical_id(value)
        if cid and cid not in ids and cid not in {"none", "null", "unknown"}:
            ids.append(cid)
    elif isinstance(value, list):
        for x in value:
            _add_id(ids, x)
    elif isinstance(value, dict):
        for k, item in value.items():
            if isinstance(item, dict):
                _add_id(ids, item.get("id") or item.get("character_id") or k)
            else:
                _add_id(ids, k)


def _scene_is_dialogue_or_pressure(player_input: str, scene_plan: dict[str, Any]) -> bool:
    text = (player_input or "").lower().replace("ё", "е")
    scene_type = str(scene_plan.get("scene_type") or scene_plan.get("type") or "").lower()
    if any(x in scene_type for x in ["dialog", "conversation", "talk", "разговор", "conflict", "pressure", "interrogation", "question"]):
        return True
    return any(w in text for w in DIALOGUE_WORDS)


def _collect_requested_character_ids(payload: dict[str, Any], current: dict[str, Any], player_input: str = "", scene_plan: dict[str, Any] | None = None) -> list[str]:
    scene_plan = scene_plan or {}
    ids: list[str] = []
    for key in ["character_requests", "characters", "requested_characters", "character_ids", "speaking_character_ids", "addressed_character_ids"]:
        _add_id(ids, payload.get(key))
        _add_id(ids, scene_plan.get(key))
    # Always include explicit current speaking/addressed characters.
    for key in ["speaking_character_ids", "addressed_character_ids", "looked_at_character_ids"]:
        _add_id(ids, current.get(key))
    # For exact first scene, only return reference identities. The exact text wins.
    if not ids and current.get("start_scene_exact_text_required") and not current.get("start_scene_completed"):
        _add_id(ids, current.get("scene_character_ids", [])[:4])
    # For dialogue/pressure after start, include active scene characters so knowledge boundaries are available.
    if (not ids or _scene_is_dialogue_or_pressure(player_input, scene_plan)) and not (current.get("start_scene_exact_text_required") and not current.get("start_scene_completed")):
        _add_id(ids, current.get("scene_character_ids") or current.get("active_character_ids") or [])
    return ids[:8]


def _grep_lines(text: str, patterns: list[str], *, max_lines: int = 10, max_chars: int = 900) -> str:
    lines = []
    for line in text.splitlines():
        low = line.lower().replace("ё", "е")
        if any(p in low for p in patterns):
            clean = line.strip()
            if clean and clean not in lines:
                lines.append(clean)
        if len(lines) >= max_lines:
            break
    return _trim("\n".join(lines), max_chars)


def _list_from_memory(memory: dict[str, Any], *keys: str) -> list[Any]:
    result: list[Any] = []
    for key in keys:
        value = memory.get(key)
        if isinstance(value, list):
            result.extend(value)
        elif isinstance(value, dict):
            # common nested dynamic schema
            for nested_key in keys:
                nv = value.get(nested_key)
                if isinstance(nv, list):
                    result.extend(nv)
        elif value:
            result.append(value)
    nested = memory.get("memory")
    if isinstance(nested, dict):
        for key in keys:
            value = nested.get(key)
            if isinstance(value, list):
                result.extend(value)
            elif value:
                result.append(value)
    # de-dupe by string representation
    out: list[Any] = []
    seen: set[str] = set()
    for item in result:
        key = json.dumps(item, ensure_ascii=False, sort_keys=True) if isinstance(item, (dict, list)) else str(item)
        if key and key not in seen:
            seen.add(key)
            out.append(item)
    return out


def _temporary_knowledge_boundary(cid: str, knowledge_text: str, memory: dict[str, Any], presence: str) -> dict[str, Any]:
    knows = _list_from_memory(memory, "knows_as_fact", "знает_как_факт", "knows", "known", "known_facts")
    believes = _list_from_memory(memory, "beliefs", "believes", "assumes", "предполагает", "suspects")
    wrong = _list_from_memory(memory, "wrong_beliefs", "misbelieves", "mistaken_beliefs", "ошибочно_считает", "wrongly_believes")
    does_not = _list_from_memory(memory, "does_not_know", "не_знает", "unknown", "knowledge_limits")
    saw = _list_from_memory(memory, "seen", "saw", "видела", "видел", "observed")
    heard = _list_from_memory(memory, "heard", "слышала", "слышал")
    static_unknown = _grep_lines(knowledge_text, ["не знает", "не зна", "unknown", "does_not_know", "нельзя", "forbidden"], max_lines=8, max_chars=900)
    static_known = _grep_lines(knowledge_text, ["знает", "known", "own knowledge", "видел", "слышал"], max_lines=8, max_chars=900)
    forbidden = list(does_not)
    if cid in {"emma", "irey"}:
        forbidden += [
            "Рэй / Восточный сектор как факт без того, что персонаж увидел записку или услышал это вслух",
            "документы Акацуми как факт без прямого наблюдения",
            "амнезия Акиры как факт до наблюдения/источника",
            "связь Джуна и Акиры как факт до источника",
        ]
    if cid == "jun":
        forbidden += [
            "имя Эммы как факт до представления/источника",
            "имя Ирэя как факт до представления/источника",
            "точные цели пришедших как факт до источника",
        ]
    return {
        "source_files": [f"characters/{cid}/knowledge.yaml", f"state/character_memory/{cid}.json"],
        "load_reason": "speaking/answering/pressure NPC requires knowledge boundary" if presence == "speaking" else f"presence={presence}",
        "can_say_as_fact": _compact(knows, max_chars=800, max_items=8, depth=2),
        "static_known_hints": static_known,
        "believes_or_assumes": _compact(believes, max_chars=900, max_items=8, depth=2),
        "wrong_beliefs": _compact(wrong, max_chars=700, max_items=6, depth=2),
        "does_not_know": _compact(does_not, max_chars=1000, max_items=10, depth=2),
        "static_unknown_hints": static_unknown,
        "seen_by_this_character": _compact(saw, max_chars=700, max_items=6, depth=2),
        "heard_by_this_character": _compact(heard, max_chars=700, max_items=6, depth=2),
        "forbidden_as_fact": _compact(forbidden, max_chars=1200, max_items=12, depth=2),
        "rule": "Before any NPC line, use can_say_as_fact/seen/heard only. Engine state, POV inventory, calendar and other character cards are not this NPC's knowledge.",
    }


def _character_slice(sid: str, cid: str, request: Any, current: dict[str, Any], player_input: str = "", scene_plan: dict[str, Any] | None = None) -> dict[str, Any]:
    if not cid:
        return {}
    scene_plan = scene_plan or {}
    main_path = f"characters/{cid}/main.yaml"
    char_path = f"characters/{cid}/character.yaml"
    know_path = f"characters/{cid}/knowledge.yaml"
    mem_path = f"state/character_memory/{cid}.json"
    main = _read_text(main_path, sid)
    char = _read_text(char_path, sid)
    knowledge = _read_text(know_path, sid)
    memory = _read_json(mem_path, sid, {})
    if not isinstance(memory, dict):
        memory = {}
    req = request if isinstance(request, dict) else {}
    needs = req.get("needs") if isinstance(req.get("needs"), dict) else {}
    presence = req.get("presence_level") or req.get("level") or "background_visible"
    current_speaking = [_canonical_id(x) for x in (current.get("speaking_character_ids") or [])]
    current_addressed = [_canonical_id(x) for x in (current.get("addressed_character_ids") or [])]
    dialogue_scene = _scene_is_dialogue_or_pressure(player_input, scene_plan)
    if cid in current_speaking or needs.get("speech") or needs.get("voice"):
        presence = "speaking"
    elif cid in current_addressed or (dialogue_scene and cid != current.get("pov_character_id") and cid in [_canonical_id(x) for x in current.get("scene_character_ids", [])]):
        presence = "speaking"
    # Start scene exact text means the writer should use exact text, not character depth.
    if current.get("start_scene_exact_text_required") and not current.get("start_scene_completed"):
        presence = "start_scene_reference_only"
    result: dict[str, Any] = {
        "id": cid,
        "visible_label_default": VISIBLE_LABELS.get(cid, cid),
        "presence_level": presence,
        "source_files": {"main": main_path, "character": char_path, "knowledge": know_path, "memory": mem_path},
        "identity_basic": _grep_lines(main, ["name", "имя", "age", "возраст", "role", "роль", "height", "рост"], max_lines=10, max_chars=900),
        "appearance_basic": _grep_lines(main, ["appearance", "внеш", "hair", "волос", "eyes", "глаз", "body", "телослож", "кожа", "одеж"], max_lines=8, max_chars=800),
    }
    if presence in {"speaking", "emotionally_relevant", "combat_or_energy"} or needs.get("voice") or needs.get("behavior"):
        result["voice_and_behavior"] = _grep_lines(char, ["voice", "speech", "style", "характер", "поведен", "голос", "речь", "habit"], max_lines=14, max_chars=1400)
    # Key change: speaking NPC always receives knowledge + memory boundary.
    if presence in {"speaking", "emotionally_relevant"} or needs.get("knowledge"):
        result["knowledge_relevant_guard"] = _grep_lines(knowledge, ["known", "unknown", "зна", "не зна", "must", "forbid", "нельзя"], max_lines=16, max_chars=1700)
        result["temporary_knowledge_state"] = _compact(memory, max_chars=1600, max_items=10, depth=2)
        result["knowledge_boundary"] = _temporary_knowledge_boundary(cid, knowledge, memory, "speaking" if presence == "speaking" else presence)
    elif needs.get("memory"):
        result["temporary_knowledge_state"] = _compact(memory, max_chars=1200, max_items=8, depth=2)
    if presence in {"combat_or_energy"} or needs.get("energy") or needs.get("combat"):
        result["energy_or_combat_hint"] = _grep_lines(char, ["energy", "энерг", "combat", "бой", "overload", "перегруз", "weak", "слаб"], max_lines=14, max_chars=1400)
    result["blocked_by_default"] = {
        "past": "not included unless explicit past trigger and API approval",
        "full_yaml": "not returned to Actions; use block slices only",
    }
    return result


def _relationship_slices(sid: str, payload: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    scene_plan = payload.get("scene_plan") if isinstance(payload.get("scene_plan"), dict) else {}
    rel_need = scene_plan.get("relationships") or scene_plan.get("needs", {}).get("relationships") or payload.get("relationships")
    if rel_need in [False, "false", "none", "no"]:
        return {}
    pair_ids = payload.get("relationship_pair_ids") or scene_plan.get("relationship_pair_ids") or current.get("relationship_pair_ids", [])[:6]
    result: dict[str, Any] = {}
    for pair in pair_ids[:6]:
        pid = str(pair or "").strip()
        if "__" not in pid:
            continue
        path = f"state/relationship_pairs/{pid}.json"
        data = _read_json(path, sid, {})
        if isinstance(data, dict) and data:
            result[pid] = {"source_file": path, "surface_slice": _compact(data, max_chars=1100, max_items=10, depth=2)}
    return result


def _extract_start_scene_text() -> str:
    text = _read_text(START_SCENE_PATH)
    m = re.search(r"```text\s*(.*?)```", text, flags=re.S)
    if m:
        return m.group(1).strip()
    return text.strip()


def _render_contract() -> dict[str, Any]:
    data = _read_json(RENDER_CONTRACT_PATH, "default", {})
    if not isinstance(data, dict) or not data:
        return {
            "source_file": RENDER_CONTRACT_PATH,
            "must_be_last_writer_instruction": True,
            "required_header": "🌘 Восточный сектор · 1206 г., {date}\n🕒 {phase} · 📍 {location}\n⚙️ Активное состояние сцены: {tension}\n✦ POV: {pov} · {visible_state}\n🧥 {outfit}\n◈ {items}\n━━━━━━━━━━━━━━━━━━━━",
            "dialogue_format_required": "**Имя** — реплика.",
            "bottom_blocks": ["Что можно сделать", "Что Акира могла бы сказать", "Мысли Акиры", "Состояние", "Риск/Отношения по факту"],
        }
    data.setdefault("source_file", RENDER_CONTRACT_PATH)
    data["must_be_last_writer_instruction"] = True
    return data


def _context_rules() -> dict[str, Any]:
    return {
        "state": "always include active current_state slice",
        "location": "basic always; visual/rules/lore only if requested by scene_plan",
        "schedule": "check schedule files; if placeholder_empty, do not invent availability",
        "characters": "return block slices by presence level, never full YAML dumps",
        "speaking_npc_knowledge": "if an NPC speaks/answers/pressures, include knowledge.yaml + character_memory boundary automatically",
        "relationships": "surface pairs included when relationship_pair_ids exist unless explicitly disabled",
        "energy": "only combat, training, overload, lecture, or explicit energy trigger",
        "past": "only hard past trigger; never because of ordinary start words",
    }


@app.get("/api/v3/sessions/{session_id}/preflight", operation_id="getPreflight")
def get_preflight(session_id: str) -> dict[str, Any]:
    sid = _sid(session_id)
    current = _ensure_current(sid)
    maintenance = _maintenance_slice(sid)
    return {
        "success": True,
        "session_id": sid,
        "runtime_version": RUNTIME_VERSION,
        "mode": "v3_preflight_light",
        "current_state": _current_state_slice(current),
        "calendar": _calendar_slice(sid, current),
        "recent_scene_history": _history_slice(sid, int(maintenance.get("recent_history_depth") or 3)),
        "schedule_status": _schedule_status(),
        "maintenance": maintenance,
        "next_action": "requestContextSlice",
        "planner_checklist": [
            "determine scene_type before requesting context blocks",
            "if any NPC may speak, mark them speaking or let dialogue-scene auto-load knowledge boundary",
            "do not use engine_visible_inventory as NPC knowledge without seen/heard source",
        ],
    }


@app.post("/api/v3/sessions/{session_id}/context-request", operation_id="requestContextSlice")
def request_context_slice(session_id: str, body: dict[str, Any] | None = Body(default=None)) -> dict[str, Any]:
    sid = _sid(session_id)
    current = _ensure_current(sid)
    payload = _payload(body)
    scene_plan = payload.get("scene_plan") if isinstance(payload.get("scene_plan"), dict) else {}
    player_input = _trim(payload.get("player_input") or payload.get("user_input") or scene_plan.get("player_input") or scene_plan.get("user_input") or current.get("last_player_input"), 400)
    char_requests = payload.get("character_requests") or scene_plan.get("character_requests") or scene_plan.get("characters") or payload.get("characters") or {}
    if not isinstance(char_requests, dict):
        char_requests = {}
    cids = _collect_requested_character_ids(payload, current, player_input, scene_plan)
    character_slices = {}
    for cid in cids:
        character_slices[cid] = _character_slice(sid, cid, char_requests.get(cid, {}), current, player_input, scene_plan)
    start_scene_available = bool(current.get("start_scene_exact_text_required") and not current.get("start_scene_completed"))
    maintenance = _maintenance_slice(sid)
    input_warning = None
    if not (payload.get("player_input") or payload.get("user_input") or scene_plan.get("player_input") or scene_plan.get("user_input")) and not start_scene_available:
        input_warning = "requestContextSlice used current_state.last_player_input because no explicit player_input/user_input was provided; ordinary turns should pass the exact player input."
    return {
        "success": True,
        "session_id": sid,
        "runtime_version": RUNTIME_VERSION,
        "mode": "v3_context_slice_light",
        "player_input": player_input,
        "input_consistency": {
            "explicit_input_received": bool(payload.get("player_input") or payload.get("user_input") or scene_plan.get("player_input") or scene_plan.get("user_input")),
            "warning": input_warning,
        },
        "scene_plan_echo": _compact(scene_plan, max_chars=900, max_items=8, depth=3),
        "context_slice": {
            "current_state": _current_state_slice(current),
            "calendar": _calendar_slice(sid, current),
            "location": _location_slice(current, scene_plan),
            "schedule": _schedule_status(),
            "maintenance": maintenance,
            "recent_scene_history": _history_slice(sid, int(maintenance.get("recent_history_depth") or 3)),
            "characters": character_slices,
            "relationships": _relationship_slices(sid, payload, current),
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
                "energy": "blocked unless combat/training/overload/energy lesson/application is present",
                "deep_lore": "blocked unless explicit question, explanation, or story-critical trigger",
                "full_character_yaml": "blocked for Actions; use semantic slices only",
            },
            "rules": _context_rules(),
            # Keep this near the end so the writer sees knowledge boundaries after facts.
            "knowledge_boundary_contract": {
                "rule": "NPC dialogue must use only that NPC's can_say_as_fact/seen/heard. Beliefs are not facts; does_not_know/forbidden_as_fact cannot be stated as knowledge.",
                "engine_state_is_not_npc_knowledge": True,
                "unknown_names_use_visible_labels": VISIBLE_LABELS,
            },
            # Must remain the last writer-facing instruction inside context_slice.
            "final_render_contract": _render_contract(),
        },
        "next_action": "writeScene" if not start_scene_available else "getStartSceneText",
    }


@app.post("/api/v3/sessions/{session_id}/context-more", operation_id="requestMoreContext")
def request_more_context(session_id: str, body: dict[str, Any] | None = Body(default=None)) -> dict[str, Any]:
    payload = _payload(body)
    return request_context_slice(session_id, payload)


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
        "message": "Full scene_contract is disabled for Actions to avoid ResponseTooLargeError. Use getPreflight then requestContextSlice.",
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
        "mode": "v3_action_safe_context_audit",
        "current_state": _current_state_slice(current),
        "short_character_summaries_used": False,
        "legacy_character_memory_path_used": False,
        "full_scene_contract_actions_disabled": True,
        "preflight_endpoint": f"/api/v3/sessions/{sid}/preflight",
        "context_request_endpoint": f"/api/v3/sessions/{sid}/context-request",
        "schedule_status": _schedule_status(),
        "maintenance": _maintenance_slice(sid),
    }


try:
    app.version = RUNTIME_VERSION
except Exception:
    pass
