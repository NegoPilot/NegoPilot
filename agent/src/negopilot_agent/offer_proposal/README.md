# 협상안 생성 (#231, #236)

회사(협력사·RFQ)와 조건을 받아 협상안 하나를 만들고, 문서를 두 가지로 나눠 낸다.

- **협력사 발송용 "단가 및 거래조건 제안서"** (PDF·DOCX): 협력사에 보여도 되는 값만 담는다. 화면에서 고친 뒤 `/render`로 다시 검사·렌더링하고, 내용 해시로 확정본을 확인한다. 메일 발송은 mail-mcp(#237)가 맡는다.
- **내부 협상 브리핑** (PDF): 협상 범위, 양보 순서, 쟁점과 대응 멘트, 협상 전략을 담는다. 모든 쪽에 "대외비 — 협력사 발송 금지"가 찍힌다.

형식은 [offer-proposal.md](../../../../docs/contracts/offer-proposal.md)와 [offer-document.md](../../../../docs/contracts/offer-document.md)에 있다.

| 파일 | 하는 일 |
| --- | --- |
| `request.py` | `OfferProposalRequest`. 조건은 엑셀 한 행과 맞출 수 있는 평평한 선택 필드 |
| `generator.py` | 빈 조건 채우기 → 후보 3개 → `check_offer` 검사·위반 수정 → 하나 고르기 → 협상 범위, 양보 순서, 쟁점, 입력 필요 |
| `writer.py` | 모델 협상 문구(전략, 대응 멘트, 제안 멘트, 제안서 인사말)와 숫자·조문·내부 용어 검사, 실패 시 코드 문구로 대체 |
| `document.py` | `OfferDocument`(협력사 제안서), 발신 정보 설정(`NEGOPILOT_BUYER_*`), 내용 해시 |
| `supplier.py` | 제안서 PDF·DOCX |
| `pdf.py` | 내부 브리핑 PDF (reportlab 내장 한글 CID 글꼴) |
| `excel.py` | `.xlsx`·`.csv` 행 읽기(한글 머리글), 일괄 처리, zip·summary.csv, 양식 |
| `service.py` | API·CLI가 같이 쓰는 흐름과 `/render` 재검사 |

숫자는 모두 코드가 business-mcp `get_price_benchmarks`와 요청 조건에서 계산한다. 문서의 모든 가격(제시가·목표 합의가·최대 수용가)은 `check_offer`로 위반 소지가 없는지 검사한다. 모델은 문장만 쓰고, 그 문장의 숫자는 협상안 값에 있어야 통과한다.

## 실행

business-mcp를 띄운 뒤 `agent/`에서:

```bash
uv run python scripts/propose_offer.py RFQ-2026-002 SUP-0060 --payment-days 90 --out-dir out/
uv run python scripts/propose_offer.py --excel eval_data/offer_proposal_template.xlsx --out-dir out/
uv run python scripts/eval_offer_proposal.py --repeat 3          # --no-writing이면 모델을 부르지 않는다
```

HTTP: `POST /internal/offer-proposals`(`?format=json|pdf|docx|briefing_pdf`), `/render`, `/batch`.

## 한계

- 화면(편집·다운로드 버튼)과 백엔드 프록시는 아직 없다. 메일 발송은 #237이다.
- 단가 하한(시장가 90%, 직전 견적 5% 인하)은 `check_offer`의 내부 경고 기준이다. 법이 정한 수치가 아니다.
- 구매자 규모(`buyer_size`)가 없으면 `check_offer`가 연동 누락을 경고로 낮춘다. 이 모듈은 원재료 비중 규칙으로 연동을 먼저 넣고, 비중을 모르면 "입력 필요"로 드러낸다.
- 모델 문구 검사는 숫자·조문·내부 용어만 본다. 문장의 뜻이 맞는지는 사람이 확인한다.
- 결과는 초안이다. 최종 결정·발송은 담당자, 법률 최종 판단은 법무 확인을 거친다.
