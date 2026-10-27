"""Run a standalone FreeRTOS instance on a selected local real recording."""

import argparse
import json
import subprocess
import tempfile
import uuid
from pathlib import Path

from backend.domain.models import DEFAULT_DATASET
from backend.engines.firmware_port import ELF, PROJECT, QEMU, availability, write_observations
from backend.sources.public_data import DatasetSource


def main():
    parser = argparse.ArgumentParser(description="FreeRTOS真实记录处理终端")
    parser.add_argument("--dataset-id", "--dataset", dest="dataset", default=DEFAULT_DATASET)
    parser.add_argument("--strategy", default="continuous", choices=["continuous", "critical", "periodic"])
    parser.add_argument("--speed", type=float, default=1)
    parser.add_argument("--phase", type=int, default=0)
    parser.add_argument("--gdb", action="store_true", help="在127.0.0.1:3333等待GDB连接")
    args = parser.parse_args()
    if not 0.5 <= args.speed <= 10 or not 0 <= args.phase < 20:
        parser.error("播放速度应为0.5至10倍，相位应为0至19秒")
    if not availability():
        parser.error("找不到QEMU或固件；请先执行 make -C firmware TARGET=qemu")
    config = {"dataset_id": args.dataset, "strategy": args.strategy, "speed": args.speed, "phase": args.phase}
    try:
        source = DatasetSource(config)
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    parent = PROJECT / "data/transports"
    parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="terminal-", dir=parent) as directory:
        write_observations(Path(directory) / "observations.bin", source, config)
        cmd = [
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
        ]
        if args.gdb:
            cmd.extend(["-S", "-gdb", "tcp:127.0.0.1:3333"])
            print("QEMU GDB ready: 127.0.0.1:3333", flush=True)
        process = subprocess.Popen(
            cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, cwd=directory
        )
        try:
            for line in process.stdout:
                print(line, end="", flush=True)
                try:
                    message = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if message.get("type") == "hello":
                    if not message.get("source_available"):
                        raise RuntimeError(message.get("source_error"))
                    command = {
                        "type": "start_prepare",
                        "expected_revision": 0,
                        "command_id": uuid.uuid4().hex,
                    }
                    process.stdin.write(json.dumps(command, separators=(",", ":")) + "\n")
                    process.stdin.flush()
                if message.get("type") == "snapshot" and message.get("session_state") in {
                    "COMPLETED",
                    "CANCELLED",
                    "FAILED",
                }:
                    if message["session_state"] == "FAILED":
                        raise RuntimeError("FreeRTOS处理失败，请查看上方诊断")
                    break
                if message.get("type") == "fatal":
                    raise RuntimeError(message.get("code", "FIRMWARE_FATAL"))
        except KeyboardInterrupt:
            pass
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()


if __name__ == "__main__":
    main()
