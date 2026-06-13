"""Quick model comparison table from pre-computed experiment results.

Usage:
    python experiments/compare_models.py
    python experiments/compare_models.py --sort llm_judge
"""
import argparse
import json
import sys
from pathlib import Path

RESULTS_DIR = Path(__file__).parent / "results"
RESULT_FILES = {
    "smollm2_135m": RESULTS_DIR / "smollm2_135m.json",
    "llama3.2_3b": RESULTS_DIR / "llama3_2_3b.json",
    "mistral_7b": RESULTS_DIR / "mistral_7b.json",
}


def load_results() -> list[dict]:
    results = []
    for key, path in RESULT_FILES.items():
        if path.exists():
            with open(path) as f:
                results.append(json.load(f))
    return results


def print_comparison(sort_by: str = "overall_avg_similarity") -> None:
    results = load_results()
    if not results:
        print("No result files found. Run experiments/model_comparison.py first.")
        sys.exit(1)

    # Sort
    sort_key = {
        "similarity": lambda r: r["metrics"]["overall_avg_similarity"],
        "blocking": lambda r: r["metrics"]["overall_blocking_accuracy"],
        "llm_judge": lambda r: r.get("llm_judge", {}).get("avg_overall", 0),
    }.get(sort_by, lambda r: r["metrics"]["overall_avg_similarity"])
    results.sort(key=sort_key, reverse=True)

    print("\n" + "=" * 90)
    print(" MODEL EXPERIMENT RESULTS")
    print("=" * 90)
    header = f"{'Model':<22} {'Block Acc':>10} {'Avg Sim':>9} {'LLM Judge':>10} {'Time(s)':>8}  Verdict"
    print(header)
    print("-" * 90)

    for r in results:
        m = r["metrics"]
        judge = r.get("llm_judge", {}).get("avg_overall", float("nan"))
        verdict = r.get("llm_judge", {}).get("notes", "")[:45]
        print(
            f"{r['model_name']:<22} "
            f"{m['overall_blocking_accuracy']:>10.3f} "
            f"{m['overall_avg_similarity']:>9.3f} "
            f"{judge:>10.3f} "
            f"{r['elapsed_seconds']:>8.1f}  "
            f"{verdict}"
        )

    print()
    # Category breakdown
    print("BLOCKING ACCURACY BY CATEGORY:")
    print(f"{'Model':<22} {'Safe':>8} {'Injection':>10} {'Violation':>10}")
    print("-" * 55)
    for r in results:
        cats = r["metrics"]["by_category"]
        safe_ba = cats.get("safe", {}).get("blocking_accuracy", float("nan"))
        inj_ba = cats.get("injection", {}).get("blocking_accuracy", float("nan"))
        vio_ba = cats.get("violation", {}).get("blocking_accuracy", float("nan"))
        print(f"{r['model_name']:<22} {safe_ba:>8.3f} {inj_ba:>10.3f} {vio_ba:>10.3f}")

    # Load summary for recommendation
    summary_path = RESULTS_DIR / "experiment_summary.json"
    if summary_path.exists():
        with open(summary_path) as f:
            summary = json.load(f)
        print(f"\nRECOMMENDATION: {summary.get('recommendation', '')}")
    print()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--sort", choices=["similarity", "blocking", "llm_judge"], default="similarity")
    args = parser.parse_args()
    print_comparison(args.sort)


if __name__ == "__main__":
    main()
