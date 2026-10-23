# 거래 사실 기반 법규 검토 결과 전달

## 범위와 책임

`POST /internal/legal-reviews` (내부 인증 필요), 웹 경로는 기존 `POST /api/legal-reviews`를 사용한다. 기존 요청 `offer`에 선택 필드 `transaction`을 추가한다. 협상 후보 생성·순위 선정·경제효과 계산·저장은 이 변경에 포함하지 않는다. 변경된 후보는 백엔드가 원래 거래 사실과 새로운 계약 조건을 함께 이 API로 다시 검토해야 한다.

`transaction`은 거래 유형, 당사자 요건 확인, 최초 계약일·갱신일, 신규/기존 발주 구분, 실제 계약금액·확정 수량, 기존 단가·감액 사유와 증빙, 지급 기산점·약정일·기성금 수령일, 예외 주장, 원재료/에너지별 연동 상세 및 증빙을 받는다. 정확한 필드와 단위는 `shared/src/negopilot_shared/transaction.py`가 기준이며 금액은 원, 비중은 0~1이다. 모르는 값은 생략 또는 nullable 필드에 null을 보낸다. enum의 미확인은 unknown이다.

## 응답

기존 응답에 선택적인 `legal_review`를 추가한다.

- `review_id`, `version`, `input_hash`: 검토 및 입력 식별. 입력이 바뀌면 재검토한다.
- `assessment.status`: 거래 사실 검토의 ready / needs_information / blocked. ready는 사용자 입력 전제의 검토 완료이지 적법성 인증이나 기업 규모 인증이 아니다. 기업 규모 결과는 기존 answer.evidence의 company_size를 별도로 확인한다.
- `assessment.findings`: 적용 전제·단가·지급·연동·기간별 고정 id, 적용 field, 사용 facts, status, summary, missing_fields, evidence_refs, law_refs, constraints.
- `assessment.missing_fields`, `constraints`: 미확인 사실과 조건 제약. 증빙을 입력했다고 진위 또는 감액 정당성을 확정하지 않는다.
- `assessment.sources_complete`: 하위 조회 상태의 완료 여부다. 각 조문 전문의 완전성 또는 최신성 인증이 아니다.
- `snapshot.offer`: 해당 검토에 사용한 입력.
- `issued_at`, `expires_at`, `review_token`: 1시간 유효 HMAC 서명. 토큰에는 검토 사실·판단·조회 법률 근거 메타데이터가 들어간다. 암호화가 아니므로 로그에 남기지 않는다. 서명은 사용자/테넌트 권한을 대체하지 않으며 백엔드가 소유권·저장을 관리해야 한다.

조회 실패는 needs_information으로 표시하고 실행 실패는 재사용할 legal_review를 발급하지 않는다. `verify_review`는 내부 Python 소비자를 위한 검증 함수이며 신규 협상 생성 API는 추가하지 않는다. 백엔드와 토큰 교환/보관 방식은 후속 통합 시 합의해야 한다.

## 협상 후보 생성·경제효과 비교

이 API는 후보 생성·순위 선정·경제효과 계산을 하지 않는다. 처음에는 참고 구현을 별도 브랜치(`feat/252-negotiation-reference`)에 보존했으나, 백엔드가 이 기능을 새로 구현하기로 해서 2026-10-23에 브랜치를 삭제했다. 이 API는 최신 main의 기존 협상 기능을 변경하지 않는다.

## 검증

- `uv sync --project agent --frozen`
- `agent/.venv/bin/python -m pytest agent/tests -q`
- `npm --prefix frontend run build`
- `npm --prefix frontend run check:api`
- backend에서 Java 21로 `mvn test`

검토 테스트는 거래 사실 누락, 감액 정당성 보류, 더 이른 지급일, 연동 상세 누락, 입력 단위, 계약 기간과 물량 분리, 변경 조건 재검토, 서명 변조·만료, 부분 조회 및 실패 응답을 다룬다. 생성·비교 모듈을 가져오지 않는 독립 테스트를 포함한다.

2026-10-23 검증 결과: 최신 main c0a0e37 기준 Agent 1,124 passed; backend 656 tests / failures 0 / errors 0 / skipped 57; frontend build 및 check:api 통과. 실제 MCP를 사용하는 검토 API에서 완비한 입력은 ready, 5273 웹에서 미입력 거래 사실은 추가 확인 필요와 기업 규모 판정 보류로 표시됨을 확인했다. 생성 버튼과 경제효과 입력·비교표는 분리된 화면에 없다. 로컬 화면 캡처는 gitignore된 eval_results에 보관한다.
