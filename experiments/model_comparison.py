"""Model comparison experiment runner.

Runs the evaluation dataset against the live API and saves results.
Designed to be run multiple times with different models set via LLM_MODEL env var.

Usage:
    # With SmolLM2 135M running:
    LLM_MODEL=smollm2:135m python experiments/model_comparison.py \\
        --model-name "SmolLM2 135M" \\
        --output experiments/results/smollm2_135m.json

    # After switching to Llama 3.2 3B:
    LLM_MODEL=llama3.2:3b python experiments/model_comparison.py \\
        --model-name "Llama 3.2 3B" \\
        --output experiments/results/llama3.2_3b.json

    # Compare results:
    python experiments/model_comparison.py --compare \\
        experiments/results/smollm2_135m.json \\
        experiments/results/llama3.2_3b.json
"""
import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import httpx

from evaluation.evaluate import run_evaluation, check_quality_gate


def run_experiment(
    model_name: str,
    base_url: str,
    dataset_path: str,
    sample_size: int,
    output_path: str,
) -> dict:
    """Run evaluation for one model and save results."""
    print(f"\n{'='*60}")
    print(f"Running experiment: {model_name}")
    print(f"API: {base_url} | Dataset: {dataset_path} | Samples: {sample_size}")
    print(f"{'='*60}")

    start = time.perf_counter()
    results = run_evaluation(dataset_path=dataset_path, base_url=base_url, sample_size=sample_size)
    elapsed = time.perf_counter() - start

    experiment = {
        "model_name": model_name,
        "timestamp": datetime.utcnow().isoformat(),
        "elapsed_seconds": round(elapsed, 1),
        "metrics": results["metrics"],
        "config": {"base_url": base_url, "sample_size": sample_size, "dataset": dataset_path},
    }

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(experiment, f, indent=2)

    print(f"\nResults saved to {output_path}")
    _print_metrics(experiment["metrics"])
    return experiment


def compare_experiments(paths: list[str]) -> None:
    """Print a comparison table of multiple experiment results."""
    experiments = []
    for p in paths:
        with open(p) as f:
            experiments.append(json.load(f))

    print(f"\n{'='*80}")
    print("MODEL COMPARISON REPORT")
    print(f"{'='*80}")
    print(f"\n{'Model':<25} {'Block Acc':>10} {'Avg Sim':>10} {'Samples':>8} {'Time(s)':>8}")
    print("-" * 65)

    for exp in experiments:
        m = exp["metrics"]
        print(
            f"{exp['model_name']:<25} "
            f"{m['overall_blocking_accuracy']:>10.3f} "
            f"{m['overall_avg_similarity']:>10.3f} "
            f"{m['total_samples']:>8} "
            f"{exp['elapsed_seconds']:>8.1f}"
        )

    print("\nPER-CATEGORY BLOCKING ACCURACY:")
    categories = set()
    for exp in experiments:
        categories.update(exp["metrics"]["by_category"].keys())

    print(f"\n{'Model':<25}", end="")
    for cat in sorted(categories):
        print(f"  {cat.upper()[:10]:>12}", end="")
    print()
    print("-" * (25 + 14 * len(categories)))

    for exp in experiments:
        print(f"{exp['model_name']:<25}", end="")
        for cat in sorted(categories):
            cat_m = exp["metrics"]["by_category"].get(cat, {})
            val = cat_m.get("blocking_accuracy", float("nan"))
            print(f"  {val:>12.3f}", end="")
        print()


def _print_metrics(metrics: dict) -> None:
    print(f"\nOverall Blocking Accuracy: {metrics['overall_blocking_accuracy']:.3f}")
    print(f"Overall Avg Similarity:    {metrics['overall_avg_similarity']:.3f}")
    for cat, cm in metrics.get("by_category", {}).items():
        print(f"  {cat}: block_acc={cm['blocking_accuracy']:.3f} sim={cm['avg_similarity']:.3f}")


def main():
    parser = argparse.ArgumentParser(description="Model comparison experiment runner")
    parser.add_argument("--model-name", default="unknown", help="Human-readable model name")
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--dataset", default="data/qa_dataset.json")
    parser.add_argument("--sample-size", type=int, default=20)
    parser.add_argument("--output", default="experiments/results/latest.json")
    parser.add_argument("--compare", nargs="+", metavar="RESULT_JSON",
                        help="Compare multiple result files instead of running an experiment")
    args = parser.parse_args()

    if args.compare:
        compare_experiments(args.compare)
    else:
        run_experiment(
            model_name=args.model_name,
            base_url=args.base_url,
            dataset_path=args.dataset,
            sample_size=args.sample_size,
            output_path=args.output,
        )


if __name__ == "__main__":
    main()
