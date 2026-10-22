# 쿼리 분해 결과 계약 (v0.1)

사용자 질문을 하위 질문으로 나눈 결과의 형식입니다. **쿼리 분해(Agent)가 만들고, Routing과 Generation이 받아서 씁니다.**
이 문서와 아래 코드가 같은 내용을 담고 있고, 둘이 다르면 코드가 기준입니다.

| 파일 | 내용 |
| --- | --- |
| [`shared/src/negopilot_shared/decomposition.py`](../../shared/src/negopilot_shared/decomposition.py) | 출력 스키마 (Pydantic). 받은 값을 이걸로 검증한다 |
| [`shared/src/negopilot_shared/intents.yaml`](../../shared/src/negopilot_shared/intents.yaml) | intent 등록표. 분해와 Routing이 같이 읽는다 |
| [`shared/fixtures/decomposition/`](../../shared/fixtures/decomposition/) | 질문별 기대 분해 결과 11개. 분해가 완성되기 전에도 Routing·Generation은 이걸로 개발·테스트한다 |
| [`shared/tests/test_contract.py`](../../shared/tests/test_contract.py) | 예시가 스키마를 통과하는지, 금지한 형태를 스키마가 막는지 |

```python
from negopilot_shared import DecompositionResult, load_intents

result = DecompositionResult.model_validate(data)   # 형식이 틀리면 ValidationError
for group in result.parallel_groups():              # [["q1", "q2"], ["q3"]] → 같은 묶음은 병렬 실행
    ...
```

## 1. 결정 사항

| # | 안건 | 결정 |
| --- | --- | --- |
| 1 | intent는 누가 붙이나 | **분해 단계**에서 붙인다 |
| 2 | intent 단위와 관리 | **도구 단위.** 등록표 `intents.yaml` 하나를 분해와 Routing이 같이 읽는다 |
| 3 | 엔티티 이름 | **MCP 도구 인자명과 같게** 쓴다 (`rfq_id`, `supplier`, `unit_price` ...) |
| 4 | 의존 관계 | `depends_on` 목록. 비어 있으면 병렬 실행 가능. **앞에 나온 하위 질문만** 가리킬 수 있다 |
| 5 | 앞 결과를 뒤 질문에 넣기 | 분해는 `inputs`에 **정해진 선택어**만 쓴다. 실제 값을 꺼내는 일은 **Routing의 결과 대응표(코드)**가 한다 |
| 6 | 정보가 부족할 때 | 질문과 관련 이력 및 조회로 해결할 수 없는 필수 정보만 되묻는다. 분해 단계의 선택지는 2~4개다 |
| 7 | 도구 없이 답할 질문 | 아래 [5. 근거와 되묻기 규칙](#5-근거와-되묻기-규칙) |
| 8 | 용어 정규화 | `question`은 **법률·코드 용어로 정규화**하고, 원래 표현은 `original_text`에 보존한다 |
| 9 | 분해 실패 | 스키마 검증 실패 시 오류를 알려주고 **1회 재시도**. 그래도 실패하면 `status: "failed"`. **추측해서 넘기지 않는다** |
| 10 | 형식 버전 | `schema_version` 필드. 형식 변경은 PR + 관련자 전원 승인 |

## 2. 출력 스키마

```json
{
  "schema_version": "0.1",
  "status": "ok",
  "original_query": "최근 RFQ 협력사 추천해줘",
  "sub_queries": [
    {
      "id": "q1",
      "question": "견적 요청(RFQ) 목록",
      "original_text": "최근 RFQ",
      "intent": "rfq_lookup",
      "grounding": "required",
      "confidence": 0.8,
      "entities": {},
      "inputs": [],
      "depends_on": []
    },
    {
      "id": "q2",
      "question": "선택된 RFQ에 맞는 2차 협력사 순위",
      "original_text": "협력사 추천해줘",
      "intent": "supplier_ranking",
      "grounding": "required",
      "confidence": 0.8,
      "entities": {},
      "inputs": [{"slot": "rfq_id", "from": "q1", "select": "top1"}],
      "depends_on": ["q1"]
    }
  ],
  "clarification": null,
  "assumptions": ["RFQ 목록의 첫 번째(마감이 가장 가까운) RFQ 기준"]
}
```

### 최상위 필드

| 필드 | 타입 | 규칙 |
| --- | --- | --- |
| `schema_version` | `"0.1"` | |
| `status` | `ok` / `clarify` / `failed` | `ok`: 하위 질문 1~5개, clarification 없음 · `clarify`: clarification만 있음 · `failed`: 둘 다 없음 |
| `original_query` | string | 사용자 원문 |
| `sub_queries` | list | 최대 **5개**. 더 필요하면 되묻는다 |
| `clarification` | `{reason, question, options}` | options는 2~4개 |
| `assumptions` | list[string] | 되묻지 않고 가정하고 진행했을 때 그 가정. Generation이 답변에 밝힌다 |

### 하위 질문 필드

| 필드 | 타입 | 규칙 |
| --- | --- | --- |
| `id` | `q1`, `q2` ... | 중복 불가 |
| `question` | string | 도구 하나로 답할 크기. 법률·코드 용어로 정규화 |
| `original_text` | string | 원문에서 해당 부분 |
| `intent` | 등록표 값 | |
| `grounding` | `required` / `none` | **등록표의 intent 값과 같아야 한다** |
| `confidence` | 0~1 | 0.6 미만이면 되묻기 후보 |
| `entities` | object | **엔티티 사전에 있는 이름만**, 그리고 **그 intent가 허용한 것만** |
| `inputs` | list | `{slot, from, select}`. `from`은 `depends_on`에도 있어야 한다 |
| `depends_on` | list | 앞에 나온 하위 질문 id만 |

## 3. intent 등록표

| intent | 서버 | 도구 | 엔티티 |
| --- | --- | --- | --- |
| `rfq_lookup` | business-mcp | `list_rfqs` | – |
| `supplier_search` | business-mcp | `search_suppliers` | process, material, region, iatf_required, min_monthly_capacity, top_n |
| `supplier_detail` | business-mcp | 기업명은 `find_suppliers` → 유일 후보의 `get_supplier`. 명시 ID는 `get_supplier` | supplier |
| `supplier_ranking` | business-mcp | `rank_suppliers` | rfq_id, top_n |
| `offer_check` | business-mcp | `check_offer` | supplier, rfq_id, unit_price, payment_days, price_linkage, raw_material_ratio, energy_ratio, contract_days, linkage_waived_by_agreement, buyer_is_small_business, as_of |
| `law_check` | law-mcp | `search_regulation` → `get_article` | law, as_of, price_change_pct |
| `law_article` | law-mcp | `get_article` | law, article_no, as_of |
| `draft_email` | mail-mcp | (예정) | supplier, rfq_id |
| `general_knowledge` | – | – | – |
| `out_of_scope` | – | – | – |

- `price_change_pct`는 스키마상 모든 intent에 허용하지만, 지금은 Routing·Generation 어디서도 쓰지 않아 **분해가 뽑지 않는다**. 증감률은 `question`에 남긴다 (#62).
- `law`는 사용자가 법령 이름(약칭 포함)을 말했을 때만 넣는다. 비우면 law-mcp가 모든 법령에서 검색한다 (#62).
- `draft_email`은 `status: planned`. 분해 결과에 나올 수 있고, mail-mcp가 생기기 전까지는 Routing이 "아직 지원하지 않는 기능"으로 처리한다.
- **`offer_check`와 `law_check` 구분:** 특정 거래의 협상안 전체에 대한 규칙 검사는 `offer_check`다. 일반적인 행위나 조건의 법적 허용 여부는 숫자가 있어도 `law_check`로 조회한다. 숫자가 있다는 이유로 기업과 RFQ 및 단가를 요구하지 않는다.
- `supplier_search`는 공정이 없어도 주어진 조건으로 검색한다. 기업 선택은 필수가 아니다.
- 추가 질문에 대한 짧은 답변은 원래 요청의 목적과 이미 주어진 조건을 함께 해석한다. 새로운 명시적 질문은 이전 요청보다 우선한다. [직접 질문 계약](direct-chat.md)에 상태별 기준과 예시가 있다.

## 4. 엔티티 사전

| 이름 | 타입 | 예 | 비고 |
| --- | --- | --- | --- |
| `rfq_id` | `RFQ-YYYY-NNN` | `RFQ-2026-001` | |
| `supplier` | string | `SUP-0003`, `서연이화` | supplier_id 또는 회사명 (business-mcp `get_supplier`와 같은 입력) |
| `oem_id` | string | `OEM-A` | |
| `process` | 다이캐스팅·프레스·사출·단조·CNC가공 | `프레스` | |
| `material` | string | `SPFH590`, `알루미늄` | |
| `region` | string | `경남` | |
| `iatf_required` | bool | `true` | |
| `min_monthly_capacity` | int > 0 | `10000` | 연간 물량이면 12로 나눈 값 |
| `top_n` | 1~100 | `5` | |
| `unit_price` | int > 0 (원) | `12000` | |
| `payment_days` | int > 0 | `60` | |
| `price_linkage` | bool | `false` | 납품대금 연동 약정 여부 |
| `raw_material_ratio` | 0~1 | `0.45` | |
| `energy_ratio` | 0~1 | `0.12` | 단가 중 에너지 비용 비중. 선택 ([협상안 검사 계약](check-offer.md)) |
| `contract_days` | int > 0 | `365` | |
| `linkage_waived_by_agreement` | bool | `false` | |
| `buyer_is_small_business` | bool | `false` | |
| `law` | law_key | `subcontract_act` | law-mcp의 `list_laws` 참고. 사용자가 법령 이름을 말했을 때만 |
| `article_no` | `제N조` 또는 `제N조의M` | `제16조의2` | |
| `as_of` | `YYYY-MM-DD` | `2026-10-20` | 생략하면 오늘. `offer_check`에서는 계약(위탁) 시점이며 사용자가 날짜를 말했을 때만 |
| `price_change_pct` | number | `-5` | 인하는 음수. 현재 분해가 뽑지 않음 |

## 5. 근거와 되묻기 규칙

| 상황 | 처리 |
| --- | --- |
| 우리 데이터나 법이 필요한 질문 | 해당 intent, `grounding: required` |
| 조문·숫자·회사 정보가 필요 없는 개념 설명 | `general_knowledge`, `grounding: none` → Generation이 도구 없이 답하고 **"근거 자료 없음"을 표시** |
| 일반 질문처럼 보여도 답에 **조문·숫자·회사 정보**가 들어감 | `grounding: required` intent로 (예: "하도급법상 대금 지급 기한은?" → `law_check`) |
| 근거가 필요한지 판단이 애매함 | **근거 필요 쪽으로** (도구를 한 번 더 부르는 비용 < 틀린 법 정보) |
| 해석이 여러 개이고 해석마다 답이 달라짐 | `status: clarify`, 선택지 제시 |
| 해석이 여러 개지만 답이 크게 다르지 않음 | 되묻지 않고 `assumptions`에 가정을 적고 진행 |
| 대상 협력사나 RFQ를 특정할 수 없음 ("우리 협력사", "거래하는 곳") | 가정으로 ID를 채우지 않는다. 그 엔티티를 비우고 되묻기 규칙을 따른다. 질문·대화 이력·선택 기업에 없는 `supplier`(SUP-NNNN)와 `rfq_id`는 코드가 지우고, 그 값을 말하는 가정도 함께 지운다 (#143) |
| 업무와 무관 | `out_of_scope` → Generation이 정중히 거절 |

**Generation 쪽 약속:** `grounding: none` 답변에 조문 번호, 구체적 숫자, 회사 정보가 들어가면 지우거나 "확인 필요"로 바꾼다.

## 6. 선택어 (`inputs.select`)

| 선택어 | 의미 |
| --- | --- |
| `top1` | 앞 결과의 첫 번째 |
| `top(n)` | 앞 결과의 상위 n개 |
| `all` | 앞 결과 전부 |
| `by_name("이름")` | 앞 결과 중 이름이 맞는 것 |

- "첫 번째"의 순서는 각 도구의 반환 순서를 따른다 (`list_rfqs`는 마감일 순, `rank_suppliers`는 점수 순, `search_suppliers`는 불량률 순).
- 결과에서 목록을 꺼내는 방법(예: `rank_suppliers` → `ranked[*].supplier_id`)은 **Routing의 결과 대응표**가 intent별로 갖는다. 분해는 도구 응답 구조를 모른다.
- 꺼낼 값이 없으면(앞 결과 0건) Routing이 실행을 멈추고 Generation이 "조건에 맞는 결과가 없습니다"라고 설명한다.

## 7. 예시 (fixtures)

| 파일 | 질문 | 보여주는 것 |
| --- | --- | --- |
| `01_law_article` | 하도급법 제4조 보여줘 | 단순 질문은 쪼개지 않는다 |
| `02_parallel_suppliers` | SUP-0001이랑 SUP-0002 품질 비교해줘 | 독립 질문 → 병렬 |
| `03_rank_then_law` | RFQ-2026-001 추천하고 1등에 5% 깎아도 돼? | 문장이 이어져도 도구 입력이 필요 없으면 병렬 |
| `04_business_wording` | 2차 업체 단가 일괄로 깎아도 돼? | 현업 표현 → 법률 용어 정규화 |
| `05_offer_check` | SUP-0003에 단가 12,000원, 결제 75일로 제안해도 돼? | 특정 기업의 협상안을 검사하는 기존 fixture |
| `06_general_knowledge` | RFQ가 뭐야? | 도구 없이 답할 질문 |
| `07_number_needs_grounding` | 하도급법상 대금 지급 기한은 며칠이야? | 숫자가 답이면 근거 필요 |
| `08_assumption_instead_of_clarify` | 최근 RFQ 협력사 추천해줘 | 가정을 밝히고 진행, `inputs`로 앞 결과 사용 |
| `09_clarify` | 그 업체 괜찮아? | 선택지와 함께 되묻기 |
| `10_out_of_scope` | 오늘 날씨 어때? | 업무와 무관 |
| `11_failed` | ㅁㄴㅇㄹ 그거 저번처럼 해줘 | 추측하지 않고 실패 |

### 검증 세트 (`shared/fixtures/validation/`)

위 11개는 **개발용**입니다. 프롬프트를 고칠 때 오답을 보고 규칙을 다듬는 데 씁니다.
`decomposition_dev/`의 질문도 개발용입니다. Routing fixture와 묶이지 않아 자유롭게 늘릴 수 있습니다 (평가 `--set dev`가 두 폴더를 함께 읽음).
`validation/`의 50개는 **검증용**입니다. 프롬프트·모델을 바꾼 뒤 점수만 확인하고, 오답을 보고 프롬프트를 고치지 않습니다.
그렇게 해야 점수가 "이 질문들에 맞춘 결과"가 아니라 실제 개선을 뜻합니다.

- 형식은 개발용과 같습니다 (`note`, `query`, `expected`). 계약 테스트가 둘 다 스키마로 검사합니다.
- 질문은 개발용 fixture, 프롬프트 예시, `intents.yaml` 예시와 겹치면 안 됩니다 (테스트로 확인).
- 검증 세트의 오답이 정답(`expected`) 쪽 잘못으로 보이면, 프롬프트가 아니라 정답을 고치는 PR을 따로 올립니다.

## 8. 역할

| 담당 | 맡는 것 |
| --- | --- |
| 쿼리 분해 | 이 형식의 출력, 분해 프롬프트, 용어 정규화, 되묻기 판단, 분해 평가셋 |
| Routing | 등록표로 서버·도구 선택, 결과 대응표, 병렬·순차 실행, 선택어 해석, `planned` intent 미지원 처리 |
| Generation | `general_knowledge` 답변과 "근거 자료 없음" 표시, `out_of_scope` 거절, 되묻기·실패 메시지, `assumptions` 안내, 근거 없는 숫자·조문 차단 |
| MCP 서버 담당 | 도구 인자명을 엔티티 사전과 맞추기, 도구가 바뀌면 등록표 갱신 |

## 9. 변경 절차

1. `decomposition.py`, `intents.yaml`, fixtures, 이 문서를 **한 PR에서 같이** 고친다.
2. `shared/` 테스트가 통과해야 한다: `cd shared && uv run python -m pytest -q`
3. 분해·Routing·Generation 담당 **전원 승인** 후 머지한다.
4. 필드를 지우거나 의미를 바꾸면 `schema_version`을 올린다.
