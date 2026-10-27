# 使用独立播放时钟重算历史记录，保留原始文件和原始结果。
"""Saved-run playback with an independent clock and deterministic native-rate DSP rebuild."""

import asyncio
import copy
import time
import uuid
from bisect import bisect_left

from backend.algorithms.policies import recovery_result
from backend.algorithms.processor import Processor
from backend.engines.session import DomainError


# 管理暂停、定位、逐秒前进和重新计算的回放实例。
class ReplayEngine:
    def __init__(self, store, source):
        if source.get("origin") != "PUBLIC_DATA":
            raise DomainError(
                "SOURCE_UNAVAILABLE", "早期演示记录仅保留原始导出；当前版本只处理公开数据记录", 422
            )
        self.id = uuid.uuid4().hex
        self.source_id = source["id"]
        self.source = copy.deepcopy(source)
        self.samples = store.read_samples(self.source_id)
        if not self.samples:
            raise DomainError("RAW_DATA_MISSING", "原始波形缺失，无法回放", 422)
        self.period_us = round(1e6 / source.get("sample_hz", source["config_snapshot"].get("sample_hz", 64)))
        self.timestamps = [r["time_us"] for r in self.samples]
        self.first = self.cursor = self.timestamps[0]
        self.end = self.timestamps[-1] + self.period_us
        self.playing = False
        self.speed = 1.0
        self.closed = False
        self.lease = time.monotonic()
        self.seq = 0
        self.processor = Processor(source["config_snapshot"])
        self.processed_index = 0
        self.windows = []
        self._refresh_windows()
        self.task = asyncio.create_task(self.run())

    # 仅公布当前回放游标之前已经完整到达的窗口。
    def _refresh_windows(self):
        self.windows = []
        marker = (
            self.source.get("source_t0_us")
            or self.source.get("t0_us")
            or self.source.get("dataset", {}).get("t0_us", 10_000_000)
        )
        for center in (0, 60, 120):
            end = marker + (center + 4) * 1_000_000
            if self.cursor >= end:
                w = self.processor.window(center, marker, self.cursor)
                w["status"] = "VALID" if w["valid"] else "MISSING"
            else:
                w = {
                    "id": f"window_{center}",
                    "center_seconds": center,
                    "start_us": marker + (center - 4) * 1_000_000,
                    "end_us": end,
                    "valid": False,
                    "pulse_bpm": None,
                    "reasons": ["NOT_READY"],
                    "status": "COLLECTING" if self.cursor >= marker + (center - 4) * 1_000_000 else "WAITING",
                }
            w["center_s"] = center
            self.windows.append(w)

    # 从已保存样本推进DSP；向后定位时从头重建状态。
    def rebuild(self, target):
        if target < self.cursor:
            self.processor = Processor(self.source["config_snapshot"])
            self.processed_index = 0
        stop = bisect_left(self.timestamps, target)
        while self.processed_index < stop:
            block = [
                dict(s) for s in self.samples[self.processed_index : min(stop, self.processed_index + 1024)]
            ]
            self.processor.feed(block)
            self.processed_index += len(block)
        self.cursor = target
        self._refresh_windows()

    # 处理回放控制命令，同时续期回放租约。
    def command(self, body):
        self.lease = time.monotonic()
        kind = body.get("type")
        if "speed" in body:
            speed = float(body["speed"])
            if speed not in {0.5, 1, 2, 5, 10}:
                raise DomainError("INVALID_PARAMETER", "播放速度应为0.5、1、2、5或10倍", 422)
            self.speed = speed
        if kind == "play":
            self.playing = True
        elif kind == "pause":
            self.playing = False
        elif kind in {"seek", "step", "restart"}:
            if kind == "step" and self.playing:
                raise DomainError("INVALID_STATE", "请先暂停，再逐秒前进")
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

    # 推进播放游标，空闲超过租约时间后结束实例。
    async def run(self):
        previous = time.monotonic()
        try:
            while not self.closed:
                await asyncio.sleep(0.1)
                now = time.monotonic()
                if now - self.lease > 30:
                    self.closed = True
                    break
                if self.playing:
                    self.rebuild(min(self.end, self.cursor + round((now - previous) * self.speed * 1e6)))
                    if self.cursor >= self.end:
                        self.playing = False
                previous = now
        except asyncio.CancelledError:
            pass

    # 返回回放计算结果，不借用原记录中的未来窗口。
    def snapshot(self):
        self.seq += 1
        snap = {k: v for k, v in self.source.items() if k not in {"waveform_tail", "events"}}
        latest = self.processor.estimate(self.cursor)
        latest["data_age_s"] = latest.get("data_age_seconds")
        windows = copy.deepcopy(self.windows)
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
            waveform_tail=self.processor.samples[-64 * 30 :],
            latest_result=latest,
            key_windows=windows,
            derived_key_windows=windows,
            recovery=recovery_result(windows),
            derived_recovery=recovery_result(windows),
            closed=self.closed,
        )
        return snap
