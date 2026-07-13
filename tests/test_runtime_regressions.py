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
    contract = client.post(
        f"/api/v3/sessions/{sid}/turn-contract",
        json={"player_input": "Что такое кайросы и энергия?"},
    ).json()
    lore_index = next(
        item["chunk_index"]
        for item in contract["required_chunks"]
        if item["chunk_type"] == "world_lore_minimal"
    )
    chunk = client.post(
        f"/api/v3/sessions/{sid}/required-context/chunk",
        json={"chunk_index": lore_index, "turn_contract": contract},
    ).json()

    files = chunk["content"]["files"]
    assert files
    assert all((base.REPO_ROOT / item["path"]).is_file() for item in files)
