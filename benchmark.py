#!/usr/bin/env python3
"""Multilingual Model Evaluation & Benchmarking Suite.

Evaluates local SLM (Ollama) performance against ground-truth social media captions
across English, Hindi, Hinglish, and Urdu.
"""
from __future__ import annotations

import csv
import logging
import sys
import time
from pathlib import Path

from analyzer import analyze_caption

CSV_PATH = Path(__file__).parent / "test_captions.csv"


def run_benchmark(model: str = "llama3.2:latest") -> dict:
    if not CSV_PATH.exists():
        print(f"Error: {CSV_PATH} not found.")
        sys.exit(1)

    with open(CSV_PATH, "r", encoding="utf-8") as f:
        reader = list(csv.DictReader(f))

    total = len(reader)
    if total == 0:
        print("No test cases found.")
        return {}

    print(f"\n=======================================================")
    print(f"  RUNNING BENCHMARK EVALUATION (Model: {model})")
    print(f"  Total test samples: {total}")
    print(f"=======================================================\n")

    cat_correct = 0
    sent_correct = 0
    lang_correct = 0
    total_time = 0.0
    results = []

    print(f"{'ID':<3} | {'Expected Cat/Sent':<22} | {'Predicted Cat/Sent':<22} | {'Lang Match':<10} | {'Latency':<7}")
    print("-" * 75)

    for row in reader:
        caption = row["caption"]
        exp_cat = row["expected_category"].strip()
        exp_sent = row["expected_sentiment"].strip()
        exp_lang = row["expected_language"].strip()

        start_t = time.time()
        pred = analyze_caption(caption=caption, model=model)
        latency = time.time() - start_t
        total_time += latency

        is_cat_match = pred["category"] == exp_cat
        is_sent_match = pred["sentiment"] == exp_sent
        is_lang_match = pred["language"].lower() == exp_lang.lower()

        if is_cat_match:
            cat_correct += 1
        if is_sent_match:
            sent_correct += 1
        if is_lang_match:
            lang_correct += 1

        exp_str = f"{exp_cat[:4]}/{exp_sent[:3]}"
        pred_str = f"{pred['category'][:4]}/{pred['sentiment'][:3]}"
        lang_str = f"{pred['language']} ({'✓' if is_lang_match else '✗'})"

        print(f"{row['id']:<3} | {exp_str:<22} | {pred_str:<22} | {lang_str:<10} | {latency:.2f}s")

        results.append({
            "id": row["id"],
            "caption": caption,
            "expected_category": exp_cat,
            "predicted_category": pred["category"],
            "expected_sentiment": exp_sent,
            "predicted_sentiment": pred["sentiment"],
            "expected_language": exp_lang,
            "predicted_language": pred["language"],
            "claims": pred["claims"],
            "latency": latency,
        })

    cat_acc = (cat_correct / total) * 100
    sent_acc = (sent_correct / total) * 100
    lang_acc = (lang_correct / total) * 100
    avg_latency = total_time / total

    print("-" * 75)
    print("\nBENCHMARK SUMMARY RESULTS:")
    print(f"  • Category Accuracy  (Army vs Non-Army): {cat_acc:.1f}% ({cat_correct}/{total})")
    print(f"  • Sentiment Accuracy (Pos/Neg/Neutral):   {sent_acc:.1f}% ({sent_correct}/{total})")
    print(f"  • Language Accuracy  (En/Hi/Hinglish/Ur): {lang_acc:.1f}% ({lang_correct}/{total})")
    print(f"  • Average Latency Per Post:             {avg_latency:.2f}s")
    print("=======================================================\n")

    return {
        "total": total,
        "category_accuracy": cat_acc,
        "sentiment_accuracy": sent_acc,
        "language_accuracy": lang_acc,
        "avg_latency": avg_latency,
        "details": results,
    }


if __name__ == "__main__":
    logging.basicConfig(level=logging.ERROR)
    run_benchmark()
