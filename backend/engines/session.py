"""Authoritative Python session; browser navigation never controls acquisition time."""

import asyncio
import time
import uuid
from collections import deque

from backend.algorithms.policies import recovery_result
from backend.algorithms.processor import Processor
from backend.domain.models import ACTIVE, TERMINAL
from backend.sources.simulator import Source
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
        self.state = "CREATED"
        self.revision = 0
        self.time_us = 0
        self.t0_us = None
        self.timebase_id = uuid.uuid4().hex
        self.created_at = utcnow()
        self.processor = Processor(self.config)
        self.source = Source(self.config)
        self.samples = []
        self.events = deque(maxlen=500)
        self.faults = []
        self.windows = []
        self.latest = {
            "valid": False,
            "pulse_bpm": None,
            "reasons": ["PREPARING"],
            "quality": "UNKNOWN",
            "data_age_s": None,
        }
        self.acquisition_state = "OFF"
        self.task = None
        self.seq = 0
        self.writer = self.store.create(self.snapshot())
        self.add_event("CREATED", "会话已创建，等待开始准备")
        self.last_estimate_us = 0
        self.last_flush_us = 0
        self.started_clock = None
        self.finalize_started = None
        self.last_data_end_us = None

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
            raise DomainError("REVISION_CONFLICT", "会话状态已变化，请刷新后重试")
        if kind == "start_prepare":
            if self.state != "CREATED":
                raise DomainError("INVALID_STATE", "当前会话已开始")
            self.state = "PREPARING"
            self.started_clock = time.monotonic()
            self.task = asyncio.create_task(self.run(), name="SessionEngine")
            self.add_event("PREPARE_STARTED", "开始采集准备历史")
        elif kind == "mark_end":
            if self.state != "PREPARING" or self.t0_us is not None:
                raise DomainError("INVALID_STATE", "只能在准备阶段标记一次运动结束")
            self.t0_us = self.time_us
            self.state = "RECOVERING"
            self.add_event("T0_LOCKED", "运动结束时刻已锁定", history_ready=self.time_us >= 6_000_000)
            if self.time_us < 6_000_000:
                self.add_event("INSUFFICIENT_HISTORY", "准备不足6秒，起点窗口可能缺测", "warning")
        elif kind == "cancel":
            if self.state not in {"CREATED", "PREPARING", "RECOVERING"}:
                raise DomainError("INVALID_STATE", "会话已结束或正在收尾")
            self.add_event("CANCELLED", "用户取消记录", "warning")
            self.finalize("CANCELLED")
        self.revision += 1
        self.writer.flush() if not self.writer.closed else None
        self.store.update(self.snapshot())
        return self.snapshot()

    def update_faults(self):
        for fault in self.faults:
            if fault["status"] in {"REVOKED", "STOPPED", "COMPLETED"}:
                continue
            start = fault.get("start_us")
            if start is None and fault["anchor"] == "t0" and self.t0_us is not None:
                start = self.t0_us + round(fault["start_offset_s"] * 1e6)
                fault["start_us"] = start
            if start is None:
                continue
            if self.time_us >= start and fault["status"] == "PLANNED":
                fault["status"] = "RUNNING"
                self.add_event("FAULT_STARTED", "故障开始：" + fault["type"], "warning", fault_id=fault["id"])
            if self.time_us >= start + round(fault["duration_s"] * 1e6) and fault["status"] == "RUNNING":
                fault["status"] = "COMPLETED"
                self.add_event("FAULT_ENDED", "故障结束：" + fault["type"], fault_id=fault["id"])

    def advance(self, target_us):
        """Advance samples in 10-point chunks, never extrapolating across the final boundary."""
        if self.state not in {"PREPARING", "RECOVERING"}:
            return
        if self.t0_us is not None:
            target_us = min(target_us, self.t0_us + 124_000_000)
        while self.time_us + 10000 <= target_us:
            self.update_faults()
            count = min(10, (target_us - self.time_us) // 10000)
            samples = self.source.block(self.time_us, count, self.t0_us, self.faults)
            self.processor.feed(samples)
            self.samples.extend(samples)
            self.writer.samples(samples)
            self.time_us += count * 10000
            last = samples[-1]
            for sample in reversed(samples):
                if (
                    sample.get("ppg") is not None
                    and sample.get("on")
                    and not sample.get("gap")
                    and not sample.get("fault")
                ):
                    self.last_data_end_us = sample["time_us"] + 10000
                    break
            self.acquisition_state = (
                "ERROR"
                if last.get("fault")
                else ("OFF" if not last["on"] else ("WARMING" if last.get("warming") else "ON"))
            )
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
                        result["center_s"] = center
                        result["status"] = "VALID" if result["valid"] else "MISSING"
                        if center == 0 and self.t0_us < 6_000_000:
                            result.update(
                                valid=False,
                                pulse_bpm=None,
                                reasons=["INSUFFICIENT_HISTORY"],
                                reason_codes=["INSUFFICIENT_HISTORY"],
                                status="MISSING",
                            )
                        self.windows.append(result)
                        self.add_event(
                            "WINDOW_RESULT",
                            f"{center}秒窗口" + ("有效" if result["valid"] else "缺测"),
                            "info" if result["valid"] else "warning",
                            window=result,
                        )
            if not last["on"] or last.get("gap") or last.get("fault") or last.get("warming"):
                reasons = [
                    "SENSOR_FAULT"
                    if last.get("fault")
                    else "DATA_GAP"
                    if last.get("gap")
                    else "ACQUISITION_OFF"
                    if not last["on"]
                    else "WARMING"
                ]
                self.latest = {
                    "valid": False,
                    "pulse_bpm": None,
                    "reasons": reasons,
                    "quality": reasons[0],
                    "support_end_us": self.last_data_end_us,
                }
            if self.time_us - self.last_flush_us >= 1_000_000:
                self.writer.flush()
                self.store.update(self.snapshot())
                self.last_flush_us = self.time_us
        if self.t0_us is not None and self.time_us >= self.t0_us + 124_000_000:
            self.finalize("COMPLETED")
        elif self.t0_us is None and self.time_us >= self.config["prepare_timeout_seconds"] * 1_000_000:
            self.add_event("PREPARE_TIMEOUT", "准备超时，自动取消", "warning")
            self.finalize("CANCELLED")

    async def run(self):
        try:
            while self.state in {"PREPARING", "RECOVERING"}:
                elapsed = (time.monotonic() - self.started_clock) * self.config["speed"]
                # Catch up in bounded steps to keep REST and sockets responsive.
                target = min(round(elapsed * 100) * 10000, self.time_us + 1_000_000)
                self.advance(target)
                await asyncio.sleep(0.02)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.add_event("ENGINE_ERROR", str(exc), "error")
            try:
                self.finalize("FAILED")
            except Exception:
                self.state = "FAILED"
                self.store.update(self.snapshot())

    def finalize(self, target):
        if self.state in TERMINAL:
            return
        self.state = "FINALIZING"
        self.revision += 1
        self.finalize_started = time.monotonic()
        self.add_event("FINALIZING", "采集结束，正在保存结果", completion_target=target)
        self.acquisition_state = "OFF"
        self.latest = {
            "valid": False,
            "pulse_bpm": None,
            "reasons": ["SESSION_ENDED"],
            "quality": "UNKNOWN",
            "support_end_us": self.time_us,
        }
        self.add_event(target, "采集终态，提交记录文件", completion_target=target)
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
            self.store.update(self.snapshot())
            raise
        self.state = target
        self.revision += 1

    def snapshot(self):
        self.seq += 1
        windows = []
        for center in (0, 60, 120):
            existing = next((w for w in self.windows if w["center_s"] == center), None)
            start = None if self.t0_us is None else self.t0_us + (center - 4) * 1_000_000
            end = None if self.t0_us is None else self.t0_us + (center + 4) * 1_000_000
            pending_status = (
                "MISSING"
                if self.state in TERMINAL
                else ("COLLECTING" if start is not None and self.time_us >= start else "WAITING")
            )
            windows.append(
                existing
                or {
                    "id": f"window_{center}",
                    "center_s": center,
                    "center_seconds": center,
                    "valid": False,
                    "pulse_bpm": None,
                    "status": pending_status,
                    "start_us": start,
                    "end_us": end,
                    "reasons": ["NOT_COLLECTED"] if self.state in TERMINAL else ["WAITING"],
                }
            )
        latest = dict(self.latest)
        support = getattr(self, "last_data_end_us", None)
        latest["data_age_s"] = (self.time_us - support) / 1e6 if support is not None else None
        if latest["data_age_s"] is not None and latest["data_age_s"] > float(
            self.config.get("stale_seconds", 2)
        ):
            latest.update(
                valid=False,
                pulse_bpm=None,
                reasons=list(dict.fromkeys(latest.get("reasons", []) + ["STALE"])),
            )
        latest.setdefault("quality", "VALID" if latest.get("valid") else "POOR_SIGNAL")
        # A bounded 30s tail preserves raw gaps; display data are never the archive.
        tail = self.samples[-3000:]
        return {
            "schema_version": 1,
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
            "session_state": self.state,
            "status": self.state,
            "origin": "SIM",
            "execution_mode": self.config["execution_mode"],
            "view_mode": "LIVE",
            "history_ready": self.time_us >= 6_000_000,
            "acquisition_state": self.acquisition_state,
            "latest_result": latest,
            "key_windows": windows,
            "recovery": recovery_result(windows),
            "waveform_tail": tail,
            "events": list(self.events)[-30:],
            "faults": self.faults,
            "config_snapshot": self.config,
            "versions": {"app": "0.1.0", "algorithm": "1.0.0", "protocol": 1},
            "diagnostics": {
                "samples": len(self.samples),
                "time_scale": self.config["speed"],
                "clock": "monotonic simulation",
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
            self.add_event("SERVICE_SHUTDOWN", "服务退出，记录标记中断", "warning")
            self.finalize("INTERRUPTED")
