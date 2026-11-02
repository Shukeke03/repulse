"""Production routing permits only real FreeRTOS/QEMU sessions, without fallback."""

import time
import uuid

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from backend.api.app import create_app
from backend.domain.models import SessionConfig
from backend.engines.firmware_port import FirmwarePort, availability
from backend.sources.public_data import list_datasets


def post(client, path, body):
    return client.post("/api" + path, json=body, headers={"Idempotency-Key": uuid.uuid4().hex})


def test_config_defaults_to_qemu_and_rejects_other_execution_modes():
    assert SessionConfig().execution_mode == "FREERTOS_QEMU"
    for mode in ("PYTHON", "STM32_DEVICE", "unknown"):
        with pytest.raises(ValidationError):
            SessionConfig(execution_mode=mode)


def test_api_rejects_python_and_reports_idle_without_a_fake_processing_task(tmp_path):
    with TestClient(create_app(tmp_path)) as client:
        for mode in ("PYTHON", "STM32_DEVICE"):
            assert post(client, "/sessions", {"execution_mode": mode}).status_code == 422
        capabilities = client.get("/api/bootstrap").json()["capabilities"]
        assert capabilities["python"] is False
        assert set(capabilities["execution_modes"]) <= {"FREERTOS_QEMU"}
        status = client.get("/api/system/status").json()
        assert status["execution_mode"] is None
        assert status["rtos_running"] is False
        assert status["tasks"] == []
        assert client.app.state.service.engine is None


def test_missing_qemu_fails_without_creating_a_host_processing_session(tmp_path, monkeypatch):
    monkeypatch.setattr("backend.engines.firmware_port.availability", lambda: False)
    monkeypatch.setattr("backend.api.app.firmware_available", lambda: False)
    with TestClient(create_app(tmp_path)) as client:
        capabilities = client.get("/api/bootstrap").json()["capabilities"]
        assert capabilities["python"] is False and capabilities["qemu"] is False
        assert capabilities["execution_modes"] == []
        for config in ({}, {"execution_mode": "FREERTOS_QEMU"}):
            response = post(client, "/sessions", config)
            assert response.status_code == 422, response.text
            assert response.json()["code"] == "SOURCE_UNAVAILABLE"
        assert client.app.state.service.engine is None
        assert client.app.state.service.store.all() == []


@pytest.mark.skipif(not availability() or not list_datasets(), reason="Build QEMU and download public data")
def test_default_api_session_runs_actual_firmware_and_reports_its_tasks(tmp_path):
    with TestClient(create_app(tmp_path)) as client:
        created = post(client, "/sessions", {"name": "仅固件运行验收", "speed": 10})
        assert created.status_code == 200, created.text
        snapshot = created.json()
        engine = client.app.state.service.engine
        assert type(engine) is FirmwarePort
        assert snapshot["execution_mode"] == "FREERTOS_QEMU"
        assert engine.hello["protocol_version"] == 2
        started = post(
            client,
            f"/sessions/{engine.id}/commands",
            {"type": "start_prepare", "expected_revision": snapshot["revision"]},
        )
        assert started.status_code == 200, started.text
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            status = client.get("/api/system/status").json()
            if len(status["tasks"]) == 6 and status["channels"]["samples"] > 0:
                break
            time.sleep(0.05)
        assert status["execution_mode"] == "FREERTOS_QEMU" and status["rtos_running"]
        assert len(status["tasks"]) == 6 and status["channels"]["samples"] > 0
        assert all(task["name"] != "SessionEngine" for task in status["tasks"])
        assert all(task["stack_high_water_words"] > 0 for task in status["tasks"])
        cancelled = post(
            client,
            f"/sessions/{engine.id}/commands",
            {"type": "cancel", "expected_revision": engine.revision},
        )
        assert cancelled.status_code == 200, cancelled.text
        assert engine.state == "CANCELLED"
        assert client.get("/api/system/status").json()["execution_mode"] is None
