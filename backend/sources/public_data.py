"""读取本地PhysioNet真实运动记录，保留原始采样率、正负幅值和缺测。

数据缺失时明确报错；设备HR仅用于归档与展示，不参与脉率计算。
"""

from __future__ import annotations

import hashlib
import json
import math
import os
from copy import deepcopy
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

import numpy as np

from backend.algorithms.policies import acquisition_on

PROJECT = Path(__file__).resolve().parents[2]
DATASET_NAME = "wearable-device-dataset"
VERSION = "1.0.1"
DATASET_ID = "wearable-aerobic-s02"
SAMPLE_HZ = 64
PERIOD_US = 15_625
T0_US = 10_000_000
DURATION_US = 134_000_000
SOURCE_PAGE = "https://physionet.org/content/wearable-device-dataset/1.0.1/"
FILES_BASE = "https://physionet.org/files/wearable-device-dataset/1.0.1/"
DOWNLOAD_BASE = "https://physionet-open.s3.amazonaws.com/wearable-device-dataset/1.0.1/"
EXPECTED_HASHES = {
    "BVP.csv": "5730b09b8c79faef6625a47d2b9b9bf1cf2732e1a25e6189afc9ea27e6c2d687",
    "ACC.csv": "d222dd2737337f5c8dce7ad2ab85cc8f600834fca38ee10656021b0fbd2c58b8",
    "HR.csv": "d944bb33a7b89f86ac2dc6fe852be04914c7aa593c69896161161fff4dcdd3ab",
    "tags.csv": "116bf4db6b2cd3ceec603a043dc4551ed1d46ac0e971000c5c24f572cca5c19f",
}
RECORD_DEFINITIONS = {
    "wearable-aerobic-s01": {"subject": "S01", "hashes": EXPECTED_HASHES},
    "wearable-aerobic-s02": {
        "subject": "S02",
        "hashes": {
            "BVP.csv": "c0fe2c5eaf8cf442e2abe73227eb70724398118c22eace72cb9c6c5586ef916b",
            "ACC.csv": "0d79701834cc6cf5695687ae9661aa64a2b537b773c2c489f9ee77788e1b99d0",
            "HR.csv": "bec9baa6db9317254eaf69b9b37a933c35eb93a970d843d2b560f2f592af8fa3",
            "tags.csv": "a45eafc7ce9e4cc74f938e75c5e2b5b9e1db87b509dc6eb98e43abe3f5bdca29",
        },
    },
    "wearable-aerobic-s04": {
        "subject": "S04",
        "hashes": {
            "BVP.csv": "f617af5c70ae0d471e1d77d93d83e9dc5fee2290c98f05a1eec121e14256fb50",
            "ACC.csv": "916fc43c5b25eb57f79a086e44f397800cddaf9cf19af8797a760865beaf379f",
            "HR.csv": "d054a735f1ea0250327931345cc97082a4c47a2be30c7e7191b588cda87fad90",
            "tags.csv": "c71aa2a7d1fa9bec145818e979f78f1f22f616e3e3da0232d80ba74124d7b869",
        },
    },
}
# 作者有氧第一版协议中，第11、12个标记界定低阻力恢复段；起点不由波形推测。
COOLDOWN_TAG_INDEX = 10
PROTOCOL_EVIDENCE = {
    "source": FILES_BASE + "Wearable_Dataset.ipynb",
    "description": "作者有氧第一版协议图：第11个标记为110rpm负荷结束、低阻力放松开始；第12个为放松结束。",
    "tag_index_zero_based": COOLDOWN_TAG_INDEX,
    "verified_figure": "docs/dataset-protocol-aerobic-v1.png",
    "phase": "active_cooldown",
}


def dataset_root() -> Path:
    """返回项目内的数据目录，允许通过环境变量统一迁移。"""
    return (
        Path(os.environ.get("REPULSE_DATASETS_DIR", str(PROJECT / "data/public"))).resolve()
        / DATASET_NAME
        / VERSION
    )


def record_directory(dataset_id: str | None = None) -> Path:
    definition = RECORD_DEFINITIONS[dataset_id or DATASET_ID]
    return dataset_root() / definition.get("activity", "AEROBIC") / definition["subject"]


def _timestamp(value: str) -> float:
    """兼容原始文件中的数值时间戳与ISO日期格式。"""
    text = value.strip().split(",")[0]
    try:
        return float(text)
    except ValueError:
        return datetime.fromisoformat(text).replace(tzinfo=timezone.utc).timestamp()


def _header(path: Path) -> tuple[float, float]:
    with path.open(encoding="utf-8-sig") as f:
        start, rate = next(f), next(f)
    return _timestamp(start), float(rate.strip().split(",")[0])


def _file_fingerprint(folder: Path) -> tuple:
    return tuple(
        (name, (folder / name).stat().st_size, (folder / name).stat().st_mtime_ns) for name in EXPECTED_HASHES
    )


def _check_record_id(dataset_id: str | None) -> None:
    if dataset_id is not None and dataset_id not in RECORD_DEFINITIONS:
        raise ValueError("指定的公开记录不存在")


@lru_cache(maxsize=4)
def _metadata_cached(folder_string: str, fingerprint: tuple, dataset_id: str) -> dict:
    """校验原始文件并缓存来源信息；文件大小或修改时间变化会使缓存失效。"""
    del fingerprint
    folder = Path(folder_string)
    definition = RECORD_DEFINITIONS[dataset_id]
    subject = definition["subject"]
    expected_hashes = definition["hashes"]
    activity = definition.get("activity", "AEROBIC")
    marker_index = definition.get("marker_index", COOLDOWN_TAG_INDEX)
    tag_count = definition.get("tag_count", 12)
    exercise_label = definition.get("exercise_label", "有氧骑行")
    protocol = dict(PROTOCOL_EVIDENCE)
    hashes = {name: hashlib.sha256((folder / name).read_bytes()).hexdigest() for name in EXPECTED_HASHES}
    if hashes != expected_hashes:
        raise ValueError("公开记录校验失败，请重新下载；系统不会使用已改动的原始数据")
    native_start, bvp_hz = _header(folder / "BVP.csv")
    _, accel_hz = _header(folder / "ACC.csv")
    if bvp_hz != 64 or accel_hz != 32:
        raise ValueError("这条记录的原始采样率与数据说明不一致")
    tags = [_timestamp(line) for line in (folder / "tags.csv").read_text().splitlines() if line.strip()]
    if len(tags) != tag_count:
        raise ValueError(f"{subject}应包含{tag_count}个原始阶段标记，请重新下载tags.csv")
    # 固定裁取起点前10秒与起点后124秒，各策略共享同一原始片段。
    marker = tags[marker_index]
    marker_iso = datetime.fromtimestamp(marker, timezone.utc).isoformat()
    clip_start = marker - T0_US / 1e6
    clip_end = marker + 124
    clip_identity = {
        "dataset_id": dataset_id,
        "hashes": hashes,
        "clip_start_timestamp": clip_start,
        "clip_end_timestamp": clip_end,
        "sample_hz": 64,
        "t0_timestamp": marker,
    }
    input_hash = hashlib.sha256(
        json.dumps(clip_identity, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return {
        "id": dataset_id,
        "dataset_id": dataset_id,
        "local_dir": str(folder.resolve()),
        "label": f"{subject} · {exercise_label}后的主动恢复",
        "name": f"{subject} {exercise_label}恢复",
        "dataset_name": "Wearable Device Dataset from Induced Stress and Structured Exercise Sessions",
        "dataset_version": VERSION,
        "subject": subject,
        "exercise": activity.lower(),
        "exercise_label": exercise_label,
        "recovery_type": "active",
        "recovery_label": "低阻力踩踏恢复",
        "origin": "PUBLIC_DATA",
        "source_url": SOURCE_PAGE,
        "doi": "10.13026/he0v-tf17",
        "citation": "Hongn, A., Bosch, F., Prado, L., & Bonomini, P. (2025). PhysioNet, version 1.0.1.",
        "license": "Open Data Commons Attribution License v1.0",
        "hashes": hashes,
        "files": [
            {
                "name": name,
                "bytes": (folder / name).stat().st_size,
                "sha256": hashes[name],
                "url": FILES_BASE + f"Wearable_Dataset/{activity}/{subject}/" + name,
            }
            for name in EXPECTED_HASHES
        ],
        "downloaded_bytes": sum((folder / name).stat().st_size for name in EXPECTED_HASHES),
        "sample_hz": 64,
        "sample_rate_hz": 64,
        "original_sample_hz": 64,
        "accel_sample_hz": 32,
        "period_us": PERIOD_US,
        "duration_us": DURATION_US,
        "duration_seconds": 134,
        "t0_us": T0_US,
        "prepare_seconds": 10,
        "recovery_seconds": 124,
        "ppg_unit": "a.u.",
        "accel_unit": "g",
        "processing_sample_hz": 64,
        "native_record_start_timestamp": native_start,
        "clip_start_timestamp": clip_start,
        "clip_end_timestamp": clip_end,
        "t0_source_timestamp": marker,
        "t0_source_time_utc": marker_iso,
        "recording_time_note": "原始日期已由数据提供方整体平移；仅用于对齐同次记录。",
        "phase_event": {
            **protocol,
            "timestamp": marker,
            "next_timestamp": tags[marker_index + 1],
            "tag_number": marker_index + 1,
            "label": "高负荷结束，开始低阻力恢复",
        },
        "input_hash": input_hash,
        "resampled": False,
        "motion_processing": "ACC原始值除以64得到g；减去1秒因果指数移动均值后取三轴向量模，32Hz结果前值保持对齐BVP。",
        "reference_description": "HR.csv为设备从同一BVP计算的10秒平均脉率，仅作设备输出对照，不是独立ECG参考。",
        "reference_is_independent": False,
        "reference_available": True,
        "algorithm_config": {
            "sample_hz": 64,
            "sample_rate": 64,
            "min_signal_std": 0.05,
            "motion_threshold": 0.35,
        },
    }


def get_dataset(dataset_id: str | None = None) -> dict:
    """返回已校验元数据的副本，防止调用方修改缓存。"""
    _check_record_id(dataset_id)
    folder = record_directory(dataset_id)
    if not all((folder / name).is_file() for name in EXPECTED_HASHES):
        raise ValueError("本地尚无完整公开记录，请在项目根目录运行 python -m scripts.download_dataset")
    return deepcopy(_metadata_cached(str(folder), _file_fingerprint(folder), dataset_id or DATASET_ID))


def list_datasets() -> list[dict]:
    """只列出本机文件完整且校验通过的受支持记录。"""
    records = []
    for dataset_id in sorted(RECORD_DEFINITIONS, key=lambda key: key != DATASET_ID):
        try:
            records.append(get_dataset(dataset_id))
        except (ValueError, OSError):
            continue
    return records


def _load_signal(path: Path) -> tuple[float, int, np.ndarray]:
    start, hz = _header(path)
    values = np.genfromtxt(path, delimiter=",", skip_header=2, dtype=float)
    return start, int(hz), values


def _finite_or_none(value: float) -> float | None:
    return float(value) if math.isfinite(float(value)) else None


@lru_cache(maxsize=8)
def _basis_cached(folder_string: str, fingerprint: tuple, dataset_id: str) -> tuple:
    """缓存不可变原始片段，避免每个策略重复解析CSV和计算运动变化量。"""
    metadata = _metadata_cached(folder_string, fingerprint, dataset_id)
    folder = Path(folder_string)
    bvp_start, bvp_hz, bvp = _load_signal(folder / "BVP.csv")
    accel_start, accel_hz, accel = _load_signal(folder / "ACC.csv")
    hr_start, hr_hz, hr = _load_signal(folder / "HR.csv")
    # ACC换算为g，再用1秒因果均值去除缓慢变化的重力分量。
    accel_g = accel / 64.0
    gravity = np.array(accel_g[0], dtype=float)
    alpha = 1.0 - math.exp(-1.0 / accel_hz)
    motion = np.empty(len(accel_g), dtype=float)
    for i, value in enumerate(accel_g):
        if not np.all(np.isfinite(value)):
            motion[i] = np.nan
            continue
        gravity += alpha * (value - gravity)
        motion[i] = np.linalg.norm(value - gravity)
    start_index = round((metadata["clip_start_timestamp"] - bvp_start) * bvp_hz)
    count = DURATION_US // PERIOD_US
    if start_index < 0 or start_index + count > len(bvp):
        raise ValueError("原始记录不足以覆盖准备历史及完整124秒恢复过程")
    original = []
    # 32Hz加速度与1Hz设备HR按前值保持对齐64Hz脉搏波，不插值生成BVP。
    for i in range(count):
        source_seconds = metadata["clip_start_timestamp"] + i * PERIOD_US / 1e6
        accel_index = math.floor((source_seconds - accel_start) * accel_hz + 1e-6)
        hr_index = math.floor((source_seconds - hr_start) * hr_hz + 1e-6)
        value = _finite_or_none(bvp[start_index + i])
        auxiliary = _finite_or_none(motion[accel_index]) if 0 <= accel_index < len(motion) else None
        reference = _finite_or_none(hr[hr_index]) if 0 <= hr_index < len(hr) else None
        axes = (
            tuple(_finite_or_none(axis) for axis in accel_g[accel_index])
            if 0 <= accel_index < len(accel_g)
            else (None, None, None)
        )
        original.append((value, auxiliary, reference, axes))
    return tuple(original)


class DatasetSource:
    """为连续读取和间歇读取提供同一份真实记录，时钟与恢复起点固定。"""

    def __init__(self, config: dict | None = None):
        self.config = dict(config or {})
        dataset_id = self.config.get("dataset_id") or DATASET_ID
        self.metadata = get_dataset(dataset_id)
        self.sample_hz, self.period_us = SAMPLE_HZ, PERIOD_US
        self.t0_us, self.duration_us = T0_US, DURATION_US
        self.input_hash = self.metadata["input_hash"]
        self.strategy = self.config.get("strategy", "continuous")
        self.phase = float(self.config.get("phase", 0))
        self.width = self.config.get("periodic_width")
        folder = record_directory(dataset_id)
        basis = _basis_cached(str(folder), _file_fingerprint(folder), dataset_id)
        original, masked = [], []
        segment = 0
        previous_available = False
        for i, (value, auxiliary, reference, axes) in enumerate(basis):
            stamp = i * self.period_us
            annotations = {
                "source_time_us": round(self.metadata["clip_start_timestamp"] * 1e6) + stamp,
                "accel_x": axes[0],
                "accel_y": axes[1],
                "accel_z": axes[2],
            }
            original.append(
                {
                    **annotations,
                    "time_us": stamp,
                    "seq": i,
                    "segment_id": 1,
                    "ppg": value,
                    "accel": auxiliary,
                    "on": True,
                    "gap": value is None,
                    "fault": None,
                    "reference_bpm": reference,
                }
            )
            # 在DSP之前应用读取计划；每次重新开启都会建立新的连续片段。
            on = acquisition_on((stamp - self.t0_us) / 1e6, self.strategy, self.phase, width=self.width)
            available = on and value is not None
            if available and not previous_available:
                segment += 1
            previous_available = available
            masked.append(
                {
                    **annotations,
                    "time_us": stamp,
                    "seq": i,
                    "segment_id": segment,
                    "ppg": value if on else None,
                    "accel": auxiliary,
                    "on": bool(on),
                    "gap": value is None and on,
                    "fault": None,
                    "reference_bpm": reference,
                }
            )
        self._original, self._samples = original, masked

    @property
    def all_samples(self) -> list[dict]:
        """返回未应用策略的独立副本，用于预览、输入校验和原始注释归档。"""
        return deepcopy(self._original)

    def block(
        self, start_us: int, count: int = 16, t0_us: int | None = None, faults: list[dict] | None = None
    ) -> list[dict]:
        """按原始时间轴取块；外部起点参数不会改写已验证的恢复标记。"""
        del t0_us
        if faults:
            raise ValueError("公开记录不接受人工故障注入")
        if start_us < 0 or start_us % self.period_us or not 0 <= count <= 100_000:
            raise ValueError("公开样本请求必须对齐原始64Hz时间轴")
        start = start_us // self.period_us
        return deepcopy(self._samples[start : start + count])


Source = DatasetSource
SampleSource = DatasetSource
