# law-mcp

하도급법과 납품대금 연동제 관련 법령을 **로컬 파일**로 들고 있는 MCP Server입니다.
일반 조회는 메모리에 적재한 데이터만 사용합니다. 관리자 최신화 요청이 들어오면 고정된 수집처에서 기존 6개 법령을 다시 수집합니다.

## 들어 있는 법령

| law_key | 법령 | 별칭 |
| --- | --- | --- |
| `subcontract_act` | 하도급거래 공정화에 관한 법률 | 하도급법 |
| `subcontract_decree` | 하도급거래 공정화에 관한 법률 시행령 | 하도급법 시행령 |
| `winwin_act` | 대ㆍ중소기업 상생협력 촉진에 관한 법률 (납품대금 연동제) | 상생협력법 |
| `winwin_decree` | 대ㆍ중소기업 상생협력 촉진에 관한 법률 시행령 | 상생협력법 시행령 |
| `sme_act` | 중소기업기본법 (중소기업·소기업 기준) | 중소기업기본법 |
| `sme_decree` | 중소기업기본법 시행령 | 중소기업기본법 시행령 |

법령마다 최근 개정본 5~6개를 함께 저장합니다. 공포됐지만 아직 시행 전인 개정이 있기 때문입니다.
예를 들어 하도급법은 2026-09-08 공포본이 **2027-09-09 시행 예정**이라, 오늘 기준 답변은 그 이전 판으로 합니다.

## 도구

| 종류 | 이름 | 설명 |
| --- | --- | --- |
| Tool | `list_laws(as_of?)` | 법령 목록, 기준일에 시행 중인 판, 시행 예정 판 |
| Tool | `get_article(law, article_no, as_of?)` | 조문 전문(항 단위), 인용 문구, 시행 예정 개정 여부 |
| Tool | `search_regulation(query, laws?, as_of?, top_k?, include_addenda?)` | 항 단위 검색 결과와 인용 문구 |
| Resource | `law://catalog` | 법령 목록 |
| Resource | `law://{law_key}/{article_no}` | 조문 전문 텍스트 (예: `law://subcontract_act/제4조`) |

- 모든 Tool은 `readOnlyHint`가 붙은 조회 도구입니다.
- `law`에는 `subcontract_act` 같은 키나 `하도급법` 같은 별칭을 쓸 수 있습니다.
- `article_no`는 `제4조`, `4`, `16조의2` 모두 됩니다.
- 결과의 `citation` 값(예: `「하도급거래 공정화에 관한 법률」 제4조②(부당한 하도급대금의 결정 금지) [시행 2026-10-02]`)을 Agent 답변에 그대로 인용하도록 설계했습니다.
- 검색은 한글 문자 bigram + BM25입니다. `1차 협력사`, `단가` 같은 현업 표현은 `원사업자`, `하도급대금` 같은 법령 용어를 덧붙여 검색합니다 (`server.py`의 `GLOSSARY`). 긴 표현부터 찾고, 이미 찾은 부분은 짧은 표현이 다시 쓰지 않습니다 (`1차 협력사`에는 `원사업자`만 붙습니다).
- 검색 색인은 법령 조합과 시행판마다 하나씩 만들어 최대 32개까지 캐시합니다 (오래 안 쓴 것부터 버림). 기준일이 달라도 시행판이 같으면 같은 색인을 씁니다.

## 실행

```bash
uv sync
uv run python server.py              # stdio
uv run python server.py --http 9103  # Streamable HTTP: http://localhost:9103/mcp
uv run python -m pytest -q           # MCP Client로 도구 호출 테스트
```

## 관리자 버전 확인과 최신화

[관리자 API 계약](../../docs/contracts/admin-laws.md)에 따라 버전과 정확한 판의 조문을 조회한다. 수동 법령 등록과 본문 편집 API는 없다.

```bash
export LAW_RUNTIME_DIR='/absolute/path/to/persistent/law-runtime'
# LAW_ADMIN_API_KEY는 비밀 설정에서 주입한다.
uv run python server.py --http 9103
```

Python 내부 API에도 동일한 `LAW_ADMIN_API_KEY`와 이 서버를 가리키는 `LAW_MCP_URL`을 설정한다. 키가 없으면 최신화 제어 요청을 인증할 수 없다. runtime 디렉터리가 없으면 최신화는 503이다. 런타임 디렉터리는 한 법령 서버 프로세스만 사용한다. 잠금으로 중복 실행을 차단한다.

최신화는 `POST /admin/refresh-jobs`로 시작한다. `X-Law-Admin-Key` 인증이 필요하다. 해당 경로는 내부망에서 Python만 호출한다. MCP 도구 목록에는 최신화 기능이 없다. Agent가 질문을 처리하다가 데이터를 변경할 수 없다.

원문은 기존 [legalize-kr 저장소](https://github.com/legalize-kr/legalize-kr)의 고정된 6개 경로에서 수집한다. 법령마다 최근 6개 커밋을 확인하고 기존 보관 판과 합친다. 전체 수집과 파싱 및 검증이 성공하면 `active.json`을 원자적으로 교체한다. 메모리 데이터와 검색 캐시도 함께 갱신한다. 실패하면 기존 자료로 조회를 계속한다. GitHub의 갱신 상태에 따라 수집 가능한 최신판이 달라진다. 국가법령정보센터의 현재 본문과 실시간 동기화된다는 의미는 아니다.

GitHub API 호출 제한에 걸리면 수집 작업은 실패한다. 필요하면 법령 서버의 비밀 환경설정에 `GITHUB_TOKEN`을 주입한다. 키를 요청 본문이나 프런트로 보내지 않는다.

작업 상태는 runtime 디렉터리의 `job-UUID.json`에 저장한다. 서버가 중단된 작업은 재시작 시 실패로 표시한다. 데이터 교체 직후 중단됐다면 `active.json`의 작업 ID로 완료를 복원한다. 성공 시 수집 시각을 기록한다. 내장 파일의 최초 수집 시각은 알 수 없어 `collected_at=null`이다.

개발자가 내장 파일 자체를 다시 만들 때 사용하는 기존 `scripts/fetch_laws.py`와 `scripts/build_index.py`는 그대로 유지한다. 관리 화면의 최신화는 이 명령의 `main`을 실행하지 않고 격리된 임시 경로에서 수집과 파싱 함수만 사용한다.

## 폴더 구조

```
data/raw/{law_key}/{시행일자}_{commit}.md   법령 원문 (frontmatter: 공포일자, 시행일자, 출처)
data/raw/manifest.json                      받은 판 목록
data/index/{law_key}.json                   조문·항·부칙 단위로 쪼갠 색인 (서버가 읽는 파일)
scripts/fetch_laws.py                       원문 내려받기 (한 번만 실행)
scripts/build_index.py                      원문 → 색인
server.py                                   MCP Server
tests/test_server.py                        MCP Client 테스트
```

## 알아둘 점

- 법령 판은 frontmatter의 **대표 시행일자**로 고릅니다. 일부 조항만 따로 늦게 시행되는 경우(부칙의 개별 시행일)는 반영하지 않으니, 필요하면 `include_addenda=true`로 부칙을 함께 확인하세요.
- 법령 원문은 저작권법상 보호받지 않는 저작물이라 그대로 저장해도 됩니다. 공정위 지침이나 가이드북을 추가할 때는 공공누리 유형을 확인하세요.
- 이 서버는 법령을 **찾아서 인용**합니다. 협상안이 법을 어기는지 판정하는 규칙 검사(`check_offer`)는 business-mcp 쪽에 결정적 규칙으로 따로 구현합니다.
