"""법률 검토 에이전트를 만들 가치가 있는지 보는 비교 실험 (2026-10-25, 실험용 스크립트).

같은 상황 문장을 세 방식으로 처리하고, 답변 생성은 셋 다 같은 AnswerGenerator(근거 검사 포함)를 쓴다.
바뀌는 것은 "어떤 조문을 근거로 가져오나" 하나뿐이다.

  A  chat   지금 채팅 경로: 쿼리 분해 → Routing(search_regulation + 상위 3개 get_article) → 생성
  B  core   A + 핵심 조문 고정 세트(하도급법 제2조·제3조·제4조·제11조·제16조의2)를 항상 근거에 넣음
  C  agent  모델이 law-mcp 도구(search_regulation, get_article)를 직접 골라 부르는 루프(최대 6회) → 생성
  D  select     (2차) A + 조문 선택 노드: 하도급법 목차(조문 번호·제목)를 보고 빠진 조문을 모델이 골라 전문을 더 가져옴
  E  agent_toc  (2차) C + 목차 도구(list_articles). 3차부터 법령명·조문 번호 인자를 정규화한다
  F  select2    (3차) D와 같되 최대 2개, 상황의 행위를 직접 규율하는 조문만 고르게 한다 (군더더기 줄이기)
  G  issues     (4차) 쟁점 검토 노드: 쟁점과 결론을 좌우하는 조문을 최대 3개 고른다(이미 찾은 조문 포함).
                고른 조문은 "쟁점 + 고른 이유"를 질문으로 한 하위 질문 하나로 다시 가져와, 생성이 그 쟁점에 답하게 한다
  H  issues_cite   (5차) G + 생성 단계에 쟁점 목록을 넘겨 쟁점마다 조문 근거를 인용하게 한다 (생성 프롬프트에 덧붙임)
  I  issues_cite2  (5차) H + 쟁점 노드 프롬프트에 서면 발급·기재 의무 쟁점 안내 한 줄 추가
  N  node          (7차) I를 실제 그래프 노드로 옮긴 것 (negopilot_agent.issue_review). law_check·offer_check에만 돈다

평가 세트
  catalog  상황별 법률 검토 세트 33문항 (4쟁점 결정·감액·조정·연동 + 법률 무관 o03). B의 고정 세트가 정답을 다 품는다
  outside  4쟁점 밖 15문항 (outside_gold.json 12개 + 원래 세트 o01·o02·o04를 조문 정답으로 다시 채점)

지표 (하도급법 조문만 셈. 정의 조문 제2조는 정답이 아닌 곳에서 들어도 감점하지 않음)
  answer_recall     꼭 들어야 할 조문 중 최종 답변이 인용한 비율  ← 주 지표
  evidence_recall   꼭 들어야 할 조문 중 근거(도구 결과)에 들어온 비율
  extra_per_answer  답변이 인용한 조문 중 정답·허용 목록 밖 조문 수 평균 (군더더기)
  oos_ok            법률 무관 질문(o03)에 조문을 인용하지 않은 비율
  cost_usd, seconds 질문당 평균

    cd agent
    uv run python scripts/eval_legal_agent.py --fake            # 모델 없이 배선만 점검 (키 불필요)
    uv run python scripts/eval_legal_agent.py --repeat 3        # 실제 모델 (gpt-4.1-mini, OPENAI_API_KEY)
    uv run python scripts/eval_legal_agent.py --arms agent --limit 5

결과: eval_results/legal_agent_<시각>.json, .md
"""

import argparse
import asyncio
import contextvars
import importlib.util
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from statistics import mean
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent
HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(ROOT / "src"), str(REPO / "shared" / "src"), str(HERE)]

from negopilot_shared import DecompositionResult, RouteResult, SubQuery, SubQueryOutcome, ToolCall  # noqa: E402

from negopilot_agent import timing  # noqa: E402

DATA = HERE / "legal_agent_data"
CORE = ["제2조", "제3조", "제4조", "제11조", "제16조의2"]
TOPIC_ARTICLES = {"price_setting": ["제4조"], "price_reduction": ["제11조"], "cost_adjustment": ["제16조의2"],
                  "price_linkage": ["제2조", "제3조"]}
ARTICLE_RE = re.compile(r"제\s?(\d+)\s?조(?:\s?의\s?(\d+))?")
AGENT_MAX_CALLS = 6
AGENT_MODEL = "gpt-4.1-mini"


# ---------- 평가 세트 ----------

def load_items() -> list[dict]:
    situation = json.loads((DATA / "situation_review_gold.json").read_text())
    outside = json.loads((DATA / "outside_gold.json").read_text())
    rescored = outside["rescored_from_situation_set"]
    items = []
    for i in situation["items"]:
        if i["id"] in rescored:
            r = rescored[i["id"]]
            items.append({"id": i["id"], "set": "outside", "query": i["query"], "must": r["must_articles"],
                          "allowed": r["extra_articles"], "oos": False})
            continue
        allowed = [a for t in i["acceptable_extra"] for a in TOPIC_ARTICLES[t]]
        items.append({"id": i["id"], "set": "catalog", "query": i["query"], "must": i["must_articles"],
                      "allowed": allowed, "oos": i["out_of_scope"]})
    for i in outside["items"]:
        items.append({"id": i["id"], "set": "outside", "query": i["query"], "must": i["must_articles"],
                      "allowed": i["extra_articles"], "oos": False})
    return items


def norm(no: str | None) -> str | None:
    m = ARTICLE_RE.search(no or "")
    if not m:
        return None
    return f"제{m.group(1)}조" + (f"의{m.group(2)}" if m.group(2) else "")


# ---------- law-mcp (in-process) ----------

LAW_MODULE = None


def load_law_mcp():
    global LAW_MODULE
    path = REPO / "mcp-servers" / "law-mcp" / "server.py"
    sys.path.insert(0, str(path.parent))
    spec = importlib.util.spec_from_file_location("eval_law_mcp", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    LAW_MODULE = module
    return module.mcp


def toc(as_of: str, law: str = "하도급법") -> list[dict]:
    """조문 목차 (번호·제목). law-mcp에 목차 도구가 없어 실험에서는 서버 모듈의 색인을 직접 읽는다.
    실제로 넣는다면 law-mcp에 list_articles 도구를 추가해야 한다."""
    m = LAW_MODULE
    v = m.version_in_force(m.resolve_law(law), m.parse_as_of(as_of))
    return [{"article_no": a["no"], "title": a["title"]} for a in v["articles"]
            if a["title"] and not a.get("deleted")]


def make_caller(client):
    async def caller(server: str, tool: str, args: dict[str, Any]) -> Any:
        if server != "law-mcp":
            raise RuntimeError(f"이 실험에서는 {server}를 띄우지 않는다 (법령 질문만)")
        return (await client.call_tool(tool, args)).data   # 시간 기록은 Router와 call()이 한다
    return caller


async def call(caller, tool: str, args: dict) -> ToolCall:
    try:
        with timing.span("tool", f"law-mcp.{tool}"):
            result = await caller("law-mcp", tool, args)
        return ToolCall(server="law-mcp", tool=tool, args=args, result=result)
    except Exception as e:  # noqa: BLE001 — 도구 오류는 결과에 남기고 계속한다
        return ToolCall(server="law-mcp", tool=tool, args=args, error=str(e)[:300])


# ---------- A, B ----------

async def run_chat(query: str, decomposer, router, as_of: str):
    d = await decomposer.decompose(query)
    routing = await router.route(d)
    return d, routing


async def add_core(d: DecompositionResult, routing: RouteResult, caller, as_of: str):
    """B: 분해가 법령 쪽 하위 질문을 하나라도 냈으면 핵심 조문 고정 세트를 하위 질문 하나로 덧붙인다."""
    if d.status != "ok" or not any(sq.intent in ("law_check", "law_article", "offer_check") for sq in d.sub_queries):
        return d, routing
    if len(d.sub_queries) >= 5:   # 계약상 하위 질문 최대 5개. 마지막 것을 대신한다 (실험에서는 거의 없음)
        d = d.model_copy(update={"sub_queries": d.sub_queries[:4]})
        routing = routing.model_copy(update={"outcomes": routing.outcomes[:4]})
    qid = f"q{max(int(sq.id[1:]) for sq in d.sub_queries) + 1}"
    sq = SubQuery(id=qid, question="하도급 대금 결정·감액·조정·연동 관련 하도급법 핵심 조문", original_text=d.original_query,
                  intent="law_check", grounding="required", confidence=1)
    calls = [await call(caller, "get_article", {"law": "하도급법", "article_no": no, "as_of": as_of}) for no in CORE]
    outcome = SubQueryOutcome(id=qid, intent="law_check", status="ok", calls=calls)
    d = d.model_copy(update={"sub_queries": [*d.sub_queries, sq]})
    routing = RouteResult(status="routed", outcomes=[*routing.outcomes, outcome])
    return d, routing


# ---------- D: 조문 선택 노드 ----------

SELECT_SYSTEM = """너는 자동차 부품 1차 협력사(원사업자) 구매 담당자의 질문을 법적으로 검토하는 담당이다.
담당자의 상황과 이미 찾은 조문, 하도급법 목차(조문 번호와 제목)를 받는다.
상황의 사실관계에 비추어 판단에 필요한데 아직 찾지 못한 하도급법 조문을 목차에서 고른다.
- 원사업자의 행위가 금지 행위나 의무 위반에 해당할 수 있는지를 기준으로 고른다.
- 용어 정의나 적용 대상 요건을 판단하는 데 필요한 조문도 고를 수 있다.
- 벌칙·과징금·절차 조문은 상황이 그것을 묻지 않으면 고르지 않는다.
- 최대 4개. 더 필요한 조문이 없거나 법률과 무관한 요청이면 빈 목록을 낸다."""

SELECT2_SYSTEM = """너는 자동차 부품 1차 협력사(원사업자) 구매 담당자의 질문을 법적으로 검토하는 담당이다.
담당자의 상황과 이미 찾은 조문, 하도급법 목차(조문 번호와 제목)를 받는다.
상황 속 원사업자의 행위를 직접 금지하거나 의무를 정한 조문 가운데 아직 찾지 못한 것만 목차에서 고른다.
- 이미 찾은 조문으로 판단할 수 있으면 고르지 않는다.
- 상황이 그 조문의 요건(용어 정의, 적용 대상, 금액·기간 기준)에 걸리는지가 결론을 바꾸면 그 요건을 정한 조문을 고를 수 있다.
- 탈법행위·벌칙·과징금·절차·분쟁조정 조문은 상황이 그것을 묻지 않으면 고르지 않는다.
- 최대 2개. 확신이 없으면 고르지 않는다. 법률과 무관한 요청이면 빈 목록을 낸다."""

SELECT3_SYSTEM = """너는 자동차 부품 1차 협력사(원사업자) 구매 담당자의 질문을 법적으로 검토하는 담당이다.
담당자의 상황과 이미 찾은 조문, 하도급법 목차(조문 번호와 제목)를 받는다.
상황에서 법적으로 따져야 할 쟁점을 정하고, 쟁점마다 결론을 좌우하는 하도급법 조문을 하나씩 고른다.
- 이미 찾은 조문이어도 결론을 좌우하면 고른다. 아직 못 찾은 조문은 목차에서 고른다.
- 상황 속 원사업자의 행위를 직접 금지하거나 의무를 정한 조문을 먼저 고른다.
- 그 의무가 적용되는지가 용어 정의(예: 연동 대상 주요 원재료, 하도급대금 연동)에 달려 있으면 정의 조문도 쟁점으로 고른다.
- 탈법행위·벌칙·과징금·절차·분쟁조정 조문은 상황이 그것을 묻지 않으면 고르지 않는다.
- 쟁점은 최대 3개. reason에는 상황의 어떤 사실이 그 조문에 걸리는지 한 문장으로 쓴다.
- 법률과 무관한 요청이면 빈 목록을 낸다."""

SELECT4_SYSTEM = SELECT3_SYSTEM.replace(
    "- 탈법행위·벌칙", "- 계약 조건(단가·연동·조정 방식)을 정하거나 바꾸는 상황이면 그 조건을 계약서에 적어 발급할 의무도 쟁점이 될 수 있다.\n- 탈법행위·벌칙")

ISSUE_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["issues"], "properties": {
    "issues": {"type": "array", "items": {"type": "object", "additionalProperties": False,
                                          "required": ["issue", "article_no", "reason"],
                                          "properties": {"issue": {"type": "string"},
                                                         "article_no": {"type": "string"},
                                                         "reason": {"type": "string"}}}}}}

SELECT_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["articles"], "properties": {
    "articles": {"type": "array", "items": {"type": "object", "additionalProperties": False,
                                            "required": ["article_no", "reason"],
                                            "properties": {"article_no": {"type": "string"},
                                                           "reason": {"type": "string"}}}}}}


async def add_selected(d: DecompositionResult, routing: RouteResult, chat, caller, as_of: str,
                       system: str = SELECT_SYSTEM, limit: int = 4):
    if d.status != "ok" or all(sq.intent in ("out_of_scope", "general_knowledge") for sq in d.sub_queries):
        return d, routing, []
    have = evidence_articles(routing)
    table = toc(as_of)
    titles = {t["article_no"]: t["title"] for t in table}
    user = json.dumps({"상황": d.original_query, "하위 질문": [sq.question for sq in d.sub_queries],
                       "이미 찾은 조문": [f"{no} {titles.get(no, '')}" for no in sorted(have)],
                       "하도급법 목차": [f"{t['article_no']} {t['title']}" for t in table]}, ensure_ascii=False)
    picked = await chat.structured(system, user, SELECT_SCHEMA)
    new = [no for a in (picked or {}).get("articles", [])[:limit]
           if (no := norm(a.get("article_no"))) and no in titles and no not in have]
    if not new:
        return d, routing, picked
    if len(d.sub_queries) >= 5:
        d = d.model_copy(update={"sub_queries": d.sub_queries[:4]})
        routing = routing.model_copy(update={"outcomes": routing.outcomes[:4]})
    qid = f"q{max(int(sq.id[1:]) for sq in d.sub_queries) + 1}"
    sq = SubQuery(id=qid, question="상황 판단에 더 필요한 하도급법 조문: " + ", ".join(f"{n} {titles[n]}" for n in new),
                  original_text=d.original_query, intent="law_check", grounding="required", confidence=1)
    calls = [await call(caller, "get_article", {"law": "하도급법", "article_no": no, "as_of": as_of}) for no in new]
    d = d.model_copy(update={"sub_queries": [*d.sub_queries, sq]})
    routing = RouteResult(status="routed", outcomes=[*routing.outcomes,
                                                     SubQueryOutcome(id=qid, intent="law_check", status="ok", calls=calls)])
    return d, routing, picked


async def add_issues(d: DecompositionResult, routing: RouteResult, chat, caller, as_of: str, limit: int = 3,
                     system: str = SELECT3_SYSTEM):
    """G: 쟁점 검토 노드. 고른 조문(이미 찾은 것 포함)을 쟁점 질문 하나로 다시 가져온다."""
    if d.status != "ok" or all(sq.intent in ("out_of_scope", "general_knowledge") for sq in d.sub_queries):
        return d, routing, []
    have = evidence_articles(routing)
    table = toc(as_of)
    titles = {t["article_no"]: t["title"] for t in table}
    user = json.dumps({"상황": d.original_query, "하위 질문": [sq.question for sq in d.sub_queries],
                       "이미 찾은 조문": [f"{no} {titles.get(no, '')}" for no in sorted(have)],
                       "하도급법 목차": [f"{t['article_no']} {t['title']}" for t in table]}, ensure_ascii=False)
    picked = await chat.structured(system, user, ISSUE_SCHEMA)
    issues, seen = [], set()
    for i in (picked or {}).get("issues", []):
        if (no := norm(i.get("article_no"))) and no in titles and no not in seen:
            seen.add(no)
            issues.append((no, i.get("issue", ""), i.get("reason", "")))
    issues = issues[:limit]
    picked = {"issues": (picked or {}).get("issues", []),
              "kept": [{"article_no": no, "title": titles[no], "issue": issue} for no, issue, _ in issues]}
    if not issues:
        return d, routing, picked
    if len(d.sub_queries) >= 5:
        d = d.model_copy(update={"sub_queries": d.sub_queries[:4]})
        routing = routing.model_copy(update={"outcomes": routing.outcomes[:4]})
    qid = f"q{max(int(sq.id[1:]) for sq in d.sub_queries) + 1}"
    question = "이 상황의 법적 쟁점별 검토: " + " / ".join(
        f"{issue} — {no} {titles[no]}: {reason}" for no, issue, reason in issues)
    sq = SubQuery(id=qid, question=question, original_text=d.original_query,
                  intent="law_check", grounding="required", confidence=1)
    calls = [await call(caller, "get_article", {"law": "하도급법", "article_no": no, "as_of": as_of})
             for no, _, _ in issues]
    d = d.model_copy(update={"sub_queries": [*d.sub_queries, sq]})
    routing = RouteResult(status="routed", outcomes=[*routing.outcomes,
                                                     SubQueryOutcome(id=qid, intent="law_check", status="ok", calls=calls)])
    return d, routing, picked


# ---------- H: 쟁점 목록을 생성 단계에 넘기기 ----------

MUST_ADDRESS: contextvars.ContextVar[list] = contextvars.ContextVar("must_address", default=[])

ISSUE_HINT = """

<review_issues>
쟁점 검토 단계가 이 상황의 법적 쟁점과 결론을 좌우하는 하도급법 조문을 골랐다.
답변에서 쟁점마다 한 단락 이상 다루고, 그 조문 근거(label에 조문 번호가 있는 근거)의 id를 인용한다.
정의 조문이면 상황 속 대상(원재료, 계약 등)이 그 정의에 해당하는지를 정의 조문 근거로 쓴다.
근거로 확인할 수 없는 쟁점은 "확인 필요"라고 쓴다. 이 목록은 다룰 쟁점일 뿐 결론이 아니다.
{lines}
</review_issues>"""


class HintBackend:
    """실험용: 생성 백엔드 앞에서 사용자 메시지 끝에 쟁점 목록을 덧붙인다 (재시도에도 같이 붙는다)."""

    def __init__(self, inner):
        self.inner = inner

    def __getattr__(self, name):
        return getattr(self.inner, name)

    async def generate(self, system: str, user: str) -> str | None:
        if must := MUST_ADDRESS.get():
            user += ISSUE_HINT.format(lines="\n".join(f"- {k['issue']}: 하도급법 {k['article_no']} ({k['title']})" for k in must))
        return await self.inner.generate(system, user)


# ---------- C: 도구 호출 에이전트 ----------

AGENT_SYSTEM = f"""너는 자동차 부품 1차 협력사(원사업자) 구매 담당자의 질문을 법적으로 검토하는 담당이다.
담당자가 말한 상황을 읽고, 검토해야 할 법적 쟁점을 빠짐없이 찾은 뒤 근거 조문 전문을 도구로 확보한다.

- 중심 법령은 하도급법(하도급거래 공정화에 관한 법률)이다. 필요하면 하도급법 시행령, 상생협력법, 중소기업기본법도 본다.
- search_regulation으로 후보 조문을 찾는다. 현업 표현(협력사, 단가)은 법령 용어(수급사업자, 원사업자, 하도급대금)로 바꿔 검색하면 잘 찾는다.
- 쟁점마다 결론을 좌우하는 조문은 get_article로 전문을 확인한다.{{toc_line}}
- 한 상황에 쟁점이 여러 개일 수 있다. 상황의 사실관계(언제, 누가, 무엇을)를 보고 해당할 수 있는 금지 행위·의무를 따져 본다.
- 검색 결과가 상황과 맞지 않으면 검색어를 바꿔 다시 찾는다.
- 법률과 무관한 요청이면 도구를 부르지 말고 바로 끝낸다.
- 도구는 모두 {AGENT_MAX_CALLS}번까지 부를 수 있다. 충분히 찾았으면 바로 끝낸다.

끝낼 때는 다른 말 없이 JSON 하나만 쓴다:
{{"out_of_scope": false, "issues": [{{"issue": "쟁점 한 줄", "articles": [{{"law": "하도급법", "article_no": "제4조"}}]}}]}}"""

TOC_LINE = "\n- list_articles로 하도급법 목차(조문 번호·제목)를 보고 상황에 맞는 금지 행위·의무 조문을 고를 수 있다."
TOC_TOOL = {"type": "function", "function": {
    "name": "list_articles", "description": "하도급법 조문 목차(조문 번호와 제목)를 돌려준다.",
    "parameters": {"type": "object", "properties": {}, "required": []}}}

AGENT_TOOLS = [
    {"type": "function", "function": {
        "name": "search_regulation",
        "description": "질문·키워드와 관련된 조문을 항 단위로 찾는다. 결과에 정의 조문과 공식 보조자료(심사지침 등)가 붙을 수 있다.",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string", "description": "검색어 (법령 용어를 쓰면 정확도가 높다)"},
            "laws": {"type": "array", "items": {"type": "string"},
                     "description": "검색할 법령 (예: 하도급법, 하도급법 시행령, 상생협력법). 생략하면 전체"}},
            "required": ["query"]}}},
    {"type": "function", "function": {
        "name": "get_article",
        "description": "법령과 조문 번호로 조문 전문을 항 단위로 가져온다.",
        "parameters": {"type": "object", "properties": {
            "law": {"type": "string", "description": "법령 이름 (예: 하도급법)"},
            "article_no": {"type": "string", "description": "조문 번호 (예: 제4조, 제16조의2)"}},
            "required": ["law", "article_no"]}}},
]


LAW_NAMES = {"하도급거래공정화에관한법률시행령": "하도급법 시행령", "하도급거래공정화에관한법률": "하도급법",
             "대ㆍ중소기업상생협력촉진에관한법률": "상생협력법", "대중소기업상생협력촉진에관한법률": "상생협력법",
             "중소기업기본법시행령": "중소기업기본법 시행령"}


def normalize_law(name: str) -> str:
    key = re.sub(r"[\s「」·ㆍ]", "", name or "")
    for full, short in LAW_NAMES.items():   # 긴 이름(시행령)부터 들어 있으므로 먼저 맞는 것을 쓴다
        if key.startswith(full.replace("ㆍ", "")):
            return short
    return name


def normalize_args(args: dict) -> dict:
    """3차: 모델이 쓴 정식 법령명과 '제4조 2항' 같은 조문 번호를 law-mcp가 받는 형식으로 고친다."""
    out = dict(args)
    if out.get("law"):
        out["law"] = normalize_law(out["law"])
    if out.get("laws"):
        out["laws"] = [normalize_law(x) for x in out["laws"]]
    if out.get("article_no"):
        m = re.search(r"제?\s?(\d+)\s?조(?:\s?의\s?(\d+))?", out["article_no"])
        if m:
            out["article_no"] = f"제{m.group(1)}조" + (f"의{m.group(2)}" if m.group(2) else "")
    return out


def brief(tool: str, result: Any) -> str:
    """모델에게 돌려줄 도구 결과. 토큰을 아끼려고 필요한 필드만 남긴다."""
    if tool == "search_regulation" and isinstance(result, list):
        rows = []
        for h in result:
            if h.get("document_id"):
                rows.append({"자료": h.get("title"), "본문": (h.get("text") or "")[:250]})
            else:
                rows.append({"법령": h.get("law_name"), "조문": h.get("article_no"), "제목": h.get("title"),
                             "항": h.get("paragraph"), "유형": h.get("type"), "본문": (h.get("text") or "")[:350]})
        return json.dumps(rows, ensure_ascii=False)
    if tool == "get_article" and isinstance(result, dict):
        text = "\n".join(f"{p.get('no', '')} {p.get('text', '')}" for p in result.get("paragraphs", []))
        return json.dumps({"법령": result.get("law_name"), "조문": result.get("article_no"), "제목": result.get("title"),
                           "본문": text[:3500]}, ensure_ascii=False)
    return json.dumps(result, ensure_ascii=False)[:2000]


def parse_final(text: str) -> dict:
    m = re.search(r"\{.*\}", text or "", re.S)
    try:
        return json.loads(m.group(0)) if m else {}
    except json.JSONDecodeError:
        return {}


class OpenAIChat:
    def __init__(self, model: str = AGENT_MODEL):
        import openai
        self.client, self.model = openai.AsyncOpenAI(), model

    async def __call__(self, messages: list[dict], tools: list[dict] | None) -> dict:
        with timing.span("llm", self.model) as meta:
            kwargs = {"tools": tools, "parallel_tool_calls": True} if tools else {}
            r = await self.client.chat.completions.create(model=self.model, messages=messages, temperature=0,
                                                          max_completion_tokens=1500, **kwargs)
            if r.usage:
                d = r.usage.prompt_tokens_details
                meta.update(input_tokens=r.usage.prompt_tokens, output_tokens=r.usage.completion_tokens,
                            cached_tokens=(d.cached_tokens or 0) if d else 0)
        m = r.choices[0].message
        return {"content": m.content, "tool_calls": [
            {"id": c.id, "name": c.function.name, "arguments": c.function.arguments} for c in (m.tool_calls or [])]}

    async def structured(self, system: str, user: str, schema: dict) -> dict | None:
        with timing.span("llm", self.model) as meta:
            r = await self.client.chat.completions.create(
                model=self.model, temperature=0, max_completion_tokens=600,
                messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                response_format={"type": "json_schema", "json_schema": {"name": "pick", "schema": schema, "strict": True}})
            if r.usage:
                d = r.usage.prompt_tokens_details
                meta.update(input_tokens=r.usage.prompt_tokens, output_tokens=r.usage.completion_tokens,
                            cached_tokens=(d.cached_tokens or 0) if d else 0)
        try:
            return json.loads(r.choices[0].message.content or "{}")
        except json.JSONDecodeError:
            return None


async def run_agent(query: str, chat, caller, as_of: str, with_toc: bool = False):
    system = AGENT_SYSTEM.replace("{toc_line}", TOC_LINE if with_toc else "")
    tools = [*AGENT_TOOLS, TOC_TOOL] if with_toc else AGENT_TOOLS
    messages: list[dict] = [{"role": "system", "content": system}, {"role": "user", "content": query}]
    calls: list[ToolCall] = []
    final: dict = {}
    for turn in range(AGENT_MAX_CALLS + 3):   # 목차 조회는 도구 한도에 안 세므로 턴 수도 막는다
        budget_left = AGENT_MAX_CALLS - len(calls)
        last_turn = turn == AGENT_MAX_CALLS + 2
        reply = await chat(messages, tools if budget_left > 0 and not last_turn else None)
        if not reply["tool_calls"]:
            final = parse_final(reply["content"])
            break
        messages.append({"role": "assistant", "content": reply["content"], "tool_calls": [
            {"id": c["id"], "type": "function", "function": {"name": c["name"], "arguments": c["arguments"]}}
            for c in reply["tool_calls"]]})
        for c in reply["tool_calls"]:
            try:
                args = json.loads(c["arguments"] or "{}")
            except json.JSONDecodeError:
                args = {}
            if c["name"] == "list_articles" and with_toc:
                content = json.dumps([f"{t['article_no']} {t['title']}" for t in toc(as_of)], ensure_ascii=False)
            elif len(calls) >= AGENT_MAX_CALLS or c["name"] not in ("search_regulation", "get_article"):
                content = "도구 호출 한도를 넘었다. 지금까지 찾은 것으로 끝내라." if len(calls) >= AGENT_MAX_CALLS else "없는 도구"
            else:
                args = {k: v for k, v in args.items() if k in ("query", "laws", "law", "article_no")} | {"as_of": as_of}
                args = normalize_args(args)
                tc = await call(caller, c["name"], args)
                calls.append(tc)
                content = f"오류: {tc.error}" if tc.error else brief(c["name"], tc.result)
            messages.append({"role": "tool", "tool_call_id": c["id"], "content": content})
    # 최종 답에 적었지만 전문을 안 가져온 조문은 가져온다 (근거 없이 인용할 수 없게)
    fetched = {(norm(tc.args.get("article_no"))) for tc in calls if tc.tool == "get_article" and not tc.error}
    issues = final.get("issues") or []
    for issue in issues:
        for a in issue.get("articles", []):
            no = norm(a.get("article_no"))
            if no and no not in fetched and len(fetched) < 12:
                calls.append(await call(caller, "get_article",
                                        {"law": normalize_law(a.get("law") or "하도급법"), "article_no": no, "as_of": as_of}))
                fetched.add(no)
    if final.get("out_of_scope") and not calls:
        sq = SubQuery(id="q1", question=query, original_text=query, intent="out_of_scope", grounding="none", confidence=1)
        d = DecompositionResult(status="ok", original_query=query, sub_queries=[sq])
        return d, RouteResult(status="routed", outcomes=[SubQueryOutcome(id="q1", intent="out_of_scope",
                                                                         status="out_of_scope")]), final
    if not calls:   # 아무것도 못 찾음: 원문으로 한 번은 검색한다
        calls.append(await call(caller, "search_regulation", {"query": query, "as_of": as_of}))
    question = "; ".join(i.get("issue", "") for i in issues if i.get("issue")) or query
    sq = SubQuery(id="q1", question=question[:400], original_text=query, intent="law_check", grounding="required",
                  confidence=1)
    d = DecompositionResult(status="ok", original_query=query, sub_queries=[sq])
    return d, RouteResult(status="routed", outcomes=[SubQueryOutcome(id="q1", intent="law_check", status="ok",
                                                                     calls=calls)]), final


# ---------- 채점 ----------

def evidence_articles(routing: RouteResult | None) -> set[str]:
    found = set()
    for o in (routing.outcomes if routing else []):
        for c in [*o.calls, *o.referenced_articles]:
            if c.error or c.result is None:
                continue
            for r in (c.result if isinstance(c.result, list) else [c.result]):
                if isinstance(r, dict) and r.get("law_key") == "subcontract_act" and (r.get("paragraphs") or r.get("text")):
                    if no := norm(r.get("article_no")):
                        found.add(no)
    return found


def answer_articles(answer) -> set[str]:
    """답변이 인용한 하도급법 조문: 인용 번호가 가리키는 근거 + 본문에 적은 조문 중 근거에 있는 것."""
    ev = {e.id: e for e in answer.evidence}
    in_evidence = {norm(e.meta.get("article_no")) for e in answer.evidence if e.meta.get("law_key") == "subcontract_act"}
    cited = set()
    for c in answer.citations:
        e = ev.get(c.evidence_id)
        if e and e.meta.get("law_key") == "subcontract_act" and (no := norm(e.meta.get("article_no"))):
            cited.add(no)
    for m in ARTICLE_RE.finditer(answer.text or ""):
        no = f"제{m.group(1)}조" + (f"의{m.group(2)}" if m.group(2) else "")
        if no in in_evidence:
            cited.add(no)
    return cited


def score(item: dict, ev: set[str], cited: set[str]) -> dict:
    must, allowed = set(item["must"]), set(item["allowed"]) | {"제2조"}
    return {"must_total": len(must), "evidence_hit": len(must & ev), "answer_hit": len(must & cited),
            "missed": sorted(must - cited), "extra": sorted(cited - must - allowed),
            "oos_ok": (not cited) if item["oos"] else None, "cited": sorted(cited), "evidence": sorted(ev)}


def summarize(rows: list[dict]) -> dict:
    out = {}
    for arm in [a for a in ("chat", "core", "select", "select2", "issues", "issues_cite", "issues_cite2", "node", "agent", "agent_toc") if any(r["arm"] == a for r in rows)]:
        out[arm] = {}
        for subset in ("catalog", "outside", "all"):
            rs = [r for r in rows if r["arm"] == arm and (subset == "all" or r["set"] == subset)]
            if not rs:
                continue
            total = sum(r["must_total"] for r in rs)
            oos = [r["oos_ok"] for r in rs if r["oos_ok"] is not None]
            out[arm][subset] = {
                "runs": len(rs),
                "answer_recall": round(sum(r["answer_hit"] for r in rs) / total, 3) if total else None,
                "evidence_recall": round(sum(r["evidence_hit"] for r in rs) / total, 3) if total else None,
                "all_must_cited": round(mean(r["answer_hit"] == r["must_total"] for r in rs if r["must_total"]), 3)
                if any(r["must_total"] for r in rs) else None,
                "extra_per_answer": round(mean(len(r["extra"]) for r in rs), 2),
                "oos_ok": round(mean(oos), 3) if oos else None,
                "cost_usd": round(mean(r["usage"].get("cost_usd", 0) for r in rs), 5),
                "seconds": round(mean(r["usage"].get("total", 0) for r in rs), 2),
                "llm_calls": round(mean(r["usage"].get("llm_calls", 0) for r in rs), 2),
                "tool_calls": round(mean(r["usage"].get("tool_calls", 0) for r in rs), 2),
                "errors": sum(bool(r.get("error")) for r in rs),
                "tool_errors": sum(len(r.get("tool_errors", [])) for r in rs)}
    return out


def markdown(report: dict) -> str:
    names = {"chat": "A 현재 채팅 경로", "core": "B 핵심 조문 고정", "agent": "C 도구 호출 에이전트",
             "select": "D A + 조문 선택 노드", "agent_toc": "E 에이전트 + 목차 도구",
             "select2": "F A + 조문 선택 노드(엄격, 최대 2)", "issues": "G A + 쟁점 검토 노드(최대 3)",
             "issues_cite": "H G + 생성에 쟁점 목록 전달", "issues_cite2": "I H + 서면 의무 안내",
             "node": "N 쟁점 검토 노드(issue_review)"}
    lines = [f"# 법률 에이전트 비교 실험 ({report['created']})", "",
             f"반복 {report['repeat']}회, 모델 {report['model']}, 기준일 {report['as_of']}", ""]
    for subset, title in (("catalog", "4쟁점 세트 (33문항)"), ("outside", "4쟁점 밖 세트 (15문항)"), ("all", "전체")):
        lines += [f"## {title}", "", "| 방식 | 답변 Recall | 근거 Recall | 정답 전부 인용 | 군더더기 조문/답 | 무관 질문 OK | 비용($/질문) | 시간(초) |",
                  "|---|---|---|---|---|---|---|---|"]
        for arm, s in report["summary"].items():
            if subset in s:
                v = s[subset]
                lines.append(f"| {names.get(arm, arm)} | {v['answer_recall']} | {v['evidence_recall']} | "
                             f"{v['all_must_cited']} | {v['extra_per_answer']} | {v['oos_ok'] if v['oos_ok'] is not None else '-'} | "
                             f"{v['cost_usd']} | {v['seconds']} |")
        lines.append("")
    misses: dict[str, dict[str, int]] = {}
    for r in report["rows"]:
        for a in r["missed"]:
            misses.setdefault(f"{r['arm']}:{r['id']}", {}).setdefault(a, 0)
            misses[f"{r['arm']}:{r['id']}"][a] += 1
    errs: dict[str, int] = {}
    for r in report["rows"]:
        for e in r.get("tool_errors", []):
            errs[f"{r['arm']}: {e}"] = errs.get(f"{r['arm']}: {e}", 0) + 1
    lines += ["## 도구 오류 (방식: 도구·인자: 메시지 → 횟수)", ""] + [f"- {k} → {v}" for k, v in sorted(errs.items())] + [""]
    lines += ["## 놓친 조문 (방식:문항 → 조문 횟수)", ""] + [f"- {k}: {v}" for k, v in sorted(misses.items())]
    return "\n".join(lines) + "\n"


# ---------- 가짜 모델 (--fake) ----------

class FakeChat:
    """첫 턴은 원문 검색, 둘째 턴은 검색 1위 조문 전문, 셋째 턴에 끝낸다."""

    async def __call__(self, messages, tools):
        turns = sum(m["role"] == "assistant" for m in messages)
        if tools and turns == 0:
            return {"content": None, "tool_calls": [{"id": "t1", "name": "search_regulation",
                                                     "arguments": json.dumps({"query": messages[1]["content"]})}]}
        if tools and turns == 1:
            first = json.loads(messages[-1]["content"])
            no = next((r.get("조문") for r in first if r.get("조문")), "제4조")
            return {"content": None, "tool_calls": [{"id": "t2", "name": "get_article",
                                                     "arguments": json.dumps({"law": "하도급법", "article_no": no})}]}
        return {"content": json.dumps({"out_of_scope": False, "issues": [{"issue": "가짜 쟁점", "articles": [
            {"law": "하도급법", "article_no": "제4조"}]}]}, ensure_ascii=False), "tool_calls": []}


FakeChat.structured = lambda self, system, user, schema: asyncio.sleep(0, {"issues": [
    {"issue": "가짜 쟁점", "article_no": "제2조 정의", "reason": "가짜"}]} if "issues" in schema["properties"] else {"articles": [
    {"article_no": "제13조", "reason": "가짜"}]})


class FakeIssueBackend:
    async def generate(self, system: str, user: str) -> str:
        return json.dumps({"issues": [{"issue": "가짜 쟁점", "article_no": "제2조 정의", "reason": "가짜"}]},
                          ensure_ascii=False)


class FakeDecomposer:
    async def decompose(self, query, history=None, **_):
        sq = SubQuery(id="q1", question=query, original_text=query, intent="law_check", grounding="required", confidence=1)
        return DecompositionResult(status="ok", original_query=query, sub_queries=[sq])


class FakeGenBackend:
    """근거 E1, E2를 인용한 답을 쓴다."""
    OUTPUT_MODEL = None

    async def generate(self, system: str, user: str) -> str | None:
        ids = re.findall(r'<e id="(E\d+)"[^>]*citable="yes"', user)[:2]
        return json.dumps({"answer": "검토 결과입니다. " + " ".join(f"[{i}]" for i in ids),
                           "used_evidence": ids, "suggested_questions": []})


# ---------- 실행 ----------

async def main(args):
    items = load_items()
    if args.only:
        items = [i for i in items if i["id"] in set(args.only.split(","))]
    if args.limit:
        items = items[:args.limit]
    as_of = args.as_of
    from fastmcp import Client

    from negopilot_agent.generation import AnswerGenerator
    from negopilot_agent.issue_review import IssueReviewer
    from negopilot_agent.routing.router import Router

    if args.fake:
        decomposer, chat, generator = FakeDecomposer(), FakeChat(), AnswerGenerator(backend=FakeGenBackend())
    else:
        from negopilot_agent.decomposition import DecomposerSettings, QueryDecomposer
        decomposer, chat, generator = QueryDecomposer(settings=DecomposerSettings()), OpenAIChat(), AnswerGenerator()
    hinted = AnswerGenerator(backend=HintBackend(FakeGenBackend() if args.fake else generator.backend))
    arms = args.arms.split(",")
    rows: list[dict] = []
    sem = asyncio.Semaphore(args.concurrency)

    async with Client(load_law_mcp()) as client:
        caller = make_caller(client)
        router = Router(caller)
        node_reviewer = IssueReviewer(caller, backend=FakeIssueBackend() if args.fake else None)

        async def one(item, arm, n):
            async with sem:
                row = {"id": item["id"], "set": item["set"], "arm": arm, "run": n}
                with timing.collect() as t:
                    try:
                        agent_final = None
                        if arm in ("agent", "agent_toc"):
                            d, routing, agent_final = await run_agent(item["query"], chat, caller, as_of,
                                                                      with_toc=arm == "agent_toc")
                        else:
                            d, routing = await run_chat(item["query"], decomposer, router, as_of)
                            if arm == "core":
                                d, routing = await add_core(d, routing, caller, as_of)
                            if arm == "select":
                                d, routing, agent_final = await add_selected(d, routing, chat, caller, as_of)
                            if arm == "select2":
                                d, routing, agent_final = await add_selected(d, routing, chat, caller, as_of,
                                                                             SELECT2_SYSTEM, 2)
                            if arm == "issues":
                                d, routing, agent_final = await add_issues(d, routing, chat, caller, as_of)
                            if arm in ("issues_cite", "issues_cite2"):
                                d, routing, agent_final = await add_issues(
                                    d, routing, chat, caller, as_of,
                                    system=SELECT4_SYSTEM if arm == "issues_cite2" else SELECT3_SYSTEM)
                                MUST_ADDRESS.set(agent_final.get("kept", []) if isinstance(agent_final, dict) else [])
                        gen = hinted if arm in ("issues_cite", "issues_cite2") else generator
                        if arm == "node":
                            routing, d, issues = await node_reviewer.review(d, routing)
                            agent_final = {"kept": [i.model_dump() for i in issues]}
                            answer = await generator.generate(d, routing, issues)
                        else:
                            answer = await gen.generate(d, routing)
                        row |= score(item, evidence_articles(routing), answer_articles(answer))
                        row["tool_errors"] = [f"{c.tool}{c.args.get('article_no') or c.args.get('query', '')}:{c.error[:120]}"
                                              for o in routing.outcomes for c in [*o.calls, *o.referenced_articles] if c.error]
                        row |= {"kind": answer.kind, "intents": [sq.intent for sq in d.sub_queries],
                                "agent_final": agent_final, "answer": answer.text[:1500]}
                    except Exception as e:  # noqa: BLE001 — 한 문항 실패로 전체를 멈추지 않는다
                        row |= score(item, set(), set()) | {"error": f"{type(e).__name__}: {e}"[:400]}
                row["usage"] = t.summary()
                print(f"[{arm}] {item['id']} #{n} cited={row['cited']} missed={row['missed']} extra={row['extra']}"
                      f" ${row['usage'].get('cost_usd', 0):.4f} {row['usage'].get('total', 0):.1f}s"
                      + (f" ERROR {row['error']}" if row.get("error") else ""), flush=True)
                rows.append(row)

        # 방식 순서가 결과에 섞이지 않게 반복마다 방식을 번갈아 돌린다
        for n in range(args.repeat):
            await asyncio.gather(*(one(item, arm, n) for item in items for arm in arms))

    report = {"created": datetime.now().isoformat(timespec="seconds"), "repeat": args.repeat, "as_of": as_of,
              "model": "fake" if args.fake else AGENT_MODEL, "arms": arms, "n_items": len(items),
              "summary": summarize(rows), "rows": sorted(rows, key=lambda r: (r["arm"], r["id"], r["run"]))}
    out = ROOT / "eval_results" / f"legal_agent_{'fake_' if args.fake else ''}{datetime.now():%Y%m%d_%H%M%S}"
    out.parent.mkdir(exist_ok=True)
    out.with_suffix(".json").write_text(json.dumps(report, ensure_ascii=False, indent=1))
    out.with_suffix(".md").write_text(markdown(report))
    print(json.dumps(report["summary"], ensure_ascii=False, indent=1))
    print(f"saved {out}.json / .md")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--arms", default="chat,core,agent")
    p.add_argument("--repeat", type=int, default=1)
    p.add_argument("--limit", type=int)
    p.add_argument("--only", help="문항 id 쉼표 목록")
    p.add_argument("--as-of", default="2026-10-23")
    p.add_argument("--concurrency", type=int, default=4)
    p.add_argument("--fake", action="store_true", help="모델 없이 배선 점검")
    a = p.parse_args()
    if not a.fake:
        from ask import load_env
        load_env()
    asyncio.run(main(a))
