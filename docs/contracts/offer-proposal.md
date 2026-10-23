# 협상안 생성 계약 (#231, #236)

회사(협력사·RFQ)와 조건을 받아 **제시할 협상안 하나**를 만들고, 협력사 발송용 제안서(PDF·DOCX)와 내부 협상 브리핑(PDF)을 돌려주는 기능의 형식입니다.
business-mcp `get_price_benchmarks`가 기준값을 주고, Agent `offer_proposal`이 후보를 만들어 `check_offer`로 검사한 뒤 하나를 고릅니다.
숫자는 모두 코드가 계산합니다. 모델은 협상 전략·대응 멘트·제안 멘트·제안서 인사말만 쓰고, 그 문장의 숫자·조문은 코드가 검사합니다(6절).
협력사 제안서 문서 형식, 편집·렌더링(`/render`), 내용 해시는 [offer-document.md](offer-document.md)에 있습니다.

| 파일 | 내용 |
| --- | --- |
| [`mcp-servers/business-mcp/server.py`](../../mcp-servers/business-mcp/server.py) | `get_price_benchmarks`, `check_offer`와 같은 시장가 쿼리(`market_quotes`) |
| [`agent/src/negopilot_agent/offer_proposal/`](../../agent/src/negopilot_agent/offer_proposal/) | 요청(`request.py`), 후보·선택·협상 구조(`generator.py`), 모델 문구(`writer.py`), 제안서(`document.py`, `supplier.py`), 내부 브리핑(`pdf.py`), 엑셀(`excel.py`) |
| [`agent/src/negopilot_agent/api/offer_api.py`](../../agent/src/negopilot_agent/api/offer_api.py) | `POST /internal/offer-proposals`, `/render`, `/batch` |
| [`docs/contracts/check-offer.md`](check-offer.md) | 검사에 쓰는 `check_offer` 형식 |

## 1. `get_price_benchmarks(rfq_id, supplier)` (business-mcp, 조회 전용)

```json
{
  "rfq": {"rfq_id": "RFQ-2026-002", "vehicle": "NX5", "part_name": "시트 레일 보강판", "process": "프레스", "material": "SPFH590",
          "annual_volume": 240000, "target_price": 1750, "required_cert": "IATF16949"},
  "market": {"median": 1810.0, "n": 31, "min": 1580, "max": 2150},
  "supplier": {"supplier_id": "SUP-0060", "name": "온새프레스", "last_price": 2050, "payment_days": 90,
               "price_linkage": true, "raw_material_ratio": 0.42, "years_dealing": 3},
  "rules": {"PAYMENT_DAYS_LIMIT": 60, "MAJOR_MATERIAL_RATIO": 0.1, "LINKAGE_EXEMPT_DAYS": 90,
            "LINKAGE_EXEMPT_AMOUNT": 100000000, "MARKET_PRICE_FLOOR": 0.9, "LAST_QUOTE_CUT": 0.05}
}
```

- `market`: 같은 공정·소재에서 활성 협력사마다 가장 최근 견적 하나씩 모은 단가. `check_offer`의 `PRICE_BELOW_MARKET`과 같은 쿼리를 쓴다. 견적이 없으면 `n=0`, 나머지는 `null`.
- `supplier.last_price`: 이 협력사의 같은 공정·소재 직전 견적. 없으면 `null`. 기존 계약이 없으면 계약 값(`payment_days` 등)이 `null`.
- `rules`: `rules.py`의 기준값. Agent는 이 값을 읽어 계산하고 따로 적어 두지 않는다.
- 오류는 `check_offer`와 같은 코드(`RFQ_NOT_FOUND`, `SUPPLIER_NOT_FOUND`, `DB_UNAVAILABLE` 등)를 쓴다.

## 2. 요청 (`OfferProposalRequest`)

나중에 엑셀 한 행으로도 받을 수 있게 조건은 평평한 필드로 둡니다. 알 수 없는 필드는 거절합니다(`extra=forbid`, strict).

| 필드 | 필수 | 뜻 | 비우면 |
| --- | --- | --- | --- |
| `supplier` | O | `SUP-0000` | |
| `rfq_id` | O | `RFQ-0000-000` | |
| `as_of` | O | 기준일 `YYYY-MM-DD`. 모든 `check_offer`에 같은 날짜 | |
| `intent` | | `target` / `market` / `payment`: 먼저 고를 기준 | 경고 수·목표 단가 차이로 고름 |
| `target_price` | | 목표 단가(원) | RFQ 목표 단가 |
| `max_price` | | 최대 수용가(원). `target_price` 이상 | 직전 견적·시장 중앙값 중 제시가 이상인 낮은 값 |
| `payment_days` | | 지급 일수 | 기존 계약 일수(60일 초과면 60일), 계약이 없으면 60일 |
| `price_linkage` | | 연동 약정 여부 | 원재료 비중·면제 기준으로 정함 |
| `raw_material_ratio`, `energy_ratio` | | 0~1 | 원재료는 기존 계약 값, 에너지는 비움 |
| `contract_days` | | 거래 기간(일) | 365 |
| `linkage_waived_by_agreement` | | 연동하지 않기로 합의함 | false |
| `notes` | | 납품·비고(500자 이하). 문서에 그대로 옮긴다 | |
| `supplier_size`, `buyer_size` | | `/review`와 같은 `ReviewCompanyFacts` | 규모 판정 보류(경고) |

- 비워서 데이터로 채운 값은 결과의 `filled[]`에 `{field, value, source}`로 적는다.
- 요청 조건이 법 기준을 넘으면 고치고 `offer.adjustments[]`에 적는다. 예: `요청 조건 지급 90일 → 법정 60일 이내로 조정`, `요청 조건 연동 없음 → 원재료 비중 42%로 법정 연동 대상이라 연동 포함`.

## 3. 협상안 만들기

| 후보 | 단가 | 지급 일수 |
| --- | --- | --- |
| `target` 목표가 중심 | max(목표 단가, ⌈시장 중앙값 × 0.9⌉, ⌈직전 견적 × 0.95⌉) 중 있는 값 | 정한 지급 일수 |
| `market` 시장가 중심 | 시장 중앙값 반올림 (시장 견적이 없으면 만들지 않음) | 정한 지급 일수 |
| `payment` 지급 조건 양보 | 목표 단가 | min(30, 정한 지급 일수) |

- 모든 후보 단가는 `max_price`를 넘지 않게 자른다.
- 연동 약정: 원재료 비중이 `MAJOR_MATERIAL_RATIO` 이상이고 거래 기간·하도급대금이 면제 기준을 넘고 미연동 합의도 아니면 넣는다. `linkage_status`는 `included` / `not_required` / `exempt` / `waived` / `needs_input`이다.
- 원재료 비중을 모르면(요청에도 기존 계약에도 없음) 연동 없음으로 두지 않는다. 요청이나 기존 계약이 연동 포함이면 포함하고, 아니면 `needs_input`(연동 약정 확인 필요)으로 두고 `input_needed`에 `raw_material_ratio`("원재료 비중을 입력하면 연동 의무를 판정합니다")를, 경고에 `RAW_MATERIAL_RATIO_REQUIRED`를 넣는다.
- 후보마다 `check_offer`를 부른다. violation이면 코드별로 고쳐(`PAYMENT_DAYS_OVER_60` → 60일, `LINKAGE_TERMS_MISSING`·`ENERGY_LINKAGE_TERMS_MISSING` → 연동 포함) 최대 2번 다시 검사한다. 고칠 수 없거나 끝까지 남으면 그 후보는 버린다.
- 하나 고르기: 요청한 `intent` → warning 수가 적음 → 목표 단가와 차이가 작음 → 위 표 순서.
- 협상 범위: 제시가 = 고른 단가, 최대 수용가 = 요청 값 또는 직전 견적·시장 중앙값 중 제시가 이상인 낮은 값(제시가보다 낮지 않음), 목표 합의가 = 둘의 가운데(반올림). 목표 합의가와 최대 수용가도 같은 조건으로 `check_offer`를 불러, 문서의 모든 가격에 위반 소지가 없게 한다.
- 양보 순서(2~4개): 1차 단가 양보(목표 합의가까지) → 지급 단축(15일, 30일 하한) → 연동 약정으로 원재료 리스크 분담 → 계약 기간 연장(2년 상한) 중 해당하는 앞의 3개, 마지막은 최대 수용가.
- 예상 쟁점과 대응: 직전 견적보다 낮음, 시장 중앙값보다 낮음, 연동(또는 미연동 합의), `check_offer`의 생산능력·인증 경고에서만 만든다.

## 4. 출력 (JSON)

```json
{
  "status": "offer",
  "rfq_id": "RFQ-2026-002", "part_name": "시트 레일 보강판", "supplier_id": "SUP-0060", "supplier_name": "온새프레스",
  "as_of": "2026-10-23", "target_price": 1750,
  "offer": {
    "headline": "SUP-0060에 단가 1,948원, 지급 60일, 연동 약정 포함, 거래 기간 365일 제시",
    "basis": "target", "label": "목표가 중심",
    "unit_price": 1948, "payment_days": 60, "price_linkage": true, "raw_material_ratio": 0.42, "energy_ratio": null,
    "contract_days": 365, "contract_amount": 467520000, "annual_volume": 240000,
    "linkage_waived_by_agreement": false, "linkage_status": "included", "notes": null,
    "gap_to_target_pct": 11.3, "gap_to_last_quote_pct": -5.0,
    "rationale": [{"source": "rfq.target_price", "value": 1750, "note": "RFQ 목표 단가 1,750원"},
                  {"source": "supplier.last_price", "value": 2050, "note": "직전 견적 2,050원에서 5% 넘게 낮추지 않는 1,948원 이상"}],
    "verdict_code": "CHECK_NEEDED", "warnings": [{"code": "BUYER_SIZE_UNKNOWN", "...": "..."}], "law_refs": [],
    "revisions": [], "adjustments": [],
    "price_range": [
      {"name": "opening", "label": "제시가", "unit_price": 1948, "vs_target_pct": 11.3, "vs_market_pct": 7.6,
       "vs_last_quote_pct": -5.0, "verdict_code": "CHECK_NEEDED", "warnings": ["BUYER_SIZE_UNKNOWN", "SME_STATUS_UNKNOWN"]},
      {"name": "settlement", "label": "목표 합의가", "unit_price": 1999, "...": "..."},
      {"name": "walk_away", "label": "최대 수용가", "unit_price": 2050, "...": "..."}],
    "concessions": ["1차 양보: 단가 +51원 → 목표 합의가 1,999원", "지급 60일 → 45일 단축 제안 (단가 대신 현금 흐름을 양보)",
                    "연동 약정 포함으로 원재료(비중 42%) 가격 변동 리스크 분담",
                    "최종 양보: 단가 최대 2,050원까지 (최대 수용가, 넘으면 협상 중단)"],
    "objections": [{"issue": "직전 견적 2,050원보다 5.0% 낮다",
                    "response": "목표 단가 1,750원 기준이며 인하 폭은 직전 견적 대비 5% 이내다"}]
  },
  "considered": [{"basis": "market", "label": "시장가 중심", "unit_price": 1810, "payment_days": 60,
                  "price_linkage": true, "reason": "확인 필요 3건으로 선택한 안(2건)보다 많음", "violations": []}],
  "filled": [{"field": "target_price", "value": 1750, "source": "RFQ 목표 단가"}],
  "input_needed": [],
  "no_offer_reasons": [],
  "benchmarks": {"...": "get_price_benchmarks 결과 그대로"},
  "explanation": null
}
```

- `offer`는 하나만 있다. `verdict_code`는 `OK` 또는 `CHECK_NEEDED`이고 `VIOLATION`인 안은 나오지 않는다.
- `rationale[].source`는 `request.*`(요청 조건), `benchmarks`의 경로(`rfq.target_price`, `market.median`, `supplier.*`, `rules.*`) 또는 `offer.payment_days`(지급 양보안의 일수)다. `value`는 그 값 그대로다.
- `revisions[]`: `{code, field, before, after}`. 처음 후보가 violation이라 고친 내역. `adjustments[]`는 요청 조건을 미리 고친 내역이다.
- 통과한 후보가 없으면 `status: "no_offer"`, `offer: null`, `no_offer_reasons`에 후보별 위반 메시지와 만들지 못한 이유를 담는다.
- `input_needed[]`: `{field, message}`. 판정을 끝내려면 사람이 넣어야 하는 값. 브리핑 맨 위에 보여 준다.
- `offer.linkage_status`: 3절 참고.

## 5. HTTP

`/internal/legal-reviews`와 같은 인증(`X-Internal-Api-Key`)과 오류 형식을 쓴다.

### `POST /internal/offer-proposals`

```json
{"request_id": "uuid", "offer": {"supplier": "SUP-0060", "rfq_id": "RFQ-2026-002", "as_of": "2026-10-23", "payment_days": 90}}
```

- `?format=json`(기본): `{request_id, offer_document, content_hash, check: {verdict_code, warnings}, internal_briefing}`
  - `offer_document`: 협력사 제안서([offer-document.md](offer-document.md)). 협상안이 없으면 `null`
  - `internal_briefing`: 4절 출력 + `writing`(6절). 협력사에 보내지 않는다
- `?format=pdf` / `?format=docx`: 협력사 제안서 파일. `?format=briefing_pdf`: 내부 브리핑 PDF
- `?writing=false`: 모델 문구를 끈다. 서버 전체는 `NEGOPILOT_OFFER_WRITING=0`
- 오류: 401 `UNAUTHORIZED`, 400 `VALIDATION_ERROR`(형식·범위), 422 `RFQ_NOT_FOUND` / `SUPPLIER_NOT_FOUND` / `VALUE_OUT_OF_RANGE`(business-mcp가 거절), 422 `NO_OFFER`(파일 요청인데 협상안 없음), 504 `UPSTREAM_TIMEOUT`, 500 `INTERNAL_ERROR`

### `POST /internal/offer-proposals/render`

고친 제안서를 다시 검사해 렌더링한다. [offer-document.md](offer-document.md) 3절.

### `POST /internal/offer-proposals/batch?filename=offers.xlsx`

- 요청 본문은 업로드한 `.xlsx`·`.csv` 파일 바이트 그대로다(multipart 아님, 2MB·50행 이하). 첫 행은 머리글이고 한 행이 요청 하나다.
- 머리글은 `OfferProposalRequest` 필드 이름이나 한글 별칭: 협력사, RFQ, 기준일, 기준, 목표단가, 최대수용가, 지급일수(지급기한), 연동(연동약정), 원재료비중, 에너지비중, 계약기간, 미연동합의, 비고. 비율은 `42%`·`0.42`·`42` 모두 받고, 연동은 예/아니오·O/X·포함/없음을 받는다. 기준일이 빈 행은 오늘(서울)로 한다.
- 응답은 zip: 행마다 `<행 번호 3자리>_제안서_<RFQ>_<SUP>_v1.pdf`·`.docx`, `<행>_내부브리핑_<RFQ>_<SUP>.pdf`, 그리고 `summary.csv`(row, supplier, rfq_id, status=`offer`/`no_offer`/`invalid`/`error`, unit_price, payment_days, linkage, warnings, input_needed, content_hash, files, error).
- 잘못된 행은 `invalid`나 `error`로 요약에만 적고 나머지 행은 계속 처리한다.
- 양식: `agent/eval_data/offer_proposal_template.xlsx` (`scripts/propose_offer.py --template`으로 다시 만들 수 있다)

## 6. 모델 협상 문구 (`writing`)

```json
{"source": "model", "strategy": "3~5문장", "objection_scripts": ["쟁점 순서대로"], "opening_message": "제안 멘트 초안",
 "cover_letter": "제안서 인사말", "attempts": 1, "first_try_passed": true, "problems": []}
```

- 입력은 협상안 결과(제시 조건, 협상 범위, 양보 순서, 쟁점, 경고, 근거 조문, 입력 필요)뿐이다. gpt-4.1-mini, `NEGOPILOT_OFFER_WRITER_MODEL`로 바꿀 수 있다. 토큰·비용·시간은 `timing`에 남는다.
- 검사:
  - 문장의 숫자는 모두 입력에 있는 값이어야 한다. 쉼표, 비율의 % 표기는 같은 값으로 본다.
  - `opening_message`와 `cover_letter`는 협력사에게 보여도 되는 값(제안 조건)만 쓸 수 있다.
  - 조문 번호는 `law_refs`에 있는 것만 쓸 수 있다.
  - `cover_letter`와 `opening_message`에는 내부 협상 용어(목표 합의가, 최대 수용가, 양보, 쟁점, 시장 견적, 직전 견적, 경고, AI 등)를 쓸 수 없다.
  - 대응 멘트 수는 쟁점 수와 같아야 한다.
- 실패하면 이유를 알려 주고 1번 다시 쓰게 한다. 그래도 실패하면 코드 문구로 대체하고 `source: "fallback"`, `problems`에 이유를 남긴다.
- 통과한 `cover_letter`만 제안서 인사말에 들어간다.

## 7. 내부 브리핑 PDF

A4, reportlab 내장 한글 CID 글꼴(HYGothic-Medium). 모든 쪽 오른쪽 위에 "대외비 — 협력사 발송 금지"를 찍는다. 파일 이름은 `내부브리핑_<RFQ>_<SUP>.pdf`다.

순서:
1. 제목
2. 입력 필요
3. 결론
4. 제시 조건 표
5. 협상 범위(목표 단가·시장 중앙값·직전 견적 대비 %)
6. 양보 순서
7. 협상 전략: "AI 작성 문구 (숫자·조문을 협상안 값과 대조해 통과)" 또는 코드 대체 표시를 붙인다
8. 제안 멘트(초안) 상자
9. 예상 쟁점과 대응 멘트
10. 법규 검토 결과
11. 숫자 근거
12. 조정한 조건과 자동으로 채운 값
13. 고지

## 8. 변경 절차

1. 이 문서와 구현, 테스트(`business-mcp/tests/test_server.py`, `agent/tests/test_offer_proposal.py`, `agent/tests/test_offer_documents.py`, `agent/tests/test_offer_api.py`)를 한 PR에서 같이 고친다.
2. 후보 계산이나 문서 내용을 바꾸면 `agent/scripts/eval_offer_proposal.py`를 돌려 결과를 PR에 적는다.
