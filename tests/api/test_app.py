# 使用隔离存储验证真实数据接口、持久化、导出和并发操作约束。
"""Real-recording API contracts against isolated on-disk application state."""

import hashlib
import json
import time
import uuid

import pytest
from fastapi.testclient import TestClient

from backend.api.app import create_app
from backend.domain.models import DEFAULT_DATASET
from backend.sources.public_data import list_datasets

pytestmark = pytest.mark.skipif(not list_datasets(), reason="Run python -m scripts.download_dataset first")


def post(client, path, body, key=None):
    return client.post("/api" + path, json=body, headers={"Idempotency-Key": key or uuid.uuid4().hex})


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path)) as c:
        yield c


def begin(client, **config):
    response = post(client, "/sessions", {"name": "真实记录验收", "dataset_id": DEFAULT_DATASET, **config})
    assert response.status_code == 200, response.text
    snap = response.json()
    response = post(
        client,
        f"/sessions/{snap['id']}/commands",
        {"type": "start_prepare", "expected_revision": snap["revision"]},
    )
    assert response.status_code == 200, response.text
    return client.app.state.service.engine


def complete(client, **config):
    engine = begin(client, **config)
    client.portal.call(engine.advance, 134_000_000)
    return engine


def test_bootstrap_only_public_source_and_local_catalog(client):
    data = client.get("/api/bootstrap").json()
    assert data["capabilities"]["origins"] == ["PUBLIC_DATA"]
    assert data["defaults"]["sample_rate"] == 64
    assert data["datasets"]
    assert all(d["origin"] == "PUBLIC_DATA" and d["resampled"] is False for d in data["datasets"])
    assert client.get("/api/datasets").json()["items"]
    metadata = client.get(f"/api/datasets/{DEFAULT_DATASET}").json()
    assert metadata["t0_us"] == 10_000_000
    samples = client.get(f"/api/datasets/{DEFAULT_DATASET}/samples?max_points=100").json()
    assert len(samples["samples"]) <= 100
    assert samples["sample_hz"] == 64 and samples["origin"] == "PUBLIC_DATA"
    assert client.get("/api/datasets/unknown").status_code == 422
    assert client.get(f"/api/datasets/{DEFAULT_DATASET}/samples?max_points=0").status_code == 422
    assert post(client, "/sessions", {"origin": "SIM"}).status_code == 422
    assert post(client, "/sessions", {"seed": 123}).status_code == 422
    assert post(client, "/sessions", {"dataset_id": "../../etc/passwd"}).status_code == 422


def test_unique_session_idempotency_and_immutable_marker(client):
    response = post(client, "/sessions", {"name": "幂等验收"})
    created = response.json()
    path = f"/sessions/{created['id']}/commands"
    body = {"type": "start_prepare", "expected_revision": 0}
    key = uuid.uuid4().hex
    first = post(client, path, body, key)
    repeat = post(client, path, body, key)
    assert first.status_code == repeat.status_code == 200
    assert first.json() == repeat.json()
    engine = client.app.state.service.engine
    assert post(client, "/sessions", {"name": "重复"}).status_code == 409
    client.portal.call(engine.advance, 10_000_000)
    assert engine.state == "RECOVERING" and engine.t0_us == 10_000_000
    assert post(client, path, {"type": "mark_end", "expected_revision": engine.revision}).status_code == 422
    assert engine.t0_us == 10_000_000
    assert (
        post(client, path, {"type": "cancel", "expected_revision": engine.revision}, key).status_code == 409
    )


def test_original_downloads_match_catalog_and_reject_other_files(client):
    metadata = client.get(f"/api/datasets/{DEFAULT_DATASET}").json()
    for entry in metadata["files"]:
        response = client.get(f"/api/datasets/{DEFAULT_DATASET}/files/{entry['name']}")
        assert response.status_code == 200
        assert len(response.content) == entry["bytes"]
        assert hashlib.sha256(response.content).hexdigest() == entry["sha256"]
        assert "attachment" in response.headers["content-disposition"]
    for name in ("manifest.json", "app.sqlite", "unknown.csv"):
        assert client.get(f"/api/datasets/{DEFAULT_DATASET}/files/{name}").status_code == 404
    assert client.get("/api/datasets/unknown/files/BVP.csv").status_code == 422


def test_native_record_persistence_and_real_exports(client):
    engine = complete(client)
    snap = client.get(f"/api/sessions/{engine.id}").json()
    assert snap["status"] == "COMPLETED" and snap["origin"] == "PUBLIC_DATA"
    assert snap["sample_hz"] == 64 and snap["engine_time_us"] == 134_000_000
    assert len(engine.samples) == 8576
    assert [w["sample_count"] for w in snap["key_windows"]] == [512, 512, 512]
    assert snap["dataset"]["input_hash"] == engine.source.input_hash
    persisted = client.app.state.service.store.read_samples(engine.id)
    for index in (0, 641, 8575):
        for field in ("source_time_us", "accel_x", "accel_y", "accel_z", "reference_bpm"):
            assert persisted[index][field] == engine.source.all_samples[index][field]
    folder = client.app.state.service.store.session_dir(engine.id)
    manifest = json.loads((folder / "manifest.json").read_text())
    for entry in manifest["files"]:
        assert hashlib.sha256((folder / entry["name"]).read_bytes()).hexdigest() == entry["sha256"]
    for fmt in ["summary", "csv", "raw"]:
        r = post(client, f"/sessions/{engine.id}/exports", {"format": fmt})
        assert r.status_code == 200, r.text
        download = client.get(r.json()["url"])
        assert download.status_code == 200 and len(download.content) > 100
    samples = client.get(f"/api/sessions/{engine.id}/samples").json()
    assert samples["sample_rate_hz"] == 64 and samples["unit"] == "设备相对单位"
    (folder / "ppg.csv").unlink()
    assert post(client, f"/sessions/{engine.id}/exports", {"format": "raw"}).status_code == 422
    assert post(client, f"/sessions/{engine.id}/exports", {"format": "summary"}).status_code == 200


def test_plan_off_invalidates_current_value_and_age(client):
    engine = begin(client, strategy="critical")
    client.portal.call(engine.advance, 25_000_000)
    snap = engine.snapshot()
    assert snap["t0_us"] == 10_000_000
    assert snap["acquisition_state"] == "OFF"
    assert snap["latest_result"]["pulse_bpm"] is None
    assert snap["latest_result"]["data_age_s"] == pytest.approx(5)


def test_replay_has_no_future_results_and_preserves_source_files(client):
    engine = complete(client)
    folder = client.app.state.service.store.session_dir(engine.id)
    before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in folder.iterdir()}
    response = post(client, "/replays", {"source_session_id": engine.id})
    assert response.status_code == 200, response.text
    run = response.json()
    rid = run["id"]
    assert all(not w["valid"] and w["pulse_bpm"] is None for w in run["key_windows"])
    assert all(w["status"] == "WAITING" for w in run["derived_key_windows"])
    assert (
        post(client, f"/replays/{rid}/commands", {"type": "seek", "time_us": 74_000_001}).status_code == 200
    )
    snap = client.get(f"/api/replays/{rid}").json()
    assert snap["derived_key_windows"][2]["pulse_bpm"] is None
    cursor = snap["cursor_us"]
    time.sleep(0.15)
    assert client.get(f"/api/replays/{rid}").json()["cursor_us"] == cursor
    assert post(client, "/sessions/delete", {"ids": [engine.id]}).status_code == 409
    post(client, f"/replays/{rid}/commands", {"type": "seek", "time_us": 134_000_000})
    rebuilt = client.get(f"/api/replays/{rid}").json()
    for actual, original in zip(rebuilt["derived_key_windows"], engine.snapshot()["key_windows"]):
        assert actual["valid"] == original["valid"]
        assert actual["pulse_bpm"] == original["pulse_bpm"]
        assert actual["reasons"] == original["reasons"]
    post(client, f"/replays/{rid}/commands", {"type": "close"})
    assert before == {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in folder.iterdir()}
    assert post(client, "/sessions/delete", {"ids": [engine.id]}).status_code == 200


def test_cancel_and_batch_delete_are_all_or_nothing(client):
    old = complete(client)
    engine = begin(client)
    assert post(client, "/sessions/delete", {"ids": [old.id, engine.id]}).status_code == 409
    assert client.get(f"/api/sessions/{old.id}").status_code == 200
    client.portal.call(engine.advance, 15_000_000)
    response = post(
        client, f"/sessions/{engine.id}/commands", {"type": "cancel", "expected_revision": engine.revision}
    )
    assert response.status_code == 200 and engine.state == "CANCELLED"
    assert engine.snapshot()["key_windows"][1]["status"] == "MISSING"
    assert post(client, "/sessions/delete", {"ids": [old.id, engine.id]}).status_code == 200
    assert client.get("/api/sessions?origin=PUBLIC_DATA").json()["total"] == 0


def test_annotation_filters_and_settings_remain_separate(client):
    engine = complete(client)
    r = client.patch(
        f"/api/sessions/{engine.id}/annotation",
        json={"name": "骑行恢复", "notes": "真实数据备注", "tags": ["骑行"]},
        headers={"Idempotency-Key": uuid.uuid4().hex},
    )
    assert r.status_code == 200
    assert client.get("/api/sessions?search=备注&tag=骑行&origin=PUBLIC_DATA").json()["total"] == 1
    old = dict(engine.config)
    settings = client.get("/api/settings").json()
    settings["chart_seconds"] = 30
    settings["motion_threshold"] = 0.5
    r = client.put("/api/settings", json=settings, headers={"Idempotency-Key": uuid.uuid4().hex})
    assert r.status_code == 200, r.text
    assert engine.config == old
    assert (
        client.put("/api/settings", json=settings, headers={"Idempotency-Key": uuid.uuid4().hex}).status_code
        == 409
    )
    assert client.get("/api/sessions?page_size=0").status_code == 422
    assert post(client, "/sessions", {"execution_mode": "STM32_DEVICE"}).status_code == 422


def test_shutdown_restart_and_idempotent_command_result(tmp_path):
    key = uuid.uuid4().hex
    with TestClient(create_app(tmp_path)) as client:
        created = post(client, "/sessions", {}).json()
        sid = created["id"]
        body = {"type": "start_prepare", "expected_revision": 0}
        original = post(client, f"/sessions/{sid}/commands", body, key).json()
        client.portal.call(client.app.state.service.engine.advance, 1_000_000)
    with TestClient(create_app(tmp_path)) as client:
        snap = client.get(f"/api/sessions/{sid}").json()
        assert snap["status"] == "INTERRUPTED"
        assert client.get("/api/bootstrap").json()["active_session"] is None
        assert post(client, f"/sessions/{sid}/commands", body, key).json() == original
        assert client.get(f"/api/sessions/{sid}/samples").json()["samples"]


def test_delete_move_crash_restores_directory(tmp_path):
    with TestClient(create_app(tmp_path)) as client:
        engine = complete(client)
        sid = engine.id
    folder = tmp_path / "sessions" / sid
    folder.rename(tmp_path / (".trash-" + sid))
    with TestClient(create_app(tmp_path)) as client:
        assert client.get(f"/api/sessions/{sid}").json()["raw_available"]
        assert folder.exists()


def test_websocket_and_control_revision(client):
    assert client.post("/api/sessions", json={}).status_code == 422
    engine = begin(client)
    assert (
        post(
            client, f"/sessions/{engine.id}/commands", {"type": "cancel", "expected_revision": 999}
        ).status_code
        == 409
    )
    with client.websocket_connect("/api/live") as ws:
        snap = ws.receive_json()
        assert snap["origin"] == "PUBLIC_DATA" and snap["dataset"]["id"] == DEFAULT_DATASET
        assert snap["sample_hz"] == 64 and snap["source_t0_us"] == 10_000_000
    assert post(client, "/faults", {"type": "noise"}).status_code in {404, 405}


def test_comparison_pairs_use_same_real_observations_and_exports(client):
    response = post(client, "/comparisons", {"dataset_ids": [DEFAULT_DATASET], "phases": [0]})
    assert response.status_code == 200, response.text
    jid = response.json()["id"]
    for _ in range(200):
        result = client.get(f"/api/comparisons/{jid}").json()
        if result["status"] not in {"RUNNING", "CANCELLING"}:
            break
        time.sleep(0.05)
    assert result["status"] == "COMPLETED", result
    assert result["progress"] == {"completed": 3, "failed": 0, "total": 3}
    assert len({r["input_hash"] for r in result["results"]}) == 1
    assert all(r["origin"] == "PUBLIC_DATA" for r in result["results"])
    assert len(result["metrics"]) == 3
    for fmt in ["json", "csv"]:
        exported = post(client, f"/comparisons/{jid}/exports", {"format": fmt})
        assert exported.status_code == 200, exported.text
        assert client.get(exported.json()["url"]).status_code == 200


def test_comparison_cancel_retains_complete_units(client):
    response = post(client, "/comparisons", {"dataset_ids": [DEFAULT_DATASET], "phases": list(range(20))})
    jid = response.json()["id"]
    assert post(client, f"/comparisons/{jid}/cancel", {}).status_code == 200
    for _ in range(200):
        result = client.get(f"/api/comparisons/{jid}").json()
        if result["status"] == "CANCELLED":
            break
        time.sleep(0.05)
    assert result["status"] == "CANCELLED"
    assert result["progress"]["completed"] < result["progress"]["total"]
    assert len(result["results"]) % 3 == 0
