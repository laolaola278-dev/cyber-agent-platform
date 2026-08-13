"""Smoke test CAP-SIB v1 dataset generation + leakage audit."""
import json
import sys

sys.path.insert(0, ".")

from app.hybrid.sib import build_sib_v1, freeze_sib, label_leakage_audit, sib_stats


def main() -> None:
    dataset = build_sib_v1()
    print("total:", len(dataset))
    print("stats:", json.dumps(sib_stats(dataset), ensure_ascii=False))
    print("hash:", freeze_sib(dataset))

    # leakage audit across the whole dataset
    findings = 0
    for scenario in dataset:
        issues = label_leakage_audit(scenario)
        if issues:
            findings += len(issues)
            if findings <= 5:
                print("LEAK:", scenario.scenario_id, issues)
    print("total leakage findings:", findings)

    # track composition
    track_b = [s for s in dataset if s.track == "B"]
    track_a = [s for s in dataset if s.track == "A"]
    print("track A:", len(track_a), "track B:", len(track_b))
    holdout = [s for s in dataset if s.split == "holdout"]
    print(
        "holdout hard negatives:",
        sum(1 for s in holdout if s.hard_negative),
        "| incomplete:",
        sum(1 for s in holdout if s.incomplete != "none"),
    )

    # sample scenario
    sample = next(s for s in dataset if s.track == "B" and not s.hard_negative)
    print("\nsample Track B input keys:", list(sample.input.keys()))
    print("sample labels:", sample.labels)
    print("sample input contains T-id? ", any("T1" in str(v) for v in json.dumps(sample.input)))


if __name__ == "__main__":
    main()
