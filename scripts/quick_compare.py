"""Repeat the three policies on the same unmodified public observations."""

import argparse
import csv
import json
from pathlib import Path

from backend.domain.models import DEFAULT_DATASET
from backend.sources.public_data import get_dataset
from backend.workers.compare import compare_unit, summarize


def main():
    parser = argparse.ArgumentParser(description="公开记录的采集策略比较")
    parser.add_argument("--dataset-id", default=DEFAULT_DATASET)
    parser.add_argument("--phases", default="0,5,10,15")
    args = parser.parse_args()
    phases = [int(v.strip()) for v in args.phases.split(",") if v.strip()]
    if not phases or len(set(phases)) != len(phases) or any(p < 0 or p > 19 for p in phases):
        parser.error("相位应为0至19之间不重复的整数")
    metadata = get_dataset(args.dataset_id)
    results = []
    for phase in phases:
        results.extend(compare_unit(args.dataset_id, phase))
        print(f"已完成{len(results)}/{len(phases) * 3}次策略运行", flush=True)
    summary = summarize(results, len(phases))
    out = Path("data/comparisons") / ("cli-" + args.dataset_id)
    out.mkdir(parents=True, exist_ok=True)
    (out / "results.json").write_text(
        json.dumps(
            {"dataset": metadata, "results": results, "summary": summary},
            ensure_ascii=False,
            indent=2,
            allow_nan=False,
        )
    )
    with (out / "metrics.csv").open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                "strategy",
                "runs",
                "on_seconds",
                "energy_estimate_mj",
                "window0_rate",
                "window60_rate",
                "window120_rate",
            ]
        )
        for row in summary["strategies"]:
            writer.writerow(
                [
                    row["strategy"],
                    row["runs"],
                    row["on_seconds"],
                    row["energy_estimate_mj"],
                    *[row["window_availability"][str(c)] for c in (0, 60, 120)],
                ]
            )
    print(json.dumps(summary, ensure_ascii=False, indent=2, allow_nan=False))
    print(f"结果保存在{out.resolve()}")


if __name__ == "__main__":
    main()
