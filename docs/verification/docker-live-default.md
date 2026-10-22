# Docker 기본 live 실행 검증

검증일: 2026-10-22

## 변경 범위

기본 `compose.yaml`은 실제 모델과 MCP를 연결한다. `compose.test.yaml`은 채팅을 stub으로 변경하고 모델 키를 제거한다. 테스트 환경은 별도 프로젝트 이름과 웹 포트 및 DB 볼륨과 세션 쿠키를 사용한다.

Docker 시작기는 필수 설정을 오프라인으로 검사한다. 모델 키의 유효성과 모델 접근 권한은 실제 호출로 확인해야 한다. Agent 코어와 Spring API 및 DB 스키마는 변경하지 않았다.

## 자동 검사

| 검사 | 결과 |
| --- | --- |
| Docker 시작기 단위 검사 | 10개 통과 |
| Compose 설정 회귀 | 7개 통과 |
| 기존 통합 helper 단위 검사 | 59개 통과 |
| 실제 Agent 이미지의 시작 동작 | 키 누락 시 종료와 키 없는 stub 실행 및 live 기본 모델 적용 확인 |
| Docker 전체 스택 | 새 볼륨에서 빌드와 시작 성공 |
| stub HTTP 통합 | 기본 회원 로그인과 가입 승인 및 실제 MCP 조회와 두 회원의 후속 대화 및 접근 격리 통과 |
| 컨테이너 재생성 | 기존 비밀번호와 회원 ID 및 메시지 8개와 전체 답변 및 제목 보존 확인 |
| 동일 브라우저 쿠키 저장소 | live와 test의 로그인 유지 및 한쪽 로그아웃 후 다른 쪽 로그인 유지 확인 |

검증은 `negopilot-compose-test-20261022`와 `negopilot-compose-live-20261022`라는 임시 프로젝트에서 실행했다. 웹 포트는 각각 60201과 60202였다. 기존 공유 서버와 해당 DB는 검증 대상으로 사용하지 않았다.

## 실제 모델 연결

질문 해석과 답변 생성 모두 OpenAI `gpt-4.1-mini`를 사용했다. 공개 Spring API를 Nginx 주소로 호출했다. 기업 선택 컨텍스트는 빈 목록으로 시작했다.

| 질문 | 확인한 도구 실행 | 결과 |
| --- | --- | --- |
| `SUP-0012 협력사 상세 정보를 알려줘` | business MCP의 `get_supplier` | live 답변 생성 완료 |
| `그 회사의 납기 준수율은 얼마야?` | 같은 `SUP-0012`를 다시 `get_supplier`로 조회 | 이전 대화로 대상을 해석하고 답변 생성 완료 |
| `하도급법 제13조 내용을 알려줘` | law MCP의 `get_article` | live 답변 생성 완료 |

세 응답 모두 `execution_status=COMPLETED`와 `generation_status=COMPLETED`였다. 조회 근거가 반환됐으며 사용자 질문과 전체 답변을 합친 메시지 6개가 DB에 저장됐다. 저장 응답을 다시 조회해 최초 응답과 일치하는지 확인했다.

처음에는 쉘의 별도 키가 공급자에게 401로 거절됐다. 애플리케이션은 `UPSTREAM_UNAVAILABLE`을 반환했고 stub 답변으로 전환하지 않았다. 프로젝트의 기존 로컬 키를 임시 live 컨테이너에 전달한 뒤 위 검증을 완료했다. 키 값은 저장소와 문서에 포함하지 않았다.

실제 모델 답변은 문장과 인용 개수를 고정해 검사하지 않았다. 이번 후속 답변에는 조회 근거가 포함됐지만 인용 목록은 비어 있었다. 나머지 두 답변의 인용은 각각 1개였다. 이 변경은 모델의 답변 품질을 개선하는 작업을 포함하지 않는다. Anthropic과 Ollama는 시작 설정만 검사했으며 실제 호출은 실행하지 않았다.

## 재현 방법

저장소 루트에서 다음 명령으로 자동 검사를 실행한다.

```bash
python3 -B -m unittest discover -s tests/integration -p 'test_*.py'
uv run --project agent --locked python -B -m unittest discover -s deploy/local -p 'test_*.py'
COMPOSE_FILE=compose.yaml:compose.test.yaml COMPOSE_PROJECT_NAME=negopilot-ci TEST_WEB_PORT=5173 python3 -B tests/integration/compose_config_test.py
```

Docker 통합 검사의 시작과 재생성 및 저장 상태 확인은 [실행 문서](../LOCAL_DOCKER.md#키-없는-통합-검사)를 따른다. 실제 모델 검증은 기본 live 환경에 유효한 키를 설정하고 같은 대화에서 위 질문을 순서대로 보낸다. Docker Compose 2.24.4 이상이 필요하다.
