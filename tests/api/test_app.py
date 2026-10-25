"""A01-A17 regression checks against isolated on-disk applications."""

import hashlib
import json
import time
import uuid

import pytest
from fastapi.testclient import TestClient

from backend.api.app import create_app


def post(client, path, body, key=None):
    return client.post("/api" + path, json=body, headers={"Idempotency-Key": key or uuid.uuid4().hex})


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path)) as c:
        yield c


def begin(client, **config):
    r = post(client, "/sessions", {"name": "接口验收", **config})
    assert r.status_code == 200, r.text
    s = r.json()
    r = post(
        client, f"/sessions/{s['id']}/commands", {"type": "start_prepare", "expected_revision": s["revision"]}
    )
    assert r.status_code == 200, r.text
    return client.app.state.service.engine


def complete(client, scenario="normal", prepare=6):
    engine = begin(client, scenario=scenario)
    client.portal.call(engine.advance, prepare * 1_000_000)
    r = post(
        client, f"/sessions/{engine.id}/commands", {"type": "mark_end", "expected_revision": engine.revision}
    )
    assert r.status_code == 200, r.text
    client.portal.call(engine.advance, engine.t0_us + 124_000_000)
    return engine


def test_unique_session_idempotency_and_t0(client):
    engine = begin(client)
    assert post(client, "/sessions", {"name": "重复"}).status_code == 409
    path = f"/sessions/{engine.id}/commands"
    client.portal.call(engine.advance, 6_000_000)
    key = uuid.uuid4().hex
    body = {"type": "mark_end", "expected_revision": engine.revision}
    first = post(client, path, body, key)
    repeat = post(client, path, body, key)
    assert first.status_code == repeat.status_code == 200
    assert first.json() == repeat.json()
    t0 = first.json()["t0_us"]
    assert post(client, path, {"type": "mark_end", "expected_revision": engine.revision}).status_code == 409
    assert engine.t0_us == t0
    assert (
        post(client, path, {"type": "cancel", "expected_revision": engine.revision}, key).status_code == 409
    )


def test_full_record_persistence_and_exports(client):
    engine = complete(client)
    assert engine.state == "COMPLETED"
    snap = client.get(f"/api/sessions/{engine.id}").json()
    assert all(w["valid"] for w in snap["key_windows"]), snap["key_windows"]
    assert snap["recovery"]["delta60"] > 0
    assert snap["recovery"]["delta120"] > snap["recovery"]["delta60"]
    folder = client.app.state.service.store.session_dir(engine.id)
    manifest = json.loads((folder / "manifest.json").read_text())
    for entry in manifest["files"]:
        assert hashlib.sha256((folder / entry["name"]).read_bytes()).hexdigest() == entry["sha256"]
    for fmt in ["summary", "csv", "raw"]:
        r = post(client, f"/sessions/{engine.id}/exports", {"format": fmt})
        assert r.status_code == 200, r.text
        download = client.get(r.json()["url"])
        assert download.status_code == 200 and len(download.content) > 50
    (folder / "ppg.csv").unlink()
    assert post(client, f"/sessions/{engine.id}/exports", {"format": "raw"}).status_code == 422
    assert post(client, f"/sessions/{engine.id}/exports", {"format": "summary"}).status_code == 200


def test_early_mark_keeps_real_start(client):
    engine = complete(client, prepare=2)
    snap = engine.snapshot()
    assert snap["t0_us"] == 2_000_000
    assert not snap["key_windows"][0]["valid"]
    assert "INSUFFICIENT_HISTORY" in snap["key_windows"][0]["reasons"]
    assert snap["recovery"]["delta60"] is None
    assert snap["recovery"]["delta120"] is None


def test_gap_propagates_independently(client):
    engine = complete(client, scenario="gap")
    snap = engine.snapshot()
    assert not snap["key_windows"][1]["valid"]
    assert snap["key_windows"][2]["valid"]
    assert snap["recovery"]["delta60"] is None
    assert snap["recovery"]["delta120"] is not None


def test_off_invalidates_and_data_age_grows(client):
    engine = begin(client)
    client.portal.call(engine.advance, 6_000_000)
    post(
        client, f"/sessions/{engine.id}/commands", {"type": "mark_end", "expected_revision": engine.revision}
    )
    client.portal.call(engine.advance, engine.t0_us + 15_000_000)
    snap = engine.snapshot()
    assert snap["acquisition_state"] == "OFF"
    assert snap["latest_result"]["pulse_bpm"] is None
    assert snap["latest_result"]["data_age_s"] >= 5


def test_replay_seek_original_integrity_and_lease(client):
    engine = complete(client, scenario="timeout")
    folder = client.app.state.service.store.session_dir(engine.id)
    before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in folder.iterdir()}
    r = post(client, "/replays", {"source_session_id": engine.id})
    assert r.status_code == 200, r.text
    rid = r.json()["id"]
    assert (
        post(client, f"/replays/{rid}/commands", {"type": "seek", "time_us": 70_000_000}).status_code == 200
    )
    snap = client.get(f"/api/replays/{rid}").json()
    assert "SENSOR_FAULT" in snap["derived_key_windows"][1]["reasons"]
    cursor = snap["cursor_us"]
    time.sleep(0.15)
    assert client.get(f"/api/replays/{rid}").json()["cursor_us"] == cursor
    assert (
        post(client, f"/replays/{rid}/commands", {"type": "seek", "time_us": 10_003_407}).status_code == 200
    )
    assert post(client, "/sessions/delete", {"ids": [engine.id]}).status_code == 409
    post(client, f"/replays/{rid}/commands", {"type": "close"})
    after = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in folder.iterdir()}
    assert before == after
    assert post(client, "/sessions/delete", {"ids": [engine.id]}).status_code == 200


def test_cancel_and_batch_atomicity(client):
    old = complete(client)
    active = begin(client)
    r = post(client, "/sessions/delete", {"ids": [old.id, active.id]})
    assert r.status_code == 409
    assert client.get(f"/api/sessions/{old.id}").status_code == 200
    post(client, f"/sessions/{active.id}/commands", {"type": "cancel", "expected_revision": active.revision})
    assert active.state == "CANCELLED"
    assert post(client, "/sessions/delete", {"ids": [old.id, active.id]}).status_code == 200
    assert client.get("/api/sessions").json()["total"] == 0


def test_fault_scheduling_stop_and_revoke(client):
    engine = begin(client)
    fault = post(
        client,
        "/faults",
        {
            "target_id": engine.id,
            "type": "noise",
            "anchor": "now",
            "duration_s": 5,
            "params": {"amplitude": 5000},
        },
    ).json()
    client.portal.call(engine.advance, 1_000_000)
    assert fault["id"] in [f["id"] for f in engine.faults]
    assert engine.faults[0]["status"] == "RUNNING"
    assert post(client, f"/faults/{fault['id']}/commands", {"type": "stop"}).status_code == 200
    future = post(
        client,
        "/faults",
        {"target_id": engine.id, "type": "gap", "anchor": "t0", "start_offset_s": 60, "duration_s": 2},
    ).json()
    assert future["status"] == "PLANNED"
    assert post(client, f"/faults/{future['id']}/commands", {"type": "revoke"}).status_code == 200
    events = client.get(f"/api/sessions/{engine.id}/events").json()["items"]
    assert any(e["type"] == "FAULT_STARTED" for e in events)
    assert any(e["type"] == "FAULT_STOPPED" for e in events)


def test_annotation_filter_settings_and_snapshot(client):
    engine = complete(client)
    r = client.patch(
        f"/api/sessions/{engine.id}/annotation",
        json={"name": "跑步恢复", "notes": "测试备注", "tags": ["跑步"]},
        headers={"Idempotency-Key": uuid.uuid4().hex},
    )
    assert r.status_code == 200
    assert client.get("/api/sessions?search=备注&tag=跑步").json()["total"] == 1
    settings = client.get("/api/settings").json()
    before = engine.config["motion_threshold"]
    settings["motion_threshold"] = 0.8
    r = client.put("/api/settings", json=settings, headers={"Idempotency-Key": uuid.uuid4().hex})
    assert r.status_code == 200
    assert engine.config["motion_threshold"] == before
    assert (
        client.put("/api/settings", json=settings, headers={"Idempotency-Key": uuid.uuid4().hex}).status_code
        == 409
    )
    assert client.get("/api/sessions?page_size=0").status_code == 422
    assert post(client, "/sessions", {"name": "test", "execution_mode": "STM32_DEVICE"}).status_code == 422


def test_shutdown_and_restart_marks_interrupted(tmp_path):
    with TestClient(create_app(tmp_path)) as c:
        engine = begin(c)
        c.portal.call(engine.advance, 1_000_000)
        sid = engine.id
    with TestClient(create_app(tmp_path)) as c:
        snap = c.get(f"/api/sessions/{sid}").json()
        assert snap["session_state"] == "INTERRUPTED"
        assert c.get("/api/bootstrap").json()["active_session"] is None
        assert c.get(f"/api/sessions/{sid}/samples").json()["samples"]


def test_websocket_snapshot_and_required_keys(client):
    assert client.post("/api/sessions", json={"name": "缺键"}).status_code == 422
    engine = begin(client)
    with client.websocket_connect("/api/live") as ws:
        snap = ws.receive_json()
        assert snap["session_id"] == engine.id
        assert snap["origin"] == "SIM" and snap["execution_mode"] == "PYTHON"
        assert "timebase_id" in snap and "seq" in snap


def test_idempotency_survives_restart(tmp_path):
    key = uuid.uuid4().hex
    with TestClient(create_app(tmp_path)) as c:
        engine = begin(c)
        c.portal.call(engine.advance, 6_000_000)
        sid = engine.id
        body = {"type": "mark_end", "expected_revision": engine.revision}
        original = post(c, f"/sessions/{sid}/commands", body, key).json()
    with TestClient(create_app(tmp_path)) as c:
        repeat = post(c, f"/sessions/{sid}/commands", body, key)
        assert repeat.status_code == 200
        assert repeat.json() == original
        assert c.get(f"/api/sessions/{sid}").json()["status"] == "INTERRUPTED"


def test_delete_move_crash_restores_directory(tmp_path):
    with TestClient(create_app(tmp_path)) as c:
        engine = complete(c)
        sid = engine.id
    folder = tmp_path / "sessions" / sid
    folder.rename(tmp_path / (".trash-" + sid))
    with TestClient(create_app(tmp_path)) as c:
        snap = c.get(f"/api/sessions/{sid}").json()
        assert snap["raw_available"]
        assert snap["session_state"] == "COMPLETED"
        assert folder.exists()


def test_zero_fault_frequency_is_rejected_before_acceptance(client):
    engine = begin(client)
    r = post(client, "/faults", {"target_id": engine.id, "type": "spike", "params": {"frequency": 0}})
    assert r.status_code == 422
    assert not engine.faults
    assert engine.state == "PREPARING"


def test_comparison_cancel_preserves_progress(client):
    r = post(client, "/comparisons", {"seeds": 3, "phases": [0, 5, 10, 15]})
    assert r.status_code == 200, r.text
    jid = r.json()["id"]
    assert post(client, f"/comparisons/{jid}/cancel", {}).status_code == 200
    for _ in range(100):
        result = client.get(f"/api/comparisons/{jid}").json()
        if result["status"] == "CANCELLED":
            break
        time.sleep(0.05)
    assert result["status"] == "CANCELLED"
    assert result["progress"]["completed"] < result["progress"]["total"]
    assert result["progress"]["failed"] == 0
    assert len(result["results"]) % 3 == 0
