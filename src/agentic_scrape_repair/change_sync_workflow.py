from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Dict, List


@dataclass
class ChangeSyncRecord:
    change_id: str
    rally_story_id: str
    github_pr_url: str
    service_name: str
    risk_level: str
    status: str
    audit_events: List[str] = field(default_factory=list)


class SlackChangeSyncOrchestrator:
    """Idempotent workflow synchronizing Slack change requests across ServiceNow, Rally, and GitHub PRs."""

    def __init__(self) -> None:
        self._records: Dict[str, ChangeSyncRecord] = {}
        self._processed_event_hashes: set[str] = set()

    @staticmethod
    def _compute_dedup_hash(event_id: str, action: str) -> str:
        return hashlib.sha256(f"{event_id}:{action}".encode("utf-8")).hexdigest()[:16]

    def submit_slack_change_request(
        self,
        event_id: str,
        rally_story_id: str,
        github_pr_url: str,
        service_name: str,
        risk_level: str = "LOW",
    ) -> ChangeSyncRecord:
        if not rally_story_id.startswith("US"):
            raise ValueError("rally_story_id must start with 'US'")
        if "github.com/" not in github_pr_url or "/pull/" not in github_pr_url:
            raise ValueError("github_pr_url must be a valid GitHub Pull Request URL")
        if risk_level not in ("LOW", "MEDIUM", "HIGH"):
            raise ValueError("risk_level must be LOW, MEDIUM, or HIGH")

        dedup_key = self._compute_dedup_hash(event_id, "SUBMIT")
        chg_num = int(hashlib.sha256(rally_story_id.encode("utf-8")).hexdigest()[:6], 16) % 900000 + 100000
        change_id = f"CHG{chg_num}"

        if dedup_key in self._processed_event_hashes and change_id in self._records:
            return self._records[change_id]

        self._processed_event_hashes.add(dedup_key)
        initial_status = "CAB_REVIEW" if risk_level == "HIGH" else "READY_TO_DEPLOY"
        rec = ChangeSyncRecord(
            change_id=change_id,
            rally_story_id=rally_story_id,
            github_pr_url=github_pr_url,
            service_name=service_name,
            risk_level=risk_level,
            status=initial_status,
            audit_events=[
                f"slack_intake:{event_id}",
                f"rally_linked:{rally_story_id}",
                f"github_linked:{github_pr_url}",
                f"servicenow_opened:{change_id}({initial_status})",
            ],
        )
        self._records[change_id] = rec
        return rec

    def record_github_pr_merged(self, event_id: str, change_id: str, commit_sha: str) -> ChangeSyncRecord:
        if change_id not in self._records:
            raise KeyError(f"Unknown change_id: {change_id}")
        rec = self._records[change_id]
        dedup_key = self._compute_dedup_hash(event_id, f"MERGE:{commit_sha}")
        if dedup_key in self._processed_event_hashes:
            return rec

        self._processed_event_hashes.add(dedup_key)
        rec.status = "DEPLOYED_AND_CLOSED"
        rec.audit_events.append(f"pr_merged:{commit_sha}->servicenow_closed")
        return rec
