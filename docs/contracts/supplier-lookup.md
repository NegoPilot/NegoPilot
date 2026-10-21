# 협력사 live 조회 API v2

협력사 DB를 읽는 조회 HTTP 계약이다. business-mcp(PostgreSQL)의 실제 협력사 데이터를 반환한다. 요청 규칙과 응답 형식은 [협력사 조회와 선택 기업 챗봇 API v0.2](supplier-chat.md)의 조회 API와 같고, 값의 출처와 버전 표시만 다르다. 명세는 [supplier-lookup.openapi.json](supplier-lookup.openapi.json)이다.

## 범위와 책임

연결 흐름은 Vue → Spring → Python FastAPI → business-mcp 순서다. v1 조회 경로는 지금처럼 stub 전용으로 fixture를 읽는다. v2 조회 경로는 AGENT_EXECUTION_MODE와 관계없이 항상 business-mcp를 읽는다. 어느 경로도 모델을 호출하지 않는다. 대화 경로는 이 계약에 포함하지 않는다.

| Spring 공개 경로 | Python 내부 경로 | 응답 data |
| --- | --- | --- |
| GET /api/v2/business/catalog | GET /internal/v2/business/catalog | processes와 regions |
| GET /api/v2/suppliers | GET /internal/v2/suppliers | items와 returned_count 및 limit |
| GET /api/v2/suppliers/{supplier_id} | GET /internal/v2/suppliers/{supplier_id} | SupplierDetail |

조회 성공 응답은 {schema_version:"0.3",mode:"live",data:{...}}다. 검색 query parameter와 기업 ID 규칙 및 오류 봉투는 v1과 같다. 검색 조건에는 process와 material, region, iatf_required, iso14001_required, min_monthly_capacity, limit이 있다.

## 데이터 출처

provenance는 {profile,financials,operations}다. profile과 financials는 협력사 DB suppliers.source 값인 "DART" 또는 "MOCK"이다. DART는 OpenDART 공시 값이고 MOCK은 가상 값이다. operations는 생산능력과 품질 및 계약과 견적이 모두 가상 데이터이므로 항상 "MOCK"이다. financials 항목의 source도 "DART" 또는 "MOCK"이다. "STUB"은 사용하지 않는다.

## 조회 규칙

Python은 business-mcp의 business://catalog 리소스와 search_suppliers, find_suppliers, get_supplier 도구를 호출해 응답을 만든다.

- 카탈로그는 business://catalog에서 협력사가 있는 공정만 반환한다. 공정과 소재 및 지역은 이름순이다.
- 검색 조건은 카탈로그 값과 정확히 일치해야 한다. business-mcp의 공정과 소재 및 지역 별칭은 적용하지 않는다. 카탈로그에 없는 공정과 소재 및 지역은 도구를 호출하지 않고 200과 빈 items를 반환한다.
- 검색은 공정의 소재마다 search_suppliers를 호출한 뒤 기업별로 묶고 supplier_id 순서로 정렬한 다음 limit을 적용한다. 소재 하나의 결과가 도구의 최대 행 수 100에 도달하면 누락 가능성이 있으므로 500 INTERNAL_ERROR를 반환한다.
- 상세 ID는 SUP-0001처럼 정확한 형식만 조회한다. 숫자만 있는 ID와 회사명 및 소문자 접두어는 404 SUPPLIER_NOT_FOUND다.
- on_time_delivery_rate는 DB의 0..1 비율에 100을 곱해 소수 첫째 자리로 반올림한다. debt_ratio는 부채총계를 자본총계로 나눈 백분율이며 자본총계가 0 이하이거나 값이 없으면 null이다. existing_contract는 기존 계약이 없으면 null이다.

## 오류

- 400 VALIDATION_ERROR: 형식과 범위 오류.
- 401 UNAUTHORIZED: Python 내부 키 오류. Spring 공개 응답에서는 502 UPSTREAM_INVALID_RESPONSE.
- 404 SUPPLIER_NOT_FOUND: 기업 없음.
- 503 UPSTREAM_UNAVAILABLE: Python 또는 business-mcp와 협력사 DB에 연결할 수 없음. retryable=true. 원인 메시지는 노출하지 않는다.
- 504 UPSTREAM_TIMEOUT: 처리 시간 초과. retryable=true.
- 502 UPSTREAM_INVALID_RESPONSE: Spring이 응답 검증에 실패함. retryable=false.
- 500 INTERNAL_ERROR: 예상하지 못한 오류 또는 검색 결과 누락 가능성.

Spring은 DB 값을 알 수 없으므로 fixture와 비교하지 않는다. schema_version과 mode, 필드와 자료형, 출처 값을 검증한다. 검색 결과가 요청한 조건과 limit을 만족하고 supplier_id 순서인지 확인한다. 상세는 요청한 기업 ID와 같은지 확인한다.
