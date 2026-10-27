"""Native public-recording state and immutable replay contracts."""

import asyncio
import hashlib

import pytest

from backend.domain.models import DEFAULTS, SessionConfig
from backend.engines.replay import ReplayEngine
from backend.engines.session import DomainError, SessionEngine
from backend.sources.public_data import list_datasets
from backend.storage.store import Store

pytestmark = pytest.mark.skipif(not list_datasets(), reason="Download the public records first")


def test_fixed_marker_and_half_open_windows(tmp_path):
    async def verify():
        store = Store(tmp_path)
        engine = SessionEngine(store, SessionConfig().model_dump(), DEFAULTS)
        engine.command("start_prepare", 0)
        engine.advance(9_000_000)
        assert engine.t0_us is None and engine.state == "PREPARING"
        with pytest.raises(DomainError):
            engine.command("mark_end", engine.revision)
        engine.advance(10_000_000)
        assert engine.t0_us == 10_000_000 and engine.state == "RECOVERING"
        engine.advance(134_000_000)
        assert engine.state == "COMPLETED"
        assert [w["start_us"] for w in engine.snapshot()["key_windows"]] == [
            6_000_000,
            66_000_000,
            126_000_000,
        ]
        assert [w["end_us"] for w in engine.snapshot()["key_windows"]] == [
            14_000_000,
            74_000_000,
            134_000_000,
        ]
        assert len(engine.samples) == 8576 and engine.samples[-1]["time_us"] == 133_984_375
        await engine.close()
        store.close()

    asyncio.run(verify())


def test_reference_values_are_archived_without_changing_waveforms(tmp_path):
    async def verify():
        store = Store(tmp_path)
        engine = SessionEngine(store, SessionConfig().model_dump(), DEFAULTS)
        source = [dict(r) for r in engine.source.all_samples]
        engine.command("start_prepare", 0)
        engine.advance(134_000_000)
        saved = store.read_samples(engine.id)
        assert [s["ppg"] for s in saved] == [s["ppg"] for s in source]
        assert [s.get("reference_bpm") for s in saved] == [s.get("reference_bpm") for s in source]
        assert engine.source.input_hash == engine.snapshot()["dataset"]["input_hash"]
        await engine.close()
        store.close()

    asyncio.run(verify())


def test_replay_seeks_rebuild_native_data_without_modifying_original(tmp_path):
    async def verify():
        store = Store(tmp_path)
        engine = SessionEngine(store, SessionConfig().model_dump(), DEFAULTS)
        engine.command("start_prepare", 0)
        engine.advance(134_000_000)
        folder = store.session_dir(engine.id)
        hashes = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in folder.iterdir()}
        replay = ReplayEngine(store, store.get(engine.id))
        assert all(not w["valid"] for w in replay.snapshot()["key_windows"])
        replay.rebuild(10_003_407)
        assert replay.processed_index == 641
        assert replay.processor.samples[-1]["time_us"] == 10_000_000
        replay.rebuild(replay.end)
        results = [(w["valid"], w["pulse_bpm"]) for w in replay.windows]
        replay.rebuild(3_000_000)
        replay.rebuild(replay.end)
        assert results == [(w["valid"], w["pulse_bpm"]) for w in replay.windows]
        replay.command({"type": "close"})
        replay.task.cancel()
        assert hashes == {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in folder.iterdir()}
        await engine.close()
        store.close()

    asyncio.run(verify())


def test_close_marks_partial_run_interrupted(tmp_path):
    async def verify():
        store = Store(tmp_path)
        engine = SessionEngine(store, SessionConfig().model_dump(), DEFAULTS)
        engine.command("start_prepare", 0)
        engine.advance(12_000_000)
        await engine.close()
        assert engine.state == "INTERRUPTED"
        assert store.get(engine.id)["status"] == "INTERRUPTED"
        assert (store.session_dir(engine.id) / "manifest.json").exists()
        store.close()

    asyncio.run(verify())
