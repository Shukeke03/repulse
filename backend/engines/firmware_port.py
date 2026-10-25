"""FreeRTOS QEMU owns acquisition, state, filtering and window results.

The bridge only translates serial protocol, archives observations and exposes snapshots.
The Python SignalProcessor is never run on QEMU telemetry for live result publication.
"""

import asyncio
import json
import queue
import shutil
import subprocess
import threading
import time
import uuid
from collections import deque
from pathlib import Path

from backend.domain.models import ACTIVE, DEFAULTS, TERMINAL
from backend.engines.session import DomainError, SessionEngine
from backend.storage.store import utcnow

PROJECT = Path(__file__).resolve().parents[2]
ELF = PROJECT / "firmware/build/qemu/repulse.elf"
QEMU = shutil.which("qemu-system-arm") or "/opt/homebrew/bin/qemu-system-arm"


def availability():
    return Path(QEMU).is_file() and ELF.is_file()


class FirmwarePort(SessionEngine):
    def __init__(self, store, config, defaults):
        if not availability():
            raise DomainError("SOURCE_UNAVAILABLE", "QEMU或已构建固件不可用，请运行Build QEMU", 409)
        # The first firmware release exposes its own normal synthetic source.
        if config.get("scenario", "normal") not in {"normal", "early"}:
            raise DomainError(
                "INVALID_PARAMETER", "当前QEMU固件只支持正常恢复和起点不足场景；故障试验请使用Python环境", 422
            )
        if config.get("strategy", "critical") == "periodic":
            raise DomainError(
                "INVALID_PARAMETER", "当前QEMU固件未验证等预算周期策略，请使用Python环境进行策略比较", 422
            )
        if config.get("speed", 1) != 1:
            raise DomainError("INVALID_PARAMETER", "QEMU运行使用真实内核时钟，速度请设为1倍", 422)
        fixed = {
            "sample_rate": 100,
            "band_low": 0.5,
            "band_high": 5.0,
            "warmup_seconds": 2.0,
            "window_seconds": 8.0,
            "stale_seconds": 2.0,
            "prepare_timeout_seconds": 1800,
            "motion_threshold": 0.65,
            "contact_threshold": None,
        }
        changed = [key for key in fixed if defaults.get(key, DEFAULTS.get(key)) != DEFAULTS.get(key)]
        if changed:
            raise DomainError(
                "INVALID_PARAMETER",
                "当前QEMU固件使用固定算法参数；请恢复这些设置的默认值或使用Python模式：" + ", ".join(changed),
                422,
            )
        self.store, self.id = store, uuid.uuid4().hex
        self.config = dict(defaults) | config | fixed
        self.state, self.revision, self.time_us, self.t0_us = "CREATED", 0, 0, None
        self.timebase_id, self.created_at = uuid.uuid4().hex, utcnow()
        self.samples, self.events, self.faults, self.windows = [], deque(maxlen=500), [], []
        self.latest = {"valid": False, "pulse_bpm": None, "reasons": ["PREPARING"], "quality": "UNKNOWN"}
        self.acquisition_state, self.task, self.seq = "OFF", None, 0
        self.last_flush_us = self.last_estimate_us = 0
        self.last_data_end_us = None
        self._segment_start_us = {}
        self.started_clock = self.finalize_started = None
        self.firmware_tasks, self.firmware_diagnostics = [], {}
        self.hello = None
        self._messages = queue.Queue(maxsize=4096)
        self._closing = False
        self._error = None
        self.process = subprocess.Popen(
            [
                QEMU,
                "-M",
                "mps2-an385",
                "-cpu",
                "cortex-m3",
                "-kernel",
                str(ELF),
                "-display",
                "none",
                "-monitor",
                "none",
                "-serial",
                "stdio",
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            cwd=PROJECT,
        )
        self._reader = threading.Thread(target=self._read, name="QEMU UART", daemon=True)
        self._reader.start()
        deadline = time.monotonic() + 8
        while self.hello is None and time.monotonic() < deadline:
            try:
                message = self._messages.get(timeout=0.2)
            except queue.Empty:
                if self.process.poll() is not None:
                    break
                continue
            if message.get("type") == "hello":
                self.hello = message
        if self.hello is None:
            self._terminate()
            raise DomainError("SOURCE_UNAVAILABLE", "QEMU启动后未收到固件握手，请查看固件诊断", 409)
        try:
            self.writer = self.store.create(self.snapshot())
        except Exception:
            self._terminate()
            raise
        self.add_event("CREATED", "真实FreeRTOS QEMU已连接；数据来自固件合成源")

    def _read(self):
        try:
            for line in self.process.stdout:
                if self._closing:
                    break
                try:
                    message = json.loads(line)
                except json.JSONDecodeError:
                    self._error = "INVALID_UART_JSON"
                    break
                try:
                    self._messages.put(message, timeout=1)
                except queue.Full:
                    self._error = "UART_BACKPRESSURE"
                    break
        except (OSError, ValueError) as exc:
            self._error = str(exc)

    def _apply(self, message):
        kind = message.get("type")
        if kind == "samples":
            rows = []
            for values in message["samples"]:
                stamp, seq, segment, ppg, accel, on, gap = values[:7]
                filtered = values[7] / 1000 if len(values) > 7 and on and not gap else None
                row = {
                    "time_us": stamp,
                    "seq": seq,
                    "segment_id": segment,
                    "ppg": ppg / 1000 if on and not gap else None,
                    "accel": accel / 1000,
                    "on": bool(on),
                    "gap": bool(gap),
                    "filtered": filtered,
                }
                if on and not gap:
                    self.last_data_end_us = stamp + 10000
                    self._segment_start_us.setdefault(segment, stamp)
                    row["warming"] = stamp - self._segment_start_us[segment] < 2_000_000
                rows.append(row)
            if not self.writer.closed:
                self.samples.extend(rows)
                self.writer.samples(rows)
                if rows:
                    self.acquisition_state = (
                        "OFF" if not rows[-1]["on"] else "WARMING" if rows[-1].get("warming") else "ON"
                    )
        elif kind == "snapshot":
            previous = self.state
            self.time_us = int(message["engine_time_us"])
            self.t0_us = message.get("t0_us")
            self.revision = int(message["revision"])
            terminal_target = message["session_state"] if message["session_state"] in TERMINAL else None
            self.state = (
                "FINALIZING" if terminal_target and not self.writer.closed else message["session_state"]
            )
            latest = message["latest_result"]
            latest["support_end_us"] = latest.get("end_us")
            self.latest = latest
            self.firmware_tasks = message.get("tasks", [])
            self.firmware_diagnostics = message.get("diagnostics", {})
            for center, result in zip((0, 60, 120), message.get("key_windows", [])):
                if result.get("reasons") == ["PENDING"] or not result.get("end_us"):
                    continue
                observed = [
                    row
                    for row in self.samples
                    if result["start_us"] <= row["time_us"] < result["end_us"] and row["ppg"] is not None
                ]
                segments = sorted({row["segment_id"] for row in observed})
                result.update(
                    sample_count=len(observed),
                    segment_ids=segments,
                    support_start_us=result["start_us"],
                    support_end_us=result["end_us"],
                    reason_codes=list(result["reasons"]),
                    warmup_start_us=min((self._segment_start_us[v] for v in segments), default=None),
                )
                result.update(
                    id=f"window_{center}",
                    center_s=center,
                    center_seconds=center,
                    status="VALID" if result["valid"] else "MISSING",
                )
                if not any(w["center_s"] == center for w in self.windows):
                    self.windows.append(result)
                    self.add_event(
                        "WINDOW_RESULT",
                        f"FreeRTOS {center}秒窗口" + ("有效" if result["valid"] else "缺测"),
                        window=result,
                    )
            if not self.writer.closed:
                if latest.get("end_us", 0) > self.last_estimate_us:
                    self.writer.estimate(latest)
                    self.last_estimate_us = latest["end_us"]
                if self.time_us - self.last_flush_us >= 1_000_000:
                    self.writer.flush()
                    self.store.update(self.snapshot())
                    self.last_flush_us = self.time_us
                if terminal_target:
                    self.add_event(terminal_target, "FreeRTOS采集结束，正在保存结果")
                    self.acquisition_state = "OFF"
                    committed = self.snapshot()
                    committed.update(session_state=terminal_target, status=terminal_target)
                    self.writer.finish(committed)
                    self.state = terminal_target
                elif self.state != previous:
                    self.store.update(self.snapshot())
        elif kind == "event":
            self.add_event(message.get("code", "FIRMWARE_EVENT"), "FreeRTOS事件：" + message.get("code", ""))
        elif kind == "fatal":
            self._error = message.get("code", "FIRMWARE_FATAL")

    def command(self, kind, revision):
        if revision != self.revision:
            raise DomainError("REVISION_CONFLICT", "固件状态已变化，请刷新后重试")
        command_id = uuid.uuid4().hex
        payload = {
            "type": kind,
            "expected_revision": revision,
            "command_id": command_id,
            "strategy": self.config["strategy"],
            "seed": self.config["seed"],
        }
        self.process.stdin.write(json.dumps(payload, separators=(",", ":")) + "\n")
        self.process.stdin.flush()
        deadline, ack = time.monotonic() + 8, None
        while time.monotonic() < deadline:
            try:
                message = self._messages.get(timeout=0.1)
            except queue.Empty:
                continue
            if message.get("type") == "ack" and message.get("command_id") == command_id:
                ack = message
                if not ack.get("applied"):
                    raise DomainError(
                        ack.get("code", "INVALID_STATE"), "固件拒绝命令：" + ack.get("code", "")
                    )
            else:
                self._apply(message)
            if ack and self.revision >= ack["revision"]:
                if self.task is None:
                    self.task = asyncio.create_task(self.run(), name="FreeRTOSQEMU")
                return self.snapshot()
        raise DomainError("COMMAND_TIMEOUT", "固件未及时确认命令，连接状态需检查", 503)

    async def run(self):
        try:
            while not self._closing:
                for _ in range(100):
                    try:
                        self._apply(self._messages.get_nowait())
                    except queue.Empty:
                        break
                if self._error or self.process.poll() is not None:
                    if self.state in ACTIVE:
                        self.add_event("FIRMWARE_DISCONNECTED", self._error or "QEMU进程退出", "error")
                        self._host_finish("INTERRUPTED")
                    return
                if self.state in TERMINAL:
                    self._terminate()
                    return
                await asyncio.sleep(0.02)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.add_event("FIRMWARE_BRIDGE_ERROR", str(exc), "error")
            try:
                self._host_finish("FAILED")
            finally:
                self._terminate()

    def _host_finish(self, target):
        self.state, self.acquisition_state = "FINALIZING", "OFF"
        self.revision += 1
        committed = self.snapshot()
        committed.update(session_state=target, status=target)
        if not self.writer.closed:
            try:
                self.writer.finish(committed)
            except Exception:
                self.state = "FAILED"
                raise
        self.state = target

    def snapshot(self):
        snap = super().snapshot()
        snap["diagnostics"] = {
            **self.firmware_diagnostics,
            "samples": len(self.samples),
            "rtos": True,
            "clock": "QEMU Cortex-M3 FreeRTOS tick (not STM32 measurement)",
            "time_scale": 1,
            "tasks": self.firmware_tasks,
            "dsp_owner": "firmware",
            "source": "firmware synthetic source",
            "qemu_pid": self.process.pid if hasattr(self, "process") else None,
        }
        snap["versions"]["kernel"] = "FreeRTOS V11.2.0"
        return snap

    def _terminate(self):
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=3)

    async def close(self):
        self._closing = True
        if self.task and not self.task.done():
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
        try:
            if self.state in ACTIVE:
                self.add_event("SERVICE_SHUTDOWN", "服务退出，QEMU记录中断", "warning")
                self._host_finish("INTERRUPTED")
        finally:
            self._terminate()
