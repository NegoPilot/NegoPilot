# Routing 결과 계약 (v0.1)

Routing이 하위 질문별 MCP 도구 호출 결과를 담아 Generation에 넘기는 형식입니다. **Routing이 만들고, Generation이 받아서 씁니다.**
이 문서와 아래 코드가 같은 내용을 담고 있고, 둘이 다르면 코드가 기준입니다.

| 파일 | 내용 |
| --- | --- |
| [`shared/src/negopilot_shared/routing.py`](../../shared/src/negopilot_shared/routing.py) | 출력 스키마 (Pydantic). `RouteResult`, `SubQueryOutcome`, `ToolCall` |
| [`shared/fixtures/routing/`](../../shared/fixtures/routing/) | 분해 fixtures 11개를 실제 business-mcp·law-mcp로 Routing한 결과. **Generation 개발의 입력 데이터** |
| [`agent/src/negopilot_agent/routing/`](../../agent/src/negopilot_agent/routing/) | Routing 구현 |

```python
from negopilot_shared import DecompositionResult, RouteResult

routing = RouteResult.model_validate(data["routing"])   # 형식이 틀리면 ValidationError
for sq in decomposition.sub_queries:
    outcome = routing.outcome(sq.id)                     # 하위 질문은 id로 맞춘다
```

## 1. 결정 사항

| # | 안건 | 결정 |
| --- | --- | --- |
| 1 | Generation 입력 | LangGraph 상태의 **`decomposition`과 `routing`을 둘 다** 받는다. 질문·`assumptions`·되묻기는 분해 결과에, 도구 결과는 Routing 결과에 있다 |
| 2 | 도구 없는 intent | status를 **`general_knowledge`와 `out_of_scope`로 나눈다**. Generation은 status만 보고 답할지 거절할지 정한다 |
| 3 | 일부만 성공 | **된 부분은 답하고, 안 된 부분만 밝힌다.** 되묻기·안내는 답변 끝에 모은다 |
| 4 | 도구 결과 | `calls[].result`는 **MCP 도구 반환값 그대로**. 프롬프트가 너무 길어지면 Routing이 intent별로 필드를 추리는 방식으로 바꾼다 |
| 5 | 법 근거 인용 | `search_regulation` 결과는 항 단위 citation을 인용한다. 해당 항에 없는 설명은 출처와 본문을 확인한 `get_article` 조문 전문을 인용할 수 있다 |
| 6 | 결과에 붙은 조문 | `rank_suppliers`(flags)·`check_offer`의 `law_refs`는 **Routing이 `get_article`로 미리 조회**해 `referenced_articles`에 넣는다 (최대 5개) |
| 7 | 사용자 문구 | 사용자에게 보일 문장은 **Generation이 쓴다.** `message`·`halt_reason`·`error`는 개발·로그용이라 그대로 보여 주지 않는다 |
| 8 | 형식 버전 | `schema_version` 필드. 형식 변경은 PR + Routing·Generation 담당 승인 |

## 2. 출력 스키마

```json
{
  "schema_version": "0.1",
  "status": "routed",
  "halted": false,
  "halt_reason": null,
  "outcomes": [
    {
      "id": "q1",
      "intent": "offer_check",
      "status": "ok",
      "calls": [
        {"server": "business-mcp", "tool": "check_offer",
         "args": {"supplier": "SUP-0003", "rfq_id": "RFQ-2026-001", "unit_price": 12000, "payment_days": 75, "price_linkage": false},
         "result": {"verdict": "위반 소지 있음", "violations": ["..."], "law_refs": ["..."]}, "error": null}
      ],
      "referenced_articles": [
        {"server": "law-mcp", "tool": "get_article", "args": {"law": "subcontract_act", "article_no": "제13조"},
         "result": {"citation": "「하도급거래 공정화에 관한 법률」 제13조(하도급대금의 지급 등) [시행 2026-10-02]", "...": "..."}, "error": null}
      ],
      "missing": [],
      "message": null
    }
  ]
}
```

| 필드 | 규칙 |
| --- | --- |
| `status` | `routed`: 하위 질문을 실행함 (outcomes 1개 이상) · `not_routed`: 분해가 `clarify`/`failed`라 실행할 것 없음 (outcomes 비어 있음) |
| `outcomes` | 분해 `sub_queries` 순서. `id`는 하위 질문 id와 같다 |
| `halted`, `halt_reason` | inputs에서 필요한 값을 추출하지 못해 이후 그룹을 중단하면 `true`와 이유를 담는다. 조회 도구 자체의 0건은 전역 중단하지 않는다 |
| `calls` | 이 하위 질문에 답하려고 부른 도구. `top(n)`·`all`이면 같은 도구를 여러 번 (최대 10번). `law_check`는 `search_regulation` 1번 + `get_article` 최대 3번 |
| `referenced_articles` | 결과의 `law_refs`를 미리 조회한 `get_article`. 조회가 실패해도 하위 질문은 `ok`이고 그 항목의 `error`만 채워진다 |
| `missing` | `missing_args`일 때 확정되지 않은 엔티티 이름. 기업명 후보가 여러 곳이면 `supplier`이며 `calls`에 후보 조회 결과가 남는다 |

## 3. status별 Generation 처리

| status | 뜻 | Generation |
| --- | --- | --- |
| (Routing `not_routed`) | 분해가 `clarify`/`failed` | Routing 결과는 보지 않고 `decomposition.status`로 처리 (되묻기 선택지 / 다시 질문 요청) |
| `ok` | 도구 결과 있음 | 도구 결과로 답하고 근거 인용 (1절 5·6) |
| `general_knowledge` | 개념 설명 | 도구 없이 답하고 **"근거 자료 없음"** 표시. 조문 번호·숫자·회사 정보는 지우거나 "확인 필요" |
| `out_of_scope` | 업무와 무관 | 정중히 거절하고 할 수 있는 일 안내 |
| `unsupported` | `status: planned` intent (`draft_email`) | 아직 지원하지 않는 기능이라고 안내 |
| `missing_args` | 필수 인자가 없거나 기업명 후보 중 대상이 미확정 | 필요한 항목만 묻는다. 구조화된 기업 후보는 `Answer.options`로 제시한다 |
| `empty` | 조회 결과가 0건이거나 inputs에서 필요한 값을 찾을 수 없음 | 결과가 없다고 안내한다. 해당 조회만으로 생성 모델을 호출하지 않는다 |
| `skipped` | 앞 하위 질문이 `ok`가 아님 | 따로 말하지 않거나 짧게 언급 |
| `error` | 도구 호출 실패 | 그 부분만 "자료를 가져오지 못했습니다" 같은 일반 문구. 오류 원문은 로그에만 |

## 4. 도구별 `result` 모양

| 도구 | 반환 | 인용할 것 |
| --- | --- | --- |
| `list_rfqs` | `[{rfq_id, oem_name, vehicle, part_name, process, material, annual_volume, target_price, required_cert, due_date}]` (마감일 순) | RFQ ID |
| `search_suppliers` | `[{supplier_id, name, region, defect_ppm, on_time_rate, ...}]` (불량률 순) | 업체 ID |
| `find_suppliers` | `{matches: [{supplier_id, name, region}], total, match_type}`. 정확한 이름 일치를 우선하며 total은 반환 제한 전 기업 수 | 기업 구분에만 사용한다. 상세 정보 근거로 만들지 않는다 |
| `get_supplier` | `{supplier_id, name, source(DART/MOCK), ..., capabilities, financials, recent_quotes}` | 업체 ID, `source` |
| `rank_suppliers` | `{rfq, ranked: [{supplier_id, name, total_score, notes, flags}], excluded: [{supplier_id, name, reason}], ...}` | 업체 ID, 제외 사유 |
| `check_offer` | `{verdict, supplier, rfq, offer, violations, warnings, info, law_refs}` (각 항목 `{code, severity, message, law_refs}`) | `verdict`, 항목 `message`, `referenced_articles`의 `citation` |
| `search_regulation` | `[{law_name, article_no, paragraph, title, text, citation, effective_date, source_url, score}]` | **`citation` 그대로** |
| `get_article` | `{law_name, article_no, title, paragraphs, citation, effective_date, source_url, upcoming_changes}` | 설명 참고, `citation` |

## 직접 질문의 결과 처리

이름으로 기업 상세를 요청하면 `find_suppliers`를 호출한다. 한 곳이면 확인된 ID로 상세를 조회한다. 여러 곳이면 `missing_args`로 대상만 묻는다. 후보가 없으면 `empty`다. 예외 원문에서 기업 이름이나 후보 ID를 추출하지 않는다.

`search_suppliers`와 `list_rfqs` 및 `search_regulation`의 빈 목록은 조회 결과 없음이다. 이때 `calls`가 있는 `empty`로 표시한다. 그 결과를 필요로 하는 질문은 건너뛰며 독립 질문은 계속한다. inputs의 선택어로 필요한 값을 추출하지 못하는 기존 `empty`는 호출 기록이 없으며 이전 전역 중단 정책을 유지한다.

`law_check`의 검색이 성공하면 후속 `get_article` 일부가 실패해도 성공한 검색 근거를 유지한다. outcome은 `ok`이며 실패한 호출에는 정제된 error가 남는다. Generation이 전문 조회 실패를 안내한다. 성공한 전문 조회에 비어 있지 않은 citation과 본문 및 호스트가 있는 HTTP(S) source_url이 있으면 전체 조문을 인용할 수 있다. 이 출처 조건을 충족하지 못한 참고 근거는 `citable=False`로 유지한다. 도구 전체 실패는 사용자 정보 부족으로 처리하지 않는다.

`supplier_detail`의 여러 상세 호출 중 유효한 결과가 하나라도 있으면 그 근거로 답하고 실패한 상세를 안내한다. 유효한 상세가 전혀 없으면 `error`다. 기업명 확인에만 사용한 `find_suppliers`는 상세 근거와 후속 inputs 추출에서 제외한다.

자세한 질문 예시와 HTTP 사용법은 [직접 질문 계약](direct-chat.md)에 있다.

## 5. 아직 정하지 않은 것

- 결과 길이 제한(조문 전문이 여러 개일 때)을 Routing이 할지 Generation이 할지
- `missing` 엔티티 이름을 사용자 말로 바꾸는 이름표를 어디에 둘지
- `get_article`의 `upcoming_changes`(개정 예정)를 답변에 알릴지
- 같은 도구를 여러 번 부른 결과(`top(n)`)를 표로 보여 줄지 문장으로 풀지

## 6. 변경 절차

1. `routing.py`, `fixtures/routing`, 이 문서를 **한 PR에서 같이** 고친다. fixtures는 `agent/scripts/route_fixtures.py --save`로 다시 만든다.
2. `shared/`와 `agent/` 테스트가 통과해야 한다.
3. Routing·Generation 담당 승인 후 머지한다.
4. 필드를 지우거나 의미를 바꾸면 `schema_version`을 올린다.
