# 실행 가이드

협력사·완성차 기업 DB를 처음 띄워 보는 사람을 위한 단계별 안내다.
프로젝트 설명과 테이블 구조는 [README.md](README.md)를 본다.

## 0. 준비물

| 준비물 | 확인 방법 | 비고 |
| --- | --- | --- |
| uv | `uv --version` | 없으면 `brew install uv`. Python 3.12가 없어도 uv가 알아서 받는다 |
| Docker Desktop | `docker --version` | 설치 후 앱을 **실행해 둔 상태**여야 한다 |
| OpenDART 인증키 | https://opendart.fss.or.kr | 회원가입 → 인증키 신청. 없어도 가상 데이터로 실행 가능 |

## 1. 폴더로 이동

모든 명령은 `mcp-servers/business-mcp` 폴더 안에서 실행한다.

```bash
cd mcp-servers/business-mcp
```

## 2. OpenDART 인증키 넣기

`.env.example`을 `.env`로 복사한 뒤, `DART_API_KEY=` 뒤에 키를 붙여 넣는다 (따옴표·공백 없이).

```bash
cp .env.example .env
```

```
DART_API_KEY=발급받은키
```

`.env`는 이름이 점(.)으로 시작하는 숨김 파일이라 Finder에서 바로 안 보인다.
Finder에서 `⌘ + Shift + .` 를 누르거나, VS Code로 폴더를 열면 보인다.
**`.env`는 `.gitignore`에 들어 있다. 다른 사람과 공유하거나 git에 올리지 않는다.**

## 3. 패키지 설치

```bash
uv sync
```

이 폴더에 `.venv`가 만들어지고 `uv.lock`에 적힌 버전 그대로 설치된다.
이후 모든 파이썬 명령은 `uv run python ...`으로 실행하면 이 환경을 쓴다 (가상환경 activate 불필요).

## 4. PostgreSQL 띄우기

```bash
docker compose up -d
docker ps          # supplier_db 컨테이너가 Up 이면 성공
```

## 5. DART 데이터 수집 (선택)

수집 결과는 실제 회사 정보라 레포에 올리지 않는다. 실제 회사를 넣고 싶으면 각자 인증키로 수집한다.

```bash
uv run python collect_dart.py --max 30    # 처음엔 30곳만 받아 보며 테스트
uv run python collect_dart.py             # 전체 수집
```

- 고유번호 목록에는 업종 정보가 없다. 그래서 종목코드가 있는 회사 약 4,000곳(상장폐지 회사 포함)의 개황을 한 번씩 조회해 자동차 부품 상장사를 골라낸다.
  첫 실행은 몇 분 걸리고, 200곳마다 진행 상황이 출력된다. `--max 30`이어도 자동차 부품사가 목록 뒤쪽에 있으면 거의 끝까지 훑는다.
- 응답은 `data/dart_raw/`에 저장되어, 중간에 끊겨도(Ctrl+C) 다시 실행하면 이어서 받고, 두 번째부터는 빠르다.
- 이 단계를 건너뛰면 다음 단계에서 150곳 전부 가상 데이터로 채운다.

## 6. DB에 데이터 넣기

```bash
uv run python seed.py
```

`완료: 협력사 150곳 (DART n, 가상 m) ...` 이 나오면 성공.
다시 실행하면 테이블을 지우고 처음부터 새로 만든다. 넣은 데이터는 `data/*.csv`로도 저장된다.

## 7. MCP 서버 연결

두 가지 방식이 있다. 경로는 각자 컴퓨터의 **절대 경로**로 바꾼다.

- **stdio**: 클라이언트가 서버를 직접 띄운다. 설정만 하면 된다.
- **HTTP**: 서버를 따로 띄워 두고(`uv run python server.py --http 9102`) 주소 `http://localhost:9102/mcp`로 연결한다. 여러 MCP를 라우팅하는 Agent에서 쓰기 편하다. (law-mcp는 9103)

**LangGraph**

```python
from langchain_mcp_adapters.client import MultiServerMCPClient

client = MultiServerMCPClient({
    "business": {"command": "uv", "args": ["run", "--directory", "/절대/경로/NegoPilot/mcp-servers/business-mcp",
                                           "python", "server.py"], "transport": "stdio"},
    # HTTP로 띄워 뒀다면
    # "business": {"url": "http://localhost:9102/mcp", "transport": "streamable_http"},
})
tools = await client.get_tools()
```

**Claude Desktop** (`~/Library/Application Support/Claude/claude_desktop_config.json`)

```json
{
  "mcpServers": {
    "business": {
      "command": "/opt/homebrew/bin/uv",
      "args": ["run", "--directory", "/절대/경로/NegoPilot/mcp-servers/business-mcp", "python", "server.py"]
    }
  }
}
```

`which uv` 로 uv의 절대 경로를 확인할 수 있다. 저장 후 Claude Desktop을 재시작한다.

**MCP Inspector**로 확인할 때:

```bash
npx @modelcontextprotocol/inspector $(which uv) run --directory $(pwd) python server.py
```

연결되면 `list_rfqs`, `search_suppliers`, `get_supplier`, `rank_suppliers` 도구 4개가 보인다.
예: "RFQ-2026-001에 맞는 협력사 순위 보여줘" → `rank_suppliers` 호출.

## 끄기 / 다시 켜기

```bash
docker compose stop       # DB 잠시 끄기 (데이터 유지)
docker compose start      # 다시 켜기
docker compose down -v    # 컨테이너와 데이터까지 삭제 (다시 쓰려면 seed.py 재실행)
```

## 자주 나는 오류

| 오류 | 원인 / 해결 |
| --- | --- |
| `No such file or directory: 'seed.py'` 등 | `business-mcp` 폴더 밖에서 실행함 → `cd mcp-servers/business-mcp` |
| `No module named 'psycopg'` / `'fastmcp'` | `uv run` 없이 시스템 파이썬으로 실행함 → `uv sync` 후 `uv run python ...`으로 실행 |
| `Cannot connect to the Docker daemon` | Docker Desktop 앱이 꺼져 있음 → 실행 후 다시 시도 |
| `port is already allocated` (5432) | 이미 다른 PostgreSQL이 5432를 씀 → 그 서버를 끄거나, `docker-compose.yml`의 포트를 `"5433:5432"`로 바꾸고 `export DATABASE_URL=postgresql://agent:agent@localhost:5433/supplier_db` |
| `role "agent" does not exist` | 컨테이너가 아니라 컴퓨터에 직접 설치된 PostgreSQL(예: Homebrew)이 5432를 쓰고 있음 → 그 서버에 계정·DB를 만든다: `psql -d postgres -c "CREATE ROLE agent LOGIN PASSWORD 'agent';"` 후 `createdb -O agent supplier_db` (이 경우 `docker compose`는 필요 없음) |
| `spawn python ENOENT` / `spawn uv ENOENT` (MCP 연결) | MCP 클라이언트(다른 앱)는 터미널의 PATH·alias를 모름 → `command`에 `which uv`로 확인한 **uv의 절대 경로**를 넣고 `--directory`에 이 폴더의 절대 경로를 준다 |
| `connection refused` (seed.py / MCP) | DB가 안 떠 있음 → `docker compose up -d`, 막 띄운 직후면 몇 초 뒤 재시도 |
| `DART_API_KEY가 없습니다` | `.env`에 키가 없음 → 2단계 확인 |
| `OpenDART 오류 010` / `011` | 인증키가 틀렸거나 아직 승인 전 → 키 다시 확인 |
| `일일 호출 한도를 넘었습니다` | 하루 20,000건 초과 → 다음 날 다시 실행하면 이어서 수집 |
| `Address already in use` (9102) | 이미 HTTP 서버가 떠 있음 → 기존 서버를 끄거나 다른 포트 사용 |
