from __future__ import annotations

import re
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Dict, List, Optional, Sequence, Tuple


@dataclass(frozen=True)
class DOMNode:
    tag: str
    attrs: Dict[str, str]
    text: str
    parent_tag: str


@dataclass(frozen=True)
class FieldSelectorSpec:
    field_name: str
    legacy_selector: str
    expected_tag: str
    semantic_tokens: Tuple[str, ...]
    value_regex: str


@dataclass(frozen=True)
class RepairCandidateResult:
    field_name: str
    repaired_selector: str
    extracted_values: List[str]
    confidence_score: float
    schema_verified: bool


VOID_HTML_TAGS = frozenset(
    {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    }
)


class _FlatDOMParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._stack: List[Tuple[str, Dict[str, str], List[str]]] = []
        self.nodes: List[DOMNode] = []

    def _emit_popped(self, t_name: str, attrs: Dict[str, str], chunks: List[str]) -> None:
        parent = self._stack[-1][0] if self._stack else "document"
        text = " ".join("".join(chunks).split())
        self.nodes.append(DOMNode(tag=t_name, attrs=attrs, text=text, parent_tag=parent))

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        t_low = tag.lower()
        attr_dict = {k.lower(): (v or "") for k, v in attrs}
        if t_low in VOID_HTML_TAGS:
            self._emit_popped(t_low, attr_dict, [])
            return
        self._stack.append((t_low, attr_dict, []))

    def handle_startendtag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        t_low = tag.lower()
        attr_dict = {k.lower(): (v or "") for k, v in attrs}
        self._emit_popped(t_low, attr_dict, [])

    def handle_data(self, data: str) -> None:
        for _, _, chunks in self._stack:
            chunks.append(data)

    def handle_endtag(self, tag: str) -> None:
        t_low = tag.lower()
        if t_low in VOID_HTML_TAGS or not self._stack:
            return
        # Find nearest matching open tag on the stack to handle unclosed inner tags gracefully
        match_idx = -1
        for idx in range(len(self._stack) - 1, -1, -1):
            if self._stack[idx][0] == t_low:
                match_idx = idx
                break
        if match_idx == -1:
            return
        while len(self._stack) > match_idx:
            t_name, attrs, chunks = self._stack.pop()
            self._emit_popped(t_name, attrs, chunks)

    def flush_unclosed(self) -> None:
        while self._stack:
            t_name, attrs, chunks = self._stack.pop()
            self._emit_popped(t_name, attrs, chunks)


def parse_dom_nodes(html: str) -> List[DOMNode]:
    parser = _FlatDOMParser()
    parser.feed(html)
    parser.flush_unclosed()
    return parser.nodes


def match_simple_selector(nodes: Sequence[DOMNode], selector: str) -> List[DOMNode]:
    """Matches tag.class, tag#id, or tag[attr='val'] selectors against parsed DOM nodes."""
    sel = selector.strip()
    attr_match = re.match(r"^([a-z0-9]+)\[([a-z0-9_-]+)=['\"]([^'\"]+)['\"]\]$", sel)
    if attr_match:
        tag, attr_k, attr_v = attr_match.groups()
        return [n for n in nodes if n.tag == tag and n.attrs.get(attr_k) == attr_v]

    class_match = re.match(r"^([a-z0-9]+)\.([a-zA-Z0-9_-]+)$", sel)
    if class_match:
        tag, cls_name = class_match.groups()
        return [
            n
            for n in nodes
            if n.tag == tag and cls_name in n.attrs.get("class", "").split()
        ]

    id_match = re.match(r"^([a-z0-9]+)#([a-zA-Z0-9_-]+)$", sel)
    if id_match:
        tag, id_val = id_match.groups()
        return [n for n in nodes if n.tag == tag and n.attrs.get("id") == id_val]

    return [n for n in nodes if n.tag == sel]


class AgenticDOMRepairEngine:
    """Synthesizes and verifies self-healing CSS/attribute selectors when legacy selectors break."""

    def __init__(self, min_confidence: float = 0.55) -> None:
        self.min_confidence = min_confidence

    @staticmethod
    def _build_selector_for_node(node: DOMNode) -> str:
        for stable_attr in ("data-testid", "data-field", "itemprop", "id"):
            if stable_attr in node.attrs and node.attrs[stable_attr]:
                val = node.attrs[stable_attr]
                if stable_attr == "id":
                    return f"{node.tag}#{val}"
                return f'{node.tag}[{stable_attr}="{val}"]'
        classes = node.attrs.get("class", "").split()
        if classes:
            return f"{node.tag}.{classes[0]}"
        return node.tag

    def repair_and_verify(
        self,
        html_snapshot: str,
        spec: FieldSelectorSpec,
        min_records: int = 1,
    ) -> RepairCandidateResult:
        nodes = parse_dom_nodes(html_snapshot)
        pattern = re.compile(spec.value_regex)

        # 1. Check if legacy selector still works and passes schema validation
        legacy_hits = match_simple_selector(nodes, spec.legacy_selector)
        legacy_vals = [n.text for n in legacy_hits if n.text and pattern.search(n.text)]
        if len(legacy_vals) >= min_records:
            return RepairCandidateResult(
                field_name=spec.field_name,
                repaired_selector=spec.legacy_selector,
                extracted_values=legacy_vals,
                confidence_score=1.0,
                schema_verified=True,
            )

        # 2. Score all candidate DOM nodes by fingerprint similarity + value regex match
        best_selector = spec.legacy_selector
        best_score = 0.0
        best_values: List[str] = []

        for node in nodes:
            if not node.text:
                continue
            score = 0.0
            if node.tag == spec.expected_tag:
                score += 0.30
            attr_blob = " ".join(f"{k}={v}" for k, v in node.attrs.items()).lower()
            token_hits = sum(1 for tok in spec.semantic_tokens if tok.lower() in attr_blob)
            if spec.semantic_tokens:
                score += 0.40 * (token_hits / len(spec.semantic_tokens))
            if pattern.search(node.text):
                score += 0.30

            candidate_sel = self._build_selector_for_node(node)
            matched_nodes = match_simple_selector(nodes, candidate_sel)
            valid_vals = [m.text for m in matched_nodes if m.text and pattern.search(m.text)]

            if score > best_score and len(valid_vals) >= min_records:
                best_score = score
                best_selector = candidate_sel
                best_values = valid_vals

        verified = best_score >= self.min_confidence and len(best_values) >= min_records
        return RepairCandidateResult(
            field_name=spec.field_name,
            repaired_selector=best_selector,
            extracted_values=best_values,
            confidence_score=round(best_score, 4),
            schema_verified=verified,
        )
