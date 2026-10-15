# Agentic Scrape Repair & Change-Management Workflow (`agentic-scrape-repair`)

Self-healing DOM selector repair workflow gated on live schema verification, paired with idempotent Slack-to-ServiceNow/Rally/GitHub change-management synchronization.

## Core Modules

1. **`dom_repair_engine.py`**:
   - Reproduces scraper extraction failures in an isolated harness against mutated HTML snapshots.
   - Scores candidate DOM elements against historical **DOM Element Fingerprints** (`tag`, `data-*` attributes, semantic tokens, parent path, and value regex) and synthesizes replacement selectors.
   - Enforces a **Live Schema Verification Gate** (field presence, regex/type validation, minimum row count) before approving any repair patch.
2. **`change_sync_workflow.py`**:
   - Parses Slack change-intake payloads, links **GitHub Pull Requests**, **Rally User Stories (`USxxxx`)**, and **ServiceNow Change Tickets (`CHGxxxx`)**, and guarantees idempotent state transitions under webhook retries.
