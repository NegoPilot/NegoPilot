"""Router: 분해 결과를 parallel_groups() 순서로 실행한다.

같은 묶음의 하위 질문은 병렬로, 다음 묶음은 앞 묶음이 끝난 뒤 실행한다.
도구를 실제로 부르는 일은 ToolCaller가 맡아서, 테스트에서는 가짜로 바꿔 끼운다.
"""

import asyncio
import contextlib
import itertools
import re
from typing import Any, Protocol

from negopilot_shared import (DecompositionResult, IntentSpec, RouteResult, SubQueryOutcome, ToolCall, load_intents,
                              public_error_code)
from negopilot_shared.decomposition import SubQuery

from negopilot_agent import timing, tracing
from negopilot_agent.routing.extract import select_values

MAX_FANOUT = 10      # top(n)/all로 값이 여러 개일 때 하위 질문 하나가 부를 수 있는 최대 호출 수
LAW_ARTICLES = 3     # law_check에서 search_regulation 결과 중 get_article로 전문을 가져올 조문 수
REFERENCED_ARTICLES = 5   # 결과의 law_refs 중 get_article로 미리 조회할 최대 조문 수
SUPPLIER_MATCH_LIMIT = 10
SUPPLIER_ID = re.compile(r"^SUP-[0-9]{4,}$", re.IGNORECASE)

# 도구 인자 = 엔티티 이름 (계약 결정 3). 이름이 다른 것만 바꿔 적는다
TOOL_PARAMS: dict[str, dict[str, str]] = {
    "list_rfqs": {},
    "search_suppliers": {"process": "process", "material": "material", "region": "region",
                         "iatf_required": "iatf_required", "min_monthly_capacity": "min_monthly_capacity",
                         "top_n": "limit"},
    "get_supplier": {"supplier": "supplier"},
    "rank_suppliers": {"rfq_id": "rfq_id", "top_n": "top_n"},
    "check_offer": {k: k for k in ("supplier", "rfq_id", "unit_price", "payment_days", "price_linkage",
                                   "raw_material_ratio", "energy_ratio", "contract_days",
                                   "linkage_waived_by_agreement", "buyer_is_small_business", "as_of")},
    "get_article": {"law": "law", "article_no": "article_no", "as_of": "as_of"},
}

# 도구의 필수 인자 (엔티티 이름). 없으면 부르지 않고 missing_args로 Generation에 넘긴다
REQUIRED: dict[str, list[str]] = {
    "get_supplier": ["supplier"],
    "rank_suppliers": ["rfq_id"],
    "check_offer": ["supplier", "rfq_id", "unit_price", "payment_days", "price_linkage"],
    "get_article": ["law", "article_no"],
}


class ToolCaller(Protocol):
    async def __call__(self, server: str, tool: str, args: dict[str, Any]) -> Any:
        """도구를 부르고 결과를 돌려준다. 실패하면 예외를 낸다."""
        ...


class Router:
    def __init__(self, caller: ToolCaller, intents: dict[str, IntentSpec] | None = None):
        self.caller = caller
        self.intents = intents or load_intents()

    async def route(self, decomposition: DecompositionResult) -> RouteResult:
        if decomposition.status != "ok":
            return RouteResult(status="not_routed")

        # caller가 연결 재사용(session)을 지원하면 route() 한 번 동안 서버별 연결 하나를 같이 쓴다
        session = getattr(self.caller, "session", None)
        async with session() if session else contextlib.nullcontext():
            return await self._route(decomposition)

    async def _route(self, decomposition: DecompositionResult) -> RouteResult:
        by_id = {sq.id: sq for sq in decomposition.sub_queries}
        done: dict[str, SubQueryOutcome] = {}
        halt_reason: str | None = None

        for group in decomposition.parallel_groups():
            if halt_reason:
                for qid in group:
                    done[qid] = SubQueryOutcome(id=qid, intent=by_id[qid].intent, status="skipped",
                                                message="앞 결과가 0건이라 실행을 멈춤")
                continue
            outcomes = await asyncio.gather(*(self._run(by_id[qid], done) for qid in group))
            for o in outcomes:
                done[o.id] = o
                # 조회 자체가 0건이면 그 결과에 의존하는 질문만 건너뛴다.
                # inputs 선택에서 값이 없는 경우의 기존 전체 중단 계약은 유지한다.
                if o.status == "empty" and not o.calls and halt_reason is None:
                    halt_reason = f"{o.id}: {o.message}"

        return RouteResult(status="routed", outcomes=[done[sq.id] for sq in decomposition.sub_queries],
                           halted=halt_reason is not None, halt_reason=halt_reason)

    async def _run(self, sq: SubQuery, done: dict[str, SubQueryOutcome]) -> SubQueryOutcome:
        spec = self.intents[sq.intent]
        if spec.status == "planned":
            return SubQueryOutcome(id=sq.id, intent=sq.intent, status="unsupported",
                                   message=f"{spec.server or sq.intent}가 아직 없어 지원하지 않는 기능")
        if spec.server is None or not spec.tools:
            # 도구 없는 intent는 general_knowledge, out_of_scope 둘뿐이다 (intents.yaml)
            status = "out_of_scope" if sq.intent == "out_of_scope" else "general_knowledge"
            return SubQueryOutcome(id=sq.id, intent=sq.intent, status=status)

        if bad := [d for d in sq.depends_on if done[d].status != "ok"]:
            return SubQueryOutcome(id=sq.id, intent=sq.intent, status="skipped",
                                   message=f"앞 하위 질문 {bad}의 결과가 없어 실행하지 않음")

        # 엔티티(값 하나)와 inputs(앞 결과에서 꺼낸 값 여러 개)를 합쳐 호출 조합을 만든다
        fixed = sq.entities.model_dump(exclude_none=True)
        fixed.pop("price_change_pct", None)          # 판단 근거일 뿐 도구 인자가 아니다
        choices: dict[str, list[Any]] = {}
        for ref in sq.inputs:
            src = done[ref.from_]
            values = select_values(src.intent, src.calls, ref.slot, ref.select)
            if not values:
                return SubQueryOutcome(id=sq.id, intent=sq.intent, status="empty",
                                       message=f"{ref.from_} 결과에서 {ref.slot}({ref.select})를 찾지 못함")
            choices[ref.slot] = values
        combos = [fixed | dict(zip(choices, vals)) for vals in itertools.product(*choices.values())]
        combos = combos[:MAX_FANOUT]

        if sq.intent == "law_check":
            return await self._law_check(sq, spec, combos[0])

        tool = spec.tools[0]
        if missing := [k for k in REQUIRED.get(tool, []) if k not in combos[0]]:
            return SubQueryOutcome(id=sq.id, intent=sq.intent, status="missing_args", missing=missing,
                                   message=f"{tool} 필수 인자 없음: {', '.join(missing)}")
        if sq.intent == "supplier_detail":
            return await self._supplier_detail(sq, spec, combos)
        if sq.intent == "offer_check":
            return await self._offer_check(sq, spec, tool, combos)
        calls = list(await asyncio.gather(*(self._call(spec.server, tool, self._args(tool, c)) for c in combos)))
        outcome = self._outcome(sq, calls)
        if outcome.status == "ok":
            outcome.referenced_articles = await self._referenced_articles(calls)
        return outcome

    async def _resolve_suppliers(self, sq: SubQuery, spec: IntentSpec, combos: list[dict[str, Any]]
                                 ) -> tuple[list[str], list[ToolCall], SubQueryOutcome | None]:
        """조합마다 supplier를 ID 하나로 확정한다. 이름은 find_suppliers로 찾는다.

        확정하지 못하면(이름 없음, 후보 0곳 또는 여러 곳, 조회 실패) 바로 돌려줄 outcome을 세 번째 값으로 준다.
        """
        calls: list[ToolCall] = []
        resolved: list[str] = []
        for combo in combos:
            supplier = combo["supplier"]
            if not isinstance(supplier, str) or not supplier.strip():
                return resolved, calls, SubQueryOutcome(id=sq.id, intent=sq.intent, status="missing_args",
                                                        missing=["supplier"], calls=calls)
            # 기존 HTTP 문맥의 SUP- 접두 ID는 여기서 형식을 더 제한하지 않는다.
            # 실제 존재 여부와 ID 해석은 뒤에 부르는 도구(get_supplier, check_offer)가 검증한다.
            if supplier.strip().upper().startswith("SUP-"):
                resolved.append(supplier)
                continue
            search = await self._call(spec.server, "find_suppliers",
                                      {"supplier": supplier, "limit": SUPPLIER_MATCH_LIMIT})
            calls.append(search)
            if search.error:
                return resolved, calls, self._outcome(sq, calls)
            result = search.result
            if not self._valid_supplier_matches(result):
                search.error = "Invalid supplier lookup response."
                return resolved, calls, self._outcome(sq, calls)
            if result["total"] == 0:
                return resolved, calls, SubQueryOutcome(id=sq.id, intent=sq.intent, status="empty", calls=calls,
                                                        message="이름에 맞는 협력사가 없음")
            if result["total"] > 1:
                return resolved, calls, SubQueryOutcome(id=sq.id, intent=sq.intent, status="missing_args",
                                                        calls=calls, missing=["supplier"],
                                                        message="동일 이름의 후보 중 대상 선택 필요")
            resolved.append(result["matches"][0]["supplier_id"])
        return resolved, calls, None

    async def _offer_check(self, sq: SubQuery, spec: IntentSpec, tool: str,
                           combos: list[dict[str, Any]]) -> SubQueryOutcome:
        """협력사를 확정한 뒤 협상안을 검사한다. 이름이 여러 곳에 맞으면 검사하지 않고 후보를 되묻는다 (#129)."""
        resolved, calls, stop = await self._resolve_suppliers(sq, spec, combos)
        if stop:
            return stop
        checks = list(await asyncio.gather(*(
            self._call(spec.server, tool, self._args(tool, combo | {"supplier": supplier}))
            for combo, supplier in zip(combos, resolved))))
        calls += checks
        outcome = self._outcome(sq, calls)
        if outcome.status == "ok":
            outcome.referenced_articles = await self._referenced_articles(checks)
        return outcome

    async def _supplier_detail(self, sq: SubQuery, spec: IntentSpec,
                               combos: list[dict[str, Any]]) -> SubQueryOutcome:
        """이름은 구조화된 후보 조회로 확정하고 ID로 상세를 조회한다."""
        resolved, calls, stop = await self._resolve_suppliers(sq, spec, combos)
        if stop:
            return stop
        details = list(await asyncio.gather(*(
            self._call(spec.server, "get_supplier", {"supplier": supplier}) for supplier in resolved)))
        for detail in details:
            if detail.error is None and not self._valid_supplier_detail(detail.result):
                detail.error = "Invalid supplier detail response."
        calls += details
        successful = [detail for detail in details if detail.error is None]
        # 상세 fan-out의 일부 실패가 확인한 다른 기업의 근거까지 버리게 하지 않는다.
        # 유효한 get_supplier 성공이 하나도 없으면 기존 error 처리를 유지한다.
        outcome = (SubQueryOutcome(id=sq.id, intent=sq.intent, status="ok", calls=calls)
                   if successful else self._outcome(sq, calls))
        if successful:
            outcome.referenced_articles = await self._referenced_articles(successful)
        return outcome

    @staticmethod
    def _valid_supplier_detail(result: Any) -> bool:
        return (isinstance(result, dict)
                and all(isinstance(result.get(key), str) and result[key].strip()
                        for key in ("supplier_id", "name")))

    @staticmethod
    def _valid_supplier_matches(result: Any) -> bool:
        if not isinstance(result, dict):
            return False
        matches, total = result.get("matches"), result.get("total")
        if (not isinstance(matches, list) or type(total) is not int or total < 0
                or not isinstance(result.get("match_type"), str)
                or result["match_type"] not in {"id", "exact", "partial"}
                or len(matches) > SUPPLIER_MATCH_LIMIT or total < len(matches)
                or bool(total) != bool(matches)):
            return False
        ids: set[str] = set()
        for match in matches:
            if (not isinstance(match, dict) or not isinstance(match.get("supplier_id"), str)
                    or not SUPPLIER_ID.fullmatch(match["supplier_id"])
                    or not isinstance(match.get("name"), str) or not match["name"].strip()
                    or match["supplier_id"] in ids):
                return False
            ids.add(match["supplier_id"])
        return True

    async def _law_check(self, sq: SubQuery, spec: IntentSpec, ents: dict[str, Any]) -> SubQueryOutcome:
        """search_regulation(질문으로 검색) → 찾은 조문을 get_article로 전문 조회."""
        args: dict[str, Any] = {"query": sq.question}
        if "law" in ents:
            args["laws"] = [ents["law"]]
        if "as_of" in ents:
            args["as_of"] = ents["as_of"]
        search = await self._call(spec.server, "search_regulation", args)
        if search.error:
            return self._outcome(sq, [search])
        if not isinstance(search.result, list) or any(not isinstance(hit, dict) for hit in search.result):
            search.error = "Invalid regulation search response."
            return self._outcome(sq, [search])
        if not search.result:
            return self._outcome(sq, [search])

        articles: list[tuple[str, str]] = []
        for hit in search.result or []:
            key = (hit.get("law_key"), hit.get("article_no"))
            if hit.get("type") == "조문" and all(key) and key not in articles:
                articles.append(key)
        article_calls = await asyncio.gather(*(
            self._call(spec.server, "get_article",
                       {"law": law, "article_no": no} | ({"as_of": ents["as_of"]} if "as_of" in ents else {}))
            for law, no in articles[:LAW_ARTICLES]))
        # 검색 근거는 이미 확보했다. 조문 전문 보충 실패가 그 근거를 버리게 하지 않는다.
        return SubQueryOutcome(id=sq.id, intent=sq.intent, status="ok", calls=[search, *article_calls])

    async def _referenced_articles(self, calls: list[ToolCall]) -> list[ToolCall]:
        """결과에 붙은 law_refs({law, article_no})를 get_article로 미리 조회한다.

        rank_suppliers: ranked[*].flags[*].law_refs, check_offer: law_refs.
        check_offer에 계약 시점(as_of)을 넘겼으면 같은 날 시행판의 조문을 가져온다 (check_offer v2).
        같은 조문이 항별로 여러 번 나와도 조문 전문은 한 번만 조회한다. 항은 Generation이 근거를 고를 때 쓴다.
        조회 실패는 하위 질문을 실패로 만들지 않고 그 ToolCall의 error로만 남긴다.
        """
        refs: list[tuple[str, str, str | None]] = []

        def add(items, as_of: str | None) -> None:
            for r in items or []:
                key = (r.get("law"), r.get("article_no"), as_of)
                if all(key[:2]) and key not in refs:
                    refs.append(key)

        for c in calls:
            if not isinstance(c.result, dict):
                continue
            as_of = c.args.get("as_of")
            add(c.result.get("law_refs"), as_of)
            for cand in c.result.get("ranked") or []:
                for flag in cand.get("flags") or []:
                    add(flag.get("law_refs"), as_of)
        return list(await asyncio.gather(*(
            self._call("law-mcp", "get_article",
                       {"law": law, "article_no": no} | ({"as_of": as_of} if as_of else {}))
            for law, no, as_of in refs[:REFERENCED_ARTICLES])))

    @staticmethod
    def _args(tool: str, ents: dict[str, Any]) -> dict[str, Any]:
        params = TOOL_PARAMS.get(tool, {})
        args = {params[k]: v for k, v in ents.items() if k in params}
        if tool == "search_suppliers" and "top_n" in ents:
            # 사용자가 요청한 개수는 capability 행이 아닌 서로 다른 기업 수다.
            args["distinct_suppliers"] = True
        return args

    async def _call(self, server: str, tool: str, args: dict[str, Any]) -> ToolCall:
        try:
            with timing.span("tool", f"{server}.{tool}"), tracing.tool_span(server, tool, args) as record:
                result = await self.caller(server, tool, args)
                record(result)
        except Exception as e:  # noqa: BLE001  도구 오류도 결과로 Generation에 넘긴다
            return ToolCall(server=server, tool=tool, args=args, error=f"{type(e).__name__}: {e}",
                            error_code=public_error_code(str(e)))
        return ToolCall(server=server, tool=tool, args=args, result=result)

    @staticmethod
    def _outcome(sq: SubQuery, calls: list[ToolCall]) -> SubQueryOutcome:
        errors = [c.error for c in calls if c.error]
        if errors:
            return SubQueryOutcome(id=sq.id, intent=sq.intent, status="error", calls=calls, message=errors[0])
        if calls and all(c.tool in {"search_suppliers", "list_rfqs", "search_regulation"}
                         and c.result == [] for c in calls):
            return SubQueryOutcome(id=sq.id, intent=sq.intent, status="empty", calls=calls,
                                   message="조회 조건에 맞는 결과가 없음")
        return SubQueryOutcome(id=sq.id, intent=sq.intent, status="ok", calls=calls)
