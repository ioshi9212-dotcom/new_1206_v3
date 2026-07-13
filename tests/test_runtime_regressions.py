from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import compact as base
from app.main import app


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    data_dir = tmp_path / "data"
    monkeypatch.setattr(base, "DATA_DIR", data_dir)
    monkeypatch.setattr(base, "SESSIONS_DIR", data_dir / "sessions")
    return TestClient(app)


def test_live_and_director_schemas_are_separate(client: TestClient) -> None:
    live = client.get("/openapi-actions.json").json()
    director = client.get("/openapi-director-actions.json").json()

    live_operations = json.dumps(live, ensure_ascii=False)
    director_operations = json.dumps(director, ensure_ascii=False)
    assert "processTurn" in live_operations
    assert "getRequiredContextChunk" in live_operations
    assert "startDirectorDraft" not in live_operations
    assert "startDirectorDraft" in director_operations
    assert "processTurn" not in director_operations


def test_fresh_start_removes_old_playthrough_state(client: TestClient) -> None:
    sid = "reset-proof"
    client.post("/api/v1/start", json={"session_id": sid})
    base.write_json("state/scene_history.json", [{"visible_scene_text": "old"}], sid)
    base.write_json("state/character_memory/akira.json", {"poison": "old-memory"}, sid)
    base.write_json("state/relationship_pairs/akira__jun.json", {"poison": "old-pair"}, sid)

    response = client.post("/api/v1/start", json={"session_id": sid})

    assert response.status_code == 200
    assert base.read_json("state/scene_history.json", sid, None) == []
    assert "poison" not in base.read_json("state/character_memory/akira.json", sid, {})
    assert "poison" not in base.read_json("state/relationship_pairs/akira__jun.json", sid, {})


def test_repository_canon_beats_stale_seed_copy(client: TestClient) -> None:
    stale = base.DATA_DIR / "scenes" / "start_scene.md"
    stale.parent.mkdir(parents=True, exist_ok=True)
    stale.write_text("STALE VOLUME COPY", encoding="utf-8")

    actual = (base.REPO_ROOT / "scenes" / "start_scene.md").read_text(encoding="utf-8")
    assert base.read_text("scenes/start_scene.md") == actual


def test_start_scene_stops_and_empty_followup_cannot_create_turn(client: TestClient) -> None:
    sid = "start-stop-proof"
    client.post("/api/v1/start", json={"session_id": sid})

    preflight = client.get(f"/api/v3/sessions/{sid}/preflight").json()
    start_scene = client.get(f"/api/v3/sessions/{sid}/start-scene-text").json()
    no_turn_contract = client.post(
        f"/api/v3/sessions/{sid}/turn-contract",
        json={"turn_id": "turn_not_created"},
    ).json()
    empty = client.post(
        f"/api/v1/sessions/{sid}/turn",
        json={"player_input": "   "},
    ).json()

    assert preflight["next_action"] == "getStartSceneText"
    assert start_scene["exact_text_required"] is True
    assert start_scene["exact_text"]
    assert "stop" in start_scene["after_output_instruction"].lower()
    assert no_turn_contract["success"] is False
    assert no_turn_contract["required_chunks"] == []
    assert empty["success"] is False
    assert base.get_pending_turn(sid) is None


def test_director_understands_inflected_names_without_forbidding_them(client: TestClient) -> None:
    response = client.post(
        "/api/director/drafts/start",
        json={
            "draft_id": "names",
            "director_request": (
                "Напиши сцену с Акирой, Джуном, Эммой и Ирэем. "
                "Рэй должен позвонить в конце."
            ),
        },
    ).json()

    packet = response["writer_packet"]
    assert "akira, jun, irey, emma, ray" in packet
    assert "## Запреты сцены\n- Рэй" not in packet


def test_lore_chunk_uses_existing_canon_files(client: TestClient) -> None:
    sid = "lore-proof"
    client.post("/api/v1/start", json={"session_id": sid})
    turn_id = client.post(
        f"/api/v1/sessions/{sid}/turn",
        json={"player_input": "Что такое кайросы и энергия?"},
    ).json()["turn_id"]
    contract = client.post(
        f"/api/v3/sessions/{sid}/turn-contract",
        json={"turn_id": turn_id},
    ).json()
    lore_index = next(
        item["chunk_index"]
        for item in contract["required_chunks"]
        if item["chunk_type"] == "world_lore_minimal"
    )
    chunk = client.post(
        f"/api/v3/sessions/{sid}/required-context/chunk",
        json={"turn_id": turn_id, "chunk_index": lore_index, "turn_contract": contract},
    ).json()

    files = chunk["content"]["files"]
    assert files
    assert all((base.REPO_ROOT / item["path"]).is_file() for item in files)


def test_process_turn_protects_one_input_and_pins_all_context(client: TestClient) -> None:
    sid = "pending-proof"
    client.post("/api/v1/start", json={"session_id": sid})
    committed_before = base.read_json("state/current_state.json", sid, {})

    first = client.post(
        f"/api/v1/sessions/{sid}/turn",
        json={"player_input": "Я бесшумно открываю дверь."},
    ).json()
    turn_id = first["turn_id"]

    assert first["success"] is True
    assert first["state_revision"] == 0
    assert base.read_json("state/current_state.json", sid, {}) == committed_before

    duplicate = client.post(
        f"/api/v1/sessions/{sid}/turn",
        json={"player_input": "Я бесшумно открываю дверь."},
    ).json()
    assert duplicate["turn_id"] == turn_id
    assert duplicate["pending_turn_reused"] is True

    conflict = client.post(
        f"/api/v1/sessions/{sid}/turn",
        json={"player_input": "Нет, остаюсь в комнате."},
    ).json()
    assert conflict["success"] is False
    assert conflict["pending_turn_id"] == turn_id

    missing = client.post(f"/api/v3/sessions/{sid}/turn-contract", json={}).json()
    stale = client.post(f"/api/v3/sessions/{sid}/turn-contract", json={"turn_id": "turn_stale"}).json()
    contract = client.post(f"/api/v3/sessions/{sid}/turn-contract", json={"turn_id": turn_id}).json()

    assert missing["success"] is False
    assert stale["success"] is False
    assert contract["success"] is True
    assert contract["turn_id"] == turn_id
    assert contract["player_input"] == "Я бесшумно открываю дверь."
    assert contract["current_frame"]["start_scene_completed"] is True

    manifest = client.post(
        f"/api/v3/sessions/{sid}/required-context/manifest",
        json={"turn_id": turn_id, "turn_contract": contract},
    ).json()
    tampered_contract = dict(contract)
    tampered_contract["required_chunks"] = []
    rebuilt_manifest = client.post(
        f"/api/v3/sessions/{sid}/required-context/manifest",
        json={"turn_id": turn_id, "turn_contract": tampered_contract},
    ).json()
    last_chunk = client.post(
        f"/api/v3/sessions/{sid}/required-context/chunk",
        json={"turn_id": turn_id, "chunk_index": manifest["total_chunks"] - 1, "turn_contract": contract},
    ).json()
    assert manifest["turn_id"] == turn_id
    assert rebuilt_manifest["total_chunks"] == manifest["total_chunks"] > 0
    assert last_chunk["turn_id"] == turn_id
    assert last_chunk["draft_scene_allowed"] is True
    assert last_chunk["visible_scene_output_allowed"] is False
    assert last_chunk["next_action"] == "draftThenApplyTurnResult"


def test_direct_process_turn_initializes_session_owned_state(client: TestClient) -> None:
    sid = "direct-process-proof"
    turn = client.post(
        f"/api/v1/sessions/{sid}/turn",
        json={"player_input": "Осматриваю комнату."},
    ).json()

    assert turn["success"] is True
    assert turn["turn_id"]
    current_path = base.SESSIONS_DIR / sid / "state" / "current_state.json"
    runtime_path = base.SESSIONS_DIR / sid / base.TURN_RUNTIME_FILE
    assert current_path.is_file()
    assert runtime_path.is_file()
    assert base.read_session_json("state/current_state.json", sid, {})["session_id"] == sid


def test_apply_is_required_idempotent_and_revisioned(client: TestClient) -> None:
    sid = "apply-proof"
    client.post("/api/v1/start", json={"session_id": sid})
    turn_id = client.post(
        f"/api/v1/sessions/{sid}/turn",
        json={"player_input": "Спускаюсь вниз и молча смотрю на незнакомцев."},
    ).json()["turn_id"]

    missing_id = client.post(
        f"/api/v1/sessions/{sid}/apply-turn-result",
        json={"visible_scene_text": "Сцена."},
    ).json()
    wrong_id = client.post(
        f"/api/v1/sessions/{sid}/apply-turn-result",
        json={"turn_id": "turn_wrong", "visible_scene_text": "Сцена."},
    ).json()
    empty_scene = client.post(
        f"/api/v1/sessions/{sid}/apply-turn-result",
        json={"turn_id": turn_id, "visible_scene_text": ""},
    ).json()
    assert missing_id["status"] == "rejected"
    assert wrong_id["status"] == "rejected"
    assert empty_scene["status"] == "rejected"

    apply_body = {
        "scene_response": {"turn_id": turn_id},
        "visible_scene_text": "Акира остановилась на нижней ступени. Разговор внизу оборвался.",
        "proposed_updates": {
            "character_memory_updates": [
                {"character_id": "emma", "memory": ["Увидела Акиру на лестнице."]}
            ],
            "relationship_pair_updates": [
                {"pair_id": "akira__emma", "tension": 2}
            ],
        },
    }
    applied = client.post(f"/api/v1/sessions/{sid}/apply-turn-result", json=apply_body).json()

    assert applied["status"] == "applied"
    assert applied["turn_id"] == turn_id
    assert applied["state_revision"] == 1
    assert applied["visible_scene_output_allowed"] is True
    assert "changed_files" not in applied
    assert "proposed_updates" not in applied
    assert base.read_turn_runtime(sid)["pending_turn"] is None
    current = base.read_json("state/current_state.json", sid, {})
    assert current["last_player_input"] == "Спускаюсь вниз и молча смотрю на незнакомцев."
    assert current["state_revision"] == 1
    assert current["start_scene_completed"] is True
    assert base.read_json("state/story_lines.json", sid, {})["turn_counter"] == 1
    history = base.read_json("state/scene_history.json", sid, [])
    entries = history.get("entries", []) if isinstance(history, dict) else history
    assert len(entries) == 1
    assert entries[0]["turn_id"] == turn_id

    replay = client.post(f"/api/v1/sessions/{sid}/apply-turn-result", json=apply_body).json()
    assert replay["status"] == "applied"
    assert replay["idempotent_replay"] is True
    assert base.read_json("state/story_lines.json", sid, {})["turn_counter"] == 1
    history = base.read_json("state/scene_history.json", sid, [])
    entries = history.get("entries", []) if isinstance(history, dict) else history
    assert len(entries) == 1

    rewritten = dict(apply_body)
    rewritten["visible_scene_text"] = "Другой текст для уже применённого хода."
    rejected = client.post(f"/api/v1/sessions/{sid}/apply-turn-result", json=rewritten).json()
    assert rejected["status"] == "rejected"


def test_interrupted_multi_file_apply_rolls_forward_once(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sid = "recovery-proof"
    client.post("/api/v1/start", json={"session_id": sid})
    turn_id = client.post(
        f"/api/v1/sessions/{sid}/turn",
        json={"player_input": "Я касаюсь перил и прислушиваюсь."},
    ).json()["turn_id"]
    apply_body = {
        "turn_id": turn_id,
        "visible_scene_text": "Дерево под ладонью было холодным. Внизу кто-то резко вдохнул.",
        "proposed_updates": {
            "character_memory_updates": [{"character_id": "akira", "memory": ["Услышала резкий вдох внизу."]}],
        },
    }

    original_writer = base._atomic_write_json_target
    failed = {"once": False}

    def flaky_writer(target: Path, data: object) -> None:
        if target.name == "scene_history.json" and not failed["once"]:
            failed["once"] = True
            raise OSError("simulated crash during transaction")
        original_writer(target, data)

    monkeypatch.setattr(base, "_atomic_write_json_target", flaky_writer)
    with pytest.raises(OSError, match="simulated crash"):
        client.post(f"/api/v1/sessions/{sid}/apply-turn-result", json=apply_body)

    monkeypatch.setattr(base, "_atomic_write_json_target", original_writer)
    recovered_transactions = base.recover_json_transactions(sid)
    assert recovered_transactions

    replay = client.post(f"/api/v1/sessions/{sid}/apply-turn-result", json=apply_body).json()
    assert replay["status"] == "applied"
    assert replay["idempotent_replay"] is True
    assert base.read_turn_runtime(sid)["state_revision"] == 1
    assert base.read_json("state/story_lines.json", sid, {})["turn_counter"] == 1
    history = base.read_json("state/scene_history.json", sid, [])
    entries = history.get("entries", []) if isinstance(history, dict) else history
    assert [entry["turn_id"] for entry in entries] == [turn_id]


def test_thirty_transactional_turns_keep_one_revision_per_scene(client: TestClient) -> None:
    sid = "thirty-turn-proof"
    client.post("/api/v1/start", json={"session_id": sid})
    turn_ids: list[str] = []

    for number in range(1, 31):
        turn = client.post(
            f"/api/v1/sessions/{sid}/turn",
            json={"player_input": f"Тестовое действие {number}."},
        ).json()
        turn_id = turn["turn_id"]
        turn_ids.append(turn_id)
        body = {
            "turn_id": turn_id,
            "visible_scene_text": f"Тестовая сцена {number}.",
            "current_state_patch": {"current_scene_id": f"scene_{number}"},
        }
        applied = client.post(f"/api/v1/sessions/{sid}/apply-turn-result", json=body).json()
        assert applied["state_revision"] == number
        if number % 7 == 0:
            replay = client.post(f"/api/v1/sessions/{sid}/apply-turn-result", json=body).json()
            assert replay["idempotent_replay"] is True

    runtime = base.read_turn_runtime(sid)
    story_lines = base.read_json("state/story_lines.json", sid, {})
    history = base.read_json("state/scene_history.json", sid, [])
    entries = history.get("entries", []) if isinstance(history, dict) else history
    assert runtime["state_revision"] == 30
    assert runtime["next_turn_number"] == 31
    assert runtime["pending_turn"] is None
    assert story_lines["turn_counter"] == 30
    assert [entry["turn_id"] for entry in entries] == turn_ids
