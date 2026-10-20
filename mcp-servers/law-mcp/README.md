# law-mcp

하도급법과 납품대금 연동제 관련 법령을 **로컬 파일**로 들고 있는 MCP Server입니다.
실행 중에는 외부 API를 부르지 않고 `data/index/*.json`만 읽습니다.

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
- 검색은 한글 문자 bigram + BM25입니다. `1차 협력사`, `단가` 같은 현업 표현은 `원사업자`, `하도급대금` 같은 법령 용어를 덧붙여 검색합니다 (`server.py`의 `GLOSSARY`).

## 실행

```bash
uv sync
uv run python server.py              # stdio
uv run python server.py --http 9103  # Streamable HTTP: http://localhost:9103/mcp
uv run python -m pytest -q           # MCP Client로 도구 호출 테스트
```

## 법령 갱신

법령 원문은 [legalize-kr](https://github.com/legalize-kr/legalize-kr) 저장소(국가법령정보센터 원문을 Markdown으로 관리)에서 받습니다.

```bash
GITHUB_TOKEN=$(gh auth token) uv run python scripts/fetch_laws.py   # data/raw 갱신
uv run python scripts/build_index.py                                 # data/index 재생성
```

법령 추가는 `scripts/fetch_laws.py`의 `TARGETS`에 경로를 넣고 위 두 단계를 다시 실행하면 됩니다.

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
