"""Domain integration contracts exercised without HTTP or wall-clock simulation."""

import asyncio
import hashlib

import pytest

from backend.domain.models import DEFAULTS, SessionConfig
from backend.engines.replay import ReplayEngine
from backend.engines.session import DomainError, SessionEngine
from backend.storage.store import Store


def run(coro):
    return asyncio.run(coro)


def config(**changes):
    return SessionConfig(**changes).model_dump()


async def stop_task(engine):
    if engine.task and not engine.task.done():
        engine.task.cancel()
        try:
            await engine.task
        except asyncio.CancelledError:
            pass


def test_single_marker_and_fixed_windows_in_domain(tmp_path):
    async def check():
        store = Store(tmp_path)
        engine = SessionEngine(store, config(), DEFAULTS)
        engine.command("start_prepare", 0)
        engine.advance(6_000_000)
        engine.command("mark_end", engine.revision)
        original = engine.t0_us
        with pytest.raises(DomainError):
            engine.command("mark_end", engine.revision)
        assert engine.t0_us == original
        engine.advance(130_000_000)
        assert engine.state == "COMPLETED"
        windows = engine.snapshot()["key_windows"]
        assert all(w["valid"] for w in windows)
        assert [w["start_us"] for w in windows] == [2_000_000, 62_000_000, 122_000_000]
        assert engine.time_us == 130_000_000
        assert engine.samples[-1]["time_us"] == 129_990_000
        assert (store.session_dir(engine.id) / "manifest.json").exists()
        await stop_task(engine)
        store.close()

    run(check())


def test_off_period_age_tracks_last_data_not_command_heartbeat(tmp_path):
    async def check():
        store = Store(tmp_path)
        engine = SessionEngine(store, config(), DEFAULTS)
        engine.command("start_prepare", 0)
        engine.advance(6_000_000)
        engine.command("mark_end", engine.revision)
        engine.advance(17_000_000)
        latest = engine.snapshot()["latest_result"]
        assert latest["valid"] is False
        assert latest["pulse_bpm"] is None
        assert latest["data_age_s"] == pytest.approx(1.0)
        await engine.close()
        store.close()

    run(check())


def test_cancel_preserves_partial_windows_and_terminal_identity(tmp_path):
    async def check():
        store = Store(tmp_path)
        engine = SessionEngine(store, config(), DEFAULTS)
        engine.command("start_prepare", 0)
        engine.advance(6_000_000)
        engine.command("mark_end", engine.revision)
        engine.advance(16_000_000)
        engine.command("cancel", engine.revision)
        snap = engine.snapshot()
        assert snap["session_state"] == "CANCELLED"
        assert snap["key_windows"][0]["valid"]
        assert not snap["key_windows"][1]["valid"]
        assert snap["recovery"]["delta60"] is None
        assert snap["recovery"]["delta120"] is None
        with pytest.raises(DomainError):
            engine.command("cancel", engine.revision)
        await stop_task(engine)
        store.close()

    run(check())


def test_timeout_reason_persists_and_replay_cannot_mutate_original(tmp_path):
    async def check():
        store = Store(tmp_path)
        engine = SessionEngine(store, config(scenario="timeout"), DEFAULTS)
        engine.command("start_prepare", 0)
        engine.advance(6_000_000)
        engine.command("mark_end", engine.revision)
        engine.advance(130_000_000)
        snap = store.get(engine.id)
        folder = store.session_dir(engine.id)
        hashes = {
            path.name: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in folder.iterdir()
            if path.is_file()
        }
        replay = ReplayEngine(store, snap)
        replay.rebuild(replay.end)
        original = next(w for w in snap["key_windows"] if w["center_s"] == 60)
        recalculated = next(w for w in replay.windows if w["center_s"] == 60)
        assert "SENSOR_FAULT" in original["reasons"]
        assert recalculated["reasons"] == original["reasons"]
        replay.rebuild(8_000_000)
        replay.rebuild(130_000_000)
        assert replay.windows[2]["valid"]
        replay.command({"type": "close"})
        replay.task.cancel()
        assert hashes == {
            path.name: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in folder.iterdir()
            if path.is_file()
        }
        await stop_task(engine)
        store.close()

    run(check())


def test_replay_seek_uses_all_samples_before_exclusive_cursor(tmp_path):
    async def check():
        store = Store(tmp_path)
        engine = SessionEngine(store, config(), DEFAULTS)
        engine.command("start_prepare", 0)
        engine.advance(6_000_000)
        engine.command("mark_end", engine.revision)
        engine.advance(130_000_000)
        replay = ReplayEngine(store, store.get(engine.id))
        replay.rebuild(10_003_407)
        assert replay.processed_index == 1001
        assert replay.processor.samples[-1]["time_us"] == 10_000_000
        assert replay.windows[0]["valid"]
        replay.closed = True
        replay.task.cancel()
        await stop_task(engine)
        store.close()

    run(check())
