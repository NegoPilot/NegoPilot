# 협력사 live 조회 API v2

협력사 DB를 읽는 조회 HTTP 계약이다. business-mcp(PostgreSQL)의 실제 협력사 데이터를 반환한다. 검색 요청 규칙과 응답 구조는 [협력사 조회와 선택 기업 챗봇 API v0.2](supplier-chat.md)의 조회 API를 따른다. 기업 ID와 출처 및 버전 표시는 아래 v2 규칙을 적용한다. 명세는 [supplier-lookup.openapi.json](supplier-lookup.openapi.json)이다.

## 범위와 책임

연결 흐름은 Vue → Spring → Python FastAPI → business-mcp 순서다. v1 조회 경로는 지금처럼 stub 전용으로 fixture를 읽는다. v2 조회 경로는 AGENT_EXECUTION_MODE와 관계없이 항상 business-mcp를 읽는다. 어느 경로도 모델을 호출하지 않는다. 대화 경로는 이 계약에 포함하지 않는다.

| Spring 공개 경로 | Python 내부 경로 | 응답 data |
| --- | --- | --- |
| GET /api/v2/business/catalog | GET /internal/v2/business/catalog | processes와 regions |
| GET /api/v2/suppliers | GET /internal/v2/suppliers | items와 returned_count 및 limit |
| GET /api/v2/suppliers/{supplier_id} | GET /internal/v2/suppliers/{supplier_id} | SupplierDetail |

조회 성공 응답은 `{schema_version:"0.3",mode:"live",data:{...}}`다. 검색 query parameter와 오류 봉투는 v1과 같다. 검색 조건은 `process`와 `material` 및 `region`이다. 인증 조건은 `iatf_required`와 `iso14001_required`다. `min_monthly_capacity`와 `limit`도 지원한다.

## 데이터 출처

`provenance`는 `{profile,financials,operations}`다. `STUB`은 사용하지 않는다.

- `profile`은 `suppliers.source` 값이다. `DART`는 OpenDART 공시 값이다. `MOCK`은 가상 값이다. `MANUAL`은 관리자 입력 또는 수정 값이다.
- `financials`는 해당 기업의 모든 재무 행의 실제 `source`를 집계한다. 한 종류면 `DART` 또는 `MOCK`이다. 두 출처가 함께 있으면 `MIXED`다. 재무 행이 없으면 `UNKNOWN`이다. 기본 정보를 수정해 `profile`이 `MANUAL`로 바뀌어도 재무 출처는 바뀌지 않는다. 상세의 각 재무 행은 `DART` 또는 `MOCK`만 허용한다.
- `operations`는 인증과 생산능력 및 존재하는 품질 자료의 저장된 출처를 집계한다. 계약이나 견적이 있으면 현재 합성 자료의 출처인 `MOCK`도 포함한다. 전부 가상이면 `MOCK`이다. 전부 관리자 입력이면 `MANUAL`이다. 함께 있으면 `MIXED`다. 품질 행이 없을 때 그 기업의 기본 `quality_source` 값은 집계하지 않는다.

검색 요약과 상세는 같은 출처 집계 규칙을 사용한다. 운영 출처 집계는 검색에 일치한 한 공정만이 아니라 기업의 전체 운영 자료를 설명한다.

## 조회 규칙

Python은 business-mcp의 `business://catalog` 리소스와 `search_suppliers` 및 `find_suppliers`와 `get_supplier` 도구를 호출해 응답을 만든다.

- 카탈로그는 business://catalog에서 협력사가 있는 공정만 반환한다. 공정과 소재 및 지역은 이름순이다.
- 검색 조건은 카탈로그 값과 정확히 일치해야 한다. business-mcp의 공정과 소재 및 지역 별칭은 적용하지 않는다. 카탈로그에 없는 공정과 소재 및 지역은 도구를 호출하지 않고 200과 빈 items를 반환한다.
- 검색은 공정의 소재마다 search_suppliers를 호출한 뒤 기업별로 묶고 supplier_id 순서로 정렬한 다음 limit을 적용한다. 소재 하나의 결과가 도구의 최대 행 수 100에 도달하면 누락 가능성이 있으므로 500 INTERNAL_ERROR를 반환한다.
- 상세 요청과 검색 및 상세 응답의 ID는 `SUP-[0-9]{4,18}` 형식이다. `SUP-0001`과 `SUP-10000`을 지원한다. 숫자만 있는 ID와 회사명 및 소문자 접두어는 400 `VALIDATION_ERROR`다. 형식에 맞아도 저장된 정규 ID와 다르면 404 `SUPPLIER_NOT_FOUND`다. 느슨한 MCP ID 정규화 결과를 다른 기업의 상세로 대신 반환하지 않는다.
- 비활성 기업은 카탈로그와 검색에서 제외한다. 비활성 기업의 상세는 없는 기업과 동일하게 404다. 관리자 등록 기업은 품질 자료가 없어도 조회할 수 있다. 이때 `quality.defect_ppm`과 `quality.on_time_delivery_rate`는 `null`이다.
- on_time_delivery_rate는 DB의 0..1 비율에 100을 곱해 소수 첫째 자리로 반올림한다. debt_ratio는 부채총계를 자본총계로 나눈 백분율이며 자본총계가 0 이하이거나 값이 없으면 null이다. existing_contract는 기존 계약이 없으면 null이다.

## 오류

- 400 VALIDATION_ERROR: 형식과 범위 오류.
- 401 UNAUTHORIZED: Python 내부 키 오류. Spring 공개 응답에서는 502 UPSTREAM_INVALID_RESPONSE.
- 404 SUPPLIER_NOT_FOUND: 기업 없음.
- 503 UPSTREAM_UNAVAILABLE: Python 또는 business-mcp와 협력사 DB에 연결할 수 없음. retryable=true. 원인 메시지는 노출하지 않는다.
- 504 UPSTREAM_TIMEOUT: 처리 시간 초과. retryable=true.
- 502 UPSTREAM_INVALID_RESPONSE: Spring이 응답 검증에 실패함. retryable=false.
- 500 INTERNAL_ERROR: 예상하지 못한 오류 또는 검색 결과 누락 가능성.

Spring은 DB 값을 알 수 없으므로 fixture와 비교하지 않는다. `schema_version`과 `mode` 및 필드와 자료형과 출처 값을 검증한다. 검색 결과가 요청한 조건과 `limit`을 만족하고 `supplier_id` 순서인지 확인한다. 상세는 요청한 기업 ID와 같은지 확인한다. 상세의 재무 출처 요약이 실제 재무 행의 출처 집계와 같은지도 확인한다.
