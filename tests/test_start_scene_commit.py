from __future__ import annotations

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


def start_and_commit(client: TestClient, sid: str) -> dict:
    started = client.post("/api/v1/start", json={"session_id": sid}).json()
    assert started["success"] is True
    opening = client.get(f"/api/v3/sessions/{sid}/start-scene-text").json()
    assert opening["exact_text_required"] is True
    assert opening["visible_scene_output_allowed"] is False
    committed = client.post(
        f"/api/v1/sessions/{sid}/commit-start-scene",
        json={
            "expected_state_revision": opening["state_revision"],
            "exact_text_sha256": opening["exact_text_sha256"],
        },
    ).json()
    assert committed["status"] == "start_scene_committed"
    return committed


def load_all_context(client: TestClient, sid: str, turn_id: str) -> dict:
    contract = client.post(
        f"/api/v3/sessions/{sid}/turn-contract",
        json={"turn_id": turn_id},
    ).json()
    assert contract["success"] is True
    manifest = client.post(
        f"/api/v3/sessions/{sid}/required-context/manifest",
        json={"turn_id": turn_id, "turn_contract": contract},
    ).json()
    for index in range(manifest["total_chunks"]):
        chunk = client.post(
            f"/api/v3/sessions/{sid}/required-context/chunk",
            json={
                "turn_id": turn_id,
                "chunk_index": index,
                "turn_contract": contract,
            },
        ).json()
        assert chunk["success"] is True
    return contract


def test_start_scene_is_committed_before_visible_output(client: TestClient) -> None:
    sid = "opening-commit"
    client.post("/api/v1/start", json={"session_id": sid})
    preflight = client.get(f"/api/v3/sessions/{sid}/preflight").json()
    opening = client.get(f"/api/v3/sessions/{sid}/start-scene-text").json()

    assert preflight["next_action"] == "getStartSceneText"
    assert preflight["visible_scene_output_allowed"] is False
    assert opening["next_action"] == "commitStartScene"
    assert opening["state_revision"] == 0
    assert opening["exact_text"]
    assert opening["exact_text_sha256"]
    assert opening["visible_scene_output_allowed"] is False

    missing_revision = client.post(
        f"/api/v1/sessions/{sid}/commit-start-scene",
        json={"exact_text_sha256": opening["exact_text_sha256"]},
    ).json()
    assert missing_revision["status"] == "start_scene_expected_revision_required"

    wrong_hash = client.post(
        f"/api/v1/sessions/{sid}/commit-start-scene",
        json={
            "expected_state_revision": 0,
            "exact_text_sha256": "bad",
        },
    ).json()
    assert wrong_hash["status"] == "start_scene_hash_mismatch"

    committed = client.post(
        f"/api/v1/sessions/{sid}/commit-start-scene",
        json={
            "expected_state_revision": 0,
            "exact_text_sha256": opening["exact_text_sha256"],
        },
    ).json()
    assert committed["success"] is True
    assert committed["state_revision"] == 1
    assert committed["visible_scene_output_allowed"] is True
    assert committed["visible_scene_text"] == opening["exact_text"]
    assert committed["rollback_available"] is True

    current = base.read_session_json("state/current_state.json", sid, default={})
    runtime = base.read_turn_runtime(sid)
    history = base.read_session_json("state/scene_history.json", sid, default=[])
    entries = history.get("entries", []) if isinstance(history, dict) else history
    assert current["start_scene_completed"] is True
    assert current["start_scene_exact_text_required"] is False
    assert current["state_revision"] == 1
    assert runtime["state_revision"] == 1
    assert runtime["last_state_transition"]["kind"] == "start_scene"
    assert runtime["last_start_scene_commit"]["exact_text_sha256"] == opening["exact_text_sha256"]
    assert len(entries) == 1
    assert entries[0]["kind"] == "opening"
    assert entries[0]["visible_scene_text"] == opening["exact_text"]


def test_start_scene_commit_is_idempotent(client: TestClient) -> None:
    sid = "opening-idempotent"
    committed = start_and_commit(client, sid)
    replay = client.post(
        f"/api/v1/sessions/{sid}/commit-start-scene",
        json={
            "expected_state_revision": 0,
            "exact_text_sha256": committed["exact_text_sha256"],
        },
    ).json()
    assert replay["success"] is True
    assert replay["idempotent_replay"] is True
    assert replay["state_revision"] == 1
    history = base.read_session_json("state/scene_history.json", sid, default=[])
    entries = history.get("entries", []) if isinstance(history, dict) else history
    assert len(entries) == 1


def test_first_player_turn_starts_from_committed_opening_revision(client: TestClient) -> None:
    sid = "opening-first-turn"
    start_and_commit(client, sid)
    turn = client.post(
        f"/api/v1/sessions/{sid}/turn",
        json={"player_input": "Остаюсь у двери и слушаю дальше."},
    ).json()
    assert turn["success"] is True
    assert turn["base_revision"] == 1
    assert turn["state_revision"] == 1
    contract = load_all_context(client, sid, turn["turn_id"])
    assert contract["current_frame"]["start_scene_completed"] is True
    assert contract["base_revision"] == 1


def test_start_scene_rollback_restores_opening_gate(client: TestClient) -> None:
    sid = "opening-rollback"
    committed = start_and_commit(client, sid)
    rolled = client.post(
        f"/api/v1/sessions/{sid}/rollback-last-turn",
        json={
            "expected_state_revision": 1,
            "reason": "replay canonical opening",
        },
    ).json()
    assert rolled["status"] == "rolled_back"
    assert rolled["rolled_back_turn_id"] == "start_scene_opening"
    assert rolled["state_revision"] == 2
    assert rolled["next_action"] == "getPreflight"

    current = base.read_session_json("state/current_state.json", sid, default={})
    history = base.read_session_json("state/scene_history.json", sid, default=[])
    entries = history.get("entries", []) if isinstance(history, dict) else history
    assert current["start_scene_completed"] is False
    assert current["start_scene_exact_text_required"] is True
    assert entries == []
    preflight = client.get(f"/api/v3/sessions/{sid}/preflight").json()
    assert preflight["next_action"] == "getStartSceneText"
    assert committed["rollback_snapshot_file"]


def test_legacy_first_turn_backfills_opening_history(client: TestClient) -> None:
    sid = "opening-compatibility"
    client.post("/api/v1/start", json={"session_id": sid})
    turn = client.post(
        f"/api/v1/sessions/{sid}/turn",
        json={"player_input": "Приоткрываю дверь ещё немного."},
    ).json()
    assert turn["success"] is True
    load_all_context(client, sid, turn["turn_id"])
    applied = client.post(
        f"/api/v1/sessions/{sid}/apply-turn-result",
        json={
            "turn_id": turn["turn_id"],
            "visible_scene_text": (
                "Акира задержалась у двери, прислушиваясь к голосам снизу. "
                "Комната осталась тихой, а решение всё ещё принадлежало ей."
            ),
            "scene_continuity_patch": {"opening_compatibility_probe": True},
        },
    ).json()
    assert applied["status"] == "applied"
    history = base.read_session_json("state/scene_history.json", sid, default=[])
    entries = history.get("entries", []) if isinstance(history, dict) else history
    assert [entry["kind"] for entry in entries[:2]] == ["opening", "gameplay"]
    assert entries[0]["commit_mode"] == "compatibility_backfill_with_first_gameplay_apply"


def test_openapi_exposes_commit_start_scene(client: TestClient) -> None:
    schema = client.get("/openapi-actions.json").json()
    assert schema["info"]["version"] == "0.11.0-v3-start-scene-commit"
    action = schema["paths"]["/api/v1/sessions/{session_id}/commit-start-scene"]["post"]
    assert action["operationId"] == "commitStartScene"
    request = action["requestBody"]["content"]["application/json"]["schema"]
    assert request["required"] == ["expected_state_revision", "exact_text_sha256"]
