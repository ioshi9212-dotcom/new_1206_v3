from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "tests/test_runtime_regressions.py"
text = PATH.read_text(encoding="utf-8")


def replace_once(old: str, new: str, label: str) -> None:
    global text
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one marker, found {count}")
    text = text.replace(old, new, 1)


replace_once(
    '''    assert len(entries) == 1
    assert entries[0]["turn_id"] == turn_id

    replay = client.post''',
    '''    assert [entry["kind"] for entry in entries] == ["opening", "gameplay"]
    assert entries[0]["turn_id"] == "start_scene_opening"
    assert entries[1]["turn_id"] == turn_id

    replay = client.post''',
    "single apply history includes opening",
)
replace_once(
    '''    entries = history.get("entries", []) if isinstance(history, dict) else history
    assert len(entries) == 1

    rewritten = dict(apply_body)''',
    '''    entries = history.get("entries", []) if isinstance(history, dict) else history
    assert len(entries) == 2
    assert [entry["turn_id"] for entry in entries] == ["start_scene_opening", turn_id]

    rewritten = dict(apply_body)''',
    "idempotent replay does not duplicate opening",
)
replace_once(
    '''    assert [entry["turn_id"] for entry in entries] == [turn_id]


def test_thirty_transactional_turns_keep_one_revision_per_scene''',
    '''    assert [entry["turn_id"] for entry in entries] == ["start_scene_opening", turn_id]


def test_thirty_transactional_turns_keep_one_revision_per_scene''',
    "recovery history includes opening",
)
replace_once(
    '''    assert story_lines["turn_counter"] == 30
    assert [entry["turn_id"] for entry in entries] == turn_ids
    memory = base.read_json''',
    '''    assert story_lines["turn_counter"] == 30
    assert entries[0]["turn_id"] == "start_scene_opening"
    gameplay_entries = [entry for entry in entries if entry.get("kind") == "gameplay"]
    assert [entry["turn_id"] for entry in gameplay_entries] == turn_ids
    memory = base.read_json''',
    "thirty turns separate opening from gameplay",
)

PATH.write_text(text, encoding="utf-8")
Path(__file__).unlink()
