# 협상안 생성 (#231)

회사(협력사·RFQ)와 조건을 받아 제시할 협상안 하나를 만든다. 형식은 [docs/contracts/offer-proposal.md](../../../../docs/contracts/offer-proposal.md).

- `request.py`: `OfferProposalRequest`. 조건은 평평한 선택 필드라 엑셀 한 행과 맞출 수 있다
- `generator.py`: 빈 조건 채우기 → 후보 3개(목표가 / 시장가 / 지급 양보) → `check_offer` 검사·위반 수정 → 하나 고르기 → 협상 범위(제시가·목표 합의가·최대 수용가), 양보 순서, 예상 쟁점
- `pdf.py`: 같은 결과를 A4 PDF로 그린다 (reportlab 내장 한글 CID 글꼴)

모델을 부르지 않는다. 숫자는 모두 코드가 business-mcp `get_price_benchmarks`와 요청 조건에서 계산하고, 문서의 모든 가격(제시가·목표 합의가·최대 수용가)은 `check_offer`로 위반 소지가 없는지 검사한다. `OfferProposer(explainer=...)`로 설명 문구 생성기를 붙일 수 있지만 기본은 꺼져 있다.

## 실행

business-mcp를 띄운 뒤 `agent/`에서:

```bash
uv run python scripts/propose_offer.py RFQ-2026-002 SUP-0060 --payment-days 90 --pdf offer.pdf
uv run python scripts/eval_offer_proposal.py --pdf-dir /tmp/offers
```

HTTP는 `POST /internal/offer-proposals` (`?format=pdf`면 PDF 첨부).

## 한계

- 챗봇 연결(협력사 확정 감지, 빠진 값 되묻기)과 화면 버튼·백엔드 프록시는 아직 없다.
- 단가 하한(시장가 90%, 직전 견적 5% 인하)은 `check_offer`의 내부 경고 기준이다. 법이 정한 수치가 아니다.
- 구매자 규모(`buyer_size`)를 넣지 않으면 `check_offer`가 연동 누락을 위반이 아닌 경고로 낮춘다. 이 모듈은 원재료 비중 규칙으로 연동을 먼저 넣어 이 경우에도 연동이 빠지지 않게 한다.
- 결과는 초안이다. 최종 결정·발송은 담당자, 법률 최종 판단은 법무 확인을 거친다.
