"""保存处理记录与SQLite索引；结束时先落盘归档文件，再更新索引状态。"""

import csv
import hashlib
import json
import os
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


def utcnow():
    return datetime.now(timezone.utc).isoformat()


def json_text(value):
    """统一JSON编码，保留中文并拒绝非有限数值。"""
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def atomic_json(path, value):
    """先写临时文件再原子替换，避免留下半份JSON。"""
    path = Path(path)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json_text(value), encoding="utf-8")
    os.replace(temp, path)


class Store:
    """管理记录索引、幂等命令、设置和批量删除事务。"""

    def __init__(self, root):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        for d in ("sessions", "analysis", "comparisons", "exports"):
            (self.root / d).mkdir(exist_ok=True)
        self.mutation_open = False
        self.pending_moves = []
        self.db = sqlite3.connect(self.root / "app.sqlite", check_same_thread=False)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS sessions(id TEXT PRIMARY KEY, snapshot TEXT NOT NULL, annotation TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS commands(key TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, result TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS settings(id INTEGER PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY, value TEXT NOT NULL);
        """)
        self.commit()
        self.recover()

    def commit(self):
        if not self.mutation_open:
            self.db.commit()

    def begin_mutation(self):
        """由API开启一次写事务，将索引更新与目录移动统一提交。"""
        self.db.execute("BEGIN IMMEDIATE")
        self.mutation_open = True

    def end_mutation(self, success):
        """提交成功后清理暂存目录；失败时恢复索引和目录。"""
        self.mutation_open = False
        if success:
            try:
                self.db.commit()
            except BaseException:
                self.db.rollback()
                for folder, trash in reversed(self.pending_moves):
                    if trash.exists():
                        trash.rename(folder)
                self.pending_moves.clear()
                raise
            for _, trash in self.pending_moves:
                try:
                    shutil.rmtree(trash)
                except OSError:
                    # 索引已经提交；残留暂存目录留待启动恢复清理，不撤销删除。
                    pass
        else:
            self.db.rollback()
            for folder, trash in reversed(self.pending_moves):
                if trash.exists():
                    trash.rename(folder)
        self.pending_moves.clear()

    def session_dir(self, sid):
        return self.root / "sessions" / sid

    def recover(self):
        """启动时恢复中断的删除、归档与离线任务，不自动续跑旧任务。"""
        # 依据索引判断上次批量删除是否提交，完成清理或恢复原目录。
        for trash in self.root.glob(".trash-*"):
            sid = trash.name.removeprefix(".trash-")
            indexed = self.db.execute("SELECT 1 FROM sessions WHERE id=?", (sid,)).fetchone()
            original = self.session_dir(sid)
            if indexed and not original.exists():
                trash.rename(original)
            elif not indexed:
                shutil.rmtree(trash)
        for sid, raw in self.db.execute("SELECT id,snapshot FROM sessions").fetchall():
            snap = json.loads(raw)
            if snap["session_state"] in {"CREATED", "PREPARING", "RECOVERING", "FINALIZING"}:
                manifest = self.session_dir(sid) / "manifest.json"
                result = self.session_dir(sid) / "result.json"
                committed = False
                # 归档清单全部校验通过才接受已完成结果，否则标记中断。
                if manifest.exists() and result.exists():
                    try:
                        m = json.loads(manifest.read_text())
                        committed = all(
                            (self.session_dir(sid) / f["name"]).is_file()
                            and hashlib.sha256((self.session_dir(sid) / f["name"]).read_bytes()).hexdigest()
                            == f["sha256"]
                            for f in m["files"]
                        )
                        if committed:
                            snap = json.loads(result.read_text())
                    except (ValueError, OSError, KeyError):
                        committed = False
                if not committed:
                    snap.update(
                        session_state="INTERRUPTED",
                        status="INTERRUPTED",
                        interruption_reason="SERVICE_RESTART",
                    )
                    snap["revision"] += 1
                    snap["latest_result"] = {
                        "valid": False,
                        "pulse_bpm": None,
                        "reasons": ["SERVICE_RESTART"],
                        "quality": "STALE",
                    }
                    folder = self.session_dir(sid)
                    for p in [
                        folder / "ppg.csv",
                        folder / "accel.csv",
                        folder / "events.jsonl",
                        folder / "estimates.jsonl",
                    ]:
                        if p.exists():
                            data = p.read_bytes()
                            if data and not data.endswith(b"\n"):
                                p.write_bytes(data[: data.rfind(b"\n") + 1])
                    if folder.exists():
                        atomic_json(folder / "result.json", snap)
                        self.manifest(sid, snap["session_state"])
                self.db.execute("UPDATE sessions SET snapshot=? WHERE id=?", (json_text(snap), sid))
        self.commit()
        # 进程重启后的离线任务明确标为中断，避免界面误认为仍在运行。
        for jid, raw in self.db.execute("SELECT id,value FROM jobs").fetchall():
            job = json.loads(raw)
            if job["status"] in {"RUNNING", "CANCELLING", "PENDING"}:
                job["status"] = "INTERRUPTED"
                self.save_job(job)

    def create(self, snapshot):
        """创建独立记录目录、初始元数据和注释索引。"""
        folder = self.session_dir(snapshot["id"])
        folder.mkdir()
        atomic_json(folder / "meta.json", snapshot)
        annotation = {"name": snapshot["name"], "notes": "", "tags": []}
        self.db.execute(
            "INSERT INTO sessions VALUES(?,?,?)", (snapshot["id"], json_text(snapshot), json_text(annotation))
        )
        self.commit()
        return SessionWriter(self, snapshot["id"])

    def update(self, snapshot):
        self.db.execute("UPDATE sessions SET snapshot=? WHERE id=?", (json_text(snapshot), snapshot["id"]))
        self.commit()

    def get(self, sid):
        """合并处理快照与可编辑注释，保持波形和计算结果独立。"""
        row = self.db.execute("SELECT snapshot,annotation FROM sessions WHERE id=?", (sid,)).fetchone()
        if not row:
            return None
        snap = json.loads(row[0])
        annotation = json.loads(row[1])
        snap.update(annotation)
        snap["annotation"] = annotation
        snap["raw_available"] = (self.session_dir(sid) / "ppg.csv").exists()
        return snap

    def all(self):
        return [self.get(r[0]) for r in self.db.execute("SELECT id FROM sessions").fetchall()]

    def annotate(self, sid, annotation):
        self.db.execute("UPDATE sessions SET annotation=? WHERE id=?", (json_text(annotation), sid))
        self.commit()

    def manifest(self, sid, status):
        """记录各归档文件的大小与SHA256，供恢复和完整性检查使用。"""
        folder = self.session_dir(sid)
        files = [
            {"name": p.name, "size": p.stat().st_size, "sha256": hashlib.sha256(p.read_bytes()).hexdigest()}
            for p in sorted(folder.iterdir())
            if p.is_file() and p.name != "manifest.json" and not p.name.endswith(".tmp")
        ]
        atomic_json(folder / "manifest.json", {"schema_version": 1, "status": status, "files": files})

    def remove(self, ids):
        """先暂存记录目录再删除索引，出错时可恢复整批目录。"""
        moved = []
        try:
            for sid in ids:
                folder = self.session_dir(sid)
                trash = self.root / (".trash-" + sid)
                if folder.exists():
                    folder.rename(trash)
                    moved.append((folder, trash))
            self.db.executemany("DELETE FROM sessions WHERE id=?", [(s,) for s in ids])
            self.commit()
        except Exception:
            for folder, trash in reversed(moved):
                if trash.exists():
                    trash.rename(folder)
            raise
        if self.mutation_open:
            self.pending_moves.extend(moved)
        else:
            for _, trash in moved:
                shutil.rmtree(trash)

    def command(self, key):
        row = self.db.execute("SELECT fingerprint,result FROM commands WHERE key=?", (key,)).fetchone()
        return (row[0], json.loads(row[1])) if row else None

    def save_command(self, key, fingerprint, result):
        self.db.execute("INSERT INTO commands VALUES(?,?,?)", (key, fingerprint, json_text(result)))
        self.commit()

    def settings(self):
        row = self.db.execute("SELECT value FROM settings WHERE id=1").fetchone()
        return json.loads(row[0]) if row else None

    def save_settings(self, settings):
        self.db.execute("INSERT OR REPLACE INTO settings VALUES(1,?)", (json_text(settings),))
        self.commit()

    def save_job(self, job):
        self.db.execute("INSERT OR REPLACE INTO jobs VALUES(?,?)", (job["id"], json_text(job)))
        self.commit()

    def jobs(self):
        return [json.loads(row[0]) for row in self.db.execute("SELECT value FROM jobs").fetchall()]

    def read_samples(self, sid):
        """从归档CSV恢复样本类型；兼容尚无原始时间戳与XYZ注释的旧记录。"""
        path = self.session_dir(sid) / "ppg.csv"
        if not path.exists():
            return None
        with path.open(newline="", encoding="utf-8") as f:
            return [
                {
                    "time_us": int(r["time_us"]),
                    "seq": int(r["seq"]),
                    "segment_id": int(r["segment_id"]),
                    "ppg": float(r["ppg"]) if r["ppg"] else None,
                    "filtered": float(r["filtered"]) if r["filtered"] else None,
                    "accel": float(r["accel"]) if r["accel"] else None,
                    "on": r["on"] == "True",
                    "gap": r["gap"] == "True",
                    "fault": r.get("fault") or None,
                    "source_time_us": int(r["source_time_us"]) if r.get("source_time_us") else None,
                    "reference_bpm": float(r["reference_bpm"]) if r.get("reference_bpm") else None,
                    **{
                        key: float(r[key]) if r.get(key) else None
                        for key in ("accel_x", "accel_y", "accel_z")
                    },
                }
                for r in csv.DictReader(f)
            ]

    def close(self):
        self.db.close()


class SessionWriter:
    """单条处理记录的顺序写入器，统一保存波形、事件和每秒估计。"""

    fields = [
        "time_us",
        "seq",
        "segment_id",
        "ppg",
        "filtered",
        "accel",
        "on",
        "gap",
        "fault",
        "source_time_us",
        "reference_bpm",
        "accel_x",
        "accel_y",
        "accel_z",
    ]

    def __init__(self, store, sid):
        self.store, self.sid = store, sid
        self.folder = store.session_dir(sid)
        self.files = {
            n: (self.folder / n).open("w", encoding="utf-8", newline="")
            for n in ("ppg.csv", "accel.csv", "events.jsonl", "estimates.jsonl")
        }
        self.csv = csv.DictWriter(self.files["ppg.csv"], fieldnames=self.fields, extrasaction="ignore")
        self.csv.writeheader()
        self.accel = csv.writer(self.files["accel.csv"])
        self.accel.writerow(["time_us", "dynamic_acceleration_g", "x_g", "y_g", "z_g"])
        self.closed = False

    def samples(self, samples):
        """脉搏波行保留完整注释，加速度另存一份便于查看的CSV。"""
        self.csv.writerows(samples)
        self.accel.writerows(
            (s["time_us"], s.get("accel"), s.get("accel_x"), s.get("accel_y"), s.get("accel_z"))
            for s in samples
        )

    def event(self, event):
        self.files["events.jsonl"].write(json_text(event) + "\n")

    def estimate(self, result):
        self.files["estimates.jsonl"].write(json_text(result) + "\n")

    def flush(self):
        for f in self.files.values():
            f.flush()

    def finish(self, snapshot):
        """同步数据文件后写入最终结果与清单，最后更新数据库状态。"""
        self.flush()
        for f in self.files.values():
            os.fsync(f.fileno())
            f.close()
        self.closed = True
        atomic_json(self.folder / "result.json", snapshot)
        self.store.manifest(self.sid, snapshot["session_state"])
        self.store.update(snapshot)
