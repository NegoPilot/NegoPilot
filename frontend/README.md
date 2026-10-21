# frontend

구매 담당자가 쓰는 Vue 화면입니다. 레이아웃은 Figma 와이어프레임(SKALA-4조)을 따릅니다.
화면은 Spring 공개 API만 부르고, 개발 서버가 `/api`와 `/health`를 Spring(`127.0.0.1:8080`)으로 넘깁니다.

| API | 계약 | 쓰는 곳 |
| --- | --- | --- |
| `/api/v1/business/catalog`, `/api/v1/suppliers`, `/api/v1/suppliers/{id}` | [supplier-chat v0.2](../docs/contracts/supplier-chat.md) (stub 전용) | 협력사 검색·상세 |
| `/api/v2/conversations`, `/api/v2/conversations/{id}/messages` | [agent-generation v0.3](../docs/contracts/agent-generation.md) | AI 도우미 패널, 근거 자료 상세 |

응답 타입은 [`src/api/types.ts`](src/api/types.ts)에 옮겨 두었습니다. 계약이 바뀌면 이 파일을 같은 PR에서 고칩니다.

## 화면

| 경로 | 와이어프레임 | 하는 일 |
| --- | --- | --- |
| `/` | 01 홈 | AI 도우미에게 묻기, RFQ 목록, AI 빠른 질문, 조회 가능한 데이터 |
| `/suppliers` | 02·03 협력사 탐색 | 왼쪽 검색 조건, 가운데 검색 결과와 선택 협력사 상세(탭 4개), 오른쪽 AI 도우미 패널 |
| `/evidence/:conversationId/:responseId/:evidenceId` | 04 근거 자료 상세 | AI 답변이 인용한 근거를 **새 탭**에서 단독으로 보여 줌 (사이드바 없음) |
| `/review` | 05 법규 검토 | 협상안 위험 검토 결과, 재검사 조건, 관련 법령 |

협력사 화면의 검색 조건, 선택 협력사(`selected`), AI 패널 열림(`ai=1`), RFQ(`rfq`)는 URL query에 둡니다. 새로고침이나 뒤로 가기에도 그대로 남습니다.

### 협력사 검색 조건

| 조건 | 입력 | API 파라미터 |
| --- | --- | --- |
| 공정 (필수) | 텍스트. 카탈로그 공정을 제안 목록으로 보여 줌 | `process` |
| 소재 | 텍스트. 입력한 공정의 소재를 제안 | `material` |
| 지역 | select. 옵션은 카탈로그의 `regions` | `region` |
| IATF 인증 | 체크박스. 체크했을 때만 보냄 (계약상 false는 전체) | `iatf_required` |
| 최소 월 생산능력 | 텍스트(숫자, 쉼표 허용) | `min_monthly_capacity` |

`min_monthly_capacity`는 아직 계약에 없어서 Spring이 400으로 거절합니다. [#43](https://github.com/NegoPilot/NegoPilot/issues/43)에서 계약에 추가하기 전까지는 이 조건을 넣으면 "아직 서버에서 지원하지 않는 조건" 안내가 나옵니다.

### AI 도우미와 근거 자료

- 선택한 협력사를 문맥(`selected_supplier_ids`)으로 v2 대화를 만들고, 협력사를 바꾸면 새 대화로 시작합니다.
- 답변 본문의 `[1]`과 "근거 자료" 목록은 근거 자료 상세를 새 탭으로 엽니다. `grounded: false`면 "근거 자료 없음", 되묻기(`clarify`)면 선택지를 보여 줍니다.
- 새 탭에는 원래 화면의 상태가 없어서, URL의 대화 ID로 `GET /api/v2/conversations/{id}`를 다시 불러와 같은 `response_id`의 답변과 근거 ID를 찾습니다. Spring은 대화를 메모리에만 저장하므로 서버를 다시 시작하면 "대화가 만료됐습니다" 안내가 나옵니다.
- 협력사 근거(`get_supplier`)는 조회 데이터 표로, 법령 근거는 조문 원문으로 보여 줍니다.

### 아직 API가 없는 부분

| 화면 | 지금 | API가 생기면 |
| --- | --- | --- |
| 홈의 RFQ 목록 | business-mcp 시드 데이터를 [`src/data/rfqs.ts`](src/data/rfqs.ts)에 옮겨 표시 | RFQ 조회(list_rfqs) API |
| 법규 검토 | 예시 협상안과 check_offer 기준표로 만든 [`src/data/lawReview.ts`](src/data/lawReview.ts). 재검사 버튼 비활성 | 협상안 검사(check_offer) API |

## 실행

Node.js 22.12 이상이 필요합니다. 화면을 띄우기 전에 Python 내부 API와 Spring을 먼저 실행합니다. stub 모드는 모델·MCP·DB 없이 테스트 데이터로 동작합니다.

```bash
# 1. Python 내부 API (127.0.0.1:9200)
cd agent && uv sync
AGENT_EXECUTION_MODE=stub AGENT_INTERNAL_API_KEY=local-test-key \
  uv run uvicorn negopilot_agent.api.app:app --host 127.0.0.1 --port 9200

# 2. Spring (저장소 루트, 127.0.0.1:8080)
mvn -f backend/pom.xml -B -ntp -DskipTests package
AGENT_EXECUTION_MODE=stub AGENT_INTERNAL_API_KEY=local-test-key AGENT_BASE_URL=http://127.0.0.1:9200 \
  java -jar backend/target/negopilot-backend-0.1.0-SNAPSHOT.jar

# 3. 화면 (http://127.0.0.1:5173)
cd frontend && npm install && npm run dev
```

Spring 주소가 다르면 `NEGOPILOT_BACKEND_URL=http://주소:포트 npm run dev`로 바꿉니다.

```bash
npm run typecheck   # vue-tsc 타입 검사
npm run build       # 타입 검사 + 배포용 빌드 (dist/)
```

## 테스트 데이터로 확인할 것

stub 모드의 협력사는 [`docs/contracts/fixtures/supplier-chat.json`](../docs/contracts/fixtures/supplier-chat.json)의 가상 기업 3곳입니다.
AI 도우미는 stub 모드에서 정해진 질문만 답합니다 (그 밖의 질문은 "처리할 수 없는 질문" 안내).

- 홈 RFQ-2026-001 "상세 →" → 협력사 화면에 다이캐스팅 / ADC12 조건이 채워지고 SUP-0034, SUP-0041 표시
- 지역 `부산` + IATF 체크 → SUP-0041만
- "AI 도우미 열기" → 협력사 선택 상태에서 `이 회사에 대해 알려줘`, `이 회사의 재무 상태를 알려줘`, `납기와 품질은 어때?`
- 협력사 없이도 `하도급법 제4조 보여줘`(법령 근거, 원문 링크), `그 업체 괜찮아?`(되묻기 선택지)
- 답변의 `[1]` 또는 근거 자료 링크 → 새 탭에서 근거 자료 상세, 새로고침해도 같은 내용
- Spring을 끄면 "백엔드 서버에 연결할 수 없습니다"

## 폴더 구조

```
src/
  api/          계약 타입, fetch 래퍼(오류 봉투 처리), 협력사(v1)·대화(v2) API
  components/   검색 조건 패널, 협력사 상세(탭), AI 도우미 패널, 빈 상태, 오류 안내
  views/        홈, 협력사, 근거 자료 상세, 법규 검토, 404
  data/         API가 아직 없는 화면의 시드·예시 데이터
  utils/        숫자 포맷(null → "정보 없음"), 오류 문구, 검색 조건 ↔ URL, 근거 파싱, 출처 표시
  router/       라우트 (근거 자료 상세는 사이드바 없는 standalone)
  styles/       와이어프레임 기준 공통 스타일
```
