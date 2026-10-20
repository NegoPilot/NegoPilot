# 실행 가이드

협력사·완성차 기업 DB를 처음 띄워 보는 사람을 위한 단계별 안내다.
프로젝트 설명과 테이블 구조는 [README.md](README.md)를 본다.

## 0. 준비물

| 준비물 | 확인 방법 | 비고 |
| --- | --- | --- |
| Python 3.10 이상 | `python3 --version` | `mcp` 패키지가 3.10 이상을 요구한다 |
| Docker Desktop | `docker --version` | 설치 후 앱을 **실행해 둔 상태**여야 한다 |
| OpenDART 인증키 | https://opendart.fss.or.kr | 회원가입 → 인증키 신청. 없어도 가상 데이터로 실행 가능 |

## 1. 폴더로 이동

모든 명령은 `mcp-servers/company-mcp` 폴더 안에서 실행한다.

```bash
cd mcp-servers/company-mcp
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
pip install -r requirements.txt
```

설치에 쓴 파이썬과 실행에 쓰는 파이썬이 같아야 한다. 헷갈리면 버전을 붙여서 쓴다.

```bash
python3.11 -m pip install -r requirements.txt
```

(가상환경을 쓰고 싶으면 먼저 `python3 -m venv .venv && source .venv/bin/activate`)

## 4. PostgreSQL 띄우기

```bash
docker compose up -d
docker ps          # supplier_db 컨테이너가 Up 이면 성공
```

## 5. DART 데이터 수집 (선택)

레포에 수집 결과(`data/dart_companies.csv`, `data/dart_financials.csv`)가 이미 들어 있으므로 **보통은 건너뛰고 6단계로 가면 된다.**
데이터를 새로 받고 싶을 때만 실행한다 (실행하면 두 파일을 덮어쓴다).

```bash
python collect_dart.py --max 30    # 처음엔 30곳만 받아 보며 테스트
python collect_dart.py             # 전체 수집
```

- 고유번호 목록에는 업종 정보가 없다. 그래서 종목코드가 있는 회사 약 4,000곳(상장폐지 회사 포함)의 개황을 한 번씩 조회해 자동차 부품 상장사를 골라낸다.
  첫 실행은 몇 분 걸리고, 200곳마다 진행 상황이 출력된다. `--max 30`이어도 자동차 부품사가 목록 뒤쪽에 있으면 거의 끝까지 훑는다.
- 응답은 `data/dart_raw/`에 저장되어, 중간에 끊겨도(Ctrl+C) 다시 실행하면 이어서 받고, 두 번째부터는 빠르다.
- 두 CSV 파일이 없는 상태에서 이 단계를 건너뛰면 다음 단계에서 150곳 전부 가상 데이터로 채운다.

## 6. DB에 데이터 넣기

```bash
python seed.py
```

`완료: 협력사 150곳 (DART n, 가상 m) ...` 이 나오면 성공.
다시 실행하면 테이블을 지우고 처음부터 새로 만든다. 넣은 데이터는 `data/*.csv`로도 저장된다.

## 7. MCP 서버 연결

`company_mcp.py`는 stdio 방식 MCP 서버라서, 직접 실행해 두기보다 **클라이언트가 실행**하게 설정한다.
경로는 각자 컴퓨터의 **절대 경로**로 바꾼다.

**LangGraph**

```python
from langchain_mcp_adapters.client import MultiServerMCPClient

client = MultiServerMCPClient({
    "company": {"command": "python3", "args": ["/절대/경로/NegoPilot/mcp-servers/company-mcp/company_mcp.py"], "transport": "stdio"},
})
tools = await client.get_tools()
```

**Claude Desktop** (`~/Library/Application Support/Claude/claude_desktop_config.json`)

```json
{
  "mcpServers": {
    "company": {
      "command": "/패키지를/설치한/python3의/절대경로",
      "args": ["/절대/경로/NegoPilot/mcp-servers/company-mcp/company_mcp.py"]
    }
  }
}
```

`which python3` 로 파이썬 절대 경로를 확인할 수 있다. 저장 후 Claude Desktop을 재시작한다.

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
| `Could not open requirements file` | `company-mcp` 폴더 밖에서 실행함 → `cd mcp-servers/company-mcp` |
| `No module named 'psycopg'` / `'mcp'` | 설치한 파이썬과 실행한 파이썬이 다름 → `python3.11 -m pip install ...` 처럼 같은 파이썬으로 설치 |
| `Cannot connect to the Docker daemon` | Docker Desktop 앱이 꺼져 있음 → 실행 후 다시 시도 |
| `port is already allocated` (5432) | 이미 다른 PostgreSQL이 5432를 씀 → 그 서버를 끄거나, `docker-compose.yml`의 포트를 `"5433:5432"`로 바꾸고 `export DATABASE_URL=postgresql://agent:agent@localhost:5433/supplier_db` |
| `role "agent" does not exist` | 컨테이너가 아니라 컴퓨터에 직접 설치된 PostgreSQL(예: Homebrew)이 5432를 쓰고 있음 → 그 서버에 계정·DB를 만든다: `psql -d postgres -c "CREATE ROLE agent LOGIN PASSWORD 'agent';"` 후 `createdb -O agent supplier_db` (이 경우 `docker compose`는 필요 없음) |
| `spawn python ENOENT` (MCP 연결) | MCP 클라이언트가 `python`을 못 찾음. 터미널의 `python`은 zsh alias라 다른 앱에서는 안 보인다 → `command`에 `/opt/homebrew/bin/python3.11`처럼 **패키지를 설치한 파이썬의 절대 경로**를, `args`에 `company_mcp.py`의 절대 경로를 넣는다 |
| `connection refused` (seed.py / MCP) | DB가 안 떠 있음 → `docker compose up -d`, 막 띄운 직후면 몇 초 뒤 재시도 |
| `DART_API_KEY가 없습니다` | `.env`에 키가 없음 → 2단계 확인 |
| `OpenDART 오류 010` / `011` | 인증키가 틀렸거나 아직 승인 전 → 키 다시 확인 |
| `일일 호출 한도를 넘었습니다` | 하루 20,000건 초과 → 다음 날 다시 실행하면 이어서 수집 |
| `SyntaxError` (`str \| None` 부근) | Python 3.9 이하 → 3.10 이상으로 실행 |
