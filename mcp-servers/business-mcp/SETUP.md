# 실행 가이드

business-mcp를 처음 띄워 보는 사람을 위한 단계별 안내입니다.
도구와 테이블 설명은 [README.md](README.md)를 보세요.

## 0. 준비물

| 준비물 | 확인 방법 | 비고 |
| --- | --- | --- |
| uv | `uv --version` | 없으면 `brew install uv`. Python 3.12가 없어도 uv가 알아서 받습니다 |
| Docker Desktop | `docker --version` | 설치 후 앱을 **실행해 둔 상태**여야 합니다. PostgreSQL을 직접 설치했다면 없어도 됩니다 |

## 1. 폴더로 이동

모든 명령은 `mcp-servers/business-mcp` 폴더 안에서 실행합니다.

```bash
cd mcp-servers/business-mcp
```

## 2. 패키지 설치

```bash
uv sync
```

이 폴더에 `.venv`가 만들어지고 `uv.lock`에 적힌 버전 그대로 설치됩니다.
이후 파이썬 명령은 모두 `uv run python ...`으로 실행합니다 (가상환경 activate는 필요 없습니다).

## 3. PostgreSQL 띄우기

```bash
docker compose up -d
docker ps          # supplier_db 컨테이너가 Up 이면 성공
```

Homebrew 등으로 PostgreSQL을 이미 설치해 5432를 쓰고 있다면, Docker 대신 그 서버에 계정과 DB만 만듭니다.

```bash
psql -d postgres -c "CREATE ROLE agent LOGIN PASSWORD 'agent';"
createdb -O agent supplier_db
```

## 4. DB에 데이터 넣기

```bash
uv run python scripts/seed.py
```

레포에 들어 있는 DART 수집 결과(`data/dart_companies.csv`, `data/dart_financials.csv`)를 읽어 넣습니다.
`완료: 협력사 150곳 (DART 30, 가상 120) ...`이 나오면 성공이고, 팀원 모두 같은 데이터가 만들어집니다.
다시 실행하면 테이블을 지우고 처음부터 새로 만듭니다. 넣은 데이터는 `data/*.csv`로도 저장됩니다.

확인용 테스트도 돌려 볼 수 있습니다. 같은 DB 안에 임시 스키마를 만들어 쓰고 지우므로 방금 넣은 데이터는 그대로입니다.

```bash
uv run python -m pytest -q
```

## 5. MCP 서버 연결

두 가지 방식이 있습니다. 경로는 각자 컴퓨터의 **절대 경로**로 바꿉니다.

- **stdio**: 클라이언트가 서버를 직접 띄웁니다. 설정만 하면 됩니다.
- **HTTP**: 서버를 따로 띄워 두고(`uv run python server.py --http 9102`) 주소 `http://localhost:9102/mcp`로 연결합니다. 여러 MCP를 라우팅하는 Agent에서 쓰기 편합니다. (law-mcp는 9103)

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

`which uv`로 uv의 절대 경로를 확인할 수 있습니다. 저장 후 Claude Desktop을 재시작합니다.

**MCP Inspector**로 확인할 때:

```bash
npx @modelcontextprotocol/inspector $(which uv) run --directory $(pwd) python server.py
```

연결되면 Tool 5개(`list_rfqs`, `search_suppliers`, `get_supplier`, `rank_suppliers`, `check_offer`)와 Resource `business://catalog`가 보입니다.
예: "RFQ-2026-001에 맞는 협력사 순위 보여줘" → `rank_suppliers`, "서연이화 재무 알려줘" → `get_supplier`, "SUP-0034에 3,800원, 75일 결제, 연동 없이 제안해도 돼?" → `check_offer`

## 데이터 추가·갱신 방법

회사를 더 넣거나 최신 공시로 바꾸고 싶을 때의 절차입니다. **실행 가이드 1~5단계에는 필요 없습니다.**
팀원 모두 같은 seed 데이터를 쓰므로, 한 사람이 수집해서 바뀐 CSV를 PR로 올리고 나머지는 pull 후 `seed.py`만 다시 실행합니다.

### 수집하는 사람

1. **이슈와 브랜치 만들기**: 예) `chore/N-dart-data-update`
2. **OpenDART 인증키 받기**: https://opendart.fss.or.kr 에서 회원가입 후 인증키를 신청합니다 (승인은 보통 바로~하루).
3. **`.env`에 키 넣기**

   ```bash
   cp .env.example .env
   ```

   `.env`를 열어 `DART_API_KEY=` 뒤에 키를 붙여 넣습니다 (따옴표·공백 없이).
   `.env`는 숨김 파일이라 Finder에서는 `⌘ + Shift + .`를 눌러야 보입니다.
   **`.env`는 `.gitignore`에 들어 있습니다. 다른 사람과 공유하거나 git에 올리지 않습니다.**

4. **수집 실행**

   ```bash
   uv run python scripts/collect_dart.py --max 30    # 30곳까지만 (테스트·소량)
   uv run python scripts/collect_dart.py             # 자동차 부품 상장사 전체
   ```

   - `data/dart_companies.csv`, `data/dart_financials.csv` 두 파일을 **덮어씁니다.**
   - 종목코드가 있는 회사 약 4,000곳의 개황을 한 번씩 조회해 자동차 부품(KSIC 303) 상장사를 골라냅니다. 첫 실행은 몇 분 걸리고 200곳마다 진행 상황이 나옵니다.
   - 응답은 `data/dart_raw/`(git 제외)에 캐시되어, 끊겨도(Ctrl+C) 다시 실행하면 이어서 받습니다.
   - 다른 업종을 넣으려면 `scripts/collect_dart.py`의 `AUTO_PARTS_KSIC`를, 수집 연도는 `YEARS`를 바꿉니다.

5. **내 DB에 넣어 확인**

   ```bash
   uv run python scripts/seed.py
   uv run python -m pytest -q
   ```

   협력사는 항상 150곳(`scripts/seed.py`의 `N_SUPPLIERS`)이고, DART 회사가 늘면 그만큼 가상 회사가 줄어듭니다.
   `SUP-` 번호는 CSV 순서대로 매겨져서 데이터가 바뀌면 번호와 회사의 대응도 바뀝니다. 문서나 Agent 프롬프트에 특정 번호를 쓰고 있다면 같이 고칩니다.

6. **바뀐 CSV만 커밋해서 PR**

   ```bash
   git add data/dart_companies.csv data/dart_financials.csv
   git commit -m "chore: DART 수집 데이터 갱신 (#N)"
   ```

   PR 본문에 수집 날짜, 회사 수 변화(예: 30곳 → 85곳), `seed.py` 출력을 적습니다.

### 나머지 팀원 (PR이 merge된 뒤)

```bash
git switch main && git pull
cd mcp-servers/business-mcp
uv run python scripts/seed.py        # 테이블을 지우고 새 CSV로 다시 만듦
```

인증키는 필요 없습니다.

## 끄기 / 다시 켜기

```bash
docker compose stop       # DB 잠시 끄기 (데이터 유지)
docker compose start      # 다시 켜기
docker compose down -v    # 컨테이너와 데이터까지 삭제 (다시 쓰려면 seed.py 재실행)
```

## 자주 나는 오류

| 오류 | 원인 / 해결 |
| --- | --- |
| `No such file or directory: 'scripts/seed.py'` 등 | `business-mcp` 폴더 밖에서 실행함 → `cd mcp-servers/business-mcp` |
| `No module named 'psycopg'` / `'fastmcp'` | `uv run` 없이 시스템 파이썬으로 실행함 → `uv sync` 후 `uv run python ...`으로 실행 |
| `Cannot connect to the Docker daemon` | Docker Desktop 앱이 꺼져 있음 → 실행 후 다시 시도 |
| `port is already allocated` (5432) | 이미 다른 PostgreSQL이 5432를 씀 → 3단계의 "이미 설치했다면" 방법을 쓰거나, `docker-compose.yml`의 포트를 `"5433:5432"`로 바꾸고 `export DATABASE_URL=postgresql://agent:agent@localhost:5433/supplier_db` |
| `role "agent" does not exist` | 컨테이너가 아니라 컴퓨터에 직접 설치된 PostgreSQL(예: Homebrew)이 5432를 쓰고 있음 → 3단계처럼 그 서버에 계정·DB를 만듦 (이 경우 `docker compose`는 필요 없음) |
| `spawn python ENOENT` / `spawn uv ENOENT` (MCP 연결) | MCP 클라이언트(다른 앱)는 터미널의 PATH·alias를 모름 → `command`에 `which uv`로 확인한 **uv의 절대 경로**를 넣고 `--directory`에 이 폴더의 절대 경로를 줌 |
| `협력사 DB에 연결할 수 없다` (도구 호출) / `connection refused` | DB가 꺼져 있음 → `docker compose up -d` (막 띄운 직후면 몇 초 뒤 재시도), 처음이면 4단계 seed도 실행 |
| `DART_API_KEY가 없습니다` | `collect_dart.py`를 실행했는데 `.env`에 키가 없음 → 데이터 추가·갱신 방법의 3번 확인 |
| `OpenDART 오류 010` / `011` | 인증키가 틀렸거나 아직 승인 전 → 키 다시 확인 |
| `일일 호출 한도를 넘었습니다` | 하루 20,000건 초과 → 다음 날 다시 실행하면 이어서 수집 |
| `Address already in use` (9102) | 이미 HTTP 서버가 떠 있음 → 기존 서버를 끄거나 다른 포트 사용 |
| pytest가 전부 `skipped` | DB에 연결할 수 없음 → 3단계 확인 |
