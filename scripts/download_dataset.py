"""下载项目支持的少量真实运动记录，并校验原始文件SHA256。

在项目根目录运行 python -m scripts.download_dataset；数据仅保存到本地数据目录。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from backend.sources.public_data import (
    DOWNLOAD_BASE,
    FILES_BASE,
    PROTOCOL_EVIDENCE,
    RECORD_DEFINITIONS,
    SOURCE_PAGE,
    VERSION,
    dataset_root,
    get_dataset,
)


def download(relative: str, destination: Path, expected: str | None = None) -> dict:
    """复用校验通过的本地文件，下载失败时尝试官方备用地址。"""
    if destination.is_file():
        digest = hashlib.sha256(destination.read_bytes()).hexdigest()
        if expected is None or digest == expected:
            return {
                "path": str(destination),
                "bytes": destination.stat().st_size,
                "sha256": digest,
                "cached": True,
            }
    destination.parent.mkdir(parents=True, exist_ok=True)
    # 下载与校验全部成功后再替换目标文件，失败不留下不完整原件。
    temp = destination.with_suffix(destination.suffix + ".download")
    errors = []
    for base in (DOWNLOAD_BASE, FILES_BASE):
        try:
            request = Request(base + relative, headers={"User-Agent": "RePulse-course-project/0.2"})
            with urlopen(request, timeout=60) as response, temp.open("wb") as output:
                while block := response.read(128 * 1024):
                    output.write(block)
            digest = hashlib.sha256(temp.read_bytes()).hexdigest()
            if expected is not None and digest != expected:
                raise ValueError(f"SHA256不符：{relative}")
            os.replace(temp, destination)
            return {
                "path": str(destination),
                "bytes": destination.stat().st_size,
                "sha256": digest,
                "url": base + relative,
                "cached": False,
            }
        except (HTTPError, URLError, TimeoutError, ValueError, OSError) as exc:
            errors.append(str(exc))
            temp.unlink(missing_ok=True)
    raise RuntimeError(f"无法下载{relative}：" + "；".join(errors))


def main():
    """按所选受试者并发下载CSV与许可信息，生成本地来源清单。"""
    parser = argparse.ArgumentParser(description="下载RePulse使用的真实运动恢复记录，不下载整套数据集")
    parser.add_argument(
        "--subjects",
        nargs="+",
        choices=[r["subject"] for r in RECORD_DEFINITIONS.values()],
        help="仅下载指定受试者；默认下载项目支持的全部小样本",
    )
    args = parser.parse_args()
    root = dataset_root()
    selected = {
        k: v for k, v in RECORD_DEFINITIONS.items() if not args.subjects or v["subject"] in args.subjects
    }
    jobs = []
    for definition in selected.values():
        subject = definition["subject"]
        for filename, expected in definition["hashes"].items():
            rel = f"Wearable_Dataset/AEROBIC/{subject}/{filename}"
            jobs.append((rel, root / f"AEROBIC/{subject}/{filename}", expected))
    for filename in ("README.txt", "LICENSE.txt", "data_constraints.txt", "SHA256SUMS.txt"):
        jobs.append((filename, root / "metadata" / filename, None))
    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(lambda arguments: download(*arguments), jobs))
    # 通过正式数据源再次校验采样率和阶段标记后，才记录下载清单。
    records = [get_dataset(dataset_id) for dataset_id in selected]
    manifest = {
        "dataset": "wearable-device-dataset",
        "version": VERSION,
        "source_url": SOURCE_PAGE,
        "license": "Open Data Commons Attribution License v1.0",
        "protocol_evidence": PROTOCOL_EVIDENCE,
        "records": records,
        "downloaded_bytes": sum(r["bytes"] for r in results),
        "original_data_unchanged": True,
    }
    root.mkdir(parents=True, exist_ok=True)
    manifest_path = root / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )
    print(
        f"已校验{len(records)}条真实记录，原始文件共{sum(r['downloaded_bytes'] for r in records) / 1e6:.2f}MB。"
    )
    print(f"数据目录：{root}")
    print("BVP保留原始64Hz，ACC保留原始32Hz，不进行重采样或信号合成。")
    print(f"来源与校验信息：{manifest_path}")


if __name__ == "__main__":
    main()
