# frontend

구매 담당자가 쓰는 Vue 화면입니다. 레이아웃은 Figma 와이어프레임(SKALA-4조)을 따릅니다.
화면은 Spring 공개 API만 부르고, 개발 서버가 `/api`와 `/health`를 Spring(`127.0.0.1:8080`)으로 넘깁니다.

| API | 계약 | 쓰는 곳 |
| --- | --- | --- |
| `/api/v1/business/catalog`, `/api/v1/suppliers`, `/api/v1/suppliers/{id}` | [supplier-chat v0.2](../docs/contracts/supplier-chat.md) (stub 전용) | 협력사 검색·상세 |
| `/api/v2/conversations`, `/api/v2/conversations/{id}/messages` | [agent-generation v0.3](../docs/contracts/agent-generation.md), [직접 질문](../docs/contracts/direct-chat.md) | AI 도우미 패널, 근거 자료 상세 |

응답 타입은 [`src/api/types.ts`](src/api/types.ts)에 옮겨 두었습니다. 계약이 바뀌면 이 파일을 같은 PR에서 고칩니다.

## 화면

| 경로 | 와이어프레임 | 하는 일 |
| --- | --- | --- |
| `/` | 01 홈 | AI 도우미에게 묻기, RFQ 목록, AI 빠른 질문, 조회 가능한 데이터 |
| `/suppliers` | 02·03 협력사 탐색 | 왼쪽 검색 조건, 가운데 검색 결과와 선택 협력사 상세(탭 4개), 오른쪽 AI 도우미 패널 |
| `/evidence/:conversationId/:responseId/:evidenceId` | 04 근거 자료 상세 | AI 답변이 인용한 근거를 **새 탭**에서 단독으로 보여 줌 (사이드바 없음) |
| `/review` | 05 법규 검토 | 협상안 위험 검토 결과, 재검사 조건, 관련 법령 |
| `/login` | 07 로그인 | 이메일·비밀번호 형식 검사, 이메일 기억하기, 관리자 로그인 |
| `/signup` | 08 회원가입 | 필수 항목, 비밀번호 규칙(영문·숫자·특수문자 8자 이상)과 확인, 인증번호 6자리, 약관 동의 |
| `/signup/pending` | 09 가입 승인 대기 | 방금 신청한 정보 표시, '신청 정보 수정하기'는 입력값을 채운 회원가입으로 |
| `/admin` | 10 관리자 대시보드 | 회원·승인 대기·팀장·이용 정지 수, 가입 승인 요청, 최근 관리 이력 |
| `/admin/members` | 11 회원·권한 관리 | 가입 승인·반려(`?request=이메일`로 신청 선택), 회원 검색·역할·상태 필터, 역할 변경·이용 정지·재개, 페이지 이동 |
| `/admin/approvals`, `/admin/history` | (와이어프레임 없음) | 사이드바 '가입 승인'·'접근 이력'. 승인 대기 전체 목록과 관리 이력 전체 |
| `/admin/data` | 12 데이터 관리 | 협력사·법령 수, 등록 화면 바로가기, 등록 요청 목록(JSON 내려받기) |
| `/admin/data/suppliers/new` | 13 협력사 등록 | business-mcp 표(suppliers, capabilities, quality, contracts, quotes, financials)에 넣을 값 입력·검사, 저장 미리보기 |
| `/admin/data/laws/new` | 14 법령 등록 | 저장된 법령과 시행 판, legalize-kr에서 새 법령 받기, 개정본 원문(.md) 올리기 |

07~09는 상단 띠와 푸터만 있는 화면, 10~14는 관리자 전용 사이드바를 씁니다. 라우트의 `meta.layout`(`public`, `admin`)으로 고릅니다.
관리자 사이드바는 묶음 3개이고, 묶음 안 화면은 상단 탭으로 오갑니다: **현황**(관리 현황, 접근 이력, 데이터 현황), **회원 관리**(회원·권한 관리, 가입 승인), **등록**(협력사 등록, 법령 등록). 묶음은 [`src/layouts/AdminLayout.vue`](src/layouts/AdminLayout.vue)의 `GROUPS`에 있습니다.

협력사 화면의 검색 조건, 선택 협력사(`selected`), AI 패널 열림(`ai=1`), RFQ(`rfq`)는 URL query에 둡니다. 새로고침이나 뒤로 가기에도 그대로 남습니다.

### 협력사 검색 조건

| 조건 | 입력 | API 파라미터 |
| --- | --- | --- |
| 공정 (필수) | 텍스트. 카탈로그 공정을 제안 목록으로 보여 줌 | `process` |
| 소재 | 텍스트. 입력한 공정의 소재를 제안 | `material` |
| 지역 | select. 옵션은 카탈로그의 `regions` | `region` |
| 인증 | 체크박스 두 개. IATF 16949, ISO 14001. 체크했을 때만 보냄 (계약상 false는 전체) | `iatf_required`, `iso14001_required` |
| 최소 월 생산능력 | 텍스트(숫자, 쉼표 허용). 1~10,000,000 | `min_monthly_capacity` |

`iso14001_required`는 아직 계약에 없어서 Spring이 400으로 거절합니다. business-mcp DB에는 `iso14001` 컬럼이 있습니다. 계약에 추가하기 전까지는([#53](https://github.com/NegoPilot/NegoPilot/issues/53)) 이 조건을 넣으면 "아직 서버에서 지원하지 않는 조건" 안내가 나옵니다. 협력사 상세의 ISO 14001은 v0.2 응답에 값이 없어 "정보 없음"으로 보입니다.

### AI 도우미와 근거 자료

- AI 도우미는 화면에서 고른 협력사와 상관없이 질문 내용만으로 답합니다 ([직접 질문 계약](../docs/contracts/direct-chat.md), #64). 대화는 빈 선택(`selected_supplier_ids: []`)으로 만들고 메시지에는 `query`만 보냅니다. 기업은 질문 속 이름·지역으로 찾습니다.
- 협력사 검색·선택을 바꿔도 대화에 영향이 없습니다. 새 대화는 패널의 '새 대화' 버튼으로 시작합니다.
- `answer.notices`는 본문에 이미 들어 있어서 따로 붙이지 않습니다.
- 답변 본문의 `[1]`과 "근거 자료" 목록은 근거 자료 상세를 새 탭으로 엽니다. `grounded: false`면 "근거 자료 없음", 되묻기(`clarify`)면 선택지를 보여 줍니다.
- 새 탭에는 원래 화면의 상태가 없어서, URL의 대화 ID로 `GET /api/v2/conversations/{id}`를 다시 불러와 같은 `response_id`의 답변과 근거 ID를 찾습니다. Spring은 대화를 메모리에만 저장하므로 서버를 다시 시작하면 "대화가 만료됐습니다" 안내가 나옵니다.
- 협력사 근거(`get_supplier`)는 조회 데이터 표로, 검색 결과(`supplier_hit`) 같은 다른 업무 근거는 항목·값 표로, 법령 근거는 조문 원문으로 보여 줍니다. 외부 원문 링크는 HTTP(S)만 엽니다.

### 아직 API가 없는 부분

| 화면 | 지금 | API가 생기면 |
| --- | --- | --- |
| 홈의 RFQ 목록 | business-mcp 시드 데이터를 [`src/data/rfqs.ts`](src/data/rfqs.ts)에 옮겨 표시 | RFQ 조회(list_rfqs) API |
| 법규 검토 | 예시 협상안과 check_offer 기준표로 만든 [`src/data/lawReview.ts`](src/data/lawReview.ts). 재검사 버튼 비활성 | 협상안 검사(check_offer) API |
| 협력사·법령 등록 | 입력을 DB 열 이름 그대로의 JSON으로 만들고 화면 안 등록 요청 목록에 둠. 백분율은 0..1 비율로, 사업자번호는 뒤 5자리를 가려 저장. 법령은 law-mcp가 파일을 읽어서 `fetch_laws.py` TARGETS·ALIASES 또는 원문 파일 저장 위치를 안내 | 협력사 등록 API(business-mcp 쓰기), 법령 반영 API |
| 로그인·회원가입·관리자 | [`src/data/accounts.ts`](src/data/accounts.ts)의 예시 회원과 [`src/stores/admin.ts`](src/stores/admin.ts)의 화면 안 상태. 로그인은 형식만 확인하고 이 브라우저에 예시 로그인 상태([`src/stores/session.ts`](src/stores/session.ts))를 남김. 로그인 전에는 업무·관리자 화면이 로그인으로 넘어가고, 관리자 화면은 '관리자 로그인'으로만 들어감. 승인·반려·역할 변경은 새로고침하면 처음으로 돌아감 | 인증·회원 API (라우터 확인을 서버 세션 확인으로 교체) |

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
- 최소 월 생산능력 `50,000` → SUP-0034만 (60,000개 공정만 표시)
- "AI 도우미 열기" → `해솔정밀에 대해 알려줘`, `부산 협력사 찾아줘`, `협력사 목록 보여줘`, `해솔에 대해 알려줘`(후보 선택지), `하도급법상 대금 지급 기한은 며칠이야?`, `결제 90일로 해도 돼?`, `RFQ가 뭐야?`, `하도급법 제4조 보여줘`
- `이 회사의 재무 상태를 알려줘` → 기업을 되묻고, `SUP-0041`이라고 답하면 원래 질문을 이어서 답함
- ISO 14001 체크 후 검색 → "아직 서버에서 지원하지 않는 조건" 안내
- `http://127.0.0.1:5173` 접속 → 로그인 화면. 아무 이메일 형식과 비밀번호로 로그인 → 홈, 사이드바 아래 '로그아웃'. '관리자 로그인' → `/admin`
- 관리자 로그인 → 사이드바 '협력사 등록'에서 필수 항목 입력 → '등록 요청 저장' → '데이터 현황'에 요청이 생기고 JSON으로 내려받기
- '법령 등록' → '원문 파일 올리기'에 `mcp-servers/law-mcp/data/raw/subcontract_act/*.md` 하나를 올리면 제목·시행일·조문 수를 읽어 보여 줌
- `/signup`에서 회사 코드 `HANBIT-2026`으로 신청 → `/signup/pending`에 소속 회사 한빛오토텍 → `/admin`의 승인 대기에 신청자가 추가됨 (같은 탭, 새로고침 전)
- 답변의 `[1]` 또는 근거 자료 링크 → 새 탭에서 근거 자료 상세, 새로고침해도 같은 내용
- Spring을 끄면 "백엔드 서버에 연결할 수 없습니다"

## 폴더 구조

```
src/
  api/          계약 타입, fetch 래퍼(오류 봉투 처리), 협력사(v1)·대화(v2) API
  components/   검색 조건 패널, 협력사 상세(탭), AI 도우미 패널, 빈 상태, 오류 안내
  views/        홈, 협력사, 근거 자료 상세, 법규 검토, 404, auth/(로그인·회원가입·승인 대기), admin/(관리자)
  layouts/      로그인 전 화면(상단 띠+푸터), 관리자 사이드바
  stores/       예시 로그인 상태, 관리자 화면이 함께 쓰는 예시 상태, 가입 신청 내용(sessionStorage), 데이터 등록 요청
  data/         API가 아직 없는 화면의 시드·예시 데이터
  utils/        숫자 포맷(null → "정보 없음"), 오류 문구, 검색 조건 ↔ URL, 근거 파싱, 출처 표시, 입력 검사, 협력사·법령 등록 폼 ↔ 저장 형식
  router/       라우트 (근거 자료 상세는 사이드바 없는 standalone)
  styles/       와이어프레임 기준 공통 스타일
```
