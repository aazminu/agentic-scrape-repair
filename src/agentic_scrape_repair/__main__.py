from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path
from typing import Any, Dict, List, Sequence

from .change_sync_workflow import SlackChangeSyncOrchestrator
from .dom_repair_engine import AgenticDOMRepairEngine, FieldSelectorSpec


def run_benchmark(seed: int = 42) -> List[Dict[str, Any]]:
    """Run reproducible DOM selector self-healing, void/nested HTML resilience, and change-sync benchmarks."""
    rng = random.Random(seed)
    engine = AgenticDOMRepairEngine(min_confidence=0.6)
    spec = FieldSelectorSpec(
        field_name="price_usd",
        legacy_selector="span.legacy-price",
        expected_tag="div",
        semantic_tokens=("price", "usd"),
        value_regex=r"^\$[0-9,]+\.[0-9]{2}$",
    )

    # 1. Mutated DOM selector synthesis & schema verification (with seeded product card batch size)
    extra_cards = rng.randint(1, 5)
    cards_html = [
        """
        <div class="card">
          <img src="router.png" alt="router"><br>
          <div data-testid="product-price-usd"><span class="currency">$</span>1,249.00</div>
        </div>
        """,
        """
        <div class="card">
          <img src="switch.png" alt="switch"><hr>
          <div data-testid="product-price-usd"><span class="currency">$</span>899.50</div>
        </div>
        """,
    ]
    for idx in range(extra_cards):
        cards_html.append(
            f"""
            <div class="card">
              <img src="node_{idx}.png" alt="node"><br>
              <div data-testid="product-price-usd"><span class="currency">$</span>{400 + idx * 50}.00</div>
            </div>
            """
        )
    mutated_html = "<html><body>" + "\n".join(cards_html) + "</body></html>"
    healed = engine.repair_and_verify(mutated_html, spec, min_records=2)

    # 2. Corrupted value rejection under strict regex schema gate
    corrupted_html = """
    <html>
      <body>
        <div data-testid="product-price-usd">Contact Sales</div>
      </body>
    </html>
    """
    rejected = engine.repair_and_verify(corrupted_html, spec, min_records=1)

    # 3. Idempotent Slack / ServiceNow / Rally / GitHub change sync lifecycle
    orch = SlackChangeSyncOrchestrator()
    rec1 = orch.submit_slack_change_request(
        event_id="evt_bench_1",
        rally_story_id="US48210",
        github_pr_url="https://github.com/aazminu/agentic-scrape-repair/pull/1",
        service_name="billing-scraper-worker",
        risk_level="LOW",
    )
    rec_dup = orch.submit_slack_change_request(
        event_id="evt_bench_1",
        rally_story_id="US48210",
        github_pr_url="https://github.com/aazminu/agentic-scrape-repair/pull/1",
        service_name="billing-scraper-worker",
        risk_level="LOW",
    )
    closed = orch.record_github_pr_merged("evt_bench_2", rec1.change_id, commit_sha="a1b2c3d")

    return [
        {
            "benchmark": "dom_selector_self_healing",
            "repaired_selector": healed.repaired_selector,
            "confidence_score": round(healed.confidence_score, 4),
            "records_extracted": len(healed.extracted_values),
            "schema_verified": healed.schema_verified,
            "status": "HEALED" if healed.schema_verified else "FAILED",
        },
        {
            "benchmark": "corrupted_payload_schema_gate",
            "repaired_selector": rejected.repaired_selector,
            "confidence_score": round(rejected.confidence_score, 4),
            "records_extracted": len(rejected.extracted_values),
            "schema_verified": rejected.schema_verified,
            "status": "BLOCKED_CORRUPT" if not rejected.schema_verified else "LEAKED",
        },
        {
            "benchmark": "idempotent_change_sync_lifecycle",
            "repaired_selector": closed.change_id,
            "confidence_score": 1.0 if rec1.change_id == rec_dup.change_id else 0.0,
            "records_extracted": len(closed.audit_events),
            "schema_verified": closed.status == "DEPLOYED_AND_CLOSED",
            "status": closed.status,
        },
    ]


def format_markdown_table(rows: Sequence[Dict[str, Any]]) -> str:
    headers = [
        "benchmark",
        "repaired_selector",
        "confidence_score",
        "records_extracted",
        "schema_verified",
        "status",
    ]
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join(["---"] * len(headers)) + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join(str(row[h]) for h in headers) + " |")
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run reproducible agentic-scrape-repair DOM healing and change-sync benchmarks."
    )
    parser.add_argument(
        "--format",
        choices=("markdown", "json"),
        default="markdown",
        help="Output format for benchmark results.",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="Random seed for reproducible benchmark execution.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Optional file path to write benchmark output.",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    rows = run_benchmark(seed=args.seed)
    rendered = (
        json.dumps({"seed": args.seed, "benchmarks": rows}, indent=2)
        if args.format == "json"
        else format_markdown_table(rows)
    )
    if args.output is not None:
        try:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(rendered + "\n", encoding="utf-8")
        except OSError as exc:
            print(f"Error writing output to {args.output}: {exc}", file=sys.stderr)
            return 1
    print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
