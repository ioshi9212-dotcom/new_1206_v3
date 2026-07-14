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


def ready_turn(client: TestClient, sid: str, text: str) -> str:
    created = client.post(
        f"/api/v1/sessions/{sid}/turn",
        json={"player_input": text},
    ).json()
    assert created["success"] is True
    turn_id = created["turn_id"]
    contract = client.post(
        f"/api/v3/sessions/{sid}/turn-contract",
        json={"turn_id": turn_id},
    ).json()
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
    return turn_id


def apply_marker(client: TestClient, sid: str, turn_id: str, marker: str) -> dict:
    return client.post(
        f"/api/v1/sessions/{sid}/apply-turn-result",
        json={
            "turn_id": turn_id,
            "visible_scene_text": (
                f"Маркер {marker}: в комнате изменилась только одна "
                "наблюдаемая деталь, без решения за героя."
            ),
            "current_state_patch": {"repair_probe": marker},
            "scene_continuity_patch": {"repair_probe": marker},
            "change_reason": f"repair fixture {marker}",
        },
    ).json()


def test_exact_repair_quarantines_tampering_and_preserves_latest_turn(
    client: TestClient,
) -> None:
    sid = "repair-exact"
    client.post("/api/v1/start", json={"session_id": sid})
    turn_id = ready_turn(client, sid, "Осматриваю комнату.")
    applied = apply_marker(client, sid, turn_id, "trusted")
    assert applied["status"] == "applied"

    snapshot = base.read_session_json(
        applied["rollback_snapshot_file"],
        sid,
        default={},
    )
    assert snapshot["schema"] == "turn_revision_snapshot_v2"
    assert snapshot["after"]["state/current_state.json"]["data"]["repair_probe"] == "trusted"

    damaged = base.read_session_json("state/current_state.json", sid, default={})
    damaged["repair_probe"] = "tampered"
    base.write_json("state/current_state.json", damaged, session_id=sid)

    integrity = client.get(f"/api/v1/sessions/{sid}/integrity").json()
    assert integrity["status"] == "integrity_errors"
    assert integrity["repair_available"] is True
    assert integrity["repair_plan"]["mode"] == "exact_after_snapshot"
    assert integrity["repair_plan"]["requires_allow_turn_loss"] is False

    confirmation = client.post(
        f"/api/v1/sessions/{sid}/repair-state",
        json={"expected_state_revision": 1},
    ).json()
    assert confirmation["status"] == "repair_confirmation_required"

    repaired = client.post(
        f"/api/v1/sessions/{sid}/repair-state",
        json={
            "expected_state_revision": 1,
            "confirm_repair": True,
            "reason": "test exact repair",
        },
    ).json()
    assert repaired["status"] == "repaired"
    assert repaired["state_revision"] == 2
    assert repaired["repair_mode"] == "exact_after_snapshot"
    assert repaired["integrity_after_repair"]["status"] == "healthy"

    restored = base.read_session_json("state/current_state.json", sid, default={})
    assert restored["repair_probe"] == "trusted"
    assert restored["state_revision"] == 2

    quarantine = base.read_session_json(repaired["quarantine_file"], sid, default={})
    assert quarantine["schema"] == "session_quarantine_v1"
    assert (
        quarantine["canonical_state_images"]["state/current_state.json"]["data"]["repair_probe"]
        == "tampered"
    )
    history = base.read_session_json("state/repair_history.json", sid, default={})
    assert history["entries"][-1]["mode"] == "exact_after_snapshot"


def test_unreadable_json_is_preserved_raw_in_quarantine_and_repaired(
    client: TestClient,
) -> None:
    sid = "repair-invalid-json"
    client.post("/api/v1/start", json={"session_id": sid})
    turn_id = ready_turn(client, sid, "Остаюсь на месте.")
    assert apply_marker(client, sid, turn_id, "valid")["status"] == "applied"

    base.write_text(
        "state/current_state.json",
        '{"broken": ',
        session_id=sid,
    )
    integrity = client.get(f"/api/v1/sessions/{sid}/integrity").json()
    assert integrity["status"] == "integrity_errors"
    assert integrity["repair_available"] is True

    repaired = client.post(
        f"/api/v1/sessions/{sid}/repair-state",
        json={
            "expected_state_revision": 1,
            "confirm_repair": True,
            "reason": "invalid json repair",
        },
    ).json()
    assert repaired["status"] == "repaired"
    quarantine = base.read_session_json(repaired["quarantine_file"], sid, default={})
    image = quarantine["canonical_state_images"]["state/current_state.json"]
    assert image["json_valid"] is False
    assert image["raw_text"] == '{"broken": '
    restored = base.read_session_json("state/current_state.json", sid, default={})
    assert restored["repair_probe"] == "valid"


def test_pending_turn_requires_explicit_discard_before_repair(
    client: TestClient,
) -> None:
    sid = "repair-pending"
    client.post("/api/v1/start", json={"session_id": sid})
    first = ready_turn(client, sid, "Смотрю в окно.")
    assert apply_marker(client, sid, first, "first")["status"] == "applied"

    current = base.read_session_json("state/current_state.json", sid, default={})
    current["repair_probe"] = "damaged"
    base.write_json("state/current_state.json", current, session_id=sid)
    pending = client.post(
        f"/api/v1/sessions/{sid}/turn",
        json={"player_input": "Подхожу к двери."},
    ).json()
    assert pending["success"] is True

    blocked = client.post(
        f"/api/v1/sessions/{sid}/repair-state",
        json={
            "expected_state_revision": 1,
            "confirm_repair": True,
        },
    ).json()
    assert blocked["status"] == "repair_pending_turn_confirmation_required"
    assert blocked["pending_turn_id"] == pending["turn_id"]

    repaired = client.post(
        f"/api/v1/sessions/{sid}/repair-state",
        json={
            "expected_state_revision": 1,
            "confirm_repair": True,
            "discard_pending_turn": True,
            "reason": "discard damaged pending repair",
        },
    ).json()
    assert repaired["status"] == "repaired"
    assert repaired["discarded_pending_turn_id"] == pending["turn_id"]
    assert base.read_turn_runtime(sid)["pending_turn"] is None


def test_repair_dry_run_never_creates_quarantine_or_changes_revision(
    client: TestClient,
) -> None:
    sid = "repair-dry-run"
    client.post("/api/v1/start", json={"session_id": sid})
    turn_id = ready_turn(client, sid, "Проверяю стол.")
    assert apply_marker(client, sid, turn_id, "stable")["status"] == "applied"
    current = base.read_session_json("state/current_state.json", sid, default={})
    current["repair_probe"] = "damaged"
    base.write_json("state/current_state.json", current, session_id=sid)

    dry = client.post(
        f"/api/v1/sessions/{sid}/repair-state",
        json={
            "expected_state_revision": 1,
            "confirm_repair": True,
            "dry_run": True,
        },
    ).json()
    assert dry["status"] == "repair_validated_dry_run"
    assert dry["would_quarantine"] is True
    assert base.read_turn_runtime(sid)["state_revision"] == 1
    quarantine_dir = base._safe_session_target(sid, "state/quarantine")
    assert not quarantine_dir.exists()


def test_repair_is_blocked_for_healthy_session(client: TestClient) -> None:
    sid = "repair-healthy"
    client.post("/api/v1/start", json={"session_id": sid})
    result = client.post(
        f"/api/v1/sessions/{sid}/repair-state",
        json={
            "expected_state_revision": 0,
            "confirm_repair": True,
        },
    ).json()
    assert result["status"] == "repair_not_required"


def test_legacy_snapshot_requires_explicit_turn_loss_confirmation(
    client: TestClient,
) -> None:
    sid = "repair-legacy"
    client.post("/api/v1/start", json={"session_id": sid})
    turn_id = ready_turn(client, sid, "Запоминаю положение двери.")
    applied = apply_marker(client, sid, turn_id, "legacy-current")
    snapshot_path = applied["rollback_snapshot_file"]
    snapshot = base.read_session_json(snapshot_path, sid, default={})
    snapshot["schema"] = "turn_revision_snapshot_v1"
    snapshot.pop("after", None)
    snapshot.pop("snapshot_sha256", None)
    base.write_json(snapshot_path, snapshot, session_id=sid)

    current = base.read_session_json("state/current_state.json", sid, default={})
    current["repair_probe"] = "damaged"
    base.write_json("state/current_state.json", current, session_id=sid)

    integrity = client.get(f"/api/v1/sessions/{sid}/integrity").json()
    assert integrity["repair_plan"]["mode"] == "legacy_before_snapshot"
    assert integrity["repair_plan"]["requires_allow_turn_loss"] is True

    blocked = client.post(
        f"/api/v1/sessions/{sid}/repair-state",
        json={
            "expected_state_revision": 1,
            "confirm_repair": True,
        },
    ).json()
    assert blocked["status"] == "repair_turn_loss_confirmation_required"

    repaired = client.post(
        f"/api/v1/sessions/{sid}/repair-state",
        json={
            "expected_state_revision": 1,
            "confirm_repair": True,
            "allow_turn_loss": True,
            "reason": "legacy fallback",
        },
    ).json()
    assert repaired["status"] == "repaired"
    restored = base.read_session_json("state/current_state.json", sid, default={})
    assert restored.get("repair_probe") != "legacy-current"


def test_openapi_exposes_strict_repair_action(client: TestClient) -> None:
    schema = client.get("/openapi-actions.json").json()
    assert schema["info"]["version"] == "0.11.0-v3-start-scene-commit"
    repair = schema["paths"]["/api/v1/sessions/{session_id}/repair-state"]["post"]
    assert repair["operationId"] == "repairSessionState"
    request = repair["requestBody"]["content"]["application/json"]["schema"]
    assert set(
        (
            "expected_state_revision",
            "confirm_repair",
            "discard_pending_turn",
            "allow_turn_loss",
            "dry_run",
            "reason",
        )
    ).issubset(request["properties"])
