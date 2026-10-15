from __future__ import annotations

import pytest

from agentic_scrape_repair import (
    AgenticDOMRepairEngine,
    FieldSelectorSpec,
    SlackChangeSyncOrchestrator,
)


def test_dom_repair_engine_heals_broken_selector_with_schema_verification() -> None:
    # Upstream website renamed `span.legacy-price` to `div[data-testid="product-price-usd"]`
    mutated_html = """
    <html>
      <body>
        <div class="card">
          <h2 data-testid="item-title">Enterprise Router X1</h2>
          <div data-testid="product-price-usd">$1,249.00</div>
        </div>
        <div class="card">
          <h2 data-testid="item-title"> Optical Switch S8</h2>
          <div data-testid="product-price-usd">$899.50</div>
        </div>
      </body>
    </html>
    """
    spec = FieldSelectorSpec(
        field_name="price_usd",
        legacy_selector="span.legacy-price",
        expected_tag="div",
        semantic_tokens=("price", "usd"),
        value_regex=r"^\$[0-9,]+\.[0-9]{2}$",
    )
    engine = AgenticDOMRepairEngine(min_confidence=0.6)
    res = engine.repair_and_verify(mutated_html, spec, min_records=2)

    assert res.schema_verified is True
    assert res.repaired_selector == 'div[data-testid="product-price-usd"]'
    assert res.extracted_values == ["$1,249.00", "$899.50"]
    assert res.confidence_score >= 0.85


def test_dom_repair_rejects_unverified_schema_when_values_corrupted() -> None:
    corrupted_html = """
    <html>
      <body>
        <div data-testid="product-price-usd">Contact Sales</div>
      </body>
    </html>
    """
    spec = FieldSelectorSpec(
        field_name="price_usd",
        legacy_selector="span.legacy-price",
        expected_tag="div",
        semantic_tokens=("price", "usd"),
        value_regex=r"^\$[0-9,]+\.[0-9]{2}$",
    )
    engine = AgenticDOMRepairEngine(min_confidence=0.6)
    res = engine.repair_and_verify(corrupted_html, spec, min_records=1)
    assert res.schema_verified is False


def test_dom_repair_handles_void_tags_and_nested_inline_spans() -> None:
    html_with_void_and_nested = """
    <html>
      <head><meta charset="utf-8"><link rel="stylesheet" href="app.css"></head>
      <body>
        <div class="card">
          <img src="router.png" alt="router">
          <br>
          <div data-testid="product-price-usd"><span class="currency">$</span>1,249.00</div>
        </div>
        <div class="card">
          <img src="switch.png" alt="switch">
          <hr>
          <div data-testid="product-price-usd"><span class="currency">$</span>899.50</div>
        </div>
      </body>
    </html>
    """
    spec = FieldSelectorSpec(
        field_name="price_usd",
        legacy_selector="span.legacy-price",
        expected_tag="div",
        semantic_tokens=("price", "usd"),
        value_regex=r"^\$[0-9,]+\.[0-9]{2}$",
    )
    engine = AgenticDOMRepairEngine(min_confidence=0.6)
    res = engine.repair_and_verify(html_with_void_and_nested, spec, min_records=2)
    assert res.schema_verified is True
    assert res.repaired_selector == 'div[data-testid="product-price-usd"]'
    assert res.extracted_values == ["$1,249.00", "$899.50"]



def test_slack_change_sync_orchestrator_idempotency_and_lifecycle() -> None:
    orch = SlackChangeSyncOrchestrator()
    rec1 = orch.submit_slack_change_request(
        event_id="evt_9001",
        rally_story_id="US48210",
        github_pr_url="https://github.com/aazminu/agentic-scrape-repair/pull/1",
        service_name="billing-scraper-worker",
        risk_level="LOW",
    )
    # Duplicate Slack retry with same event_id must be idempotent
    rec_dup = orch.submit_slack_change_request(
        event_id="evt_9001",
        rally_story_id="US48210",
        github_pr_url="https://github.com/aazminu/agentic-scrape-repair/pull/1",
        service_name="billing-scraper-worker",
        risk_level="LOW",
    )
    assert rec1.change_id == rec_dup.change_id
    assert len(rec_dup.audit_events) == 4
    assert rec1.status == "READY_TO_DEPLOY"

    closed = orch.record_github_pr_merged("evt_9002", rec1.change_id, commit_sha="a1b2c3d")
    assert closed.status == "DEPLOYED_AND_CLOSED"

    with pytest.raises(ValueError, match="rally_story_id"):
        orch.submit_slack_change_request(
            event_id="evt_bad",
            rally_story_id="STORY-123",
            github_pr_url="https://github.com/aazminu/agentic-scrape-repair/pull/1",
            service_name="svc",
        )


def test_cli_benchmark_markdown_json_and_reproducibility(
    capsys: pytest.CaptureFixture[str], tmp_path: pytest.TempPathFactory
) -> None:
    import json
    import subprocess
    import sys
    from pathlib import Path
    from agentic_scrape_repair.__main__ import format_markdown_table, main, run_benchmark

    rows_1 = run_benchmark(seed=42)
    rows_2 = run_benchmark(seed=42)
    assert rows_1 == rows_2
    assert len(rows_1) == 3

    md = format_markdown_table(rows_1)
    assert "| benchmark | repaired_selector | confidence_score |" in md
    assert "dom_selector_self_healing" in md
    assert "idempotent_change_sync_lifecycle" in md

    out_path = Path(str(tmp_path)) / "scrape_bench.json"
    rc = main(["--format", "json", "--seed", "42", "--output", str(out_path)])
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert data["seed"] == 42
    assert data["benchmarks"] == rows_1
    assert json.loads(out_path.read_text(encoding="utf-8")) == data

    repo_root = Path(__file__).resolve().parents[1]
    proc = subprocess.run(
        [sys.executable, str(repo_root / "scripts" / "benchmark.py"), "--format", "markdown"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "corrupted_payload_schema_gate" in proc.stdout

    with pytest.raises(SystemExit):
        main(["--format", "invalid_fmt"])

