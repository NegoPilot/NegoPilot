# frontend

구매 담당자가 쓰는 Vue 화면입니다. Spring 공개 API만 부르고, 개발 서버가 `/api`와 `/health`를 Spring(`127.0.0.1:8080`)으로 넘깁니다.
응답 형식은 [supplier-chat 계약 v0.2](../docs/contracts/supplier-chat.md)를 따르고, 타입은 [`src/api/types.ts`](src/api/types.ts)에 옮겨 두었습니다.

## 화면

| 경로 | 화면 | 하는 일 |
| --- | --- | --- |
| `/suppliers` | 협력사 탐색 | 검색 조건으로 협력사 찾기. 조건은 URL query에 남아 새로고침·뒤로 가기에도 유지 |
| `/suppliers/:supplierId` | 협력사 상세 | 기본 정보, 품질·납기, 생산능력, 재무, 최근 견적. "AI에게 이 회사 질문하기"로 대화 시작 |
| `/chat/:conversationId` | 협상 도우미 | 선택한 기업에 대해 질문. 답변, 기업 카드, 되묻기 선택지 |
| `/evidence/:conversationId/:responseId/:evidenceId` | 근거 자료 상세 | 답변 카드의 "근거 자료 상세"를 누르면 **새 탭**으로 열림 |

### 협력사 검색 조건

| 조건 | 입력 | API 파라미터 |
| --- | --- | --- |
| 공정 (필수) | 텍스트. 카탈로그 공정을 제안 목록으로 보여 줌 | `process` |
| 소재 | 텍스트. 입력한 공정의 소재를 제안 | `material` |
| 최소 월 생산능력 | 텍스트(숫자, 쉼표 허용) | `min_monthly_capacity` |
| 지역 | select. 옵션은 `GET /api/v1/business/catalog`의 `regions` | `region` |
| IATF 16949 인증 | 체크박스. 체크했을 때만 보냄 (계약상 false는 전체) | `iatf_required` |

`min_monthly_capacity`는 아직 계약에 없어서 Spring이 400으로 거절합니다. [#43](https://github.com/NegoPilot/NegoPilot/issues/43)에서 계약에 추가할 예정이고, 그 전까지는 이 조건을 넣으면 "아직 서버에서 지원하지 않는 조건" 안내가 나옵니다.

### 근거 자료 상세가 새 탭에서 동작하는 방식

새 탭에는 원래 화면의 상태가 없어서, URL의 대화 ID로 `GET /api/v1/conversations/{id}`를 다시 불러와 같은 `response_id`의 답변과 `evidence_id`를 찾습니다.
Spring은 대화를 메모리에만 저장하므로 서버를 다시 시작하면 "대화가 만료됐습니다" 안내가 나옵니다.

## 실행

Node.js 22.12 이상이 필요합니다. 화면을 띄우기 전에 Python 내부 API와 Spring을 먼저 실행합니다 (둘 다 `AGENT_EXECUTION_MODE=stub`).

```bash
# 1. Python 내부 API (agent/README.md 참고, 127.0.0.1:9200)
cd agent
AGENT_EXECUTION_MODE=stub AGENT_INTERNAL_API_KEY=local-test-key \
  uv run uvicorn negopilot_agent.api.app:app --host 127.0.0.1 --port 9200

# 2. Spring (backend/README.md 참고, 127.0.0.1:8080)
mvn -f backend/pom.xml -B -ntp -DskipTests package
AGENT_EXECUTION_MODE=stub AGENT_INTERNAL_API_KEY=local-test-key AGENT_BASE_URL=http://127.0.0.1:9200 \
  java -jar backend/target/negopilot-backend-0.1.0-SNAPSHOT.jar

# 3. 화면 (http://127.0.0.1:5173)
cd frontend
npm install
npm run dev
```

Spring 주소가 다르면 `NEGOPILOT_BACKEND_URL=http://주소:포트 npm run dev`로 바꿉니다.

```bash
npm run typecheck   # vue-tsc 타입 검사
npm run build       # 타입 검사 + 배포용 빌드 (dist/)
```

## 테스트 데이터로 확인할 것

stub 데이터는 [`docs/contracts/fixtures/supplier-chat.json`](../docs/contracts/fixtures/supplier-chat.json)의 가상 기업 3곳입니다.

- 공정 `다이캐스팅` → SUP-0034(인천), SUP-0041(부산). 지역 `부산` + IATF 체크 → SUP-0041
- 공정 `단조` → 빈 결과
- 상세 → "AI에게 이 회사 질문하기" → "이 회사에 대해 알려줘" → 기업 카드와 "근거 자료 상세"(새 탭)
- 지원하지 않는 질문 → 지원 질문 3개를 선택지로 보여 줌
- Spring을 끄면 "백엔드 서버에 연결할 수 없습니다"

## 폴더 구조

```
src/
  api/          계약 타입, fetch 래퍼(오류 봉투 처리), 협력사·대화 API
  components/   검색 폼, 협력사 상세 패널, 대화 카드·메시지, 빈 상태, 오류 안내
  views/        화면 5개 (탐색, 상세, 대화, 근거 상세, 404)
  utils/        숫자 포맷(null → "정보 없음"), 오류 문구, 검색 조건 ↔ URL 변환
  router/       라우트
  styles/       기본 스타일 (Figma 와이어프레임 반영 전)
```

레이아웃은 Figma 와이어프레임(SKALA-4조)을 반영하기 전의 기본 스타일입니다.
