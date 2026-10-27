"""Stream a public recording with its own source clock and fixed exercise marker."""

import asyncio
import time
import uuid
from collections import deque

from backend.algorithms.policies import recovery_result
from backend.algorithms.processor import Processor
from backend.domain.models import ACTIVE, TERMINAL
from backend.sources.public_data import DatasetSource
from backend.storage.store import utcnow


class DomainError(Exception):
    def __init__(self, code, message, status=409):
        self.code, self.message, self.status = code, message, status
        super().__init__(message)


class SessionEngine:
    def __init__(self, store, config, defaults):
        self.store = store
        self.id = uuid.uuid4().hex
        self.config = dict(defaults) | config
        try:
            self.source = DatasetSource(self.config)
        except (ValueError, OSError) as exc:
            raise DomainError("DATASET_UNAVAILABLE", str(exc), 422) from exc
        self.config.update(
            sample_hz=self.source.sample_hz,
            sample_rate=self.source.sample_hz,
            dataset_metadata=self.source.metadata,
        )
        self.processor = Processor(self.config)
        self.state, self.revision, self.time_us, self.t0_us = "CREATED", 0, 0, None
        self.timebase_id, self.created_at = uuid.uuid4().hex, utcnow()
        self.samples = []
        self.events = deque(maxlen=500)
        self.windows = []
        self.latest = {"valid": False, "pulse_bpm": None, "reasons": ["NOT_READY"], "quality": "UNKNOWN"}
        self.acquisition_state, self.task, self.seq = "OFF", None, 0
        self.last_estimate_us = self.last_flush_us = 0
        self.started_clock = self.finalize_started = self.last_data_end_us = None
        self.writer = self.store.create(self.snapshot())
        self.add_event("CREATED", "记录已创建", dataset_id=self.config["dataset_id"])

    def add_event(self, kind, message, severity="info", **payload):
        event = {
            "id": uuid.uuid4().hex,
            "session_id": self.id,
            "time_us": self.time_us,
            "type": kind,
            "message": message,
            "severity": severity,
            "payload": payload,
        }
        self.events.append(event)
        if hasattr(self, "writer") and not self.writer.closed:
            self.writer.event(event)

    def command(self, kind, revision):
        if revision != self.revision:
            raise DomainError("REVISION_CONFLICT", "记录状态已变化，请重新载入后重试")
        if kind == "start_prepare":
            if self.state != "CREATED":
                raise DomainError("INVALID_STATE", "这条记录已经开始处理")
            self.state = "PREPARING"
            self.started_clock = time.monotonic()
            self.task = asyncio.create_task(self.run(), name="PublicRecording")
            self.add_event("PREPARE_STARTED", "开始读取起点前的原始记录", source_t0_us=self.source.t0_us)
        elif kind == "cancel":
            if self.state not in {"CREATED", "PREPARING", "RECOVERING"}:
                raise DomainError("INVALID_STATE", "记录已结束或正在保存")
            self.add_event("CANCELLED", "用户结束本次处理", "warning")
            self.finalize("CANCELLED")
        else:
            raise DomainError("INVALID_STATE", "恢复起点来自原记录的阶段标记，不能手动修改")
        self.revision += 1
        if not self.writer.closed:
            self.writer.flush()
        self.store.update(self.snapshot())
        return self.snapshot()

    def _mark_source_event(self):
        if self.state == "PREPARING" and self.time_us >= self.source.t0_us:
            self.t0_us = self.source.t0_us
            self.state = "RECOVERING"
            self.revision += 1
            self.add_event(
                "T0_LOCKED",
                "进入原记录的主动恢复阶段",
                dataset_id=self.config["dataset_id"],
                phase_description=self.source.metadata.get("phase_event", {}).get("label"),
            )

    def advance(self, target_us):
        if self.state not in {"PREPARING", "RECOVERING"}:
            return
        period = self.source.period_us
        target = min(int(target_us), self.source.duration_us, self.source.t0_us + 124_000_000)
        while self.time_us + period <= target:
            self._mark_source_event()
            count = min(16, (target - self.time_us) // period)
            # Do not cross the known marker inside a block; state ownership remains exact.
            if self.t0_us is None:
                count = min(count, max(1, (self.source.t0_us - self.time_us) // period))
            rows = self.source.block(self.time_us, count, self.source.t0_us)
            if len(rows) != count:
                self.add_event("DATASET_ENDED", "原记录不足以覆盖当前观察范围", "error")
                self.finalize("FAILED")
                return
            self.processor.feed(rows)
            self.samples.extend(rows)
            self.writer.samples(rows)
            self.time_us += count * period
            self._mark_source_event()
            last = rows[-1]
            usable = next(
                (s for s in reversed(rows) if s.get("ppg") is not None and s.get("on") and not s.get("gap")),
                None,
            )
            if usable:
                self.last_data_end_us = usable["time_us"] + period
            self.acquisition_state = "OFF" if not last["on"] else "WARMING" if last.get("warming") else "ON"
            if self.time_us - self.last_estimate_us >= 1_000_000:
                self.latest = self.processor.estimate(self.time_us)
                self.last_estimate_us = self.time_us
                self.writer.estimate(self.latest)
            if self.t0_us is not None:
                for center in (0, 60, 120):
                    if self.time_us >= self.t0_us + (center + 4) * 1_000_000 and not any(
                        w["center_s"] == center for w in self.windows
                    ):
                        result = self.processor.window(center, self.t0_us, self.time_us)
                        result.update(center_s=center, status="VALID" if result["valid"] else "MISSING")
                        self.windows.append(result)
                        self.add_event(
                            "WINDOW_RESULT",
                            f"{center}秒窗口" + ("已完成" if result["valid"] else "信号不足"),
                            "info" if result["valid"] else "warning",
                            window=result,
                        )
            if not last["on"] or last.get("gap") or last.get("warming"):
                reason = "ACQUISITION_OFF" if not last["on"] else "DATA_GAP" if last.get("gap") else "WARMUP"
                self.latest = {
                    "valid": False,
                    "pulse_bpm": None,
                    "reasons": [reason],
                    "quality": reason,
                    "support_end_us": self.last_data_end_us,
                }
            if self.time_us - self.last_flush_us >= 1_000_000:
                self.writer.flush()
                self.store.update(self.snapshot())
                self.last_flush_us = self.time_us
        if self.time_us >= self.source.t0_us + 124_000_000:
            self.finalize("COMPLETED")

    async def run(self):
        try:
            while self.state in {"PREPARING", "RECOVERING"}:
                elapsed = (time.monotonic() - self.started_clock) * self.config["speed"]
                target = min(int(elapsed * 1e6), self.time_us + 1_000_000)
                self.advance(target)
                await asyncio.sleep(0.02)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.add_event("PROCESSING_ERROR", str(exc), "error")
            try:
                self.finalize("FAILED")
            except Exception:
                self.state = "FAILED"

    def finalize(self, target):
        if self.state in TERMINAL:
            return
        self.state = "FINALIZING"
        self.revision += 1
        self.finalize_started = time.monotonic()
        self.acquisition_state = "OFF"
        self.latest = {"valid": False, "pulse_bpm": None, "reasons": ["SESSION_ENDED"], "quality": "UNKNOWN"}
        self.add_event("FINALIZING", "正在保存本次处理结果", completion_target=target)
        snap = self.snapshot()
        snap.update(session_state=target, status=target, revision=self.revision + 1)
        for window in snap["key_windows"]:
            if window["status"] in {"WAITING", "COLLECTING"}:
                window.update(status="MISSING", reasons=["NOT_COLLECTED"])
        snap["finalize_duration_ms"] = (time.monotonic() - self.finalize_started) * 1000
        try:
            self.writer.finish(snap)
        except Exception:
            self.state = "FAILED"
            self.revision += 1
            raise
        self.state = target
        self.revision += 1

    def snapshot(self):
        self.seq += 1
        marker = self.source.t0_us
        windows = []
        for center in (0, 60, 120):
            existing = next((w for w in self.windows if w["center_s"] == center), None)
            start = marker + (center - 4) * 1_000_000
            end = marker + (center + 4) * 1_000_000
            pending = (
                "MISSING" if self.state in TERMINAL else "COLLECTING" if self.time_us >= start else "WAITING"
            )
            windows.append(
                existing
                or {
                    "id": f"window_{center}",
                    "center_s": center,
                    "center_seconds": center,
                    "valid": False,
                    "pulse_bpm": None,
                    "status": pending,
                    "start_us": start,
                    "end_us": end,
                    "reasons": ["NOT_COLLECTED"] if self.state in TERMINAL else ["NOT_READY"],
                }
            )
        latest = dict(self.latest)
        support = getattr(self, "last_data_end_us", None)
        latest["data_age_s"] = (self.time_us - support) / 1e6 if support is not None else None
        if latest["data_age_s"] is not None and latest["data_age_s"] > self.config.get("stale_seconds", 2):
            latest.update(
                valid=False,
                pulse_bpm=None,
                reasons=list(dict.fromkeys(latest.get("reasons", []) + ["STALE"])),
            )
        latest.setdefault("quality", "VALID" if latest.get("valid") else "POOR_SIGNAL")
        dataset = self.source.metadata
        return {
            "schema_version": 2,
            "id": self.id,
            "session_id": self.id,
            "name": self.config["name"],
            "created_at_utc": self.created_at,
            "stream_id": self.timebase_id,
            "seq": self.seq,
            "revision": self.revision,
            "timebase_id": self.timebase_id,
            "engine_time_us": self.time_us,
            "t0_us": self.t0_us,
            "source_t0_us": marker,
            "duration_us": self.source.duration_us,
            "session_state": self.state,
            "status": self.state,
            "origin": "PUBLIC_DATA",
            "execution_mode": self.config["execution_mode"],
            "view_mode": "LIVE",
            "dataset": dataset,
            "history_ready": self.time_us >= 6_000_000,
            "sample_hz": self.source.sample_hz,
            "acquisition_state": self.acquisition_state,
            "latest_result": latest,
            "key_windows": windows,
            "recovery": recovery_result(windows),
            "waveform_tail": self.samples[-self.source.sample_hz * 30 :],
            "events": list(self.events)[-30:],
            "config_snapshot": self.config,
            "versions": {"app": "0.2.0", "algorithm": "2.0.0", "protocol": 2},
            "diagnostics": {
                "samples": len(self.samples),
                "playback_speed": self.config["speed"],
                "clock": "recording timestamps",
                "rtos": False,
            },
        }

    async def close(self):
        if self.task and not self.task.done():
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
        if self.state in ACTIVE:
            self.add_event("SERVICE_SHUTDOWN", "服务关闭，本次处理已中断", "warning")
            self.finalize("INTERRUPTED")
