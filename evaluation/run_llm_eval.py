"""Run LLM-as-judge evaluation on sample conversations.

Usage:
    python evaluation/run_llm_eval.py
    python evaluation/run_llm_eval.py --output results.json
    ANTHROPIC_API_KEY=sk-... python evaluation/run_llm_eval.py
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from evaluation.llm_evaluator import LLMEvaluator

SAMPLE_CONVERSATIONS = [
    {
        "question": "Where is my order ORD-10001?",
        "response": "Your order ORD-10001 was shipped on May 15th via UPS (tracking: 1Z999AA10123456784). Estimated delivery is May 22nd. You can track it at ups.com.",
        "intent": "order_status",
    },
    {
        "question": "I want to return my damaged wireless headphones. My order is ORD-10003.",
        "response": "I'm sorry to hear your headphones arrived damaged — that's not the experience we want for you. Your order is within our 30-day return window, so you are eligible for a full refund. Please visit returns.example.com, enter order number ORD-10003, and print the prepaid return label. Refunds are processed within 3-5 business days.",
        "intent": "return_request",
    },
    {
        "question": "How much do the wireless headphones cost and are they in stock?",
        "response": "The Wireless Headphones Pro (P001) are $79.99 and currently in stock (45 units available). They feature 30-hour battery life and Bluetooth 5.0 connectivity.",
        "intent": "product_question",
    },
    {
        "question": "This is completely unacceptable. I want to speak to a manager immediately.",
        "response": "I completely understand your frustration, and I sincerely apologize for the experience you've had. This is not the standard we hold ourselves to. I'm escalating this to a senior team member who will personally reach out to you within 2 business hours. Could you confirm the best way to reach you — email or phone?",
        "intent": "escalation",
    },
    {
        "question": "Do you have any running shoes in size 10?",
        "response": "Yes! We carry several running shoe styles. The TrailRunner Pro (SKU-RS10) is available in size 10 at $89.99, and the SpeedStride Elite (SKU-RS22) is $129.99. Both are currently in stock. Would you like more details on either?",
        "intent": "product_question",
    },
    # Intentionally poor response — should score low
    {
        "question": "My package hasn't arrived after 2 weeks. Order ORD-10005.",
        "response": "ok",
        "intent": "order_status",
    },
]


def main():
    parser = argparse.ArgumentParser(description="Run LLM-as-judge evaluation")
    parser.add_argument("--output", "-o", default=None, help="Write results to JSON file")
    parser.add_argument("--threshold", type=float, default=0.55, help="Minimum average overall score (default: 0.55)")
    args = parser.parse_args()

    evaluator = LLMEvaluator()
    print(f"Judge model: {evaluator._judge_model if evaluator._client else 'heuristic (no API key)'}")
    print(f"Evaluating {len(SAMPLE_CONVERSATIONS)} sample conversations...\n")

    results_data = []
    eval_results = evaluator.evaluate_batch(SAMPLE_CONVERSATIONS)

    for conv, result in zip(SAMPLE_CONVERSATIONS, eval_results):
        print(f"Intent: {conv['intent']}")
        print(f"  Q: {conv['question'][:80]}")
        print(f"  Scores: overall={result.overall:.2f} | acc={result.accuracy:.2f} | help={result.helpfulness:.2f} | tone={result.tone:.2f} | complete={result.completeness:.2f}")
        print()
        results_data.append({"conversation": conv, "scores": result.to_dict()})

    avg_overall = sum(r.overall for r in eval_results) / len(eval_results)
    print(f"{'='*50}")
    print(f"Average overall score: {avg_overall:.3f}")
    print(f"Threshold:             {args.threshold:.3f}")
    passed = avg_overall >= args.threshold
    print(f"Result: {'PASS' if passed else 'FAIL'}")

    if args.output:
        with open(args.output, "w") as f:
            json.dump({"summary": {"avg_overall": avg_overall, "passed": passed, "threshold": args.threshold}, "results": results_data}, f, indent=2)
        print(f"\nResults saved to {args.output}")

    if not passed:
        sys.exit(1)


if __name__ == "__main__":
    main()
