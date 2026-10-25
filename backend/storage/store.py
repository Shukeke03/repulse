"""Single writer storage; terminal files commit before the SQLite index."""

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
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def atomic_json(path, value):
    path = Path(path)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json_text(value), encoding="utf-8")
    os.replace(temp, path)


class Store:
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
        self.db.execute("BEGIN IMMEDIATE")
        self.mutation_open = True

    def end_mutation(self, success):
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
                    # The index transaction is already committed. Startup recovery
                    # removes this unindexed trash; cleanup must not undo deletion.
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
        # Complete or roll back an interrupted directory move used by batch delete.
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
        # Offline jobs cannot silently resume after a process restart.
        for jid, raw in self.db.execute("SELECT id,value FROM jobs").fetchall():
            job = json.loads(raw)
            if job["status"] in {"RUNNING", "CANCELLING", "PENDING"}:
                job["status"] = "INTERRUPTED"
                self.save_job(job)

    def create(self, snapshot):
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
        folder = self.session_dir(sid)
        files = [
            {"name": p.name, "size": p.stat().st_size, "sha256": hashlib.sha256(p.read_bytes()).hexdigest()}
            for p in sorted(folder.iterdir())
            if p.is_file() and p.name != "manifest.json" and not p.name.endswith(".tmp")
        ]
        atomic_json(folder / "manifest.json", {"schema_version": 1, "status": status, "files": files})

    def remove(self, ids):
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
                }
                for r in csv.DictReader(f)
            ]

    def close(self):
        self.db.close()


class SessionWriter:
    fields = ["time_us", "seq", "segment_id", "ppg", "filtered", "accel", "on", "gap", "fault"]

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
        self.accel.writerow(["time_us", "accel"])
        self.closed = False

    def samples(self, samples):
        self.csv.writerows(samples)
        self.accel.writerows((s["time_us"], s.get("accel")) for s in samples)

    def event(self, event):
        self.files["events.jsonl"].write(json_text(event) + "\n")

    def estimate(self, result):
        self.files["estimates.jsonl"].write(json_text(result) + "\n")

    def flush(self):
        for f in self.files.values():
            f.flush()

    def finish(self, snapshot):
        self.flush()
        for f in self.files.values():
            os.fsync(f.fileno())
            f.close()
        self.closed = True
        atomic_json(self.folder / "result.json", snapshot)
        self.store.manifest(self.sid, snapshot["session_state"])
        self.store.update(snapshot)
