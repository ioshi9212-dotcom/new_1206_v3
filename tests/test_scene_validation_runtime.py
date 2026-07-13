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


def ready_turn(client: TestClient, sid: str, text: str, **fields: object) -> str:
    turn = client.post(
        f"/api/v1/sessions/{sid}/turn",
        json={"player_input": text, **fields},
    ).json()
    assert turn["success"] is True
    turn_id = turn["turn_id"]
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
            json={"turn_id": turn_id, "chunk_index": index, "turn_contract": contract},
        ).json()
        assert chunk["success"] is True
    return turn_id


def test_valid_scene_passes_gate_and_applies(client: TestClient) -> None:
    sid = "scene-gate-valid"
    client.post("/api/v1/start", json={"session_id": sid})
    turn_id = ready_turn(client, sid, "Молча смотрю на Джуна.")

    result = client.post(
        f"/api/v1/sessions/{sid}/apply-turn-result",
        json={
            "turn_id": turn_id,
            "visible_scene_text": "Джун задержал взгляд на лестнице. Внизу по-прежнему говорили вполголоса.",
            "safety_checks": {
                "used_only_loaded_characters": True,
                "respected_knowledge_boundaries": True,
                "no_hidden_past_without_trigger": True,
                "no_unjustified_character_arrival": True,
                "no_major_pov_choice_for_player": True,
                "no_major_akira_choice_for_player": True,
                "akira_non_pov_actions_are_low_stakes": True,
                "clock_changed_only_through_elapsed_minutes": True,
                "npc_routes_respect_eta": True,
                "missed_events_do_not_script_pov": True,
            },
        },
    ).json()

    assert result["status"] == "applied"
    assert result["scene_validation"]["passed"] is True
    assert result["visible_scene_output_allowed"] is True


def test_internal_json_is_rejected_and_same_turn_can_be_rewritten(client: TestClient) -> None:
    sid = "scene-gate-json-rewrite"
    client.post("/api/v1/start", json={"session_id": sid})
    turn_id = ready_turn(client, sid, "Подхожу к двери.")

    rejected = client.post(
        f"/api/v1/sessions/{sid}/apply-turn-result",
        json={
            "turn_id": turn_id,
            "visible_scene_text": '{"proposed_updates": {"current_state_patch": {"x": 1}}}',
        },
    ).json()
    assert rejected["status"] == "rewrite_required"
    assert rejected["pending_turn_preserved"] is True
    assert rejected["visible_scene_output_allowed"] is False
    assert any(item["code"] == "internal_json_visible" for item in rejected["scene_validation"]["errors"])
    assert base.get_pending_turn(sid)["turn_id"] == turn_id

    applied = client.post(
        f"/api/v1/sessions/{sid}/apply-turn-result",
        json={
            "turn_id": turn_id,
            "visible_scene_text": "Дверь осталась закрытой. За ней на секунду стихли голоса.",
            "scene_validation": {"repair_attempt": 1},
        },
    ).json()
    assert applied["status"] == "applied"
    assert applied["turn_id"] == turn_id


def test_unloaded_known_character_cannot_speak(client: TestClient) -> None:
    sid = "scene-gate-unloaded-speaker"
    client.post("/api/v1/start", json={"session_id": sid})
    turn_id = ready_turn(
        client,
        sid,
        "Смотрю на Джуна.",
        pov_character_id="akira",
        active_character_ids=["akira", "jun"],
        scene_character_ids=["akira", "jun"],
        present_character_ids=["akira", "jun"],
    )

    result = client.post(
        f"/api/v1/sessions/{sid}/apply-turn-result",
        json={
            "turn_id": turn_id,
            "visible_scene_text": "**Райден** — Я всё слышал.\n\nДжун резко обернулся.",
        },
    ).json()

    assert result["status"] == "rewrite_required"
    assert any(item["code"] == "unloaded_character_spoke" for item in result["scene_validation"]["errors"])


def test_non_pov_akira_major_choice_is_blocked_but_micro_reply_is_allowed(client: TestClient) -> None:
    sid = "scene-gate-akira-boundary"
    client.post("/api/v1/start", json={"session_id": sid})
    fields = {
        "pov_character_id": "jun",
        "active_character_ids": ["jun", "akira"],
        "scene_character_ids": ["jun", "akira"],
        "present_character_ids": ["jun", "akira"],
        "addressed_character_ids": ["akira"],
        "relationship_pair_ids": ["akira__jun"],
    }
    turn_id = ready_turn(client, sid, "Спрашиваю Акиру, поедет ли она со мной.", **fields)

    rejected = client.post(
        f"/api/v1/sessions/{sid}/apply-turn-result",
        json={
            "turn_id": turn_id,
            "visible_scene_text": "Акира согласилась поехать с Джуном и поднялась за курткой.",
        },
    ).json()
    assert rejected["status"] == "rewrite_required"
    assert any(
        item["code"] == "major_non_pov_akira_choice_not_in_player_input"
        for item in rejected["scene_validation"]["errors"]
    )

    applied = client.post(
        f"/api/v1/sessions/{sid}/apply-turn-result",
        json={
            "turn_id": turn_id,
            "visible_scene_text": "Акира посмотрела на Джуна поверх плеча.\n\n**Акира** — Ты удивительно быстро перескочил к финалу разговора.",
            "scene_validation": {
                "repair_attempt": 1,
                "speaker_character_ids": ["akira"],
                "addressed_character_responses": {"akira": True},
            },
        },
    ).json()
    assert applied["status"] == "applied"


def test_false_attestation_forces_rewrite(client: TestClient) -> None:
    sid = "scene-gate-false-attestation"
    client.post("/api/v1/start", json={"session_id": sid})
    turn_id = ready_turn(client, sid, "Жду ответа.")

    result = client.post(
        f"/api/v1/sessions/{sid}/apply-turn-result",
        json={
            "turn_id": turn_id,
            "visible_scene_text": "Джун не ответил сразу.",
            "safety_checks": {"respected_knowledge_boundaries": False},
        },
    ).json()

    assert result["status"] == "rewrite_required"
    assert any(item["code"] == "failed_safety_attestation" for item in result["scene_validation"]["errors"])


def test_previous_scene_cannot_be_applied_again_as_new_turn(client: TestClient) -> None:
    sid = "scene-gate-repeat"
    client.post("/api/v1/start", json={"session_id": sid})
    scene = (
        "Джун медленно опустил ладонь на перила и прислушался к голосам внизу. "
        "Пауза затянулась, но никто не поднялся по лестнице. "
        "В комнате оставалось достаточно тихо, чтобы слышать дождь за окном."
    )
    first = ready_turn(client, sid, "Остаюсь у двери.")
    assert client.post(
        f"/api/v1/sessions/{sid}/apply-turn-result",
        json={"turn_id": first, "visible_scene_text": scene},
    ).json()["status"] == "applied"

    second = ready_turn(client, sid, "Снова прислушиваюсь.")
    repeated = client.post(
        f"/api/v1/sessions/{sid}/apply-turn-result",
        json={"turn_id": second, "visible_scene_text": scene},
    ).json()
    assert repeated["status"] == "rewrite_required"
    assert any(item["code"] == "scene_repeats_previous_result" for item in repeated["scene_validation"]["errors"])


def test_live_openapi_advertises_validation_and_rewrite_loop(client: TestClient) -> None:
    schema = client.get("/openapi-actions.json").json()
    apply_schema = schema["paths"]["/api/v1/sessions/{session_id}/apply-turn-result"]["post"]
    request = apply_schema["requestBody"]["content"]["application/json"]["schema"]

    assert schema["info"]["version"] == "0.9.0-v3-session-recovery-rollback"
    assert "scene_validation" in request["properties"]
    assert "safety_checks" in request["properties"]
    assert "rewrite" in apply_schema["summary"].lower()


def test_fifty_turns_survive_rewrites_pov_switches_and_idempotent_replays(client: TestClient) -> None:
    sid = "scene-gate-50-turn-proof"
    client.post("/api/v1/start", json={"session_id": sid})

    for index in range(1, 51):
        pov = "akira" if index % 2 else "jun"
        active = [pov]
        turn_id = ready_turn(
            client,
            sid,
            f"Ход {index}: наблюдаю за комнатой и не принимаю важных решений.",
            pov_character_id=pov,
            active_character_ids=active,
            scene_character_ids=active,
            present_character_ids=active,
        )
        if index % 7 == 0:
            rejected = client.post(
                f"/api/v1/sessions/{sid}/apply-turn-result",
                json={
                    "turn_id": turn_id,
                    "visible_scene_text": json.dumps({"turn_id": turn_id, "proposed_updates": {}}, ensure_ascii=False),
                },
            ).json()
            assert rejected["status"] == "rewrite_required"
            assert base.get_pending_turn(sid)["turn_id"] == turn_id

        scene_variants = (
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
        applied = client.post(
            f"/api/v1/sessions/{sid}/apply-turn-result",
            json=payload,
        ).json()
        assert applied["status"] == "applied"
        assert applied["state_revision"] == index
        if index % 11 == 0:
            replay = client.post(
                f"/api/v1/sessions/{sid}/apply-turn-result",
                json=payload,
            ).json()
            assert replay["status"] == "applied"
            assert replay["idempotent_replay"] is True

    runtime = base.read_turn_runtime(sid)
    assert runtime["state_revision"] == 50
    assert runtime["pending_turn"] is None
