# 협상안 검사 계약 (check_offer v2)

business-mcp `check_offer`의 입력, 출력, 오류 형식입니다. **business-mcp가 만들고, Agent(Routing·Generation)와 Spring·화면이 받아서 씁니다.**
v1과 비교해 **기존 필드는 이름도 뜻도 바꾸지 않고, 새 필드만 추가**합니다. 그래서 v2가 머지돼도 지금 돌아가는 Agent와 화면은 깨지지 않습니다.

| 파일 | 내용 |
| --- | --- |
| [`mcp-servers/business-mcp/server.py`](../../mcp-servers/business-mcp/server.py) | `check_offer` 구현 |
| [`docs/contracts/routing.md`](routing.md) | Routing 결과 전체 형식. `check_offer` 결과는 `calls[].result`에 그대로 담긴다 |
| [`docs/contracts/query-decomposition.md`](query-decomposition.md) | `offer_check` intent가 받는 엔티티 |

## 0. 이 계약을 먼저 정하는 이유

1. **세 사람이 동시에 작업할 수 있습니다.** 검사기를 만드는 쪽(business-mcp)과 쓰는 쪽(Agent, Spring 화면)이 다릅니다. 모양을 먼저 정하면 서로 기다리지 않습니다.
2. **같은 곳을 두 번 고치지 않습니다.** 법 기준 정리와 오류 코드는 둘 다 `check_offer`를 바꿉니다. 한 번에 정해야 Agent와 화면 코드를 두 번 고치지 않습니다.
3. **화면이 나중에 깨지지 않습니다.** 출력 형식이 고정된 뒤에 화면을 연결합니다.
4. **사용자에게 보여 줄 오류를 의도적으로 고릅니다.** 오류 원문은 보안상 가리고, 정해진 코드만 통과시킵니다.
5. **결정한 내용이 남습니다.** 담당이 바뀌어도 이 문서를 기준으로 판단합니다.

## 1. 입력

| 인자 | 타입 | 필수 | 뜻 | v2 |
| --- | --- | --- | --- | --- |
| `supplier` | 문자열 | 예 | 협력사 ID(`SUP-0001`) 또는 회사명 | |
| `rfq_id` | 문자열 | 예 | `RFQ-2026-001` 형식 | |
| `unit_price` | 정수 | 예 | 제시할 개당 단가(원), 0보다 큼 | |
| `payment_days` | 정수 | 예 | 목적물 수령일부터 대금 지급까지 일수, 0보다 큼 | |
| `price_linkage` | 불리언 | 예 | 납품대금 연동 약정을 넣는지. `true`면 주요 원재료와 주요 에너지 모두 약정에 포함한 것으로 본다 | 뜻 보완 |
| `raw_material_ratio` | 실수 0~1 | 아니오 | 단가 중 원재료 비용 비중. 생략하면 기존 계약 값 | |
| `energy_ratio` | 실수 0~1 | 아니오 | 단가 중 에너지 비용 비중. 생략하면 에너지 검사를 건너뛰고 `ENERGY_RATIO_UNKNOWN`(info)을 남긴다 | **추가** |
| `contract_days` | 정수 | 아니오 | 거래 기간(일). 기본 365 | |
| `linkage_waived_by_agreement` | 불리언 | 아니오 | 연동하지 않기로 합의했는지. 기본 `false` | |
| `buyer_is_small_business` | 불리언 | 아니오 | 원사업자(우리)가 소기업인지. 기본 `false` | |
| `as_of` | `YYYY-MM-DD` | 아니오 | **계약(위탁) 시점.** 이 날 시행 중인 법 기준으로 판정한다. 생략하면 오늘 | **추가** |

- 필수 인자는 v1과 같은 5개입니다. 하나라도 없으면 Routing이 도구를 부르지 않고 `missing_args`로 되묻습니다([분해 계약](query-decomposition.md)의 #47 규칙).
- 에너지 비중을 모를 때 `warning`을 내지 않는 이유: 원재료는 기존 계약 값으로 대신할 수 있지만 에너지는 DB에 값이 없습니다. 모를 때마다 경고하면 거의 모든 검사가 "확인 필요"가 됩니다.
- `as_of`는 law-mcp와 분해 계약이 쓰는 이름과 형식을 그대로 씁니다.

## 2. 출력

```json
{
  "schema_version": "2",
  "verdict": "위반 소지 있음",
  "verdict_code": "VIOLATION",
  "basis": {"as_of": "2026-10-22", "law_versions": {"subcontract_act": "2026-10-02", "subcontract_decree": "2026-08-11"}},
  "supplier": {"supplier_id": "SUP-0030", "name": "...", "source": "MOCK", "revenue_eok": 850, "...": "..."},
  "rfq": {"rfq_id": "RFQ-2026-004", "part_name": "...", "process": "...", "material": "...", "annual_volume": 120000, "target_price": 15000},
  "offer": {"unit_price": 15200, "payment_days": 75, "price_linkage": false, "raw_material_ratio": 0.4,
            "energy_ratio": 0.12, "contract_days": 365, "contract_amount": 1824000000},
  "violations": [
    {"code": "PAYMENT_DAYS_OVER_60", "severity": "violation", "message": "지급 기한 75일: ...",
     "law_refs": [{"law": "subcontract_act", "article_no": "제13조", "paragraph": "①"}]}
  ],
  "warnings": [],
  "info": [],
  "law_refs": [{"law": "subcontract_act", "article_no": "제13조", "paragraph": "①"}]
}
```

| 필드 | 뜻 | v2 |
| --- | --- | --- |
| `schema_version` | 이 결과 형식의 버전. v2는 `"2"`. 필드를 지우거나 뜻을 바꾸면 올린다 | **추가** |
| `verdict` | 사람이 읽는 판정 문구 (`위반 소지 있음`, `확인 필요`, `문제 없음`). 문구는 다듬을 수 있으므로 **화면 분기에 쓰지 않는다** | |
| `verdict_code` | 화면이 분기에 쓰는 판정 코드. `violations`가 있으면 `VIOLATION`, `warnings`만 있으면 `CHECK_NEEDED`, 둘 다 없으면 `OK` | **추가** |
| `basis` | 판정 기준. `as_of`와, 판정에 쓴 법령별 시행판의 시행일 | **추가** |
| `supplier`, `rfq` | 검사 대상 정보 | |
| `offer` | 검사한 조건. 생략한 인자는 실제로 쓴 값(기존 계약 값, 기본값)으로 채운다. `energy_ratio`는 입력이 없으면 `null` | `energy_ratio` 추가 |
| `violations`, `warnings`, `info` | 검사 항목 목록. 각 항목은 `{code, severity, message, law_refs}` | |
| `law_refs` | 모든 항목의 근거 조문을 중복 없이 모은 목록 | |
| `law_refs[].paragraph` | 근거가 되는 항. 원문자(`①`)로 쓰고, 16항 이상은 law-mcp 색인의 표기를 그대로 쓴다. 항을 특정할 수 없으면 생략한다. **`article_no`는 그대로 두므로** Routing은 지금처럼 `get_article(law, article_no)`를 부른다 | **추가** |

### 검사 항목 코드

기존 코드는 이름을 바꾸지 않습니다. v2에서는 에너지 코드 3개만 추가합니다.

| 코드 | severity | 언제 | v2 |
| --- | --- | --- | --- |
| `PAYMENT_DAYS_OVER_60` | violation | 지급 기한이 수령일부터 60일 초과 (하도급법 제13조①) | |
| `LINKAGE_TERMS_MISSING` | violation | 원재료 비중 10% 이상인데 연동 약정 없음 (제2조⑯, 제3조②3) | |
| `LINKAGE_WAIVED_NEEDS_WRITTEN_REASON` | warning | 원재료 연동 대상인데 연동하지 않기로 합의 (제3조④4) | |
| `RAW_MATERIAL_RATIO_UNKNOWN` | warning | 원재료 비중을 알 수 없음 | |
| `LINKAGE_EXEMPT` | info | 원재료 연동 대상이지만 예외 해당 가능 (제3조④1~3) | |
| `ENERGY_LINKAGE_TERMS_MISSING` | violation | 에너지 비중 10% 이상인데 연동 약정 없음 (제2조⑰, 제3조②3) | **추가** |
| `ENERGY_LINKAGE_EXEMPT` | info | 에너지 연동 대상이지만 예외 해당 가능 (제3조④1~3) | **추가** |
| `ENERGY_RATIO_UNKNOWN` | info | 에너지 비중을 입력하지 않아 에너지 검사를 건너뜀 | **추가** |
| `PRICE_BELOW_MARKET` | warning | 단가가 같은 공정·소재 최근 견적 중앙값의 90% 미만 (제4조) | |
| `PRICE_CUT_FROM_LAST_QUOTE` | warning | 이 협력사의 직전 견적보다 5% 넘게 낮음 (제4조) | |
| `ABOVE_TARGET_PRICE` | info | 단가가 RFQ 목표 단가보다 높음 | |
| `SUBCONTRACT_ACT_APPLIES`, `SME_STATUS_CHECK`, `SME_STATUS_UNKNOWN` | info | 수급사업자(중소기업자) 해당 여부 | |
| `CERT_MISSING`, `CAPABILITY_MISSING`, `CAPACITY_SHORT` | warning | RFQ 거래 조건(인증, 생산능력) | |

에너지 연동 누락은 원재료와 함께 약정 하나로 다루므로, 원재료 연동 합의(`linkage_waived_by_agreement`)는 에너지에도 같이 적용합니다.

### 화면이 기대도 되는 필드

Spring 응답 검증기와 화면은 아래 필드만 기준으로 삼습니다. 이 필드의 이름이나 뜻을 바꾸려면 `schema_version`을 올리고 이 문서를 먼저 고칩니다.

`schema_version`, `verdict_code`, `basis`, `violations`, `warnings`, `info`, 각 항목의 `code`·`severity`·`message`·`law_refs`

## 3. 오류

### 오류 코드

`check_offer`의 입력 오류는 `ToolError` 메시지 맨 앞에 코드를 붙입니다.

```
[RFQ_NOT_FOUND] RFQ 'RFQ-2026-099'가 없다. 사용 가능: RFQ-2026-001, ...
```

| 코드 | 언제 | 화면까지 전달 | 사용자 안내 (Generation이 코드별 고정 문구로 작성) |
| --- | --- | --- | --- |
| `RFQ_NOT_FOUND` | 없는 RFQ 번호 | 예 | RFQ 번호를 확인해 달라고 안내 |
| `SUPPLIER_NOT_FOUND` | 없는 협력사 | 예 | 협력사를 찾지 못했다고 안내 |
| `SUPPLIER_AMBIGUOUS` | 이름이 여러 협력사에 맞음 | 예 | 협력사를 하나로 골라 달라고 안내. 아래처럼 Routing이 먼저 막으므로 거의 생기지 않는다 |
| `VALUE_OUT_OF_RANGE` | 단가·일수가 0 이하, 비중이 0~1 밖, 날짜 형식 오류 | 예 | 어떤 값을 어떤 범위로 넣어야 하는지 안내 |
| `DB_UNAVAILABLE` | DB 연결 실패 | **아니오** | 지금처럼 "자료를 가져오지 못했습니다" |
| (코드 없음) | 그 밖의 모든 예외 | **아니오** | 지금처럼 "자료를 가져오지 못했습니다" |

- **원문 메시지는 계속 가립니다.** 오류 원문에는 DB 연결 정보 같은 내부 내용이 섞일 수 있습니다. Agent의 `SafeToolCaller`와 Spring의 응답 정리 코드는 지금처럼 원문을 가리고, **허용 목록에 있는 코드만** 통과시킵니다.
- **전달 경로:** business-mcp `ToolError("[CODE] ...")` → Agent가 `^\[([A-Z_]+)\]`로 코드만 꺼내 `ToolCall.error_code`에 담음(원문은 가림) → Spring이 허용 목록의 `error_code`만 응답에 남김 → Generation과 화면이 코드로 안내.
- `ToolCall.error_code`는 Routing 결과에 새로 생기는 선택 필드입니다. 기존 필드를 바꾸지 않으므로 [Routing 계약](routing.md)의 `schema_version`은 올리지 않습니다.

### 회사 이름이 모호할 때

`offer_check`도 `supplier_detail`처럼 **Routing이 `find_suppliers`로 대상을 먼저 확정**합니다.

| `find_suppliers` 결과 | Routing 처리 |
| --- | --- |
| 한 곳 | 확정된 `supplier_id`로 `check_offer` 호출 |
| 여러 곳 | `check_offer`를 부르지 않고 `missing_args`(`missing: ["supplier"]`). Generation이 후보를 `Answer.options`로 보여 준다 |
| 없음 | `check_offer`를 부르지 않고 `empty` |

그래서 `check_offer`는 사실상 확정된 ID만 받고, `SUPPLIER_AMBIGUOUS`는 다른 클라이언트가 직접 부를 때를 위한 예비 코드로 남습니다.

## 4. 작업 나누기

이 문서가 머지되면 아래 세 작업을 동시에 시작합니다.

| 작업 | 범위 |
| --- | --- |
| business-mcp 법 규칙 모듈화 | 법 규칙을 `rules.py`로 분리, `as_of`·에너지 규칙·`paragraph`·`basis`·`verdict_code`·오류 코드 구현, law-mcp 색인과 대조하는 테스트, DB 없는 경계값 테스트 |
| MCP 서버 정비 | business-mcp 연결 풀과 협력사 이름 해석 통합, law-mcp 검색 캐시 상한과 용어 확장 수정 |
| Agent 연결 | `offer_check`에 `find_suppliers` 선행, `error_code` 추출과 허용 목록, 분해 계약에 `energy_ratio`·`as_of` 추가, 검증 세트 전후 측정 |

Spring의 허용 목록(`verdict_code`, `basis`, `schema_version`, `error_code`)과 화면 연결 방식(전용 API 또는 채팅 응답의 구조화 필드)은 위 작업이 머지된 뒤 BE와 함께 진행합니다.

## 5. 변경 절차

1. 이 문서와 `check_offer` 구현, 관련 테스트를 **한 PR에서 같이** 고칩니다.
2. business-mcp, Agent, 백엔드 테스트가 통과해야 합니다.
3. business-mcp·Agent 담당과 BE 담당 승인 후 머지합니다.
4. 필드를 지우거나 뜻을 바꾸면 `schema_version`을 올립니다.
