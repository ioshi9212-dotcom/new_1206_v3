from __future__ import annotations

import json
import sys
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


def load_all_context(
    client: TestClient,
    sid: str,
    turn_id: str,
    *,
    scene_plan: dict | None = None,
) -> tuple[dict, dict, list[dict]]:
    contract_body = {"turn_id": turn_id}
    if scene_plan:
        contract_body["scene_plan"] = scene_plan
    contract = client.post(f"/api/v3/sessions/{sid}/turn-contract", json=contract_body).json()
    assert contract["success"] is True
    manifest = client.post(
        f"/api/v3/sessions/{sid}/required-context/manifest",
        json={"turn_id": turn_id, "turn_contract": contract},
    ).json()
    chunks = []
    for chunk_index in range(manifest["total_chunks"]):
        chunk = client.post(
            f"/api/v3/sessions/{sid}/required-context/chunk",
            json={"turn_id": turn_id, "chunk_index": chunk_index, "turn_contract": contract},
        ).json()
        assert chunk["success"] is True
        chunks.append(chunk)
    return contract, manifest, chunks


def begin_ready_turn(
    client: TestClient,
    sid: str,
    player_input: str,
    **turn_fields: object,
) -> tuple[str, dict, list[dict]]:
    turn = client.post(
        f"/api/v1/sessions/{sid}/turn",
        json={"player_input": player_input, **turn_fields},
    ).json()
    assert turn["success"] is True
    turn_id = turn["turn_id"]
    contract, _manifest, chunks = load_all_context(client, sid, turn_id)
    return turn_id, contract, chunks


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
    assert "app.v3_full_cards_scene_contract_runtime_patch" not in sys.modules


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
    contract, _manifest, chunks = load_all_context(client, sid, turn_id)
    lore_index = next(
        item["chunk_index"]
        for item in contract["required_chunks"]
        if item["chunk_type"] == "world_lore_minimal"
    )
    chunk = chunks[lore_index]

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
    out_of_order = client.post(
        f"/api/v3/sessions/{sid}/required-context/chunk",
        json={"turn_id": turn_id, "chunk_index": manifest["total_chunks"] - 1, "turn_contract": contract},
    ).json()
    first_chunk = client.post(
        f"/api/v3/sessions/{sid}/required-context/chunk",
        json={"turn_id": turn_id, "chunk_index": 0, "turn_contract": contract},
    ).json()
    replayed_first_chunk = client.post(
        f"/api/v3/sessions/{sid}/required-context/chunk",
        json={"turn_id": turn_id, "chunk_index": 0, "turn_contract": contract},
    ).json()
    loaded_chunks = [first_chunk]
    for chunk_index in range(1, manifest["total_chunks"]):
        loaded_chunks.append(client.post(
            f"/api/v3/sessions/{sid}/required-context/chunk",
            json={"turn_id": turn_id, "chunk_index": chunk_index, "turn_contract": contract},
        ).json())
    last_chunk = loaded_chunks[-1]
    assert manifest["turn_id"] == turn_id
    assert rebuilt_manifest["total_chunks"] == manifest["total_chunks"] > 0
    assert out_of_order["success"] is False
    assert out_of_order["next_chunk_index"] == 0
    assert replayed_first_chunk["success"] is True
    assert replayed_first_chunk["chunk_replayed"] is True
    assert replayed_first_chunk["next_chunk_index"] == first_chunk["next_chunk_index"] == 1
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


def test_one_context_snapshot_freezes_writer_cards_and_server_sources(client: TestClient) -> None:
    sid = "snapshot-proof"
    client.post("/api/v1/start", json={"session_id": sid})
    turn_id = client.post(
        f"/api/v1/sessions/{sid}/turn",
        json={"player_input": "Я смотрю на беловолосую девушку и жду ответа."},
    ).json()["turn_id"]

    first_contract = client.post(
        f"/api/v3/sessions/{sid}/turn-contract",
        json={"turn_id": turn_id, "scene_plan": {"speaking_characters": ["emma"]}},
    ).json()
    snapshot_before = base.read_session_json(base.CONTEXT_SNAPSHOT_FILE, sid, {})
    snapshot_hash = first_contract["context_snapshot_sha256"]

    # Even an out-of-band state mutation cannot make later chunks disagree with
    # the already frozen turn snapshot.
    base.write_json(
        "state/character_memory/emma.json",
        {"knows_as_fact": ["MUTATION_AFTER_SNAPSHOT_MUST_NOT_LEAK"]},
        sid,
    )
    second_contract = client.post(
        f"/api/v3/sessions/{sid}/turn-contract",
        json={"turn_id": turn_id, "scene_plan": {"speaking_characters": ["ray"]}},
    ).json()
    manifest = client.post(
        f"/api/v3/sessions/{sid}/required-context/manifest",
        json={"turn_id": turn_id, "turn_contract": second_contract},
    ).json()

    assert second_contract["context_snapshot_reused"] is True
    assert second_contract["context_snapshot_sha256"] == snapshot_hash
    assert second_contract["character_ids"] == first_contract["character_ids"]
    assert "ray" not in second_contract["character_ids"]
    assert manifest["context_snapshot_sha256"] == snapshot_hash
    assert base.read_session_json(base.CONTEXT_SNAPSHOT_FILE, sid, {})["built_at"] == snapshot_before["built_at"]

    packets = []
    for chunk_index in range(manifest["total_chunks"]):
        packet = client.post(
            f"/api/v3/sessions/{sid}/required-context/chunk",
            json={"turn_id": turn_id, "chunk_index": chunk_index, "turn_contract": second_contract},
        ).json()
        assert packet["context_snapshot_sha256"] == snapshot_hash
        packets.append(packet)

    core = next(packet["content"] for packet in packets if packet["chunk_type"] == "characters_core")
    knowledge = next(packet["content"] for packet in packets if packet["chunk_type"] == "knowledge_boundaries")
    assert set(core["characters"]) == set(knowledge["characters"]) == set(first_contract["character_ids"])
    for cid, card in core["characters"].items():
        assert card["identity_brief"]
        assert card["current_goal_priority"]
        assert card["voice_behavior_habits"]
        assert "must_react_to_now" in card
        knowledge_card = knowledge["characters"][cid]
        assert "known_as_fact" in knowledge_card
        assert "unknown_or_forbidden" in knowledge_card
        assert knowledge_card["speech_and_name_guard"]
    assert "MUTATION_AFTER_SNAPSHOT_MUST_NOT_LEAK" not in json.dumps(packets, ensure_ascii=False)


def test_explicit_non_akira_pov_does_not_load_akira_as_fallback(client: TestClient) -> None:
    sid = "non-akira-pov-proof"
    client.post("/api/v1/start", json={"session_id": sid})
    akira_world_state_before = base.read_json("state/calendar_runtime.json", sid, {})["npc_autonomy"]["akira"]
    turn = client.post(
        f"/api/v1/sessions/{sid}/turn",
        json={
            "player_input": "Молча выдерживаю паузу.",
            "pov_character_id": "jun",
            "active_character_ids": ["jun", "emma"],
            "scene_character_ids": ["jun", "emma"],
            "present_character_ids": ["jun", "emma"],
            "relationship_pair_ids": ["jun__emma"],
        },
    ).json()
    contract = client.post(
        f"/api/v3/sessions/{sid}/turn-contract",
        json={"turn_id": turn["turn_id"]},
    ).json()

    assert contract["success"] is True
    assert contract["pov_character_id"] == "jun"
    assert contract["pov_loaded"] is True
    assert contract["character_ids"] == ["jun", "emma"]
    assert "akira" not in contract["character_ids"]

    _contract, _manifest, _chunks = load_all_context(client, sid, turn["turn_id"])
    applied = client.post(
        f"/api/v1/sessions/{sid}/apply-turn-result",
        json={
            "turn_id": turn["turn_id"],
            "visible_scene_text": "Джун выдержал паузу. Эмма не отвела взгляда."
        },
    ).json()
    assert applied["status"] == "applied"
    assert base.read_json("state/calendar_runtime.json", sid, {})["npc_autonomy"]["akira"] == akira_world_state_before


def test_non_akira_pov_keeps_akira_alive_but_preserves_her_meaningful_choices(client: TestClient) -> None:
    sid = "non-akira-pov-akira-micro-agency-proof"
    client.post("/api/v1/start", json={"session_id": sid})
    _turn_id, contract, chunks = begin_ready_turn(
        client,
        sid,
        "Я смотрю на Акиру и спрашиваю, ела ли она сегодня.",
        pov_character_id="jun",
        active_character_ids=["jun", "akira"],
        scene_character_ids=["jun", "akira"],
        present_character_ids=["jun", "akira"],
        addressed_character_ids=["akira"],
        relationship_pair_ids=["akira__jun"],
    )
    core_cards = {
        cid: card
        for chunk in chunks if chunk["chunk_type"] == "characters_core"
        for cid, card in chunk["content"]["characters"].items()
    }
    render = next(
        chunk["content"]["final_render_contract"]
        for chunk in chunks if chunk["chunk_type"] == "location_inventory_calendar_render"
    )

    assert contract["pov_character_id"] == "jun"
    assert contract["character_roles"] == {"jun": "pov", "akira": "addressed"}
    assert contract["writer_card_contract"]["player_character_rule"].startswith("When Akira is present as non-POV")
    assert core_cards["jun"]["response_obligation"]["mode"] == "player_controlled"
    assert core_cards["jun"]["player_control_boundary"]["mode"] == "current_pov_player_controlled"
    assert core_cards["akira"]["response_obligation"]["mode"] == "akira_low_stakes_reply_or_hold"
    assert core_cards["akira"]["response_obligation"]["required"] is True
    boundary = core_cards["akira"]["player_control_boundary"]
    assert boundary["mode"] == "non_pov_low_stakes_scene_continuity"
    assert any("brief factual/neutral answer" in item for item in boundary["allowed_without_player_input"])
    assert any("meaningful yes/no" in item for item in boundary["must_wait_for_player"])
    assert "never use npc_autonomy_updates" in boundary["state_rule"].lower()
    assert "current POV keeps normal player-choice protection" in render["player_character_rule"]


def test_scene_response_schema_checks_akira_control_in_every_pov() -> None:
    schema = json.loads((base.REPO_ROOT / "api_contracts/chatgpt_scene_response.schema.json").read_text(encoding="utf-8"))
    safety = schema["properties"]["safety_checks"]
    required = set(safety["required"])

    assert "no_major_pov_choice_for_player" in required
    assert "no_major_akira_choice_for_player" in required
    assert "akira_non_pov_actions_are_low_stakes" in required
    assert "non_akira_pov_remains_autonomous" not in required
    npc_updates = schema["properties"]["proposed_updates"]["properties"]["npc_autonomy_updates"]
    assert "никогда не используют этот блок" in npc_updates["description"]


def test_behavior_cards_keep_beliefs_out_of_facts_and_require_addressed_response(client: TestClient) -> None:
    sid = "evidence-buckets-proof"
    client.post("/api/v1/start", json={"session_id": sid})
    base.write_json(
        "state/character_memory/emma.json",
        {
            "character_id": "emma",
            "knows_as_fact": ["Эмма видела Акиру на лестнице."],
            "assumes": ["Джун может лгать о маршруте Акиры."],
            "memory_events": [
                {
                    "event_id": "old-belief",
                    "turn_id": "turn_old",
                    "kind": "belief",
                    "text": "Акира могла услышать разговор.",
                    "source_type": "scene_inference",
                    "evidence": "Эмма заметила движение наверху.",
                }
            ],
        },
        sid,
    )
    turn = client.post(
        f"/api/v1/sessions/{sid}/turn",
        json={
            "player_input": "Эмма, ответь прямо.",
            "pov_character_id": "jun",
            "active_character_ids": ["jun", "emma"],
            "scene_character_ids": ["jun", "emma"],
            "present_character_ids": ["jun", "emma"],
            "addressed_character_ids": ["emma"],
            "relationship_pair_ids": ["jun__emma"],
        },
    ).json()
    contract, _manifest, chunks = load_all_context(client, sid, turn["turn_id"])
    core_cards = {
        cid: card
        for chunk in chunks if chunk["chunk_type"] == "characters_core"
        for cid, card in chunk["content"]["characters"].items()
    }
    knowledge_chunks = [chunk["content"] for chunk in chunks if chunk["chunk_type"] == "knowledge_boundaries"]
    knowledge_cards = {
        cid: card
        for content in knowledge_chunks
        for cid, card in content["characters"].items()
    }
    emma = knowledge_cards["emma"]

    assert contract["memory_character_ids"] == ["jun", "emma"]
    assert contract["relationship_pair_ids"] == ["jun__emma"]
    assert core_cards["emma"]["response_obligation"] == {
        "required": True,
        "mode": "answer_or_visible_refusal",
    }
    assert "Эмма видела Акиру на лестнице." in emma["known_as_fact"]
    assert "Джун может лгать о маршруте Акиры." in emma["beliefs_and_suspicions"]
    assert "Джун может лгать о маршруте Акиры." not in emma["known_as_fact"]
    assert "Акира могла услышать разговор." in emma["beliefs_and_suspicions"]
    assert "Calendar" in knowledge_chunks[0]["calendar_exclusion_rule"]


def test_relationship_loader_uses_active_pair_or_explicit_thought_not_all_pov_pairs(client: TestClient) -> None:
    sid = "pair-relevance-proof"
    client.post("/api/v1/start", json={"session_id": sid})
    turn = client.post(
        f"/api/v1/sessions/{sid}/turn",
        json={
            "player_input": "Смотрю на Джуна и вспоминаю резкого незнакомца.",
            "pov_character_id": "akira",
            "active_character_ids": ["akira", "jun"],
            "scene_character_ids": ["akira", "jun"],
            "present_character_ids": ["akira", "jun"],
            "relationship_pair_ids": [
                "akira__jun", "akira__emma", "akira__irey", "akira__raiden", "akira__ray"
            ],
            "relationship_focus_pair_ids": ["akira__raiden"],
            "thinking_about_character_ids": ["raiden"],
        },
    ).json()
    contract = client.post(
        f"/api/v3/sessions/{sid}/turn-contract",
        json={"turn_id": turn["turn_id"]},
    ).json()

    assert contract["character_ids"] == ["akira", "jun"]
    assert contract["memory_character_ids"] == ["akira", "jun"]
    assert contract["relationship_pair_ids"] == ["akira__raiden", "akira__jun"]
    assert contract["relationship_pair_selection"]["relevance_reason"]["akira__raiden"] == (
        "explicit_pair_focus_or_active_character_thinks_about_other"
    )
    assert "akira__emma" not in contract["relationship_pair_ids"]
    assert "raiden" not in contract["memory_character_ids"]


def test_missing_full_card_pov_is_blocked_without_summary_fallback(client: TestClient) -> None:
    sid = "missing-pov-proof"
    client.post("/api/v1/start", json={"session_id": sid})
    turn = client.post(
        f"/api/v1/sessions/{sid}/turn",
        json={
            "player_input": "Продолжаю сцену.",
            "pov_character_id": "ghost",
            "active_character_ids": ["ghost"],
            "scene_character_ids": ["ghost"],
            "present_character_ids": ["ghost"],
        },
    ).json()
    contract = client.post(
        f"/api/v3/sessions/{sid}/turn-contract",
        json={"turn_id": turn["turn_id"]},
    ).json()

    assert contract["success"] is False
    assert contract["required_chunks"] == []
    assert contract["builder_diagnostics"][0]["fallback_blocked"] == "summary_as_behavior_source"


def test_maximal_context_snapshot_keeps_each_action_chunk_bounded(client: TestClient) -> None:
    sid = "bounded-snapshot-proof"
    client.post("/api/v1/start", json={"session_id": sid})
    cast = ["akira", "jun", "irey", "emma", "ray", "raiden", "haru"]
    turn = client.post(
        f"/api/v1/sessions/{sid}/turn",
        json={
            "player_input": (
                "Я вспоминаю академию, спрашиваю про кайрос, энергию и браслет, "
                "осматриваю одежду, документы и оружие."
            ),
            "active_character_ids": cast,
            "scene_character_ids": cast,
            "present_character_ids": cast,
        },
    ).json()
    contract, manifest, chunks = load_all_context(client, sid, turn["turn_id"])
    snapshot = base.read_session_json(base.CONTEXT_SNAPSHOT_FILE, sid, {})

    assert contract["character_ids"] == cast
    assert manifest["total_chunks"] >= 7
    assert {chunk["chunk_type"] for chunk in chunks} >= {
        "characters_core",
        "knowledge_boundaries",
        "energy_lore",
        "world_lore_minimal",
        "past_memory_minimal",
    }
    core_chunks = [chunk for chunk in chunks if chunk["chunk_type"] == "characters_core"]
    knowledge_chunks = [chunk for chunk in chunks if chunk["chunk_type"] == "knowledge_boundaries"]
    assert len(core_chunks) == len(knowledge_chunks) == 2
    assert {
        cid for chunk in core_chunks for cid in chunk["content"]["characters"]
    } == set(cast)
    assert {
        cid for chunk in knowledge_chunks for cid in chunk["content"]["characters"]
    } == set(cast)
    assert max(len(json.dumps(chunk, ensure_ascii=False)) for chunk in chunks) < 25_000
    assert len(json.dumps(snapshot, ensure_ascii=False)) < 100_000
    past = next(chunk["content"] for chunk in chunks if chunk["chunk_type"] == "past_memory_minimal")
    assert "past_excerpts" not in past
    assert all("selected_lines" in item for item in past["past_slices"].values())


def test_past_request_without_hard_trigger_is_denied(client: TestClient) -> None:
    sid = "past-guard-proof"
    client.post("/api/v1/start", json={"session_id": sid})
    turn = client.post(
        f"/api/v1/sessions/{sid}/turn",
        json={"player_input": "Где сейчас Райден?"},
    ).json()
    contract = client.post(
        f"/api/v3/sessions/{sid}/turn-contract",
        json={"turn_id": turn["turn_id"], "needs": {"past": True}},
    ).json()

    assert contract["success"] is True
    assert contract["needs_decided_by_railway"]["past"] is False
    assert "past_memory_minimal" not in {item["chunk_type"] for item in contract["required_chunks"]}


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
    before_context = client.post(
        f"/api/v1/sessions/{sid}/apply-turn-result",
        json={"turn_id": turn_id, "visible_scene_text": "Слишком ранняя сцена."},
    ).json()
    assert missing_id["status"] == "rejected"
    assert wrong_id["status"] == "rejected"
    assert empty_scene["status"] == "rejected"
    assert before_context["status"] == "rejected"
    assert before_context["next_action"] == "getTurnContract"

    _contract, _manifest, chunks = load_all_context(client, sid, turn_id)
    assert chunks[-1]["all_required_chunks_served"] is True

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
    closed_snapshot = base.read_session_json(base.CONTEXT_SNAPSHOT_FILE, sid, {})
    assert closed_snapshot["status"] == "applied"
    assert closed_snapshot["context_snapshot_sha256"] == applied["context_snapshot_sha256"]
    assert "chunk_packets" not in closed_snapshot
    assert base.read_json("state/story_lines.json", sid, {})["turn_counter"] == 1
    history = base.read_json("state/scene_history.json", sid, [])
    entries = history.get("entries", []) if isinstance(history, dict) else history
    assert [entry["kind"] for entry in entries] == ["opening", "gameplay"]
    assert entries[0]["turn_id"] == "start_scene_opening"
    assert entries[1]["turn_id"] == turn_id

    replay = client.post(f"/api/v1/sessions/{sid}/apply-turn-result", json=apply_body).json()
    assert replay["status"] == "applied"
    assert replay["idempotent_replay"] is True
    assert base.read_json("state/story_lines.json", sid, {})["turn_counter"] == 1
    history = base.read_json("state/scene_history.json", sid, [])
    entries = history.get("entries", []) if isinstance(history, dict) else history
    assert len(entries) == 2
    assert [entry["turn_id"] for entry in entries] == ["start_scene_opening", turn_id]

    rewritten = dict(apply_body)
    rewritten["visible_scene_text"] = "Другой текст для уже применённого хода."
    rejected = client.post(f"/api/v1/sessions/{sid}/apply-turn-result", json=rewritten).json()
    assert rejected["status"] == "rejected"


def test_apply_writes_evidence_events_and_bounded_relationship_delta(client: TestClient) -> None:
    sid = "evidence-apply-proof"
    client.post("/api/v1/start", json={"session_id": sid})
    turn_id = client.post(
        f"/api/v1/sessions/{sid}/turn",
        json={"player_input": "Спускаюсь на лестницу и смотрю на женщину внизу."},
    ).json()["turn_id"]
    load_all_context(client, sid, turn_id)

    unsourced = client.post(
        f"/api/v1/sessions/{sid}/apply-turn-result",
        json={
            "turn_id": turn_id,
            "visible_scene_text": "Эмма подняла взгляд на лестницу.",
            "character_memory_updates": [
                {"character_id": "emma", "knows_as_fact": ["Акира стоит на лестнице."]}
            ],
        },
    ).json()
    assert unsourced["status"] == "rejected"
    assert unsourced["validation_errors"][0]["code"] == "invalid_or_missing_source_type"
    assert base.read_turn_runtime(sid)["state_revision"] == 0
    assert base.read_turn_runtime(sid)["pending_turn"]["turn_id"] == turn_id

    calendar_leak = client.post(
        f"/api/v1/sessions/{sid}/apply-turn-result",
        json={
            "turn_id": turn_id,
            "visible_scene_text": "Эмма подняла взгляд на лестницу.",
            "character_memory_updates": [
                {
                    "character_id": "emma",
                    "events": [{
                        "kind": "fact",
                        "text": "Завтра прибудет новый отряд.",
                        "source_type": "calendar",
                        "evidence": "Сюжетный календарь.",
                    }],
                }
            ],
        },
    ).json()
    assert calendar_leak["validation_errors"][0]["code"] == "forbidden_knowledge_source"

    touch_mind_read = client.post(
        f"/api/v1/sessions/{sid}/apply-turn-result",
        json={
            "turn_id": turn_id,
            "visible_scene_text": "Ирэй коснулся её запястья и тут же отпустил.",
            "character_memory_updates": [
                {
                    "character_id": "irey",
                    "events": [{
                        "kind": "fact",
                        "text": "Ирэй узнал мысли и воспоминания Акиры.",
                        "source_type": "intentional_touch_sensory",
                        "evidence": "Касание дало только телесный сенсорный отклик.",
                    }],
                }
            ],
        },
    ).json()
    assert touch_mind_read["validation_errors"][0]["code"] == "fact_without_confirming_source"

    applied = client.post(
        f"/api/v1/sessions/{sid}/apply-turn-result",
        json={
            "turn_id": turn_id,
            "visible_scene_text": "Эмма подняла взгляд на лестницу и замолчала на полуслове.",
            "proposed_updates": {
                "character_memory_updates": [
                    {
                        "character_id": "emma",
                        "events": [
                            {
                                "kind": "fact",
                                "text": "Акира показалась на лестнице.",
                                "source_type": "direct_observation",
                                "evidence": "Эмма увидела Акиру на лестнице.",
                            },
                            {
                                "kind": "belief",
                                "text": "Акира может попытаться уйти.",
                                "source_type": "scene_inference",
                                "evidence": "Акира остановилась у выхода с лестницы.",
                            },
                        ],
                    }
                ],
                "relationship_pair_updates": [
                    {
                        "pair_id": "akira__emma",
                        "note": "Первый прямой зрительный контакт усилил давление.",
                        "tension": 3,
                        "evidence": "Эмма оборвала фразу, увидев Акиру.",
                    }
                ],
            },
        },
    ).json()
    memory = base.read_json("state/character_memory/emma.json", sid, {})
    pair = base.read_json("state/relationship_pairs/akira__emma.json", sid, {})

    assert applied["status"] == "applied"
    assert applied["state_update_summary"] == {
        "character_memory_files": 1,
        "relationship_pair_files": 1,
    }
    assert {event["kind"] for event in memory["memory_events"]} >= {"fact", "belief"}
    assert all(event["turn_id"] == turn_id for event in memory["memory_events"])
    assert all(event["evidence"] for event in memory["memory_events"])
    assert "Акира показалась на лестнице." in memory["знает_как_факт"]
    assert "Акира может попытаться уйти." in memory["предполагает"]
    assert pair["metrics"]["tension"] == 3
    assert pair["relationship_events"][-1]["turn_id"] == turn_id
    assert pair["relationship_events"][-1]["deltas"] == {"tension": 3.0}


def test_apply_rejects_unloaded_pair_large_delta_and_personality_rewrite(client: TestClient) -> None:
    sid = "state-scope-guard-proof"
    client.post("/api/v1/start", json={"session_id": sid})
    turn = client.post(
        f"/api/v1/sessions/{sid}/turn",
        json={
            "player_input": "Жду ответа Эммы.",
            "pov_character_id": "jun",
            "active_character_ids": ["jun", "emma"],
            "scene_character_ids": ["jun", "emma"],
            "present_character_ids": ["jun", "emma"],
            "addressed_character_ids": ["emma"],
            "relationship_pair_ids": ["jun__emma", "akira__raiden"],
        },
    ).json()
    turn_id = turn["turn_id"]
    contract, _manifest, _chunks = load_all_context(client, sid, turn_id)
    assert contract["relationship_pair_ids"] == ["jun__emma"]

    unloaded_pair = client.post(
        f"/api/v1/sessions/{sid}/apply-turn-result",
        json={
            "turn_id": turn_id,
            "visible_scene_text": "Эмма не ответила сразу.",
            "relationship_pair_updates": [{"pair_id": "akira__raiden", "tension": 1}],
        },
    ).json()
    assert unloaded_pair["validation_errors"][0]["code"] == "relationship_pair_not_loaded"

    absent_memory = client.post(
        f"/api/v1/sessions/{sid}/apply-turn-result",
        json={
            "turn_id": turn_id,
            "visible_scene_text": "Эмма не ответила сразу.",
            "character_memory_updates": [
                {"character_id": "akira", "memory": ["Узнала о разговоре, хотя отсутствовала."]}
            ],
        },
    ).json()
    assert absent_memory["validation_errors"][0]["code"] == "character_memory_not_loaded"

    large_delta = client.post(
        f"/api/v1/sessions/{sid}/apply-turn-result",
        json={
            "turn_id": turn_id,
            "visible_scene_text": "Эмма не ответила сразу.",
            "relationship_pair_updates": [{"pair_id": "jun__emma", "tension": 11}],
        },
    ).json()
    assert large_delta["validation_errors"][0]["code"] == "relationship_delta_too_large"

    personality = client.post(
        f"/api/v1/sessions/{sid}/apply-turn-result",
        json={
            "turn_id": turn_id,
            "visible_scene_text": "Эмма не ответила сразу.",
            "character_memory_updates": [
                {"character_id": "emma", "patch": {"personality": "Теперь всегда послушная."}}
            ],
        },
    ).json()
    assert personality["validation_errors"][0]["code"] == "static_character_rewrite_blocked"
    assert base.read_turn_runtime(sid)["state_revision"] == 0

    valid = client.post(
        f"/api/v1/sessions/{sid}/apply-turn-result",
        json={"turn_id": turn_id, "visible_scene_text": "Эмма выдержала паузу и ответила вопросом на вопрос."},
    ).json()
    assert valid["status"] == "applied"


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
    load_all_context(client, sid, turn_id)
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
    assert [entry["turn_id"] for entry in entries] == ["start_scene_opening", turn_id]


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
        _contract, _manifest, context_chunks = load_all_context(client, sid, turn_id)
        assert max(len(json.dumps(chunk, ensure_ascii=False)) for chunk in context_chunks) < 25_000
        body = {
            "turn_id": turn_id,
            "visible_scene_text": f"Тестовая сцена {number}.",
            "current_state_patch": {"current_scene_id": f"scene_{number}"},
            "proposed_updates": {
                "character_memory_updates": [
                    {"character_id": "emma", "memory": [f"Тестовое наблюдение {number}."]}
                ],
                "relationship_pair_updates": [
                    {
                        "pair_id": "akira__emma",
                        "note": f"Тестовое изменение отношений {number}.",
                        "tension": 1,
                    }
                ],
            },
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
    assert entries[0]["turn_id"] == "start_scene_opening"
    gameplay_entries = [entry for entry in entries if entry.get("kind") == "gameplay"]
    assert [entry["turn_id"] for entry in gameplay_entries] == turn_ids
    memory = base.read_json("state/character_memory/emma.json", sid, {})
    relationship = base.read_json("state/relationship_pairs/akira__emma.json", sid, {})
    assert len(memory["memory_events"]) == 30
    assert len({event["event_id"] for event in memory["memory_events"]}) == 30
    assert len(relationship["relationship_events"]) == 30
    assert relationship["metrics"]["tension"] == 30


def test_world_clock_rejects_backward_and_direct_time_rewrites(client: TestClient) -> None:
    sid = "clock-guard-proof"
    client.post("/api/v1/start", json={"session_id": sid})
    turn_id, _contract, _chunks = begin_ready_turn(client, sid, "Я остаюсь у двери и слушаю ещё немного.")

    backward = client.post(
        f"/api/v1/sessions/{sid}/apply-turn-result",
        json={
            "turn_id": turn_id,
            "visible_scene_text": "Внизу всё ещё говорили.",
            "time_advance": {
                "elapsed_minutes": -1,
                "mode": "scene",
                "reason": "ошибка",
                "evidence": "ошибка"
            },
        },
    ).json()
    assert backward["status"] == "rejected"
    assert backward["validation_errors"][0]["code"] == "time_cannot_move_backward"

    direct = client.post(
        f"/api/v1/sessions/{sid}/apply-turn-result",
        json={
            "turn_id": turn_id,
            "visible_scene_text": "Внизу всё ещё говорили.",
            "current_state_patch": {"current_datetime": "1206-09-10T12:00"},
        },
    ).json()
    assert direct["status"] == "rejected"
    assert direct["validation_errors"][0]["code"] == "direct_clock_patch_blocked"

    applied = client.post(
        f"/api/v1/sessions/{sid}/apply-turn-result",
        json={
            "turn_id": turn_id,
            "visible_scene_text": "За дверью прошла короткая тяжёлая пауза.",
            "time_advance": {
                "elapsed_minutes": 5,
                "mode": "scene",
                "reason": "короткая пауза в продолжающемся разговоре",
                "evidence": "Сцена показывает несколько минут ожидания."
            },
        },
    ).json()
    assert applied["status"] == "applied"
    assert applied["world_update_summary"]["elapsed_world_minutes"] == 5
    assert base.read_json("state/calendar_runtime.json", sid, {})["current_datetime"] == "1206-08-31T23:45"
    assert base.read_json("state/current_state.json", sid, {})["current_datetime"] == "1206-08-31T23:45"


def test_missed_event_changes_world_without_scripting_akira(client: TestClient) -> None:
    sid = "missed-event-proof"
    client.post("/api/v1/start", json={"session_id": sid})
    turn_id, _contract, _chunks = begin_ready_turn(client, sid, "Я медленно проверяю окно и не отвечаю людям внизу.")
    applied = client.post(
        f"/api/v1/sessions/{sid}/apply-turn-result",
        json={
            "turn_id": turn_id,
            "visible_scene_text": "Пока Акира проверяла раму, голоса внизу стали жёстче.",
            "time_advance": {
                "elapsed_minutes": 16,
                "mode": "scene",
                "reason": "осмотр окна и развитие конфликта внизу",
                "evidence": "В сцене проходит достаточно времени, чтобы тихое окно реакции закрылось."
            },
        },
    ).json()
    assert applied["status"] == "applied"
    runtime = base.read_json("state/calendar_runtime.json", sid, {})
    event = next(item for item in runtime["pending_events"] if item["event_id"] == "player_reaction_window")
    consequence = runtime["world_consequences"][-1]
    assert event["status"] == "missed"
    assert consequence["player_action_inferred"] is False
    assert consequence["player_thought_inferred"] is False
    assert consequence["character_knowledge_created"] is False
    assert "Акира" not in consequence["summary"]


def test_explicit_sleep_timeskip_updates_date_phase_and_current_day_only(client: TestClient) -> None:
    sid = "timeskip-proof"
    client.post("/api/v1/start", json={"session_id": sid})
    turn_id, _contract, _chunks = begin_ready_turn(
        client,
        sid,
        "Я закрываю дверь, ложусь спать и пропускаю время до утра.",
        time_intent={"mode": "sleep", "explicit": True, "requested_elapsed_minutes": 500},
    )
    frozen = client.post(
        f"/api/v1/sessions/{sid}/apply-turn-result",
        json={
            "turn_id": turn_id,
            "visible_scene_text": "Ночь просто исчезла, а все остались ждать на тех же местах.",
            "current_state_patch": {
                "active_character_ids": ["akira"],
                "scene_character_ids": ["akira"],
                "present_character_ids": ["akira"]
            },
            "time_advance": {
                "elapsed_minutes": 500,
                "mode": "sleep",
                "reason": "явный сон и переход к утру",
                "evidence": "Игрок прямо выбрал сон и пропуск времени до утра."
            },
            "event_updates": [{
                "event_id": "player_reaction_window",
                "action": "resolve",
                "evidence": "Игрок закрыл дверь и выбрал не продолжать немедленное взаимодействие."
            }]
        },
    ).json()
    assert frozen["status"] == "rejected"
    assert any(error["code"] == "large_timeskip_freezes_present_npcs" for error in frozen["validation_errors"])

    body = {
        "turn_id": turn_id,
        "visible_scene_text": "Ночь прошла. Серый утренний свет лёг на край стола.",
        "current_state_patch": {
            "active_character_ids": ["akira"],
            "scene_character_ids": ["akira"],
            "present_character_ids": ["akira"]
        },
        "time_advance": {
            "elapsed_minutes": 500,
            "mode": "sleep",
            "reason": "явный сон и переход к утру",
            "evidence": "Игрок прямо выбрал сон и пропуск времени до утра.",
            "target_datetime": "1206-09-01T08:00"
        },
        "event_updates": [{
            "event_id": "player_reaction_window",
            "action": "resolve",
            "evidence": "Игрок закрыл дверь и выбрал не продолжать немедленное взаимодействие."
        }],
        "npc_autonomy_updates": [
            {
                "character_id": "jun",
                "action": "set_activity",
                "activity": "deal_with_house_pressure_offscreen",
                "category": "scene_duty",
                "availability": "offscreen",
                "evidence": "После закрытия двери Джун продолжил собственное противостояние внизу."
            },
            {
                "character_id": "emma",
                "action": "set_activity",
                "activity": "continue_mission_offscreen",
                "category": "scene_goal",
                "availability": "offscreen",
                "evidence": "Эмма не остаётся ждать игрока и продолжает свою задачу."
            },
            {
                "character_id": "irey",
                "action": "set_activity",
                "activity": "contain_risk_offscreen",
                "category": "scene_goal",
                "availability": "offscreen",
                "evidence": "Ирэй продолжает действовать по своей цели вне комнаты Акиры."
            }
        ]
    }
    applied = client.post(f"/api/v1/sessions/{sid}/apply-turn-result", json=body).json()
    assert applied["status"] == "applied"
    runtime = base.read_json("state/calendar_runtime.json", sid, {})
    current = base.read_json("state/current_state.json", sid, {})
    assert runtime["current_datetime"] == current["current_datetime"] == "1206-09-01T08:00"
    assert runtime["current_day_phase"] == "утро"
    assert runtime["current_day_file"] == "calendar/days/1206-09-01.yaml"
    assert "1206-09-02.yaml" not in json.dumps(runtime, ensure_ascii=False)

    replay = client.post(f"/api/v1/sessions/{sid}/apply-turn-result", json=body).json()
    assert replay["idempotent_replay"] is True
    assert base.read_json("state/calendar_runtime.json", sid, {})["elapsed_world_minutes"] == 500


def test_raiden_trigger_travel_eta_and_delayed_arrival(client: TestClient) -> None:
    sid = "raiden-eta-proof"
    client.post("/api/v1/start", json={"session_id": sid})
    first_id, _contract, _chunks = begin_ready_turn(client, sid, "Чужая энергия внизу резко рвёт тишину.")
    started = client.post(
        f"/api/v1/sessions/{sid}/apply-turn-result",
        json={
            "turn_id": first_id,
            "visible_scene_text": "Выброс прошёл сквозь дом. Далеко у моря Райден поднял голову и двинулся к мотоциклу.",
            "time_advance": {
                "elapsed_minutes": 2,
                "mode": "scene",
                "reason": "короткий энергетический выброс и реакция вне кадра",
                "evidence": "Сцена подтверждает чужой кайросский выброс."
            },
            "event_updates": [
                {
                    "event_id": "player_reaction_window",
                    "action": "resolve",
                    "evidence": "Сцена перешла из тихого окна в открытый энергетический кризис."
                },
                {
                    "event_id": "raiden_delayed_conditional_arrival",
                    "action": "trigger",
                    "evidence": "Райден почувствовал подтверждённый чужой кайросский выброс, не зная об Акире."
                }
            ],
            "npc_autonomy_updates": [{
                "character_id": "raiden",
                "action": "start_travel",
                "from_location_id": "east_coast",
                "destination_location_id": "jun_house_exterior",
                "travel_minutes": 18,
                "evidence": "Райден едет проверять направление выброса как опытный рейдер."
            }]
        },
    ).json()
    assert started["status"] == "applied"
    state = base.read_json("state/calendar_runtime.json", sid, {})["npc_autonomy"]["raiden"]
    assert state["availability"] == "in_transit"
    assert state["earliest_arrival_at"] == "1206-09-01T00:00"

    second_id, _contract, _chunks = begin_ready_turn(client, sid, "Жду ещё немного, прислушиваясь к дороге.")
    early = client.post(
        f"/api/v1/sessions/{sid}/apply-turn-result",
        json={
            "turn_id": second_id,
            "visible_scene_text": "У дома появился мотоцикл.",
            "time_advance": {
                "elapsed_minutes": 10,
                "mode": "wait",
                "reason": "ожидание",
                "evidence": "Проходит десять минут."
            },
            "npc_autonomy_updates": [{
                "character_id": "raiden",
                "action": "arrive",
                "evidence": "Райден доехал до источника выброса."
            }]
        },
    ).json()
    assert early["status"] == "rejected"
    assert early["validation_errors"][0]["code"] == "arrival_before_eta"

    arrived = client.post(
        f"/api/v1/sessions/{sid}/apply-turn-result",
        json={
            "turn_id": second_id,
            "visible_scene_text": "Только к полуночи звук мотоцикла дошёл до улицы у дома.",
            "time_advance": {
                "elapsed_minutes": 18,
                "mode": "wait",
                "reason": "ожидание правдоподобного времени дороги",
                "evidence": "Игрок ждёт, пока проходит полный минимальный путь."
            },
            "npc_autonomy_updates": [{
                "character_id": "raiden",
                "action": "arrive",
                "destination_location_id": "jun_house_exterior",
                "availability": "nearby",
                "evidence": "Райден завершил путь после ETA и остановился у района выброса."
            }]
        },
    ).json()
    assert arrived["status"] == "applied"
    state = base.read_json("state/calendar_runtime.json", sid, {})["npc_autonomy"]["raiden"]
    assert state["location_id"] == "jun_house_exterior"
    assert state["availability"] == "nearby"
    assert "earliest_arrival_at" not in state


def test_unavailable_npc_is_reference_only_and_cannot_be_teleported_present(client: TestClient) -> None:
    sid = "availability-proof"
    client.post("/api/v1/start", json={"session_id": sid})
    turn_id, contract, chunks = begin_ready_turn(
        client,
        sid,
        "Рэй, ответьте мне.",
        active_character_ids=["akira", "jun", "emma", "irey", "ray"],
        scene_character_ids=["akira", "jun", "emma", "irey", "ray"],
        present_character_ids=["akira", "jun", "emma", "irey", "ray"],
        addressed_character_ids=["ray"],
    )
    assert contract["character_roles"]["ray"] == "referenced"
    diagnostic = next(item for item in contract["builder_diagnostics"] if item["fallback_blocked"] == "npc_teleport_or_unavailable_presence")
    assert diagnostic["characters"][0]["character_id"] == "ray"
    world_chunk = next(chunk for chunk in chunks if chunk["chunk_type"] == "location_inventory_calendar_render")
    world = world_chunk["content"]["calendar_and_npc_autonomy"]
    assert world["current_day_file"] == "calendar/days/1206-08-31.yaml"
    assert world["npc_autonomy"]["ray"]["location_zone"] == "east_sector"

    rejected = client.post(
        f"/api/v1/sessions/{sid}/apply-turn-result",
        json={
            "turn_id": turn_id,
            "visible_scene_text": "Рэй внезапно оказался в комнате.",
            "current_state_patch": {
                "present_character_ids": ["akira", "jun", "emma", "irey", "ray"]
            }
        },
    ).json()
    assert rejected["status"] == "rejected"
    assert any(error["code"] == "npc_presence_without_arrival" for error in rejected["validation_errors"])


def test_autonomy_never_controls_akira_or_gives_offscreen_scene_knowledge(client: TestClient) -> None:
    sid = "autonomy-scope-proof"
    client.post("/api/v1/start", json={"session_id": sid})
    memory_before = base.read_json("state/character_memory/raiden.json", sid, {})
    turn_id, _contract, _chunks = begin_ready_turn(client, sid, "Я остаюсь в комнате.")
    blocked = client.post(
        f"/api/v1/sessions/{sid}/apply-turn-result",
        json={
            "turn_id": turn_id,
            "visible_scene_text": "Акира осталась у двери.",
            "npc_autonomy_updates": [{
                "character_id": "akira",
                "action": "set_activity",
                "activity": "obey_ray",
                "category": "duty",
                "evidence": "Так удобнее сцене."
            }]
        },
    ).json()
    assert blocked["status"] == "rejected"
    assert blocked["validation_errors"][0]["code"] == "player_character_autonomy_blocked"

    applied = client.post(
        f"/api/v1/sessions/{sid}/apply-turn-result",
        json={
            "turn_id": turn_id,
            "visible_scene_text": "Акира осталась у двери. В Восточном секторе Рэй продолжал ночную работу, ничего об этой паузе не зная.",
            "npc_autonomy_updates": [{
                "character_id": "ray",
                "action": "set_activity",
                "activity": "continue_command_duty",
                "category": "duty",
                "availability": "busy",
                "evidence": "Рэй остаётся на своей командной работе вне сцены."
            }]
        },
    ).json()
    assert applied["status"] == "applied"
    assert base.read_json("state/character_memory/raiden.json", sid, {}) == memory_before


def test_until_16_accountability_allows_rest_but_flags_only_all_day_drift(client: TestClient) -> None:
    sid = "accountability-proof"
    client.post("/api/v1/start", json={"session_id": sid})
    calendar_state = base.read_json("state/calendar_runtime.json", sid, {})
    calendar_state.update({
        "current_datetime": "1206-09-01T08:00",
        "current_date": "1206-09-01",
        "current_time": "08:00",
        "current_day_phase": "утро",
        "time_of_day": "утро",
        "current_day_file": "calendar/days/1206-09-01.yaml",
        "pending_events": [],
    })
    current = base.read_json("state/current_state.json", sid, {})
    current.update({
        "current_datetime": "1206-09-01T08:00",
        "current_date": "1206-09-01",
        "date": "1206-09-01",
        "current_time": "08:00",
        "current_day_phase": "утро",
        "time_of_day": "утро",
        "active_character_ids": ["akira"],
        "scene_character_ids": ["akira"],
        "present_character_ids": ["akira"],
    })
    base.write_json("state/calendar_runtime.json", calendar_state, sid)
    base.write_json("state/current_state.json", current, sid)

    turn_id, _contract, _chunks = begin_ready_turn(
        client,
        sid,
        "Жду до четырёх часов дня, пока база живёт своими делами.",
        time_intent={"mode": "wait", "explicit": True, "requested_elapsed_minutes": 485},
    )
    applied = client.post(
        f"/api/v1/sessions/{sid}/apply-turn-result",
        json={
            "turn_id": turn_id,
            "visible_scene_text": "К четырём дня движение на базе сменило ритм.",
            "time_advance": {
                "elapsed_minutes": 485,
                "mode": "wait",
                "reason": "явное ожидание до времени после 16:00",
                "evidence": "Игрок прямо ждёт, пока проходит рабочий отрезок дня."
            },
            "npc_autonomy_updates": [
                {
                    "character_id": "alex",
                    "action": "record_activity",
                    "activity": "rest_and_personal_time",
                    "category": "rest",
                    "duration_minutes": 480,
                    "evidence": "Алекс весь доступный отрезок провела только в отдыхе без рабочего блока."
                },
                {
                    "character_id": "miki",
                    "action": "record_activity",
                    "activity": "squad_training",
                    "category": "training",
                    "duration_minutes": 60,
                    "evidence": "Мики провела содержательную часовую тренировку, а остальное время распорядилась свободно."
                }
            ]
        },
    ).json()
    assert applied["status"] == "applied"
    runtime = base.read_json("state/calendar_runtime.json", sid, {})
    observations = runtime["staff_observations"]
    assert any(item["character_id"] == "alex" for item in observations)
    assert not any(item["character_id"] == "miki" for item in observations)
    alex_observation = next(item for item in observations if item["character_id"] == "alex")
    assert alex_observation["forced_action"] is False
    assert alex_observation["character_knowledge_created"] is False
    assert any(item["character_id"] == "alex" and item["category"] == "rest" for item in runtime["activity_ledger"])


def test_twelve_timed_turns_keep_clock_revision_and_chunks_bounded(client: TestClient) -> None:
    sid = "timed-long-run-proof"
    client.post("/api/v1/start", json={"session_id": sid})
    for number in range(1, 13):
        turn_id, _contract, chunks = begin_ready_turn(client, sid, f"Жду пять минут. Шаг {number}.")
        assert max(len(json.dumps(chunk, ensure_ascii=False)) for chunk in chunks) < 25_000
        applied = client.post(
            f"/api/v1/sessions/{sid}/apply-turn-result",
            json={
                "turn_id": turn_id,
                "visible_scene_text": f"Прошло ещё пять минут. Шаг {number}.",
                "time_advance": {
                    "elapsed_minutes": 5,
                    "mode": "wait",
                    "reason": "короткое явное ожидание",
                    "evidence": "Игрок ждёт пять минут."
                }
            },
        ).json()
        assert applied["status"] == "applied"
        assert applied["state_revision"] == number
        if number in {4, 8, 12}:
            replay = client.post(
                f"/api/v1/sessions/{sid}/apply-turn-result",
                json={
                    "turn_id": turn_id,
                    "visible_scene_text": f"Прошло ещё пять минут. Шаг {number}.",
                    "time_advance": {
                        "elapsed_minutes": 5,
                        "mode": "wait",
                        "reason": "короткое явное ожидание",
                        "evidence": "Игрок ждёт пять минут."
                    }
                },
            ).json()
            assert replay["idempotent_replay"] is True

    runtime = base.read_json("state/calendar_runtime.json", sid, {})
    assert runtime["current_datetime"] == "1206-09-01T00:40"
    assert runtime["elapsed_world_minutes"] == 60
    assert runtime["time_revision"] == 12
    assert base.read_turn_runtime(sid)["state_revision"] == 12
