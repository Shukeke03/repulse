"""Repeatable offline comparison; uses the same processing chain as online acquisition."""

import argparse
import csv
import json
from pathlib import Path

from backend.workers.compare import compare_unit, summarize


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--seeds", type=int, default=3)
    parser.add_argument(
        "--scenario",
        default="normal",
        choices=["normal", "interference", "early", "detachment", "gap", "timeout"],
    )
    args = parser.parse_args()
    rows = []
    for seed in range(20261025, 20261025 + args.seeds):
        for phase in [0, 5, 10, 15]:
            rows.extend(compare_unit(seed, phase, args.scenario))
            print(f"已完成 {len(rows)}/{args.seeds * 12} 个策略运行", flush=True)
    summary = summarize(rows, args.seeds * 4)
    out = Path("data/comparisons/cli-" + args.scenario)
    out.mkdir(parents=True, exist_ok=True)
    (out / "results.json").write_text(
        json.dumps({"results": rows, "summary": summary}, ensure_ascii=False, indent=2, allow_nan=False)
    )
    with (out / "metrics.csv").open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(
            f,
            fieldnames=[
                "strategy",
                "runs",
                "on_seconds",
                "energy_estimate_mj",
                "delta60_availability",
                "delta120_availability",
            ],
        )
        w.writeheader()
        for row in summary["strategies"]:
            w.writerow({k: row[k] for k in w.fieldnames})
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"结果已保存：{out.resolve()}")


if __name__ == "__main__":
    main()
