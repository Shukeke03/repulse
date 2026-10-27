"""Real recorded observations processed by an actual Cortex-M3 FreeRTOS kernel.

The host validates and serializes observations; it never computes live QEMU DSP.
A versioned semihosted file carries bulk samples in each direction. UART carries
only commands, acknowledgements and real kernel diagnostics.
"""

import asyncio
import hashlib
import json
import math
import queue
import shutil
import struct
import subprocess
import threading
import time
import uuid
import zlib
from collections import deque
from pathlib import Path

from backend.algorithms.policies import matched_periodic_width
from backend.domain.models import ACTIVE, TERMINAL
from backend.engines.session import DomainError, SessionEngine
from backend.sources.public_data import DatasetSource
from backend.storage.store import utcnow

PROJECT = Path(__file__).resolve().parents[2]
ELF = PROJECT / "firmware/build/qemu/repulse.elf"
QEMU = shutil.which("qemu-system-arm") or "/opt/homebrew/bin/qemu-system-arm"
INPUT_HEADER = struct.Struct("<8s11I")
INPUT_ROW = struct.Struct("<IIffII")
OUTPUT_HEADER = struct.Struct("<8sII")
OUTPUT_ROW = struct.Struct("<IIHBBfff")
PROTOCOL_VERSION = 2
POLICIES = {"continuous": 0, "critical": 1, "periodic": 2}
FIRMWARE_MESSAGES = {
    "INPUT_FILE_MISSING": "输入文件缺失",
    "INVALID_INPUT_HEADER": "输入格式或校验不一致",
    "TRUNCATED_INPUT": "输入文件不完整",
    "INPUT_SEQUENCE_ERROR": "输入序号或时间不连续",
    "INPUT_CRC_MISMATCH": "输入文件校验失败",
    "OUTPUT_FILE_ERROR": "无法创建处理结果文件",
    "OUTPUT_WRITE_FAILED": "处理结果写入失败",
    "RAW_QUEUE_OVERFLOW": "样本处理队列已满",
    "STACK_OVERFLOW": "固件任务栈溢出",
    "KERNEL_ASSERT": "固件内核检查失败",
    "REVISION_CONFLICT": "记录状态已变化，请重试",
    "INVALID_STATE": "当前状态不能执行此操作",
    "RECOVERY_STARTED": "已到原始记录的恢复起点",
    "SESSION_TERMINAL": "FreeRTOS处理已结束",
}


def availability():
    return Path(QEMU).is_file() and ELF.is_file()


def write_observations(path: Path, source: DatasetSource, config: dict) -> dict:
    """Serialize only observed native-rate samples, with an immutable data clock."""
    if source.sample_hz != 64 or source.period_us != 15625:
        raise ValueError("当前固件仅接受原始64Hz脉搏波")
    policy = config.get("strategy", "continuous")
    if policy not in POLICIES:
        raise ValueError("采集策略不存在")
    phase = float(config.get("phase", 0))
    speed = float(config.get("speed", 1))
    if not 0 <= phase < 20 or not 0.5 <= speed <= 10:
        raise ValueError("相位或播放速度超出范围")
    # The immutable full input is passed to the guest. Only guest Acquisition
    # applies policy masking, before qRaw and C DSP see any samples.
    full = source.all_samples
    payload = bytearray()
    for index, row in enumerate(full):
        if row["time_us"] != index * source.period_us or row["seq"] != index:
            raise ValueError("原始记录时间轴不连续，无法准备固件输入")
        ppg = row.get("ppg")
        accel = row.get("accel")
        gap = bool(row.get("gap") or ppg is None or not math.isfinite(float(ppg)))
        if accel is None or not math.isfinite(float(accel)):
            raise ValueError("当前固件需要完整运动观测；这条记录的缺失运动数据可用Python处理")
        payload += INPUT_ROW.pack(
            row["time_us"],
            index,
            0.0 if gap else float(ppg),
            0.0 if accel is None else float(accel),
            int(gap),
            0,
        )
    if len(full) * source.period_us != source.duration_us:
        raise ValueError("固件输入长度与所选片段不一致")
    width = matched_periodic_width(phase)
    prefix = struct.pack(
        "<8s10I",
        b"RPLREAL2",
        PROTOCOL_VERSION,
        64,
        len(full),
        source.t0_us,
        source.duration_us,
        zlib.crc32(payload),
        POLICIES[policy],
        round(phase * 1e6),
        round(width * 1e6),
        round(speed * 1000),
    )
    data = prefix + struct.pack("<I", zlib.crc32(prefix)) + payload
    path.write_bytes(data)
    return {
        "version": PROTOCOL_VERSION,
        "sample_hz": 64,
        "count": len(full),
        "payload_crc32": zlib.crc32(payload),
        "sha256": hashlib.sha256(data).hexdigest(),
        "float_format": "IEEE754 float32 little-endian",
        "t0_us": source.t0_us,
        "duration_us": source.duration_us,
        "strategy": policy,
        "phase_seconds": phase,
        "periodic_width_seconds": width,
        "playback_speed": speed,
    }


class FirmwarePort(SessionEngine):
    def __init__(self, store, config, defaults):
        if not availability():
            raise DomainError("SOURCE_UNAVAILABLE", "尚未找到QEMU或固件，请先执行Build QEMU", 422)
        fixed = {
            "sample_rate": 64,
            "sample_hz": 64,
            "band_low": 0.5,
            "band_high": 5.0,
            "warmup_seconds": 2.0,
            "window_seconds": 8.0,
            "stale_seconds": 2.0,
            "min_signal_std": 0.05,
            "motion_threshold": 0.35,
            "contact_threshold": None,
        }
        changed = [key for key, value in fixed.items() if key in defaults and defaults[key] != value]
        if changed:
            raise DomainError(
                "INVALID_PARAMETER",
                "当前FreeRTOS固件使用固定算法参数，请恢复默认设置：" + ", ".join(changed),
                422,
            )
        self.store = store
        self.id = uuid.uuid4().hex
        self.config = dict(defaults) | config | fixed | {"origin": "PUBLIC_DATA"}
        try:
            self.source = DatasetSource(self.config)
        except (ValueError, OSError) as exc:
            raise DomainError("SOURCE_UNAVAILABLE", str(exc), 422) from exc
        self.dataset = self.source.metadata
        self._source_annotations = self.source.all_samples
        self.period_us, self.sample_hz = self.source.period_us, self.source.sample_hz
        self.config["dataset"] = self.source.metadata
        self.config.setdefault("name", self.source.metadata["name"])
        self.config.setdefault("execution_mode", "FREERTOS_QEMU")
        self.config.setdefault("strategy", "continuous")
        self.config.setdefault("speed", 1)
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
        self._output = None
        self._output_buffer = b""
        self._output_verified = False
        self._output_crc = 0
        self.transport_dir = store.root / "transports" / self.id
        self.transport_dir.mkdir(parents=True)
        try:
            self.input_manifest = write_observations(
                self.transport_dir / "observations.bin", self.source, self.config
            )
        except (OSError, ValueError) as exc:
            shutil.rmtree(self.transport_dir, ignore_errors=True)
            raise DomainError("SOURCE_UNAVAILABLE", str(exc), 422) from exc
        self.config["firmware_input"] = self.input_manifest
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
                "-semihosting-config",
                "enable=on,target=native",
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            cwd=self.transport_dir,
        )
        self._reader = threading.Thread(target=self._read, name="FreeRTOS UART", daemon=True)
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
        if self.hello is None or not self.hello.get("source_available"):
            reason = self.hello.get("source_error") if self.hello else "没有收到固件应答"
            self._terminate()
            raise DomainError(
                "SOURCE_UNAVAILABLE",
                "FreeRTOS读取真实记录失败：" + FIRMWARE_MESSAGES.get(reason, str(reason)),
                422,
            )
        try:
            self.writer = self.store.create(self.snapshot())
        except Exception:
            self._terminate()
            raise
        self.add_event("CREATED", "真实记录已载入FreeRTOS", dataset_id=self.source.metadata["id"])

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
            if not self._closing:
                self._error = str(exc)

    def _read_output(self):
        path = self.transport_dir / "processed.bin"
        if self._output is None:
            if not path.exists():
                return
            self._output = path.open("rb", buffering=0)
        self._output_buffer += self._output.read()
        if not self._output_verified:
            if len(self._output_buffer) < OUTPUT_HEADER.size:
                return
            magic, version, hz = OUTPUT_HEADER.unpack_from(self._output_buffer)
            if magic != b"RPLOUT02" or version != 2 or hz != 64:
                raise RuntimeError("固件输出头校验失败")
            self._output_buffer = self._output_buffer[OUTPUT_HEADER.size :]
            self._output_verified = True
        count = len(self._output_buffer) // OUTPUT_ROW.size
        if not count:
            return
        body = self._output_buffer[: count * OUTPUT_ROW.size]
        self._output_buffer = self._output_buffer[len(body) :]
        self._output_crc = zlib.crc32(body, self._output_crc)
        rows = []
        for stamp, seq, segment, on, gap, ppg, accel, filtered in OUTPUT_ROW.iter_unpack(body):
            if seq != len(self.samples) + len(rows) or stamp != seq * self.period_us:
                raise RuntimeError("固件输出出现序号或时间缺口")
            if not all(math.isfinite(value) for value in (ppg, accel, filtered)):
                raise RuntimeError("固件输出包含非有限数值")
            row = {
                "time_us": stamp,
                "seq": seq,
                "segment_id": segment,
                "ppg": ppg if on and not gap else None,
                "accel": accel,
                "on": bool(on),
                "gap": bool(gap),
                "fault": None,
                "filtered": filtered if on and not gap else None,
            }
            original = self._source_annotations[seq]
            for key in ("reference_bpm", "source_time_us", "accel_x", "accel_y", "accel_z"):
                row[key] = original.get(key)
            if on and not gap:
                self.last_data_end_us = stamp + self.period_us
                self._segment_start_us.setdefault(segment, stamp)
                row["warming"] = stamp - self._segment_start_us[segment] < 2_000_000
            rows.append(row)
        self.samples.extend(rows)
        if not self.writer.closed:
            self.writer.samples(rows)
        last = rows[-1]
        self.acquisition_state = "OFF" if not last["on"] else "WARMING" if last.get("warming") else "ON"

    def _apply(self, message):
        kind = message.get("type")
        if kind == "snapshot":
            self._read_output()
            previous = self.state
            self.time_us = int(message["engine_time_us"])
            self.t0_us = message.get("t0_us")
            self.revision = int(message["revision"])
            target = message["session_state"] if message["session_state"] in TERMINAL else None
            self.state = "FINALIZING" if target and not self.writer.closed else message["session_state"]
            latest = message["latest_result"]
            latest["support_end_us"] = latest.get("end_us")
            reason = (latest.get("reasons") or ["UNKNOWN"])[0]
            latest["quality"] = (
                "VALID"
                if latest.get("valid")
                else {
                    "ACQUISITION_OFF": "OFF",
                    "DATA_GAP": "DATA_GAP",
                    "WARMUP": "UNKNOWN",
                    "INSUFFICIENT_HISTORY": "UNKNOWN",
                    "PENDING": "UNKNOWN",
                }.get(reason, "POOR_SIGNAL")
            )
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
                    id=f"window_{center}",
                    center_s=center,
                    center_seconds=center,
                    status="VALID" if result["valid"] else "MISSING",
                    sample_count=len(observed),
                    segment_ids=segments,
                    support_start_us=result["start_us"],
                    support_end_us=result["end_us"],
                    reason_codes=list(result["reasons"]),
                    warmup_start_us=min((self._segment_start_us[s] for s in segments), default=None),
                )
                if not any(w["center_s"] == center for w in self.windows):
                    self.windows.append(result)
                    self.add_event("WINDOW_RESULT", f"{center}秒窗口已处理", window=result)
            if not self.writer.closed:
                if latest.get("end_us", 0) > self.last_estimate_us:
                    self.writer.estimate(latest)
                    self.last_estimate_us = latest["end_us"]
                if self.time_us - self.last_flush_us >= 1_000_000:
                    self.writer.flush()
                    self.store.update(self.snapshot())
                    self.last_flush_us = self.time_us
                if target:
                    self._read_output()
                    if self._output_buffer or len(self.samples) != self.firmware_diagnostics.get(
                        "output_rows"
                    ):
                        raise RuntimeError("固件结束时原始输出尚不完整")
                    if self._output_crc != self.firmware_diagnostics.get("output_crc32"):
                        raise RuntimeError("固件输出CRC校验失败")
                    if target == "COMPLETED" and (
                        len(self.samples) != self.input_manifest["count"]
                        or self.firmware_diagnostics.get("source_crc32")
                        != self.input_manifest["payload_crc32"]
                    ):
                        raise RuntimeError("固件未处理完整真实记录")
                    self.add_event(target, "FreeRTOS处理结束，记录已保存")
                    committed = self.snapshot()
                    committed.update(session_state=target, status=target)
                    for window in committed["key_windows"]:
                        if window["status"] in {"WAITING", "COLLECTING"}:
                            window.update(status="MISSING", reasons=["NOT_COLLECTED"])
                    self.writer.finish(committed)
                    self.state = target
                    self.acquisition_state = "OFF"
                elif self.state != previous:
                    self.store.update(self.snapshot())
        elif kind == "event":
            code = message.get("code", "FIRMWARE_EVENT")
            self.add_event(code, FIRMWARE_MESSAGES.get(code, "FreeRTOS状态已更新"), firmware_code=code)
        elif kind == "fatal":
            self._error = message.get("code", "FIRMWARE_FATAL")

    def command(self, kind, revision):
        if revision != self.revision:
            raise DomainError("REVISION_CONFLICT", "记录状态已变化，请刷新后重试")
        if kind == "mark_end":
            raise DomainError("SOURCE_EVENT_IMMUTABLE", "恢复起点来自原始记录，不能手动更改")
        command_id = uuid.uuid4().hex
        payload = {"type": kind, "expected_revision": revision, "command_id": command_id}
        self.process.stdin.write(json.dumps(payload, separators=(",", ":")) + "\n")
        self.process.stdin.flush()
        deadline, ack = time.monotonic() + 8, None
        while time.monotonic() < deadline:
            try:
                message = self._messages.get(timeout=0.05)
            except queue.Empty:
                self._read_output()
                continue
            if message.get("type") == "ack" and message.get("command_id") == command_id:
                ack = message
                if not ack.get("applied"):
                    code = ack.get("code", "INVALID_STATE")
                    raise DomainError(
                        code, FIRMWARE_MESSAGES.get(code, "FreeRTOS未能执行此操作，请查看系统状态")
                    )
            else:
                self._apply(message)
            if ack and self.revision >= ack["revision"]:
                if self.task is None and self.state not in TERMINAL:
                    self.task = asyncio.create_task(self.run(), name="FreeRTOSQEMU")
                return self.snapshot()
        raise DomainError("COMMAND_TIMEOUT", "FreeRTOS没有及时应答，请查看系统状态", 503)

    async def run(self):
        try:
            while not self._closing:
                self._read_output()
                for _ in range(100):
                    try:
                        self._apply(self._messages.get_nowait())
                    except queue.Empty:
                        break
                if self._error or self.process.poll() is not None:
                    if self.state in ACTIVE:
                        self.add_event(
                            "FIRMWARE_DISCONNECTED",
                            FIRMWARE_MESSAGES.get(self._error, self._error or "QEMU进程退出"),
                            "error",
                        )
                        self._host_finish("INTERRUPTED")
                    self._terminate()
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
        # The processed-file channel can arrive before a UART state snapshot.
        # Display only samples covered by that authoritative snapshot, never
        # future waveform points or a negative data age.
        visible = [row for row in self.samples if row["time_us"] < self.time_us]
        snap["waveform_tail"] = visible[-self.sample_hz * 30 :]
        supported = next(
            (
                row["time_us"] + self.period_us
                for row in reversed(visible)
                if row["ppg"] is not None and row["on"] and not row["gap"]
            ),
            None,
        )
        snap["latest_result"]["data_age_s"] = (
            max(0, (self.time_us - supported) / 1e6) if supported is not None else None
        )
        if visible:
            last = visible[-1]
            snap["acquisition_state"] = (
                "OFF" if not last["on"] else "WARMING" if last.get("warming") else "ON"
            )
        snap.update(
            origin="PUBLIC_DATA",
            dataset=self.source.metadata,
            dataset_id=self.source.metadata["id"],
            planned_t0_us=self.source.t0_us,
            duration_us=self.source.duration_us,
            read_only_t0=True,
        )
        snap["diagnostics"] = {
            **self.firmware_diagnostics,
            "samples": len(self.samples),
            "rtos": True,
            "clock": "原始记录时间轴",
            "time_scale": self.config["speed"],
            "playback_speed": self.config["speed"],
            "tasks": self.firmware_tasks,
            "dsp_owner": "firmware",
            "source": "公开记录",
            "transport": "validated semihosted observations",
            "qemu_pid": self.process.pid if hasattr(self, "process") else None,
        }
        snap["versions"].update(kernel="FreeRTOS V11.2.0", protocol=2)
        return snap

    def _terminate(self):
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=3)
        self._closing = True
        if self._output:
            self._output.close()
            self._output = None
        for handle in (self.process.stdin, self.process.stdout, self.process.stderr):
            if handle:
                try:
                    handle.close()
                except OSError:
                    pass
        shutil.rmtree(self.transport_dir, ignore_errors=True)

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
                self.add_event("SERVICE_SHUTDOWN", "服务退出，记录已中断", "warning")
                self._host_finish("INTERRUPTED")
        finally:
            self._terminate()
