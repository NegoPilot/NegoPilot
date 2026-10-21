# 로컬 PostgreSQL 초기화

루트 Compose가 사용하는 이미지다. 빌드 context는 저장소 루트이며 Dockerfile 경로는 `deploy/local/postgres/Dockerfile`이다.

| 용도 | DB | 로그인 role | 비밀번호 환경 변수 |
| --- | --- | --- | --- |
| 초기화와 healthcheck | `postgres` | `POSTGRES_USER` | `POSTGRES_PASSWORD` |
| 회원과 세션 및 개인 대화 | `negopilot_app` | `negopilot_app` | `APP_DB_PASSWORD` |
| Spring 관리자 협력사 수정 | `negopilot_business` | `negopilot_business_owner` | `BUSINESS_DB_PASSWORD` |
| business MCP 조회 | `negopilot_business` | `negopilot_business_reader` | `BUSINESS_READER_PASSWORD` |

`POSTGRES_DB=postgres`를 사용한다. 초기화 스크립트에는 비밀번호를 저장하지 않는다. psql은 환경 변수 값을 SQL 문자열로 인용해서 역할을 만든다.

최초 named volume 생성 시 공식 PostgreSQL entrypoint가 `10-init.sh`를 실행한다. DB가 이미 존재하면 스크립트는 거절한다. business 스키마도 별도로 비어 있는지 검사한다. seed와 관리자 migration은 한 트랜잭션에서 적용한다. app DB의 테이블은 Spring Flyway가 만든다.

이미지 빌드 단계는 기존 `mcp-servers/business-mcp/scripts/seed.py`의 SQL 실행 요청을 캡처한다. 저장소의 DART CSV 스냅샷과 기존 난수 seed를 그대로 사용한다. 외부 DART API와 DB에는 접속하지 않는다. 원본 CSV도 다시 쓰지 않는다. 기존 `schema.sql`에 삭제 구문이 있으므로 생성된 SQL을 기존 DB에 수동으로 적용하면 안 된다. 이 이미지의 빈 DB 초기화 hook에서만 사용한다.

business MCP reader는 업무 테이블 8개와 `supplier_overview`의 `SELECT` 권한만 받는다. app DB에는 접속할 수 없다. 관리자 감사 테이블과 sequence의 권한도 받지 않는다. Spring의 business owner 연결은 별도 계정을 사용한다.

초기화가 모두 성공한 뒤 `postgres.public.negopilot_local_init`에 `business-seed-v1` marker를 기록한다. 이미지의 healthcheck는 TCP 연결로 이 marker를 확인한다. 초기화 중 사용하는 socket 전용 서버를 준비 완료로 처리하지 않는다.

컨테이너 재시작과 이미지 재빌드는 기존 named volume의 DB를 유지한다. 환경 변수의 비밀번호를 바꿔도 기존 role 비밀번호를 자동으로 변경하지 않는다. 초기화 실패 후 marker가 없으면 healthcheck도 실패한다. 기존 DB를 복구하거나 명시적으로 로컬 데이터를 초기화해야 하며 자동 seed 재실행은 하지 않는다.

business MCP 이미지는 `deploy/local/business.Dockerfile`이다. 기본 연결 설정은 아래와 같다. Compose는 reader의 비밀번호만 `PGPASSWORD`로 전달한다.

```text
DATABASE_URL=dbname=negopilot_business
PGHOST=postgres
PGPORT=5432
PGUSER=negopilot_business_reader
```

오프라인 SQL 생성 테스트:

```bash
mcp-servers/business-mcp/.venv/bin/python -m unittest discover \
  -s deploy/local/postgres -p 'test_*.py' -v
bash -n deploy/local/postgres/10-init.sh
bash -n deploy/local/postgres/healthcheck.sh
```
