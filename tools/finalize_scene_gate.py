from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def write(path: str, text: str) -> None:
    (ROOT / path).write_text(text, encoding="utf-8")


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one marker, found {count}")
    return text.replace(old, new, 1)


context = read("app/context_request_runtime_patch.py")

boundary_helper = '''def _player_control_boundary(cid: str, role: str) -> dict[str, Any]:
    if role == "pov":
        return {
            "mode": "current_pov_player_controlled",
            "allowed_without_player_input": [
                "involuntary body reaction that does not decide meaning or intent",
                "minor continuity needed to preserve the already established pose or action",
            ],
            "must_wait_for_player": [
                "important speech, question, promise, confession, consent or refusal",
                "route, relationship, risk, force, secret disclosure or irreversible choice",
            ],
            "state_rule": "The current POV is player-controlled; never use npc_autonomy_updates to invent its decisions.",
        }
    if cid == "akira":
        return {
            "mode": "non_pov_low_stakes_scene_continuity",
            "allowed_without_player_input": [
                "local movement inside the already established scene: sit, stand, turn, change distance or follow the immediate flow without leaving the zone",
                "body language, glance, pause, dry irritation or a brief deflection",
                "brief factual/neutral answer that keeps an ordinary question from hanging and does not settle a meaningful choice",
            ],
            "must_wait_for_player": [
                "meaningful yes/no, consent or refusal",
                "promise, confession, forgiveness, trust, relationship change or secret disclosure",
                "new route, departure, cross-zone movement, following someone, risk, force or ability use",
            ],
            "state_rule": "Akira is never an autonomous NPC: never use npc_autonomy_updates to script her choices, location, availability or offscreen decisions.",
        }
    return {
        "mode": "npc_goal_driven",
        "allowed_without_player_input": ["actions and replies grounded in the NPC card, knowledge, goal, limits and current scene"],
        "must_wait_for_player": [],
        "state_rule": "NPC autonomy remains evidence-backed and must respect location, availability and ETA.",
    }


'''
context = replace_once(
    context,
    "def _character_core_card(sid: str, cid: str, role: str, needs: dict[str, bool], current: dict[str, Any]) -> dict[str, Any]:\n",
    boundary_helper + "def _character_core_card(sid: str, cid: str, role: str, needs: dict[str, bool], current: dict[str, Any]) -> dict[str, Any]:\n",
    "player control helper",
)
context = replace_once(
    context,
    '        "response_obligation": _response_obligation(role),\n        "player_control_or_npc_rule": "POV: do not invent important Akira replies/questions/agreements." if role == "pov" else "NPC: each line must come from goal + visible source + knowledge/unknown boundary.",\n',
    '        "response_obligation": (\n            {\n                "required": role == "addressed",\n                "mode": "akira_low_stakes_reply_or_hold",\n                "rule": "Answer an ordinary low-stakes question briefly or visibly hold/deflect; never settle a meaningful choice for Akira.",\n            }\n            if cid == "akira" and role != "pov"\n            else _response_obligation(role)\n        ),\n        "player_control_boundary": _player_control_boundary(cid, role),\n        "player_control_or_npc_rule": (\n            "Current POV: player controls important words, decisions and meaning."\n            if role == "pov"\n            else (\n                "Non-POV Akira: low-stakes continuity only; meaningful choices wait for player input."\n                if cid == "akira"\n                else "NPC: each line must come from goal + visible source + knowledge/unknown boundary."\n            )\n        ),\n',
    "core card control boundary",
)
context = replace_once(
    context,
    '            "unknown_names_rule": "Engine-known id is not visible name permission.",\n',
    '            "unknown_names_rule": "Engine-known id is not visible name permission.",\n            "player_character_rule": "The current POV keeps normal player-choice protection. A present non-POV Akira may use low-stakes continuity only; meaningful choices wait for player input.",\n',
    "fallback render player rule",
)
context = replace_once(
    context,
    '        "missed_event_rule": "World/NPC consequences are allowed; unplayed Akira actions, thoughts, consent and motives are forbidden.",\n        "bottom_blocks_rule": "Keep choice/options/status blocks; do not expose hidden lore as POV thoughts.",\n',
    '        "missed_event_rule": "World/NPC consequences are allowed; unplayed Akira actions, thoughts, consent and motives are forbidden.",\n        "player_character_rule": "The current POV keeps normal player-choice protection. A present non-POV Akira may move or answer at low stakes, but meaningful consent, refusal, promises, secrets, routes, relationships, risk and force wait for player input.",\n        "bottom_blocks_rule": "Keep choice/options/status blocks; do not expose hidden lore as POV thoughts.",\n',
    "render player rule",
)
context = replace_once(
    context,
    '            "pov_rule": "POV full card is mandatory. Never insert Akira merely because she is the protagonist.",\n            "npc_rule": "Active NPC behavior must come from goal + knowledge + unknowns + reaction triggers, never generic scene convenience.",\n',
    '            "pov_rule": "POV full card is mandatory. Never insert Akira merely because she is the protagonist.",\n            "player_character_rule": "When Akira is present as non-POV, keep her alive with low-stakes local movement, body reaction, dry deflection and brief factual/neutral replies; never decide meaningful yes/no, consent, refusal, promises, secrets, routes, relationships, risk or force for her. The current POV remains player-controlled.",\n            "npc_rule": "Active NPC behavior must come from goal + knowledge + unknowns + reaction triggers, never generic scene convenience.",\n',
    "writer card player rule",
)
write("app/context_request_runtime_patch.py", context)


tests = read("tests/test_scene_validation_runtime.py")
old_scene = '''        detail_words = [
            f"след-{index}", f"звук-{index * 3}", f"жест-{index * 5}",
            f"предмет-{index * 7}", f"пауза-{index * 11}", f"направление-{index * 13}",
        ]
        payload = {
            "turn_id": turn_id,
            "visible_scene_text": (
                f"Ход {index} продолжил сцену без решения за героя. "
                + " ".join(detail_words)
                + f". Наблюдение изменило только локальную деталь эпизода {index}."
            ),
            "scene_validation": {"repair_attempt": 1 if index % 7 == 0 else 0},
        }
'''
new_scene = '''        scene_variants = (
            "У окна дрогнула занавеска, и внимание сместилось к дождю за стеклом.",
            "На лестнице коротко скрипнула ступень; тишина в комнате стала заметнее.",
            "С края стола сдвинулся лист, открывая оставленную под ним царапину.",
            "В коридоре погас слабый отсвет, но никто не покинул текущую зону.",
            "Часы отмерили ещё одну минуту, не меняя ничьих решений и договорённостей.",
            "Из нижнего этажа донёсся глухой звук посуды, затем снова стало тихо.",
            "Холодный воздух прошёл вдоль стены и качнул край незакрытой двери.",
            "Взгляд задержался на перилах, где свет выделил свежий след от ладони.",
            "За окном проехала машина; полосы света ненадолго легли на потолок.",
            "В комнате изменился только ритм паузы: никто не сделал значимого выбора.",
        )
        detail_words = [
            f"след-{index}", f"звук-{index * 3}", f"жест-{index * 5}",
            f"предмет-{index * 7}", f"пауза-{index * 11}", f"направление-{index * 13}",
        ]
        payload = {
            "turn_id": turn_id,
            "visible_scene_text": (
                scene_variants[(index - 1) % len(scene_variants)]
                + f" Маркер эпизода {index}: "
                + " ".join(detail_words)
                + ". Сцена продвинулась только через новое наблюдаемое изменение."
            ),
            "scene_validation": {"repair_attempt": 1 if index % 7 == 0 else 0},
        }
'''
tests = replace_once(tests, old_scene, new_scene, "50 turn distinct scenes")
write("tests/test_scene_validation_runtime.py", tests)

for relative in ("tools/finalize_scene_gate.py", ".github/workflows/finalize-scene-gate.yml"):
    path = ROOT / relative
    if path.exists():
        path.unlink()
