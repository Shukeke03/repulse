"""Independent replay clock; seeking deterministically rebuilds causal DSP from the first sample."""

import asyncio
import copy
import math
import time
import uuid

from backend.algorithms.policies import recovery_result
from backend.algorithms.processor import Processor
from backend.engines.session import DomainError


class ReplayEngine:
    def __init__(self, store, source):
        self.id = uuid.uuid4().hex
        self.source_id = source["id"]
        self.source = copy.deepcopy(source)
        self.samples = store.read_samples(self.source_id)
        if not self.samples:
            raise DomainError("RAW_DATA_MISSING", "原始波形缺失，无法回放", 422)
        self.cursor = self.samples[0]["time_us"]
        self.first = self.cursor
        self.end = self.samples[-1]["time_us"] + 10000
        self.playing = False
        self.speed = 1.0
        self.closed = False
        self.lease = time.monotonic()
        self.seq = 0
        self.processor = Processor(source["config_snapshot"])
        self.processed_index = 0
        self.windows = []
        self.task = asyncio.create_task(self.run())

    def rebuild(self, target):
        if target < self.cursor:
            self.processor = Processor(self.source["config_snapshot"])
            self.processed_index = 0
        stop = min(len(self.samples), max(0, math.ceil((target - self.first) / 10000)))
        while self.processed_index < stop:
            block = [
                dict(s) for s in self.samples[self.processed_index : min(stop, self.processed_index + 1000)]
            ]
            self.processor.feed(block)
            self.processed_index += len(block)
        self.cursor = target
        self.windows = []
        if self.source["t0_us"] is not None:
            for center in (0, 60, 120):
                if self.cursor >= self.source["t0_us"] + (center + 4) * 1_000_000:
                    w = self.processor.window(center, self.source["t0_us"], self.cursor)
                    w["center_s"] = center
                    w["status"] = "VALID" if w["valid"] else "MISSING"
                    if center == 0 and self.source["t0_us"] < 6_000_000:
                        w.update(
                            valid=False, pulse_bpm=None, reasons=["INSUFFICIENT_HISTORY"], status="MISSING"
                        )
                    self.windows.append(w)

    def command(self, body):
        self.lease = time.monotonic()
        kind = body.get("type")
        if kind == "play":
            self.speed = float(body.get("speed", self.speed))
            if self.speed not in {0.5, 1, 2, 5, 10}:
                raise DomainError("INVALID_PARAMETER", "回放速度应为0.5、1、2、5或10倍", 422)
            self.playing = True
        elif kind == "pause":
            self.playing = False
        elif kind in {"seek", "step", "restart"}:
            self.playing = False
            target = (
                self.first
                if kind == "restart"
                else self.cursor + 1_000_000
                if kind == "step"
                else int(body.get("time_us", self.first))
            )
            self.rebuild(min(self.end, max(self.first, target)))
        elif kind == "close":
            self.closed = True
            self.playing = False
        else:
            raise DomainError("INVALID_PARAMETER", "未知回放命令", 422)
        return self.snapshot()

    async def run(self):
        prev = time.monotonic()
        try:
            while not self.closed:
                await asyncio.sleep(0.1)
                now = time.monotonic()
                if now - self.lease > 30:
                    self.closed = True
                    break
                if self.playing:
                    self.rebuild(min(self.end, self.cursor + round((now - prev) * self.speed * 100) * 10000))
                    if self.cursor >= self.end:
                        self.playing = False
                prev = now
        except asyncio.CancelledError:
            pass

    def snapshot(self):
        self.seq += 1
        snap = {k: v for k, v in self.source.items() if k not in {"waveform_tail", "events"}}
        snap.update(
            id=self.id,
            replay_id=self.id,
            source_session_id=self.source_id,
            view_mode="REPLAY",
            stream_id=self.id,
            seq=self.seq,
            cursor_us=self.cursor,
            engine_time_us=self.cursor,
            duration_us=self.end - self.first,
            first_us=self.first,
            last_us=self.end,
            playing=self.playing,
            speed=self.speed,
            waveform_tail=self.samples[max(0, self.processed_index - 3000) : self.processed_index],
            latest_result=self.processor.estimate(self.cursor),
            derived_key_windows=self.windows,
            derived_recovery=recovery_result(self.windows),
            closed=self.closed,
        )
        return snap
