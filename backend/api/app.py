"""Local-only RePulse API and static frontend. Run one worker."""

import asyncio
import fcntl
import hashlib
import inspect
import json
import math
import os
import platform
import shutil
import time
import uuid
import zipfile
from concurrent.futures import ProcessPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from backend.domain.models import (
    ACTIVE,
    DEFAULTS,
    TERMINAL,
    Annotation,
    Command,
    ComparisonConfig,
    SessionConfig,
)
from backend.engines.firmware_port import FirmwarePort
from backend.engines.firmware_port import availability as firmware_available
from backend.engines.replay import ReplayEngine
from backend.engines.session import DomainError, SessionEngine
from backend.sources.public_data import DatasetSource, get_dataset, list_datasets, record_directory
from backend.storage.store import Store, atomic_json, json_text, utcnow

PROJECT = Path(__file__).resolve().parents[2]


def comparison_unit(config, dataset_id, phase):
    from backend.workers.compare import compare_unit

    return compare_unit(dataset_id, phase, config.get("algorithm_config"))


def aggregate(results):
    from backend.workers.compare import summarize

    output = []
    for row in summarize(results)["strategies"]:
        if not row["runs"]:
            continue
        output.append(
            {
                **row,
                "window0_rate": row["window_availability"]["0"],
                "window60_rate": row["window_availability"]["60"],
                "window120_rate": row["window_availability"]["120"],
                "delta60_rate": row["delta60_availability"],
                "delta120_rate": row["delta120_availability"],
            }
        )
    return output


class Service:
    def __init__(self, data_root):
        root = Path(data_root).resolve()
        root.mkdir(parents=True, exist_ok=True)
        self.lockfile = (root / ".service.lock").open("a")
        try:
            fcntl.flock(self.lockfile, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self.lockfile.close()
            raise RuntimeError("此数据目录已有RePulse进程运行。只允许单进程单worker。")
        self.store = Store(root)
        stored = self.store.settings() or {}
        display_keys = {"chart_seconds", "show_filtered", "ui_hz", "screen_timeout_s"}
        self.settings = {
            "version": max(1, stored.get("version", 0)),
            **DEFAULTS,
            **{key: stored[key] for key in display_keys if key in stored},
        }
        if stored.get("sample_rate") == 64:
            self.settings.update({key: stored[key] for key in DEFAULTS if key in stored})
        elif stored:
            self.settings["version"] += 1
            self.store.save_settings(self.settings)
        self.engine = None
        self.replays = {}
        self.jobs = {j["id"]: j for j in self.store.jobs() if "dataset_ids" in j.get("config", {})}
        self.job_tasks = {}
        self.pool = None
        self.lock = asyncio.Lock()
        self.run_id = uuid.uuid4().hex
        self.started = time.monotonic()
        self.logs = []
        self.exports = {}

    def active(self):
        return self.engine if self.engine and self.engine.state in ACTIVE else None

    def get(self, sid):
        if self.engine and self.engine.id == sid:
            snap = self.engine.snapshot()
            stored = self.store.get(sid)
            if stored:
                snap.update({k: stored[k] for k in ("name", "notes", "tags", "annotation", "raw_available")})
            return snap
        result = self.store.get(sid)
        if not result:
            raise DomainError("NOT_FOUND", "记录不存在", 404)
        return result

    def capabilities(self):
        available = bool(list_datasets())
        qemu = firmware_available() and available
        connected = bool(self.active() and self.engine.config["execution_mode"] == "FREERTOS_QEMU")
        return {
            "python": available,
            "qemu": qemu,
            "device": False,
            "qemu_connected": connected,
            "qemu_installed": firmware_available(),
            "execution_modes": ["PYTHON"] + (["FREERTOS_QEMU"] if qemu else []),
            "origins": ["PUBLIC_DATA"],
            "qemu_limits": {
                "strategies": ["continuous", "critical", "periodic"],
                "speeds": [1, 2, 5, 10],
                "fixed_parameters": True,
            },
            "qemu_reason": "读取同一份公开记录，由FreeRTOS任务处理"
            if qemu
            else "需要本地数据和已构建的QEMU固件",
            "device_reason": "尚未连接开发板和传感器",
            "download_command": "python -m scripts.download_dataset",
        }

    async def mutate(self, request, callback):
        key = request.headers.get("Idempotency-Key")
        if not key or len(key) > 200:
            raise DomainError("INVALID_PARAMETER", "写入请求需要Idempotency-Key", 422)
        raw = await request.body()
        fingerprint = hashlib.sha256(request.method.encode() + request.url.path.encode() + raw).hexdigest()
        async with self.lock:
            cached = self.store.command(key)
            if cached:
                if cached[0] != fingerprint:
                    raise DomainError("IDEMPOTENCY_CONFLICT", "此命令键已用于其他请求")
                return cached[1]
            self.store.begin_mutation()
            try:
                result = callback()
                if inspect.isawaitable(result):
                    result = await result
                self.store.save_command(key, fingerprint, result)
                self.store.end_mutation(True)
                return result
            except BaseException:
                self.store.end_mutation(False)
                raise

    async def compare(self, job):
        if self.pool is None:
            self.pool = ProcessPoolExecutor(max_workers=1)
        config = job["config"]
        try:
            loop = asyncio.get_running_loop()
            for dataset_id in config["dataset_ids"]:
                for phase in config["phases"]:
                    if job["status"] == "CANCELLING":
                        job["status"] = "CANCELLED"
                        self.store.save_job(job)
                        return
                    job["current_dataset_id"], job["current_phase"] = dataset_id, phase
                    try:
                        rows = await loop.run_in_executor(
                            self.pool, comparison_unit, config, dataset_id, phase
                        )
                        job["results"].extend(rows)
                        job["progress"]["completed"] += 3
                        job["metrics"] = aggregate(job["results"])
                        from backend.workers.compare import summarize

                        summary = summarize(
                            job["results"], len(config["dataset_ids"]) * len(config["phases"])
                        )
                        job["paired_units"] = summary["paired_units"]
                        job["excluded_count"] = summary["excluded_units"]
                    except Exception as exc:
                        job["progress"]["failed"] += 3
                        job["excluded_count"] += 1
                        job["errors"].append({"dataset_id": dataset_id, "phase": phase, "message": str(exc)})
                    self.store.save_job(job)
                    folder = self.store.root / "comparisons" / job["id"]
                    atomic_json(folder / "progress.json", job)
                    await asyncio.sleep(0)
            job["status"] = "COMPLETED" if not job["progress"]["failed"] else "COMPLETED_WITH_ERRORS"
        except asyncio.CancelledError:
            job["status"] = "INTERRUPTED"
        finally:
            from backend.workers.compare import summarize

            summary = summarize(job["results"], len(config["dataset_ids"]) * len(config["phases"]))
            job["paired_units"] = summary["paired_units"]
            job["excluded_count"] = summary["excluded_units"]
            self.store.save_job(job)
            atomic_json(self.store.root / "comparisons" / job["id"] / "progress.json", job)

    async def close(self):
        if self.engine:
            await self.engine.close()
        for replay in self.replays.values():
            replay.closed = True
            replay.task.cancel()
        for task in self.job_tasks.values():
            task.cancel()
        if self.job_tasks:
            await asyncio.gather(*self.job_tasks.values(), return_exceptions=True)
        if self.pool:
            self.pool.shutdown(wait=False, cancel_futures=True)
        self.store.close()
        self.lockfile.close()


def create_app(data_root=None):
    @asynccontextmanager
    async def lifespan(app):
        app.state.service = Service(data_root or os.environ.get("REPULSE_DATA_DIR", str(PROJECT / "data")))
        yield
        await app.state.service.close()

    app = FastAPI(title="RePulse 复脉 API", version="0.2.0", lifespan=lifespan)

    def service(request):
        return request.app.state.service

    @app.exception_handler(DomainError)
    async def domain_error(request, exc):
        return JSONResponse(
            status_code=exc.status,
            content={
                "code": exc.code,
                "message": exc.message,
                "field_errors": {},
                "request_id": uuid.uuid4().hex,
            },
        )

    @app.exception_handler(RequestValidationError)
    async def invalid(request, exc):
        return JSONResponse(
            status_code=422,
            content={
                "code": "INVALID_PARAMETER",
                "message": "参数不符合要求",
                "field_errors": {".".join(map(str, e["loc"])): e["msg"] for e in exc.errors()},
                "request_id": uuid.uuid4().hex,
            },
        )

    @app.exception_handler(ValueError)
    async def invalid_data(request, exc):
        return JSONResponse(
            status_code=422,
            content={
                "code": "INVALID_PARAMETER",
                "message": str(exc),
                "field_errors": {},
                "request_id": uuid.uuid4().hex,
            },
        )

    @app.exception_handler(OSError)
    async def storage_error(request, exc):
        return JSONResponse(
            status_code=500,
            content={
                "code": "STORAGE_ERROR",
                "message": f"存储错误：{exc.strerror or str(exc)}",
                "field_errors": {},
                "request_id": uuid.uuid4().hex,
            },
        )

    @app.get("/api/bootstrap")
    async def bootstrap(request: Request):
        s = service(request)
        return {
            "version": "0.2.0",
            "stream_id": s.run_id,
            "defaults": s.settings,
            "capabilities": s.capabilities(),
            "active_session": s.active().snapshot() if s.active() else None,
            "datasets": list_datasets(),
        }

    @app.get("/api/datasets")
    async def datasets():
        return {"items": list_datasets(), "download_command": "python -m scripts.download_dataset"}

    @app.get("/api/datasets/{dataset_id}/samples")
    async def dataset_samples(
        dataset_id: str, start_us: int = 0, end_us: int | None = None, max_points: int = 2000
    ):
        if start_us < 0 or not 1 <= max_points <= 20000 or (end_us is not None and end_us < start_us):
            raise DomainError("INVALID_PARAMETER", "请使用有效的时间范围和显示点数", 422)
        source = DatasetSource({"dataset_id": dataset_id, "strategy": "continuous"})
        rows = [
            r
            for r in source.all_samples
            if r["time_us"] >= start_us and (end_us is None or r["time_us"] < end_us)
        ]
        stride = max(1, (len(rows) + max_points - 1) // max_points)
        return {
            "samples": rows[::stride],
            "first_us": 0,
            "last_us": source.duration_us,
            "t0_us": source.t0_us,
            "sample_hz": source.sample_hz,
            "unit": "设备相对单位",
            "accel_unit": "g",
            "decimation": stride,
            "origin": "PUBLIC_DATA",
        }

    @app.get("/api/datasets/{dataset_id}")
    async def dataset_detail(dataset_id: str):
        return get_dataset(dataset_id)

    @app.get("/api/datasets/{dataset_id}/files/{name}")
    async def dataset_file(dataset_id: str, name: str):
        metadata = get_dataset(dataset_id)
        if name not in {entry["name"] for entry in metadata["files"]}:
            raise DomainError("NOT_FOUND", "原始数据文件不存在", 404)
        return FileResponse(record_directory(dataset_id) / name, filename=f"{metadata['subject']}_{name}")

    @app.post("/api/sessions")
    async def create_session(body: SessionConfig, request: Request):
        s = service(request)

        def action():
            if s.active():
                raise DomainError("ACTIVE_SESSION_EXISTS", "已有记录运行中，请返回实时监护")
            if body.execution_mode == "STM32_DEVICE":
                raise DomainError("SOURCE_UNAVAILABLE", "该执行环境尚未通过接入验证，暂不可用", 422)
            cls = FirmwarePort if body.execution_mode == "FREERTOS_QEMU" else SessionEngine
            s.engine = cls(s.store, body.model_dump(), s.settings)
            return s.engine.snapshot()

        return await s.mutate(request, action)

    @app.get("/api/sessions")
    async def sessions(
        request: Request,
        page: int = 1,
        page_size: int = 20,
        search: str = "",
        status: str = "",
        origin: str = "",
        tag: str = "",
        sort: str = "desc",
        date_from: str = "",
        date_to: str = "",
    ):
        s = service(request)
        if page < 1 or not 1 <= page_size <= 100:
            raise DomainError("INVALID_PARAMETER", "分页参数超出范围", 422)
        items = s.store.all()
        if s.engine:
            items = [s.get(i["id"]) for i in items]
        items = [
            i
            for i in items
            if (not search or search.lower() in (i["name"] + i.get("notes", "")).lower())
            and (not status or i["status"] == status)
            and (not origin or i["origin"] == origin)
            and (not tag or tag in i.get("tags", []))
            and (not date_from or i["created_at_utc"][:10] >= date_from)
            and (not date_to or i["created_at_utc"][:10] <= date_to)
        ]
        items.sort(key=lambda i: i["created_at_utc"], reverse=sort != "asc")
        total = len(items)
        items = items[(page - 1) * page_size : page * page_size]
        return {
            "items": [{k: v for k, v in i.items() if k not in {"waveform_tail", "events"}} for i in items],
            "total": total,
            "page": page,
            "page_size": page_size,
        }

    @app.get("/api/sessions/{sid}/snapshot")
    @app.get("/api/sessions/{sid}")
    async def session(sid: str, request: Request):
        return service(request).get(sid)

    @app.post("/api/sessions/{sid}/commands")
    async def command(sid: str, body: Command, request: Request):
        s = service(request)

        def action():
            if not s.engine or s.engine.id != sid:
                s.get(sid)
                raise DomainError("INVALID_STATE", "该记录已结束，无法再次采集")
            snap = s.engine.command(body.type, body.expected_revision)
            cid = uuid.uuid4().hex
            return {
                **snap,
                "snapshot": snap,
                "command_id": cid,
                "accepted": True,
                "command_status": "applied",
            }

        return await s.mutate(request, action)

    @app.get("/api/commands/{cid}")
    async def get_command(cid: str, request: Request):
        for (raw,) in service(request).store.db.execute("SELECT result FROM commands").fetchall():
            result = json.loads(raw)
            if result.get("command_id") == cid:
                return result
        raise DomainError("NOT_FOUND", "命令不存在", 404)

    @app.patch("/api/sessions/{sid}/annotation")
    async def annotate(sid: str, body: Annotation, request: Request):
        s = service(request)

        def action():
            s.get(sid)
            s.store.annotate(sid, body.model_dump())
            return s.get(sid)

        return await s.mutate(request, action)

    @app.post("/api/sessions/delete")
    async def delete_sessions(request: Request):
        s = service(request)
        body = await request.json()

        def action():
            ids = body.get("ids", [])
            if not ids or len(ids) > 100 or len(set(ids)) != len(ids):
                raise DomainError("INVALID_PARAMETER", "请选择1至100条不重复记录", 422)
            protected = []
            for sid in ids:
                snap = s.get(sid)
                if snap["session_state"] in ACTIVE or any(
                    not r.closed and r.source_id == sid and time.monotonic() - r.lease <= 30
                    for r in s.replays.values()
                ):
                    protected.append(sid)
            if protected:
                raise DomainError("RECORD_IN_USE", "以下记录正在使用，整批未删除：" + ", ".join(protected))
            s.store.remove(ids)
            if s.engine and s.engine.id in ids:
                s.engine = None
            return {"deleted": ids, "count": len(ids)}

        return await s.mutate(request, action)

    @app.get("/api/sessions/{sid}/samples")
    async def samples(
        sid: str, request: Request, start_us: int = 0, end_us: int | None = None, max_points: int = 20000
    ):
        s = service(request)
        snap = s.get(sid)
        rows = s.engine.samples if s.engine and s.engine.id == sid else s.store.read_samples(sid)
        if rows is None:
            raise DomainError("RAW_DATA_MISSING", "原始数据文件缺失", 422)
        if max_points < 1 or max_points > 100000:
            raise DomainError("INVALID_PARAMETER", "max_points应为1至100000", 422)
        selected = [r for r in rows if r["time_us"] >= start_us and (end_us is None or r["time_us"] < end_us)]
        stride = max(1, (len(selected) + max_points - 1) // max_points)
        return {
            "samples": selected[::stride],
            "first_us": rows[0]["time_us"] if rows else 0,
            "last_us": rows[-1]["time_us"] if rows else 0,
            "unit": "设备相对单位" if snap["origin"] == "PUBLIC_DATA" else "ADC counts",
            "origin": snap["origin"],
            "sample_rate_hz": snap.get("sample_hz", snap.get("config_snapshot", {}).get("sample_rate", 64)),
            "decimation": stride,
        }

    @app.get("/api/sessions/{sid}/events")
    async def events(sid: str, request: Request, cursor: int = 0, limit: int = 100):
        s = service(request)
        s.get(sid)
        if cursor < 0 or not 1 <= limit <= 500:
            raise DomainError("INVALID_PARAMETER", "事件分页超出范围", 422)
        if s.engine and s.engine.id == sid:
            s.engine.writer.flush() if not s.engine.writer.closed else None
        path = s.store.session_dir(sid) / "events.jsonl"
        items = [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []
        return {
            "items": items[cursor : cursor + limit],
            "next_cursor": cursor + limit if cursor + limit < len(items) else None,
            "total": len(items),
        }

    @app.post("/api/sessions/{sid}/exports")
    async def export(sid: str, request: Request):
        s = service(request)
        body = await request.json()

        def action():
            snap = s.get(sid)
            if snap["session_state"] not in TERMINAL:
                raise DomainError("INVALID_STATE", "请等待记录结束并保存后再导出")
            fmt = body.get("format", "summary")
            eid = uuid.uuid4().hex
            folder = s.store.root / "exports" / eid
            folder.mkdir()
            source = s.store.session_dir(sid)
            if fmt in {"summary", "json"}:
                path = folder / "summary.json"
                atomic_json(path, {k: v for k, v in snap.items() if k != "waveform_tail"})
            elif fmt in {"csv", "results"}:
                path = folder / "results.csv"
                estimates = source / "estimates.jsonl"
                if not estimates.exists():
                    raise DomainError("RAW_DATA_MISSING", "结果文件缺失", 422)
                import csv

                with path.open("w", newline="", encoding="utf-8-sig") as f:
                    w = csv.writer(f)
                    w.writerow(["support_end_us", "pulse_bpm", "valid", "reasons", "origin"])
                    for line in estimates.read_text().splitlines():
                        r = json.loads(line)
                        w.writerow(
                            [
                                r.get("support_end_us", r.get("end_us")),
                                r.get("pulse_bpm"),
                                r.get("valid"),
                                "|".join(r.get("reasons", [])),
                                snap["origin"],
                            ]
                        )
            elif fmt in {"raw", "zip"}:
                required = [
                    "meta.json",
                    "ppg.csv",
                    "accel.csv",
                    "events.jsonl",
                    "estimates.jsonl",
                    "result.json",
                    "manifest.json",
                ]
                missing = [n for n in required if not (source / n).exists()]
                if missing:
                    raise DomainError(
                        "RAW_DATA_MISSING", "缺少文件：" + ", ".join(missing) + "；可仅导出摘要", 422
                    )
                path = folder / "raw-record.zip"
                with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
                    for n in required:
                        z.write(source / n, n)
                    z.writestr("annotation.json", json_text(snap.get("annotation", {})))
            else:
                raise DomainError("INVALID_PARAMETER", "不支持的导出类型", 422)
            s.exports[eid] = path
            return {"export_id": eid, "url": "/api/downloads/" + eid, "filename": path.name}

        return await s.mutate(request, action)

    @app.get("/api/downloads/{eid}")
    async def download(eid: str, request: Request):
        if len(eid) != 32 or not all(c in "0123456789abcdef" for c in eid):
            raise DomainError("NOT_FOUND", "下载不存在", 404)
        s = service(request)
        folder = s.store.root / "exports" / eid
        files = list(folder.iterdir()) if folder.is_dir() else []
        if len(files) != 1 or not files[0].is_file():
            raise DomainError("NOT_FOUND", "下载文件不存在", 404)
        return FileResponse(files[0], filename=files[0].name)

    @app.post("/api/replays")
    async def create_replay(request: Request):
        s = service(request)
        body = await request.json()

        def action():
            snap = s.get(body.get("source_session_id", ""))
            if snap["session_state"] not in TERMINAL:
                raise DomainError("INVALID_STATE", "正在采集的记录不可回放")
            replay = ReplayEngine(s.store, snap)
            s.replays[replay.id] = replay
            folder = s.store.root / "analysis" / replay.id
            folder.mkdir()
            atomic_json(
                folder / "config.json",
                {"source_session_id": replay.source_id, "config_snapshot": snap["config_snapshot"]},
            )
            return replay.snapshot()

        return await s.mutate(request, action)

    def get_replay(s, rid):
        replay = s.replays.get(rid)
        if not replay or replay.closed:
            raise DomainError("NOT_FOUND", "回放已关闭或租约超时，请重新打开", 404)
        replay.lease = time.monotonic()
        return replay

    @app.get("/api/replays/{rid}")
    async def replay(rid: str, request: Request):
        return get_replay(service(request), rid).snapshot()

    @app.post("/api/replays/{rid}/commands")
    async def replay_command(rid: str, request: Request):
        s = service(request)
        body = await request.json()
        return await s.mutate(request, lambda: get_replay(s, rid).command(body))

    @app.get("/api/comparisons")
    async def comparisons(request: Request):
        return {"items": list(reversed(list(service(request).jobs.values())))}

    @app.post("/api/comparisons")
    async def start_comparison(body: ComparisonConfig, request: Request):
        s = service(request)

        def action():
            if any(j["status"] in {"RUNNING", "CANCELLING"} for j in s.jobs.values()):
                raise DomainError("COMPARISON_ACTIVE", "已有策略比较正在进行")
            for dataset_id in body.dataset_ids:
                get_dataset(dataset_id)
            job = {
                "id": uuid.uuid4().hex,
                "status": "RUNNING",
                "created_at_utc": utcnow(),
                "config": {**body.model_dump(), "algorithm_config": dict(s.settings)},
                "progress": {
                    "completed": 0,
                    "failed": 0,
                    "total": len(body.dataset_ids) * len(body.phases) * 3,
                },
                "results": [],
                "metrics": [],
                "excluded_count": 0,
                "errors": [],
            }
            s.jobs[job["id"]] = job
            folder = s.store.root / "comparisons" / job["id"]
            folder.mkdir()
            atomic_json(folder / "config.json", job["config"])
            s.store.save_job(job)
            s.job_tasks[job["id"]] = asyncio.create_task(s.compare(job))
            return job

        return await s.mutate(request, action)

    def get_job(s, jid):
        if jid not in s.jobs:
            raise DomainError("NOT_FOUND", "比较任务不存在", 404)
        return s.jobs[jid]

    @app.get("/api/comparisons/{jid}")
    async def comparison(jid: str, request: Request):
        return get_job(service(request), jid)

    @app.post("/api/comparisons/{jid}/exports")
    async def comparison_export(jid: str, request: Request):
        s = service(request)
        body = await request.json()

        def action():
            job = get_job(s, jid)
            if job["status"] in {"RUNNING", "CANCELLING"}:
                raise DomainError("INVALID_STATE", "请等待比较结束或取消后再导出")
            fmt = body.get("format", "json")
            eid = uuid.uuid4().hex
            folder = s.store.root / "exports" / eid
            folder.mkdir()
            if fmt == "json":
                path = folder / "comparison.json"
                atomic_json(path, job)
            elif fmt == "csv":
                import csv

                path = folder / "comparison.csv"
                with path.open("w", newline="", encoding="utf-8-sig") as f:
                    writer = csv.writer(f)
                    writer.writerow(
                        [
                            "dataset_id",
                            "strategy",
                            "phase_seconds",
                            "window_seconds",
                            "valid",
                            "pulse_bpm",
                            "reasons",
                            "input_hash",
                            "on_seconds",
                            "difference_from_continuous_bpm",
                        ]
                    )
                    for row in job["results"]:
                        for window in row["windows"]:
                            center = window["center_seconds"]
                            writer.writerow(
                                [
                                    row["dataset_id"],
                                    row["strategy"],
                                    row["phase"],
                                    center,
                                    window["valid"],
                                    window["pulse_bpm"],
                                    "|".join(window["reasons"]),
                                    row["input_hash"],
                                    row["on_seconds"],
                                    row["same_window_error_bpm"].get(str(center)),
                                ]
                            )
            else:
                raise DomainError("INVALID_PARAMETER", "导出格式仅支持json或csv", 422)
            return {"export_id": eid, "url": "/api/downloads/" + eid, "filename": path.name}

        return await s.mutate(request, action)

    @app.post("/api/comparisons/{jid}/cancel")
    async def cancel_comparison(jid: str, request: Request):
        s = service(request)

        def action():
            job = get_job(s, jid)
            if job["status"] not in {"RUNNING", "CANCELLING"}:
                raise DomainError("INVALID_STATE", "任务已结束")
            job["status"] = "CANCELLING"
            s.store.save_job(job)
            return job

        return await s.mutate(request, action)

    @app.get("/api/settings")
    async def settings(request: Request):
        return service(request).settings

    @app.put("/api/settings")
    async def save_settings(request: Request):
        s = service(request)
        body = await request.json()

        def action():
            if body.get("version") != s.settings["version"]:
                raise DomainError("REVISION_CONFLICT", "设置已变化，请重新载入")
            updated = {**s.settings, **body}
            if updated.get("chart_seconds") not in {10, 30} or updated.get("theme") not in {"light"}:
                raise DomainError("INVALID_PARAMETER", "图表长度应为10或30秒；当前版本使用浅色主题", 422)
            if updated.get("ui_hz", 10) not in {5, 10}:
                raise DomainError("INVALID_PARAMETER", "显示刷新应为5或10Hz", 422)
            if (
                not isinstance(updated.get("screen_timeout_s", 30), (int, float))
                or not 10 <= updated.get("screen_timeout_s", 30) <= 120
            ):
                raise DomainError("INVALID_PARAMETER", "亮屏时长应为10至120秒", 422)
            for k, lo, hi in (
                ("stale_seconds", 0.5, 10),
                ("motion_threshold", 0.05, 5),
            ):
                if (
                    not isinstance(updated[k], (int, float))
                    or not math.isfinite(updated[k])
                    or not lo <= updated[k] <= hi
                ):
                    raise DomainError("INVALID_PARAMETER", f"{k}应为{lo}至{hi}", 422)
            if updated.get("strategy") not in {"continuous", "critical", "periodic"}:
                raise DomainError("INVALID_PARAMETER", "请选择已有采集策略", 422)
            if (
                not isinstance(updated.get("min_signal_std"), (int, float))
                or not math.isfinite(updated["min_signal_std"])
                or not 0.001 <= updated["min_signal_std"] <= 100
            ):
                raise DomainError("INVALID_PARAMETER", "有效波动阈值应为0.001至100", 422)
            for k, value in (
                ("sample_rate", 64),
                ("sample_hz", 64),
                ("band_low", 0.5),
                ("band_high", 5.0),
                ("warmup_seconds", 2.0),
                ("window_seconds", 8.0),
            ):
                if updated.get(k) != value:
                    raise DomainError("INVALID_PARAMETER", f"第一版{k}固定为{value}", 422)
            updated["version"] += 1
            s.settings = updated
            s.store.save_settings(updated)
            return {**updated, "effective": {"display": "immediate", "algorithm": "next_session"}}

        return await s.mutate(request, action)

    @app.get("/api/system/status")
    async def system_status(request: Request):
        s = service(request)
        disk = shutil.disk_usage(s.store.root)
        active = s.active()
        qemu = bool(active and active.config["execution_mode"] == "FREERTOS_QEMU")
        snapshot = active.snapshot() if active else None
        return {
            "version": "0.2.0",
            "python_version": platform.python_version(),
            "protocol_version": 2,
            "algorithm_version": "2.0.0",
            "platform": platform.platform(),
            "uptime_seconds": round(time.monotonic() - s.started, 1),
            "service": "RUNNING",
            "execution_mode": "FREERTOS_QEMU" if qemu else "PYTHON",
            "capabilities": s.capabilities(),
            "data_dir": str(s.store.root),
            "storage": {
                "path": str(s.store.root),
                "free_bytes": disk.free,
                "sessions": len(s.store.all()),
                "status": "OK",
            },
            "tasks": snapshot["diagnostics"].get("tasks", [])
            if qemu
            else (
                [
                    {
                        "name": "SessionEngine",
                        "kind": "Python记录处理",
                        "state": active.state,
                        "priority": None,
                        "stack_high_water": None,
                    }
                ]
                if active
                else []
            ),
            "channels": {
                "samples": len(active.samples) if active else 0,
                "queue_depth": None,
                "dropped_blocks": 0,
                "data_age_s": active.snapshot()["latest_result"].get("data_age_s") if active else None,
            },
            "clock": {
                "type": "recording timestamps",
                "engine_time_us": active.time_us if active else None,
                "t0_us": active.t0_us if active else None,
            },
            "battery_voltage": None,
            "current_ma": None,
            "rtos_running": qemu,
            "logs": list(active.events)[-50:] if active else s.logs,
        }

    @app.websocket("/api/live")
    async def live(ws: WebSocket):
        await ws.accept()
        s = ws.app.state.service
        try:
            while True:
                snap = s.engine.snapshot() if s.engine else {"type": "idle", "stream_id": s.run_id}
                await ws.send_json(snap)
                await asyncio.sleep(0.1)
        except (WebSocketDisconnect, RuntimeError):
            pass

    @app.websocket("/api/replays/{rid}/live")
    async def replay_live(ws: WebSocket, rid: str):
        await ws.accept()
        s = ws.app.state.service
        try:
            while True:
                r = get_replay(s, rid)
                await ws.send_json(r.snapshot())
                await asyncio.sleep(0.1)
        except (WebSocketDisconnect, RuntimeError, DomainError):
            pass

    app.mount("/", StaticFiles(directory=PROJECT / "web", html=True), name="web")
    return app


app = create_app()
