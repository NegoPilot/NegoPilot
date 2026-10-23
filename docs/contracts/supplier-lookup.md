# 협력사 페이지 조회 API v0.4

일반 협력사 화면의 전체 조회와 선택 필터 및 이름 또는 ID 검색 계약이다. Vue → Spring → Python FastAPI → business-mcp → PostgreSQL 순서로 동작한다. 조회는 항상 실제 DB를 읽으며 모델을 호출하지 않는다. 대화 실행 모드와 별개다.

## 경로와 호환성

| 공개 경로 | 내부 경로 | 반환 데이터 |
| --- | --- | --- |
| GET /api/business/catalog | GET /internal/business/catalog | 공정별 소재와 지역 카탈로그 |
| GET /api/suppliers | GET /internal/suppliers | 협력사 페이지 |
| GET /api/suppliers/{supplier_id} | GET /internal/suppliers/{supplier_id} | 확장 상세 |
| GET /api/suppliers/{supplier_id}/quotes | GET /internal/suppliers/{supplier_id}/quotes | 견적 페이지 |

조회 성공 응답은 `{schema_version:"0.4",mode:"live",data:{...}}`다. 대화의 v0.3 계약은 바뀌지 않는다. 이번 변경은 조회 소비자인 Vue와 Spring 및 Python을 함께 전환한다. 구버전 조회 응답 형태는 유지하지 않는다. 과거 v1 경로를 복원하지 않는다.

`limit`은 이전 호출자의 페이지 크기 입력을 위한 별칭이다. `page`와 `size`가 모두 없을 때만 허용한다. 함께 보내면 400이다. `limit=3`은 `page=0&size=3`이며 응답은 새 페이지 형식이다.

## 기본 조회와 선택 필터

검색 조건을 생략하면 전체 ACTIVE 협력사를 조회한다. 생산능력과 품질 및 계약이 없는 기업도 포함한다. 비활성 기업의 상세와 견적은 없는 기업과 동일한 404다.

```http
GET /api/suppliers
GET /api/suppliers?q=정밀&page=0&size=20&sort=name&direction=asc
GET /api/suppliers?q=sup-0001
GET /api/suppliers?material=AL6061
GET /api/suppliers?process=CNC가공
GET /api/suppliers?process=CNC가공&material=AL6061&region=경남
```

| 파라미터 | 규칙 |
| --- | --- |
| q | 이름 또는 ID 부분 일치. 앞뒤 공백 제거 후 빈 값은 조건 없음. 최대 100자. 영문 대소문자 무시. `%`와 `_`는 검색용 와일드카드가 아닌 문자 |
| process / material / region | 각각 선택. 공정 없이 소재만 선택 가능. 지정했다면 앞뒤 공백 제거 후 1부터 100자 |
| iatf_required / iso14001_required | true 또는 false. 기본 false는 인증 여부 전체 |
| min_monthly_capacity | 선택 정수. 1부터 10000000까지. 단위 개/월 |
| page | 0부터 1000000까지. 기본 0. 화면에서는 1부터 표시 |
| size | 1부터 100까지. 기본 20. 전체 결과 수 상한이 아닌 페이지 크기 |
| sort | 아래 허용 키. 기본 name |
| direction | asc 또는 desc. 기본 asc |

이름과 ID는 OR로 검색한다. 검색어와 다른 필터는 AND로 적용한다. 공정과 소재 및 최소 생산능력은 동일한 capability 행이 모두 만족해야 한다. 서로 다른 행의 공정과 소재를 결합하지 않는다. 조건이 없으면 기업의 전체 생산능력을 반환한다. 생산능력이 없는 기업은 빈 배열을 반환한다.

문자 필터는 저장값의 정확한 일치로 비교한다. 이름과 ID 검색을 제외하면 챗봇 도구의 별칭 정규화는 적용하지 않는다. 알 수 없는 공정과 소재 및 지역의 유효한 문자열은 빈 결과다. 알 수 없는 파라미터와 중복 파라미터 및 잘못된 자료형과 범위는 400이다.

페이지 데이터는 `items`와 `page` 및 `size`와 `returned_count` 및 `total`과 `total_pages` 및 `sort`와 `direction`이다. `total`은 필터에 맞는 서로 다른 기업 수다. `total_pages=ceil(total/size)`이며 빈 결과는 0이다. 범위를 벗어난 페이지는 전체 개수를 유지하고 빈 items를 반환한다.

## 정렬

DB에서 전체 필터를 먼저 적용한 뒤 정렬과 페이지 분할을 수행한다. 모든 방향에서 결측값은 마지막이다. 동률의 보조 키는 `supplier_id ASC`다. 이름과 지역은 소문자로 변환한 UTF-8 C 정렬을 사용한다. 괄호와 숫자 및 회사 형태 표기를 삭제하지 않는다. 한글 음절은 가나다순이다. 영문은 대소문자 구분 없이 ABC순이며 혼합 문자열도 동일한 규칙을 따른다.

| 정렬 키 | 값 |
| --- | --- |
| name / region / founded_year | 기업명과 지역 및 설립연도 |
| monthly_capacity | 조건에 일치한 생산능력 중 최대값. 조건이 없으면 보유 능력 전체 중 최대값 |
| defect_ppm / on_time_delivery_rate | 불량 ppm과 납기 준수율 |
| audit_grade / years_dealing | A부터 D까지 감사등급과 거래기간 |
| payment_days / raw_material_ratio | 대금 지급기한과 원재료 비중 |
| latest_quote_date | 선택 공정과 소재에 맞는 가장 최근 견적일. 조건이 없으면 전체 견적 기준 |
| latest_quote_price | 최신 견적 단가. 공정과 소재가 모두 있어야 하며 없으면 400 |

최대 생산능력은 서로 다른 공정의 능력을 더한 값이 아니다. 대표값의 공정과 소재를 함께 표시한다. 최신 견적일이 같으면 `quote_id DESC`로 대표 견적을 선택한다. 데이터가 바뀌지 않은 상태에서는 페이지 간 중복과 누락이 없다. 동시 등록이나 수정이 발생하면 전체 개수와 페이지 내용이 바뀔 수 있으므로 화면에서 다시 조회한다.

## 목록과 상세

목록은 기존 ID와 이름 및 지역과 인증 및 matched_capabilities와 provenance에 아래 정보를 추가한다. 목록 비교를 위해 각 기업의 상세 API를 자동 호출하지 않는다.

- founded_year와 employees 및 updated_at
- max_monthly_capacity
- quality: defect_ppm과 on_time_delivery_rate 및 audit_grade와 years_dealing
- existing_contract: payment_days와 price_linkage 및 raw_material_ratio와 auto_renewal. 계약이 없으면 null
- latest_quote: quote_id와 process 및 material과 unit_price_krw 및 quoted_at과 source. 없으면 null

상세는 기존 capabilities와 quality 및 financials와 recent_quotes 및 existing_contract와 provenance를 유지한다. 추가 기본정보는 founded_year와 employees 및 corp_code와 stock_code 및 induty_code와 biz_no_masked 및 created_at과 updated_at이다. 재무는 기존 매출과 영업이익 및 부채비율에 fs_div와 net_income_krw 및 total_assets_krw와 total_liabilities_krw 및 total_equity_krw를 포함한다.

품질 행이 없어도 quality 객체는 유지하며 내부 값은 null이다. 생산능력과 재무 및 견적은 미등록 시 빈 배열이다. 상세 ID는 `SUP-[0-9]{4,18}` 형식이며 요청한 정규 ID와 동일한 기업만 반환한다. 숫자만 있는 ID나 이름을 상세 경로에서 대신 해석하지 않는다.

## 전체 견적 이력

상세의 recent_quotes는 최근 5건 요약이다. 전체 견적은 별도 페이지 경로로 조회한다. 페이지 응답에는 목록 메타데이터에 supplier_id가 추가된다. 견적은 quote_id로 식별한다. 각 견적의 source는 MOCK 또는 MANUAL이며 최신 main의 V3 컬럼을 사용한다. 목록의 최신 견적과 상세의 최근 견적 및 전체 견적 페이지에 모두 포함한다.

```http
GET /api/suppliers/SUP-0001/quotes?page=0&size=20&sort=quoted_at&direction=desc
GET /api/suppliers/SUP-0001/quotes?process=CNC가공&material=AL6061&sort=unit_price_krw&direction=asc
```

견적의 선택 필터는 process와 material이다. page와 size 범위는 협력사 목록과 같다. 정렬은 quoted_at 또는 unit_price_krw이며 기본 quoted_at 내림차순이다. 단가 정렬은 공정과 소재를 모두 지정해야 한다. 동률은 quote_id 내림차순이다. 견적이 없으면 total 0과 빈 items다.

## 단위와 출처

생산능력은 개/월이다. 불량률은 ppm이다. years_dealing은 년이다. 0은 신규 후보를 뜻한다. 대금 지급기한은 일이다. `_krw` 금액은 원이다. raw_material_ratio는 0부터 1까지의 비율이다. on_time_delivery_rate와 debt_ratio는 이미 백분율이므로 다시 100을 곱하지 않는다. 부채비율은 자본이 0 이하이거나 값이 없으면 null이다. 미등록을 0으로 대체하지 않는다.

provenance는 profile과 financials 및 operations다. profile은 DART 또는 MOCK 또는 MANUAL이다. financials는 DART 또는 MOCK 또는 MIXED 또는 UNKNOWN이다. operations는 MOCK 또는 MANUAL 또는 MIXED다. 프로필과 재무 및 운영 출처는 독립적이다. 기존 계약 자료는 MOCK으로 반영한다. 견적은 전체 이력의 source를 반영한다. 최근 5건 밖의 과거 견적도 출처 집계에 포함한다. 품질 자료가 없으면 기업의 기본 quality_source 값은 집계하지 않는다.

DART 기업의 기본정보와 재무를 실제 DB에서 읽어도 생산능력과 인증 및 품질과 계약과 견적은 실습용 가상 자료일 수 있다. seed 견적은 MOCK이며 관리자 입력 견적은 MANUAL이다. MANUAL은 관리자 입력이며 외부 검증 완료를 뜻하지 않는다. 화면은 고정 MOCK 예시 배지 대신 영역별 출처와 견적별 출처를 표시한다.

created_at과 updated_at은 데이터 관리 시각이다. 원천자료의 수집일이나 품질 평가 기준일로 표시하지 않는다. 건별 거래 실적과 원천 기준일 및 견적별 수량과 유효기간은 현재 DB에 없어 이번에 값을 만들지 않는다. 담당자 정보는 mail-mcp의 별도 권한 범위로 유지한다.

## 구현 경계와 오류

UI 전용 페이지 도구를 통해 DB에서 기업별 count와 페이지를 계산한다. 기존 챗봇 search_suppliers와 top_n 및 추천 도구의 제한과 의미는 유지한다. 관리자 등록과 수정 API도 바뀌지 않는다.

Spring과 Python은 조회 계약의 필드와 자료형 및 ID와 필터 및 페이지 일관성을 검사한다. 실제 값을 고정 샘플과 비교하지 않는다. 필터가 없다면 생산능력 배열이 비어 있어도 허용한다.

- 400 VALIDATION_ERROR: 잘못된 요청
- 401 UNAUTHORIZED: 내부 키 오류. Spring에서는 upstream 오류로 처리
- 404 SUPPLIER_NOT_FOUND: 없는 기업 또는 비활성 기업
- 503 UPSTREAM_UNAVAILABLE: Python 또는 MCP와 DB 연결 실패
- 504 UPSTREAM_TIMEOUT: 처리 시간 초과
- 502 UPSTREAM_INVALID_RESPONSE: Spring의 상위 응답 검증 실패
- 500 INTERNAL_ERROR: 예상하지 못한 내부 오류

조회 실패를 가상 응답으로 대신하지 않는다. 인증과 회원 권한은 기존 정책을 유지한다.
