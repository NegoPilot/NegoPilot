"""Routing 결과(도구 원본) → 근거 목록(Evidence).

Routing은 도구 결과를 손대지 않고 넘기고(ToolCall.result), 근거 단위로 펼치는 일은 여기서 한다.
도구마다 결과 모양이 달라서 도구별 변환 함수를 두고, 모르는 도구는 JSON 그대로 근거 하나로 만든다.
도구별 모양은 docs/contracts/routing.md 4절.

인용 규칙 (Routing 계약 1절 5·6)
- 법 근거는 search_regulation 결과의 citation(항 단위)을 그대로 인용한다
- law_check의 get_article 조문 전문도 정상 본문과 원문 URL 및 citation이 있으면 인용할 수 있다
- law_article(get_article만 부름)은 그 결과의 citation을 인용한다
- rank_suppliers flags·check_offer의 law_refs 조문은 Routing이 미리 조회한 referenced_articles의 citation을 인용한다.
  조회에 실패한 조문은 근거로 만들지 않고, 검사 항목 본문의 "관련 조문: 하도급법 제13조"처럼 번호만 남는다

근거 본문(text)에는 도구가 준 숫자를 **바꾸지 않고** 넣는다. 숫자 검사(verify.py)가 이 본문과 비교한다.
"""

import json
from collections.abc import Callable, Iterable
from typing import Any
from urllib.parse import urlsplit

from negopilot_shared import RouteResult, SubQueryOutcome, ToolCall

from negopilot_agent.generation.answer import Evidence

MAX_TEXT = 3000          # 근거 하나에 넣을 본문 최대 길이 (긴 조문은 자른다)
MAX_LIST_ITEMS = 10      # 목록형 결과(RFQ, 검색 결과)에서 근거로 쓸 최대 개수

LAW_NAMES = {
    "subcontract_act": "하도급법",
    "subcontract_decree": "하도급법 시행령",
    "winwin_act": "상생협력법",
    "winwin_decree": "상생협력법 시행령",
    "sme_act": "중소기업기본법",
    "sme_decree": "중소기업기본법 시행령",
}

Draft = dict[str, Any]   # id를 붙이기 전의 근거 (Evidence 필드에서 id만 빠진 것)


def compact(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str, separators=(",", ":"))


def clip(text: str) -> str:
    return text if len(text) <= MAX_TEXT else text[:MAX_TEXT] + " …(생략)"


def law_refs_text(refs: Iterable[dict]) -> str:
    names = [f"{LAW_NAMES.get(r.get('law'), r.get('law'))} {r.get('article_no')}" for r in refs]
    return f" 관련 조문: {', '.join(names)}" if names else ""


# ---------- 도구별 변환 ----------

def from_get_article(r: dict) -> list[Draft]:
    paragraphs = "\n".join(f"{p.get('no', '')} {p.get('text', '')}".strip() for p in r.get("paragraphs", []))
    text = f"{r.get('citation')}\n{paragraphs}"
    upcoming = r.get("upcoming_changes") or []
    if upcoming:
        text += "\n[시행 예정 개정] " + "; ".join(
            f"{c.get('effective_date')} {c.get('change')}" for c in upcoming)
    return [{"source": "law", "kind": "article", "label": r.get("citation") or f"{r.get('law_name')} {r.get('article_no')}",
             "text": clip(text), "source_url": r.get("source_url"),
             "meta": {"law_key": r.get("law_key"), "article_no": r.get("article_no"),
                      "effective_date": r.get("effective_date"), "upcoming_changes": upcoming}}]


def from_search_regulation(hits: list[dict]) -> list[Draft]:
    out = []
    for h in hits[:MAX_LIST_ITEMS]:
        out.append({"source": "law", "kind": "regulation_hit", "label": h.get("citation", ""),
                    "text": clip(f"{h.get('citation')}\n{h.get('text', '')}"), "source_url": h.get("source_url"),
                    "meta": {"law_key": h.get("law_key"), "article_no": h.get("article_no"),
                             "paragraph": h.get("paragraph"), "effective_date": h.get("effective_date"),
                             "score": h.get("score")}})
    return out


def from_check_offer(r: dict) -> list[Draft]:
    supplier = r.get("supplier", {})
    rfq = r.get("rfq", {})
    who = f"{supplier.get('name')}({supplier.get('supplier_id')}) · {rfq.get('rfq_id')}"
    out = [{"source": "business", "kind": "offer_verdict", "label": f"협상안 검사 결과: {who}",
            "text": compact({"verdict": r.get("verdict"), "supplier": supplier, "rfq": rfq, "offer": r.get("offer")}),
            "meta": {"verdict": r.get("verdict")}}]
    for severity, items in (("위반 소지", r.get("violations", [])), ("확인 필요", r.get("warnings", [])),
                            ("참고", r.get("info", []))):
        for f in items:
            out.append({"source": "business", "kind": "offer_finding",
                        "label": f"협상안 검사 [{severity}] {f.get('code')}",
                        "text": f"[{severity}] {f.get('message')}{law_refs_text(f.get('law_refs', []))}",
                        "meta": {"code": f.get("code"), "severity": f.get("severity"),
                                 "law_refs": f.get("law_refs", [])}})
    return out


RANK_FIELDS = ("supplier_id", "name", "source", "region", "total_score", "scores", "last_price",
               "gap_to_target_pct", "defect_ppm", "on_time_rate", "audit_grade", "monthly_capacity",
               "payment_days", "price_linkage", "notes")


def from_rank_suppliers(r: dict) -> list[Draft]:
    rfq = r.get("rfq", {})
    rid = rfq.get("rfq_id")
    out = [{"source": "business", "kind": "ranking_summary", "label": f"{rid} 협력사 순위 계산 조건",
            "text": compact({"rfq": rfq, "weights": r.get("weights"),
                             "needed_monthly_capacity": r.get("needed_monthly_capacity"),
                             "candidate_count": r.get("candidate_count"),
                             "excluded": r.get("excluded", [])[:MAX_LIST_ITEMS]}),
            "meta": {"rfq_id": rid}}]
    for rank, c in enumerate(r.get("ranked", []), start=1):
        flags = c.get("flags", [])
        text = compact({"rank": rank, **{k: c[k] for k in RANK_FIELDS if k in c}})
        if flags:
            text += " flags: " + "; ".join(f"[{f.get('severity')}] {f.get('message')}{law_refs_text(f.get('law_refs', []))}"
                                           for f in flags)
        out.append({"source": "business", "kind": "ranked_supplier",
                    "label": f"{rid} 순위 {rank}위 {c.get('name')}({c.get('supplier_id')})",
                    "text": clip(text), "meta": {"rfq_id": rid, "rank": rank, "supplier_id": c.get("supplier_id"),
                                                 "flags": flags}})
    return out


def from_get_supplier(r: dict) -> list[Draft]:
    return [{"source": "business", "kind": "supplier", "label": f"협력사 정보 {r.get('name')}({r.get('supplier_id')})",
             "text": clip(compact(r)), "meta": {"supplier_id": r.get("supplier_id"), "data_source": r.get("source")}}]


def from_list_rfqs(rows: list[dict]) -> list[Draft]:
    return [{"source": "business", "kind": "rfq", "label": f"RFQ {row.get('rfq_id')} ({row.get('part_name')})",
             "text": clip(compact(row)), "meta": {"rfq_id": row.get("rfq_id")}} for row in rows[:MAX_LIST_ITEMS]]


def from_search_suppliers(rows: list[dict]) -> list[Draft]:
    return [{"source": "business", "kind": "supplier_hit", "label": f"협력사 검색 결과 {row.get('name')}({row.get('supplier_id')})",
             "text": clip(compact(row)), "meta": {"supplier_id": row.get("supplier_id")}}
            for row in (rows or [])[:MAX_LIST_ITEMS]]


CONVERTERS: dict[str, Callable[[Any], list[Draft]]] = {
    "get_article": from_get_article,
    "search_regulation": from_search_regulation,
    "check_offer": from_check_offer,
    "rank_suppliers": from_rank_suppliers,
    "get_supplier": from_get_supplier,
    "list_rfqs": from_list_rfqs,
    "search_suppliers": from_search_suppliers,
}


def from_call(call: ToolCall) -> list[Draft]:
    if call.error or call.result is None or call.tool == "find_suppliers":
        # 이름 후보는 대상 확인에만 쓴다. 상세 조회가 반환한 사실을 근거로 사용한다.
        return []
    convert = CONVERTERS.get(call.tool)
    try:
        if convert:
            return convert(call.result)
    except (AttributeError, TypeError, KeyError):
        pass   # 결과 모양이 예상과 다르면 아래 기본 변환으로
    source = "law" if call.server == "law-mcp" else "business"
    return [{"source": source, "kind": call.tool, "label": f"{call.tool} 결과", "text": clip(compact(call.result))}]


def _citable_article(call: ToolCall, draft: Draft) -> bool:
    """검색 뒤 보충한 전문은 확인 가능한 원문과 정상 본문이 있을 때만 인용한다."""
    if call.server != "law-mcp" or draft.get("kind") != "article" or not isinstance(call.result, dict):
        return False
    citation, url = call.result.get("citation"), call.result.get("source_url")
    paragraphs = call.result.get("paragraphs")
    if (not isinstance(citation, str) or not citation.strip()
            or not isinstance(url, str) or not url.strip()
            or not isinstance(paragraphs, list)
            or not any(isinstance(p, dict) and isinstance(p.get("text"), str) and p["text"].strip()
                       for p in paragraphs)):
        return False
    try:
        parsed = urlsplit(url)
        return parsed.scheme in {"http", "https"} and bool(parsed.hostname)
    except ValueError:
        return False


def from_outcome(outcome: SubQueryOutcome) -> list[Draft]:
    """하위 질문 하나의 결과를 근거로 펼친다."""
    if outcome.status != "ok":
        return []
    # law_check는 검색 문단과 조문 전문을 구분한다. 정상 전문도 실제 설명의 근거로 인용할 수 있다.
    searched = any(c.tool == "search_regulation" for c in outcome.calls)
    drafts: list[Draft] = []
    for call in outcome.calls:
        for d in from_call(call):
            if call.tool == "get_article" and searched:
                d = d | {"kind": "article_reference", "citable": _citable_article(call, d)}
            drafts.append(d)
    for ref in outcome.referenced_articles:        # 실패한 조회(error)는 from_call이 건너뛴다
        drafts += [d | {"kind": "referenced_article"} for d in from_call(ref)]
    return drafts


def build_evidence(routing: RouteResult) -> list[Evidence]:
    """RouteResult 전체를 근거 목록으로. id는 E1부터 순서대로 붙인다."""
    evidence: list[Evidence] = []
    for outcome in routing.outcomes:
        for d in from_outcome(outcome):
            evidence.append(Evidence(id=f"E{len(evidence) + 1}", sub_query=outcome.id, **d))
    return evidence
