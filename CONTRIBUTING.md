# 협업 규칙

NegoPilot 팀이 코드를 함께 관리하는 방법입니다. 처음 합류했다면 이 문서부터 읽어 주세요.
담당별로 무엇부터 개발할지는 [개발 시작 가이드](docs/GETTING_STARTED.md)를 보세요.

## 작업 흐름

```
1. 이슈 생성      템플릿(기능 / 버그 / 작업) 중 하나를 골라 작성
2. 브랜치 생성    종류/이슈번호-짧은-설명
3. 작업과 커밋
4. PR 생성        본문에 "Closes #이슈번호"
5. 리뷰           다른 팀원 1명 이상 승인
6. 머지           Squash merge → 이슈 자동 종료, 브랜치 삭제
```

`main`에는 직접 푸시하지 않고, 모든 변경은 PR로 들어갑니다.

## 브랜치 이름

`종류/이슈번호-짧은-설명` (영어 소문자, 단어는 `-`로 연결)

| 종류 | 용도 | 예 |
| --- | --- | --- |
| `feat` | 새 기능 | `feat/12-business-mcp` |
| `fix` | 버그 수정 | `fix/15-law-search-score` |
| `docs` | 문서 | `docs/18-readme-setup` |
| `refactor` | 동작은 그대로, 구조 개선 | `refactor/20-mcp-client` |
| `test` | 테스트 추가·보강 | `test/21-check-offer-cases` |
| `chore` | 설정, 의존성, CI | `chore/22-docker-compose` |

## 커밋 메시지

`종류: 무엇을 했는지 (#이슈번호)`

```
feat: business-mcp 기업 검색 도구 추가 (#12)
fix: 조문 번호에 공백이 있으면 조회되지 않던 문제 수정 (#15)
docs: law-mcp 실행 방법 정리 (#18)
```

- 한 커밋에는 한 가지 변경만 담습니다.
- 커밋은 본인 GitHub 계정의 이름과 이메일로 작성합니다.

## PR 규칙

- PR 템플릿의 항목을 채웁니다. 특히 **확인 방법**은 리뷰어가 그대로 따라 할 수 있게 적습니다.
- PR 하나에 기능 하나. 리뷰어가 30분 안에 볼 수 있는 크기로 나눕니다.
- 리뷰 요청 후 하루 안에 리뷰가 없으면 팀 채팅에서 알려 주세요.
- 승인 후 코드를 바꾸면 승인이 풀리므로 다시 리뷰를 받습니다.

## 리뷰 규칙

- 다른 팀원 **1명 이상**이 승인해야 머지할 수 있습니다. 자기 PR은 자기가 승인할 수 없습니다.
- 리뷰는 **확인 방법을 직접 실행해 보고** 승인합니다.
- 코멘트 앞에 성격을 붙이면 대화가 빨라집니다.
  - `[필수]` 머지 전에 꼭 고쳐야 함
  - `[제안]` 고치면 좋지만 작성자가 판단
  - `[질문]` 이해를 위한 질문

## 이슈 라벨

| 종류 라벨 | 컴포넌트 (이슈 본문에서 선택) |
| --- | --- |
| `feature`, `bug`, `task` | `law-mcp`, `business-mcp`, `mail-mcp`, `agent`, `backend`, `frontend`, `infra` |

## 폴더 구조

```
mcp-servers/
  law-mcp/        하도급법·연동제 법령 조회·검색
  business-mcp/   협력사·완성차 정보, 협상안 법규 검사 (예정)
  mail-mcp/       메일 발송, 협상 문서 템플릿 (예정)
shared/           컴포넌트 간 공용 계약 (쿼리 분해 스키마, intent 등록표, 예시)
docs/contracts/   계약 문서 (형식을 바꾸면 shared/와 같은 PR에서 고치고 관련자 전원 승인)
agent/            LangGraph Agent, Harness (예정)
backend/          Spring Boot (예정)
frontend/         Vue (예정)
```

## 터미널에서 편하게 하기

```bash
gh issue create                                             # 템플릿을 골라 이슈 생성
gh issue develop 12 --name feat/12-business-mcp --checkout  # 이슈에 연결된 브랜치 생성
gh pr create --fill                                         # 템플릿이 채워진 PR 생성
gh pr checkout 34                                           # 리뷰할 PR을 로컬로 받기
gh pr review 34 --approve                                   # 승인
```

## 하지 말 것

- `main`에 직접 푸시하거나 강제 푸시(`--force`)하기
- `.env`, API 키, 실제 회사·개인 정보를 커밋하기 (목업 데이터는 가상 이름만 사용)
- 리뷰 없이 머지하기
