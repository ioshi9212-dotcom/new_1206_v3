"""Hybrid manifest/chunk context pipeline for Akira 1206 v3.

Single replacement file. No extra runtime layers.

Principle:
- Railway plans the turn and decides what context is needed.
- Custom GPT receives several small chunks, not one huge JSON blob.
- Character depth is preserved: identity brief + voice + behavior + goal + knowledge boundary are always present.
- Energy, deep appearance, lore and past are loaded only when the current turn actually needs them.
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
START_SCENE_PATH = "scenes/start_scene.md"
RENDER_CONTRACT_PATH = "gpt/scene_output_contract_1206.json"

ID_ALIASES = {
    "акира": "akira", "akira": "akira", "кира": "akira",
    "джун": "jun", "jun": "jun", "jun_carter": "jun",
    "эмма": "emma", "emma": "emma", "беловолосая девушка": "emma",
    "ирэй": "irey", "ирей": "irey", "irey": "irey", "беловолосый парень": "irey",
    "рэй": "ray", "рей": "ray", "ray": "ray", "ray_carter": "ray", "мужчина в форме": "ray",
    "рейден": "raiden", "рейдон": "raiden", "raiden": "raiden", "sterling": "raiden", "стерлинг": "raiden",
    "хару": "haru", "haru": "haru", "haru_foster": "haru",
    "мики": "miki", "miki": "miki",
    "алекс": "alex", "alex": "alex",
    "широ": "shiro", "shiro": "shiro",
    "юна": "yuna", "yuna": "yuna",
    "кай": "kai", "kai": "kai",
}

IDENTITY_OVERRIDES = {
    "akira": "25 лет; взрослая девушка; рост 165; платиново-белые/белые волосы; тёмно-карие глаза с янтарным отблеском. Нейтрально не называть девочкой.",
    "jun": "взрослый мужчина; хозяин дома; для Акиры — Джун/отец/опекун по сыгранному контексту; для Эммы/Ирэя на старте — неизвестный мужчина.",
    "irey": "взрослый мужчина/парень; белые волосы; пространственно-сенсорная связь через касание; внешне сдержаннее, чем его внутренняя цель.",
    "emma": "взрослая девушка/женщина; резкая, грубая, эмоциональная; не декор и не удобная кнопка экспозиции.",
    "ray": "взрослый мужчина в форме; командующий Восточного сектора; условное/отложенное появление.",
    "raiden": "взрослый высокий мужчина/рейдер; условное/отложенное появление, не активен в стартовой комнате.",
}

DEFAULT_VISIBLE_LABELS = {
    "akira": "Акира",
    "jun": "Джун",
    "emma": "беловолосая девушка",
    "irey": "беловолосый парень",
    "ray": "мужчина в форме",
    "raiden": "высокий рейдер",
}

UNKNOWN_NAME_RULES = [
    "Internal character_id/display_name is engine knowledge, not automatic scene permission.",
    "If POV or speaker has not heard/read a name in-scene or from state knowledge, use a stable visible descriptor.",
    "Akira does not know Emma/Irey by name at the start: use descriptors until a visible name source appears.",
    "Emma and Irey do not know engine:jun by name at the start: in their speech use 'мужчина', 'хозяин дома', 'тот, кто её прятал', not 'Джун/Картер'.",
    "Do not call 25-year-old Akira 'девочка' in narration or neutral labels. Use 'Акира', 'девушка', 'цель', or an intentionally hostile descriptor only if character voice requires it.",
]

DIALOGUE_HINTS = (
    "говор", "объяс", "спрос", "ответ", "кто", "что", "зачем", "почему",
    "молчи", "слуш", "вопрос", "назов", "памят", "помн", "узна", "смотр",
)
ENERGY_HINTS = (
    "энерг", "сила", "поток", "эхо", "кайрос", "простран", "холод", "огонь",
    "вода", "воздух", "подавлен", "перегруз", "браслет", "барьер", "касани",
)
PAST_HINTS = ("прошл", "вспом", "памят", "академ", "райден", "самуэль", "беремен", "ребен", "ребён", "лаборатор")
APPEARANCE_HINTS = ("осмотреть", "выгляд", "лицо", "рост", "волос", "глаза", "шрам", "одеж", "форма", "опис")
LORE_HINTS = ("эхо", "кайрос", "восточный сектор", "рейдер", "сектор", "самуэль", "система", "заказчик")
INVENTORY_HINTS = ("карман", "ножниц", "документ", "записк", "блокнот", "оруж", "стол", "взять", "убрать", "пояс")

CHARACTER_PATTERNS = {
    "voice": ("voice", "speech", "голос", "реплик", "tone", "style", "говорит", "речь"),
    "goal": ("goal", "цель", "primary_goal", "current_goal", "защит", "контрол", "скры", "достав", "заказчик", "система"),
    "reaction": ("не узна", "не помн", "пуст", "чуж", "теряется", "пауза", "сух", "реакц", "trigger", "visible", "боль", "сдерж", "дистанц", "вопрос"),
    "behavior": ("behavior", "body_language", "habit", "микр", "движ", "дистанц", "рук", "выход", "угроз", "взгляд"),
    "player_control": ("player_control", "игрок", "не писать", "не задавать", "не соглаш", "pov_rules"),
    "ability": ("energy", "ability", "способ", "поток", "энерг", "касани", "простран", "вода", "холод", "огонь"),
}

KNOWLEDGE_PATTERNS = {
    "knows": ("знает", "knows", "known", "stable_knows", "starting_knowledge", "known_at_start"),
    "unknowns": ("не знает", "does_not_know", "unknown", "stable_does_not_know", "unknown_at_start", "strict_unknowns"),
    "hides": ("скрывает", "withholds", "hides", "sealed", "locked", "не раскры"),
    "rules": ("rule", "правил", "disclosure", "inference", "assumption", "предполага", "источник"),
}


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
            if key not in merged:
                merged[key] = value
        return merged
    return body


def _trim(value: Any, max_chars: int = 500) -> str:
    text = str(value or "")
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1].rstrip() + "…"


def _read_text(path: str, sid: str | None = None) -> str:
    try:
        return base.read_text(path, session_id=sid, default="") or ""
    except Exception:
        return ""


def _read_json(path: str, sid: str, default: Any) -> Any:
    try:
        value = base.read_json(path, session_id=sid, default=default)
        return default if value is None else value
    except Exception:
        return default


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
        if cid and cid not in {"none", "null", "unknown", "false"} and cid not in ids:
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


def _compact(value: Any, *, max_chars: int = 700, max_items: int = 8, depth: int = 2) -> Any:
    if depth <= 0:
        if isinstance(value, str):
            return _trim(value, max_chars)
        if isinstance(value, (int, float, bool)) or value is None:
            return value
        return _trim(json.dumps(value, ensure_ascii=False, sort_keys=True), max_chars)
    if isinstance(value, dict):
        keys = [
            "summary", "current_goal", "current_status", "memory", "knows_as_fact", "knows",
            "does_not_know", "suspects", "assumes", "wrongly_believes", "is_hiding",
            "future_hooks", "recent_scene_notes", "last_interaction", "surface_dynamic",
            "ray_to_akira", "akira_to_ray", "irey_to_akira", "emma_to_akira", "jun_to_akira",
        ]
        ordered = [k for k in keys if k in value] + [k for k in value if k not in keys]
        return {k: _compact(value[k], max_chars=max_chars, max_items=max_items, depth=depth - 1) for k in ordered[:max_items]}
    if isinstance(value, list):
        return [_compact(item, max_chars=max_chars, max_items=max_items, depth=depth - 1) for item in value[:max_items]]
    if isinstance(value, str):
        return _trim(value, max_chars)
    return value


def _matching_lines(text: str, patterns: tuple[str, ...], *, max_lines: int = 10, max_chars: int = 1200) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for line in text.splitlines():
        clean = line.strip()
        if not clean or clean in seen:
            continue
        low = clean.lower().replace("ё", "е")
        if any(p in low for p in patterns):
            seen.add(clean)
            out.append(clean)
        if len(out) >= max_lines:
            break
    joined = _trim("\n".join(out), max_chars)
    return [x for x in joined.splitlines() if x.strip()]


def _first_nonempty(*values: Any, max_chars: int = 500) -> str:
    for value in values:
        text = _trim(value, max_chars)
        if text:
            return text
    return ""


def _memory_lines(memory: dict[str, Any], keys: tuple[str, ...], *, max_items: int = 8, max_chars: int = 1000) -> list[str]:
    result: list[str] = []

    def walk(obj: Any) -> None:
        if len(result) >= max_items:
            return
        if isinstance(obj, dict):
            for key, value in obj.items():
                key_low = str(key).lower().replace("ё", "е")
                if any(k in key_low for k in keys):
                    if isinstance(value, list):
                        for item in value:
                            if len(result) >= max_items:
                                return
                            result.append(_trim(item, 220))
                    elif value:
                        result.append(_trim(value, 220))
                elif key in ("memory", "current_status", "temporary_knowledge_state"):
                    walk(value)

    if isinstance(memory, dict):
        walk(memory)
    joined = _trim("\n".join(x for x in result if x), max_chars)
    return [x for x in joined.splitlines() if x.strip()]


def _scene_text(payload: dict[str, Any], current: dict[str, Any], scene_plan: dict[str, Any]) -> str:
    explicit = payload.get("player_input") or payload.get("user_input") or scene_plan.get("player_input") or scene_plan.get("user_input")
    return _trim(explicit or current.get("last_player_input"), 700)


def _scene_is_dialogue_or_pressure(player_input: str, scene_plan: dict[str, Any]) -> bool:
    text = (player_input or "").lower().replace("ё", "е")
    scene_type = str(scene_plan.get("scene_type") or scene_plan.get("type") or "").lower()
    if any(x in scene_type for x in ("dialog", "разговор", "опрос", "конфликт", "наблюдение")):
        return True
    return any(x in text for x in DIALOGUE_HINTS)


def _needs(payload: dict[str, Any], current: dict[str, Any], scene_plan: dict[str, Any], player_input: str) -> dict[str, bool]:
    low = " ".join([player_input, json.dumps(scene_plan, ensure_ascii=False), str(current.get("scene_goal") or "")]).lower().replace("ё", "е")
    requested = payload.get("needs") if isinstance(payload.get("needs"), dict) else {}
    required_blocks = scene_plan.get("required_blocks") if isinstance(scene_plan.get("required_blocks"), dict) else {}
    return {
        "dialogue_or_pressure": _scene_is_dialogue_or_pressure(player_input, scene_plan),
        "energy": bool(requested.get("energy") or required_blocks.get("energy") or any(x in low for x in ENERGY_HINTS)),
        "past": bool(current.get("load_past") or requested.get("past") or any(x in low for x in PAST_HINTS)),
        "deep_appearance": bool(requested.get("appearance") or any(x in low for x in APPEARANCE_HINTS)),
        "lore": bool(requested.get("lore") or any(x in low for x in LORE_HINTS)),
        "inventory": bool(requested.get("inventory") or required_blocks.get("inventory") or any(x in low for x in INVENTORY_HINTS)),
        "calendar": True,
        "location": True,
        "render_contract": True,
    }


def _collect_character_ids(payload: dict[str, Any], current: dict[str, Any], scene_plan: dict[str, Any], player_input: str) -> list[str]:
    ids: list[str] = []
    _add_id(ids, current.get("pov_character_id"))
    for key in ("speaking_character_ids", "addressed_character_ids", "present_character_ids", "observing_character_ids"):
        _add_id(ids, current.get(key))
    for key in ("speaking_characters", "addressed_characters", "present_characters", "observing_characters", "character_ids", "characters", "character_requests"):
        _add_id(ids, scene_plan.get(key))
        _add_id(ids, payload.get(key))
    _add_id(ids, current.get("scene_character_ids") or current.get("active_character_ids"))
    # Keep chunk size bounded. Further characters must be delayed or requested through context-more.
    return ids[:7]


def _role_for(cid: str, current: dict[str, Any], scene_plan: dict[str, Any]) -> str:
    pov = _canonical_id(current.get("pov_character_id"))
    if cid == pov:
        return "pov"
    speaking = [_canonical_id(x) for x in (current.get("speaking_character_ids") or [])]
    speaking += [_canonical_id(x) for x in (scene_plan.get("speaking_characters") or [])]
    addressed = [_canonical_id(x) for x in (current.get("addressed_character_ids") or [])]
    addressed += [_canonical_id(x) for x in (scene_plan.get("addressed_characters") or [])]
    if cid in speaking:
        return "speaking"
    if cid in addressed:
        return "addressed"
    if cid in [_canonical_id(x) for x in (current.get("present_character_ids") or [])]:
        return "present_reaction"
    return "referenced"


def _visible_labels_for(cid: str, current: dict[str, Any]) -> dict[str, str]:
    visible_start = current.get("visible_relationships_start") if isinstance(current.get("visible_relationships_start"), dict) else {}
    for_pov = DEFAULT_VISIBLE_LABELS.get(cid, cid)
    if cid in visible_start and isinstance(visible_start[cid], dict):
        for_pov = str(visible_start[cid].get("visible_label") or for_pov)
    return {
        "for_pov_akira": for_pov,
        "for_emma": "мужчина / хозяин дома" if cid == "jun" else DEFAULT_VISIBLE_LABELS.get(cid, cid),
        "for_irey": "мужчина / хозяин дома" if cid == "jun" else DEFAULT_VISIBLE_LABELS.get(cid, cid),
        "rule": "Use visible descriptor unless the speaking character has an in-scene/source-backed name permission.",
    }


def _character_sources(sid: str, cid: str) -> tuple[str, str, str, dict[str, Any]]:
    char_text = _read_text(f"characters/{cid}/character.yaml", sid)
    know_text = _read_text(f"characters/{cid}/knowledge.yaml", sid)
    main_text = _read_text(f"characters/{cid}/main.yaml", sid)
    memory = _read_json(f"state/character_memory/{cid}.json", sid, {})
    if not isinstance(memory, dict):
        memory = {}
    return char_text, know_text, main_text, memory


def _goal_override(cid: str) -> list[str]:
    if cid == "emma":
        return [
            "External goal: get Akira to the hidden customer/system; pressure first, do not become exposition tool.",
            "Emma believes Irey is moving in the same task, but does not know his true personal/protective goal.",
            "Emma may bluff or misread; confidence is not proof of knowledge.",
        ]
    if cid == "irey":
        return [
            "External cover: appears connected to the same search/task as Emma.",
            "Hidden personal goal: protect Akira and prevent the system/Samuel line from taking her if possible.",
            "If Akira looks at him as a stranger, he must register it: pause, test, question, misread or hide the reaction.",
        ]
    if cid == "jun":
        return ["Current goal: delay, protect Akira, keep the downstairs pressure away from her as long as possible."]
    if cid == "akira":
        return ["POV: player controls important words/choices; show guarded body, silence, microreaction, not invented confessions."]
    return []


def _knowledge_guard_override(cid: str) -> dict[str, list[str]]:
    if cid == "emma":
        return {
            "knows": ["Akira is needed by the hidden customer/system; Akira's trace was found near the house; Irey reacts too personally."],
            "does_not_know": ["Does not know engine:jun by name; does not know he hid/raised/protected Akira; does not know Akira's amnesia; does not know Ray/Raiden connection."],
            "speech_guard": ["Must not say 'Джун' or 'Картер' until an in-scene source gives the name."],
        }
    if cid == "irey":
        return {
            "knows": ["Knows more about Akira than Emma; has a personal/protective motive; can notice body/recognition mismatch."],
            "does_not_know": ["Does not know engine:jun by name at start unless a played source gives it; does not know exact last two years; does not know what Akira currently remembers."],
            "speech_guard": ["Must refer to engine:jun as 'мужчина', 'хозяин дома', 'тот, кто её прятал' until name source."],
        }
    if cid == "akira":
        return {
            "knows": ["Remembers the last two years with Jun and current visible objects; does not automatically know hidden lore or strangers' names."],
            "does_not_know": ["Does not know Emma/Irey names at start; does not know East Sector structure, Kairos/Echo terms, Raiden/Ray/Samuel history unless source appears."],
            "speech_guard": ["If player did not write Akira's speech outside parentheses, do not invent important Akira speech."],
        }
    return {"knows": [], "does_not_know": [], "speech_guard": []}


def _character_core_card(sid: str, cid: str, role: str, needs: dict[str, bool]) -> dict[str, Any]:
    char_text, know_text, main_text, memory = _character_sources(sid, cid)
    line_boost = 11 if role == "pov" else 8
    if needs.get("dialogue_or_pressure"):
        line_boost += 2
    identity_lines = _matching_lines(main_text + "\n" + char_text, ("возраст", "рост", "волос", "глаз", "внеш", "appearance", "height", "age"), max_lines=6 if needs.get("deep_appearance") else 3, max_chars=500)
    return {
        "id": cid,
        "role_in_scene": role,
        "identity_brief": IDENTITY_OVERRIDES.get(cid) or _trim("; ".join(identity_lines), 500) or f"{cid}: use loaded card only; do not invent appearance.",
        "visible_labels": _visible_labels_for(cid, _ensure_current(sid)),
        "current_goal_priority": _goal_override(cid) + _matching_lines(main_text + "\n" + char_text, CHARACTER_PATTERNS["goal"], max_lines=6, max_chars=700),
        "voice_behavior_habits": _matching_lines(char_text, CHARACTER_PATTERNS["voice"] + CHARACTER_PATTERNS["behavior"], max_lines=line_boost, max_chars=1200),
        "must_react_to_now": _matching_lines(char_text + "\n" + know_text, CHARACTER_PATTERNS["reaction"], max_lines=line_boost, max_chars=1200),
        "player_control_or_npc_rule": "POV: do not invent important Akira replies/questions/agreements." if role == "pov" else "NPC: each line must come from goal + visible source + knowledge/unknown boundary.",
        "energy_loaded": bool(needs.get("energy")),
        "energy_note": "Energy is omitted in this chunk because the scene did not request/trigger energy." if not needs.get("energy") else "Energy details are in energy_lore chunk.",
        "source_files_used": [f"characters/{cid}/character.yaml", f"characters/{cid}/knowledge.yaml", f"state/character_memory/{cid}.json"],
    }


def _character_knowledge_card(sid: str, cid: str, role: str, needs: dict[str, bool]) -> dict[str, Any]:
    char_text, know_text, main_text, memory = _character_sources(sid, cid)
    guard = _knowledge_guard_override(cid)
    line_boost = 10 if role in {"pov", "speaking", "addressed"} else 7
    return {
        "id": cid,
        "role_in_scene": role,
        "known_as_fact": guard.get("knows", []) + _matching_lines(know_text, KNOWLEDGE_PATTERNS["knows"] + KNOWLEDGE_PATTERNS["rules"], max_lines=line_boost, max_chars=1100) + _memory_lines(memory, ("knows", "assumes", "suspects", "предполага", "знает"), max_items=5, max_chars=700),
        "unknown_or_forbidden": guard.get("does_not_know", []) + _matching_lines(know_text, KNOWLEDGE_PATTERNS["unknowns"] + KNOWLEDGE_PATTERNS["hides"], max_lines=line_boost, max_chars=1100) + _memory_lines(memory, ("does_not_know", "не знает", "hiding", "is_hiding", "скры"), max_items=5, max_chars=700),
        "speech_and_name_guard": guard.get("speech_guard", []) + UNKNOWN_NAME_RULES,
        "unknowns_are_active_rule": "Unknowns should create questions, checks, pauses, pressure, evasion, bluffing or wrong assumptions — not omniscience and not silence.",
    }


def _energy_card(sid: str, cids: list[str]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for cid in cids:
        char_text, know_text, _main_text, _memory = _character_sources(sid, cid)
        ability = _matching_lines(char_text + "\n" + know_text, CHARACTER_PATTERNS["ability"], max_lines=7, max_chars=900)
        if ability:
            result[cid] = ability
    return {"energy_loaded_for": list(result.keys()), "characters": result, "rule": "Use only if energy is visible, used, sensed, discussed or mechanically relevant this turn."}


def _relationship_cards(sid: str, current: dict[str, Any], payload: dict[str, Any], cids: list[str]) -> dict[str, Any]:
    pairs = payload.get("relationship_pair_ids") or current.get("relationship_pair_ids", [])
    focus = set(cids)
    result: dict[str, Any] = {}
    for pair in list(pairs)[:8]:
        pid = str(pair or "").strip()
        if "__" not in pid:
            continue
        left, right = pid.split("__", 1)
        if left not in focus and right not in focus:
            continue
        path = f"state/relationship_pairs/{pid}.json"
        data = _read_json(path, sid, {})
        if not isinstance(data, dict) or not data:
            continue
        result[pid] = {
            "source_file": path,
            "summary": _first_nonempty(
                data.get("surface_dynamic", {}).get("summary") if isinstance(data.get("surface_dynamic"), dict) else "",
                data.get("summary"),
                max_chars=500,
            ),
            "reaction_hooks": _compact(
                {
                    "left_to_right": data.get(str(left) + "_to_" + str(right)),
                    "right_to_left": data.get(str(right) + "_to_" + str(left)),
                    "last_interaction": data.get("last_interaction"),
                    "open_thread": data.get("open_thread") or data.get("future_hooks"),
                },
                max_chars=850,
                max_items=6,
                depth=2,
            ),
        }
    return result


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
        "visible_inventory": _compact(current.get("visible_inventory", []), max_chars=450, max_items=8, depth=2),
        "nearby_items": _compact(current.get("nearby_items", []), max_chars=450, max_items=8, depth=2),
        "scene_goal": _trim(current.get("scene_goal") or current.get("current_scene_goal"), 500),
        "last_player_input": _trim(current.get("last_player_input"), 450),
        "start_scene_exact_text_required": bool(current.get("start_scene_exact_text_required")),
        "start_scene_completed": bool(current.get("start_scene_completed")),
        "boundary_note": "Inventory/state are not NPC knowledge unless the NPC saw/heard/source confirms it.",
    }


def _calendar_slice(sid: str, current: dict[str, Any]) -> dict[str, Any]:
    runtime = _read_json(CALENDAR_RUNTIME_FILE, sid, {})
    if not isinstance(runtime, dict):
        runtime = {}
    return {
        "current_date": current.get("current_date") or runtime.get("current_date"),
        "current_day_phase": current.get("current_day_phase") or runtime.get("current_day_phase"),
        "current_beat_id": runtime.get("current_beat_id") or current.get("current_beat_id"),
        "pending_events": _compact(runtime.get("pending_events", []), max_chars=450, max_items=5, depth=2),
        "rules": _compact(runtime.get("rules", []), max_chars=450, max_items=4, depth=2),
    }


def _history_slice(sid: str, depth: int = 4) -> list[dict[str, Any]]:
    history = _read_json(SCENE_HISTORY_FILE, sid, [])
    if isinstance(history, dict):
        history = history.get("entries", [])
    if not isinstance(history, list):
        return []
    out = []
    for item in history[-depth:]:
        if isinstance(item, dict):
            out.append({
                "scene_id": item.get("scene_id") or item.get("id"),
                "player_input": _trim(item.get("player_input"), 160),
                "summary": _trim(item.get("summary") or item.get("visible_scene_text") or item.get("scene_text"), 450),
            })
    return out


def _location_slice(current: dict[str, Any], scene_plan: dict[str, Any], needs: dict[str, bool]) -> dict[str, Any]:
    return {
        "location_id": scene_plan.get("location_id") or current.get("current_location_id") or current.get("location_id"),
        "location_text": current.get("current_location_text") or current.get("location_text"),
        "requested_depth": "short_rules" if not needs.get("deep_appearance") else "visual_plus_rules",
        "rule": "Use explicit location/current_state. Do not invent background NPCs or noises without source.",
    }


def _inventory_slice(current: dict[str, Any], needs: dict[str, bool]) -> dict[str, Any]:
    if not needs.get("inventory"):
        return {"loaded": False, "note": "Inventory not triggered beyond current_state visible_inventory/nearby_items."}
    return {
        "loaded": True,
        "visible_inventory": _compact(current.get("visible_inventory", []), max_chars=700, max_items=12, depth=2),
        "nearby_items": _compact(current.get("nearby_items", []), max_chars=700, max_items=12, depth=2),
        "inventory_state": _compact(current.get("inventory_state", {}), max_chars=900, max_items=8, depth=2),
        "rule": "A taken item is visible to Akira, but not automatically known to NPCs unless they saw/heard it.",
    }


def _lore_slice(sid: str, needs: dict[str, bool]) -> dict[str, Any]:
    if not needs.get("lore"):
        return {"loaded": False, "note": "Lore omitted: no current trigger."}
    # Keep this intentionally tiny; character knowledge still controls disclosure.
    files = ["canon_lore/index.yaml", "canon_lore/core/world_background.yaml", "canon_lore/world/echo.yaml", "canon_lore/world/kairos.yaml"]
    result = []
    for path in files:
        text = _read_text(path, sid)
        if text:
            result.append({"path": path, "excerpt": _trim(text, 800)})
    return {"loaded": True, "files": result[:3], "rule": "Lore is engine context, not automatic NPC knowledge."}


def _extract_start_scene_text() -> str:
    text = _read_text(START_SCENE_PATH)
    match = re.search(r"```text\s*(.*?)```", text, flags=re.S)
    return match.group(1).strip() if match else text.strip()


def _render_contract_small() -> dict[str, Any]:
    data = _read_json(RENDER_CONTRACT_PATH, "default", {})
    if not isinstance(data, dict) or not data:
        return {
            "dialogue_format": "**Имя/видимый дескриптор** — реплика.",
            "pov_rule": "Respect POV knowledge and player control.",
            "bottom_blocks": ["Что можно сделать", "Что Акира могла бы сказать", "Мысли Акиры", "Состояние"],
            "unknown_names_rule": "Engine-known id is not visible name permission.",
        }
    return {
        "source_file": RENDER_CONTRACT_PATH,
        "must_be_last_writer_instruction": True,
        "dialogue_format_required": data.get("dialogue_format_required") or "**Имя/видимый дескриптор** — реплика.",
        "unknown_names_rule": "If POV/speaker does not know a name, use visible descriptor, not engine id/display_name.",
        "bottom_blocks_rule": "Keep choice/options/status blocks; do not expose hidden lore as POV thoughts.",
    }


def _build_turn_contract(sid: str, payload: dict[str, Any]) -> dict[str, Any]:
    current = _ensure_current(sid)
    scene_plan = payload.get("scene_plan") if isinstance(payload.get("scene_plan"), dict) else {}
    player_input = _scene_text(payload, current, scene_plan)
    needs = _needs(payload, current, scene_plan, player_input)
    cids = _collect_character_ids(payload, current, scene_plan, player_input)
    roles = {cid: _role_for(cid, current, scene_plan) for cid in cids}
    chunks: list[dict[str, Any]] = [
        {"chunk_index": 0, "chunk_type": "characters_core", "contains": cids, "why": "identity brief + voice + behavior + goals; energy omitted unless triggered"},
        {"chunk_index": 1, "chunk_type": "knowledge_boundaries", "contains": cids, "why": "knows/unknowns/hidden/name permissions for current speakers"},
        {"chunk_index": 2, "chunk_type": "state_relationships_memory", "contains": ["current_state", "recent_history", "relationship_pairs"], "why": "continuity and relationship pressure"},
        {"chunk_index": 3, "chunk_type": "location_inventory_calendar_render", "contains": ["location", "inventory_if_needed", "calendar", "render_contract"], "why": "scene mechanics and final writer rules"},
    ]
    if needs.get("energy"):
        chunks.append({"chunk_index": len(chunks), "chunk_type": "energy_lore", "contains": cids, "why": "energy/power/echo was triggered by this turn"})
    if needs.get("lore"):
        chunks.append({"chunk_index": len(chunks), "chunk_type": "world_lore_minimal", "contains": ["canon_lore_minimal"], "why": "world/lore terms were triggered by this turn"})
    if needs.get("past"):
        chunks.append({"chunk_index": len(chunks), "chunk_type": "past_memory_minimal", "contains": cids, "why": "past/memory trigger exists; still bounded"})
    return {
        "success": True,
        "session_id": sid,
        "runtime_version": RUNTIME_VERSION,
        "mode": "v3_hybrid_turn_contract",
        "player_input": player_input,
        "current_frame": _current_state_slice(current),
        "scene_plan_used": scene_plan,
        "needs_decided_by_railway": needs,
        "character_ids": cids,
        "character_roles": roles,
        "required_chunks": chunks,
        "total_chunks": len(chunks),
        "must_load_rule": "Call getRequiredContextManifest, then getRequiredContextChunk from chunk_index=0 until has_more=false before writing the scene.",
        "next_action": "getRequiredContextManifest",
    }


def _manifest_from_contract(contract: dict[str, Any]) -> dict[str, Any]:
    chunks = contract.get("required_chunks") if isinstance(contract.get("required_chunks"), list) else []
    return {
        "success": True,
        "session_id": contract.get("session_id"),
        "runtime_version": RUNTIME_VERSION,
        "mode": "v3_hybrid_required_context_manifest",
        "total_chunks": len(chunks),
        "chunks": chunks,
        "load_order_rule": "Load chunks in numeric order. Do not write scene until the last returned chunk has has_more=false.",
        "next_action": "getRequiredContextChunk" if chunks else "writeScene",
        "next_chunk_index": 0 if chunks else None,
    }


def _chunk_content(sid: str, contract: dict[str, Any], chunk_index: int) -> dict[str, Any]:
    current = _ensure_current(sid)
    needs = contract.get("needs_decided_by_railway") if isinstance(contract.get("needs_decided_by_railway"), dict) else {}
    cids = contract.get("character_ids") if isinstance(contract.get("character_ids"), list) else []
    cids = [_canonical_id(x) for x in cids][:7]
    roles = contract.get("character_roles") if isinstance(contract.get("character_roles"), dict) else {cid: _role_for(cid, current, {}) for cid in cids}
    chunks = contract.get("required_chunks") if isinstance(contract.get("required_chunks"), list) else []
    chunk_type = "unknown"
    if 0 <= chunk_index < len(chunks) and isinstance(chunks[chunk_index], dict):
        chunk_type = str(chunks[chunk_index].get("chunk_type") or "unknown")

    if chunk_type == "characters_core":
        return {
            "characters": {cid: _character_core_card(sid, cid, str(roles.get(cid) or "referenced"), needs) for cid in cids},
            "global_character_rules": [
                "Character behavior comes from loaded cards first, not generic scene convenience.",
                "Appearance is brief unless deep_appearance=true; never invent hair/age/height against identity_brief.",
                "Akira is 25 and controlled/empty/guarded; do not soften her or write major speech for her.",
            ],
        }
    if chunk_type == "knowledge_boundaries":
        return {
            "characters": {cid: _character_knowledge_card(sid, cid, str(roles.get(cid) or "referenced"), needs) for cid in cids},
            "visible_source_rule": "Characters know only what they saw, heard, were told, or can plausibly infer from visible signs. Engine ids and loaded file names are not in-world knowledge.",
        }
    if chunk_type == "state_relationships_memory":
        return {
            "current_state": _current_state_slice(current),
            "recent_scene_history": _history_slice(sid, 5),
            "relationships": _relationship_cards(sid, current, {"relationship_pair_ids": current.get("relationship_pair_ids", [])}, cids),
            "story_lines_note": _compact(_read_json(STORY_LINES_FILE, sid, {}), max_chars=1200, max_items=6, depth=2),
        }
    if chunk_type == "location_inventory_calendar_render":
        scene_plan = contract.get("scene_plan_used") if isinstance(contract.get("scene_plan_used"), dict) else {}
        start_scene_available = bool(current.get("start_scene_exact_text_required") and not current.get("start_scene_completed"))
        return {
            "location": _location_slice(current, scene_plan, needs),
            "inventory": _inventory_slice(current, needs),
            "calendar": _calendar_slice(sid, current),
            "start_scene": {"exact_text_required": start_scene_available, "text_endpoint": f"/api/v3/sessions/{sid}/start-scene-text" if start_scene_available else None},
            "final_render_contract": _render_contract_small(),
            "write_after_this_if_no_more_chunks": True,
        }
    if chunk_type == "energy_lore":
        return _energy_card(sid, cids)
    if chunk_type == "world_lore_minimal":
        return _lore_slice(sid, needs)
    if chunk_type == "past_memory_minimal":
        result = {}
        for cid in cids:
            text = _read_text(f"characters/{cid}/past.yaml", sid)
            if text:
                result[cid] = _trim(text, 1200)
        return {"past_loaded_for": list(result.keys()), "past_excerpts": result, "rule": "Use past only for triggered subtext/recognition, not exposition dump."}
    return {"warning": "Unknown chunk type", "chunk_type": chunk_type}


# Replace old action-safe endpoints from earlier patches.
for _path, _method in [
    ("/api/v3/sessions/{session_id}/preflight", "GET"),
    ("/api/v3/sessions/{session_id}/context-request", "POST"),
    ("/api/v3/sessions/{session_id}/context-more", "POST"),
    ("/api/v3/sessions/{session_id}/start-scene-text", "GET"),
    ("/api/v3/sessions/{session_id}/turn-contract", "POST"),
    ("/api/v3/sessions/{session_id}/required-context/manifest", "POST"),
    ("/api/v3/sessions/{session_id}/required-context/chunk", "POST"),
]:
    _remove_route(_path, _method)


@app.get("/api/v3/sessions/{session_id}/preflight", operation_id="getPreflight")
def get_preflight(session_id: str) -> dict[str, Any]:
    sid = _sid(session_id)
    current = _ensure_current(sid)
    return {
        "success": True,
        "session_id": sid,
        "runtime_version": RUNTIME_VERSION,
        "mode": "v3_preflight_hybrid_small",
        "current_state": _current_state_slice(current),
        "calendar": _calendar_slice(sid, current),
        "recent_scene_history": _history_slice(sid, 3),
        "next_action": "getTurnContract",
        "writer_note": "Do not write scene from preflight. Call getTurnContract, manifest, then all required chunks.",
    }


@app.post("/api/v3/sessions/{session_id}/turn-contract", operation_id="getTurnContract")
def get_turn_contract(session_id: str, body: dict[str, Any] | None = Body(default=None)) -> dict[str, Any]:
    sid = _sid(session_id)
    return _build_turn_contract(sid, _payload(body))


@app.post("/api/v3/sessions/{session_id}/required-context/manifest", operation_id="getRequiredContextManifest")
def get_required_context_manifest(session_id: str, body: dict[str, Any] | None = Body(default=None)) -> dict[str, Any]:
    sid = _sid(session_id)
    payload = _payload(body)
    contract = payload.get("turn_contract") if isinstance(payload.get("turn_contract"), dict) else None
    if not contract:
        contract = _build_turn_contract(sid, payload)
    return _manifest_from_contract(contract)


@app.post("/api/v3/sessions/{session_id}/required-context/chunk", operation_id="getRequiredContextChunk")
def get_required_context_chunk(session_id: str, body: dict[str, Any] | None = Body(default=None)) -> dict[str, Any]:
    sid = _sid(session_id)
    payload = _payload(body)
    contract = payload.get("turn_contract") if isinstance(payload.get("turn_contract"), dict) else None
    if not contract:
        contract = _build_turn_contract(sid, payload)
    try:
        chunk_index = int(payload.get("chunk_index", 0))
    except Exception:
        chunk_index = 0
    chunks = contract.get("required_chunks") if isinstance(contract.get("required_chunks"), list) else []
    total = len(chunks)
    has_more = chunk_index + 1 < total
    chunk_meta = chunks[chunk_index] if 0 <= chunk_index < total and isinstance(chunks[chunk_index], dict) else {"chunk_index": chunk_index, "chunk_type": "unknown"}
    return {
        "success": True,
        "session_id": sid,
        "runtime_version": RUNTIME_VERSION,
        "mode": "v3_hybrid_required_context_chunk",
        "chunk_index": chunk_index,
        "chunk_type": chunk_meta.get("chunk_type"),
        "total_chunks": total,
        "has_more": has_more,
        "next_chunk_index": chunk_index + 1 if has_more else None,
        "content": _chunk_content(sid, contract, chunk_index),
        "next_action": "getRequiredContextChunk" if has_more else "writeScene",
        "write_scene_allowed": not has_more,
    }


@app.post("/api/v3/sessions/{session_id}/context-request", operation_id="requestContextSlice")
def request_context_slice(session_id: str, body: dict[str, Any] | None = Body(default=None)) -> dict[str, Any]:
    sid = _sid(session_id)
    payload = _payload(body)
    contract = _build_turn_contract(sid, payload)
    manifest = _manifest_from_contract(contract)
    return {
        "success": True,
        "session_id": sid,
        "runtime_version": RUNTIME_VERSION,
        "mode": "v3_legacy_context_request_points_to_manifest_chunks",
        "do_not_write_scene_yet": True,
        "turn_contract": contract,
        "required_context_manifest": manifest,
        "next_action": "getRequiredContextChunk",
        "next_chunk_index": 0,
    }


@app.post("/api/v3/sessions/{session_id}/context-more", operation_id="requestMoreContext")
def request_more_context(session_id: str, body: dict[str, Any] | None = Body(default=None)) -> dict[str, Any]:
    payload = _payload(body)
    payload.setdefault("reason", "expanded_followup_context")
    payload.setdefault("chunk_index", 0)
    return get_required_context_chunk(session_id, payload)


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
        "after_output_instruction": "After outputting exact_text, wait for the player. On next input call processTurn.",
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
        "message": "Full scene_contract is disabled for Actions. Use getTurnContract -> getRequiredContextManifest -> getRequiredContextChunk loop.",
        "next_action": "getTurnContract",
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
        "mode": "v3_hybrid_context_audit",
        "current_state": _current_state_slice(current),
        "pov_character_must_load": True,
        "full_scene_contract_actions_disabled": True,
        "turn_contract_endpoint": f"/api/v3/sessions/{sid}/turn-contract",
        "manifest_endpoint": f"/api/v3/sessions/{sid}/required-context/manifest",
        "chunk_endpoint": f"/api/v3/sessions/{sid}/required-context/chunk",
    }


try:
    app.version = RUNTIME_VERSION
except Exception:
    pass
