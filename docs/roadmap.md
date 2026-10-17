# 보강 계획: Compose · MCP · 승인형 HITL · 브리핑 자동화

작성일 2026-10-16 · 상태: 계획 (구현 전)

이 문서는 영화 챗봇에 다음 네 가지를 추가하는 계획이다. 순서대로 진행하고, 각 단계는 **완료 기준**을 모두 채운 뒤 다음으로 넘어간다.

1. Docker Compose 원클릭 실행
2. 영화 데이터 도구를 MCP 서버로 분리
3. Postgres 체크포인터 + 승인형 HITL
4. 주간 박스오피스 브리핑 자동화

Movie Scene Finder는 검색 품질·평가를 맡고, 이 저장소는 에이전트 시스템·자동화를 맡는다. 같은 기능을 두 저장소에 중복해서 넣지 않는다.

## 현재 상태 (2026-10-16 `main` 기준)

| 항목 | 상태 |
| --- | --- |
| 관측 | LangSmith 환경변수, `scripts/trace.py`, trace 스킬 |
| 평가 | 골든셋 18개, LLM-as-judge(fail-closed), 수동 CI 워크플로(`evals.yml`) |
| 체크포인트 | `AsyncSqliteSaver` (Railway 볼륨의 파일 하나) |
| HITL | 없음 |
| MCP | 없음. 단, 도구 4개(`imdb_search`, `imdb_sql`, `kobis_search`, `web_search`)가 이미 독립 `@tool` 함수 |
| 자동화 | 없음 |
| Docker | 없음 (Railway nixpacks) |

---

## 1. Docker Compose 원클릭 실행

`.env`에 API 키 3개를 넣고 `docker compose up` 한 번으로 웹·API·DB가 뜨게 한다. 데이터가 CSV로 저장소에 있고 벡터스토어는 시작할 때 만들어지므로 막히는 부분이 없다. 이후 단계가 모두 이 위에 올라가므로 가장 먼저 한다.

### 서비스 구성

| 서비스 | 이미지 / 빌드 | 역할 | 단계 |
| --- | --- | --- | --- |
| `postgres` | `postgres:16` | 체크포인트, 브리핑·승인 기록 | 3 (1단계에서는 쓰는 곳이 없어 미룸) |
| `api` | `docker/api.Dockerfile` (python:3.13-slim) | `server.py`, 에이전트 실행 | 1 |
| `web` | `web/Dockerfile` (node:22, Next.js standalone) | 채팅 UI, `/api/chat` 프록시 | 1 |
| `mcp` | `docker/api.Dockerfile` (api와 같은 이미지, 명령만 다름) | 영화 데이터 도구 서버 | 2 |
| `n8n` | `n8nio/n8n` | 트리거·실패 알림, `profiles: [automation]` | 4 |

### 설계 포인트

- **브라우저는 `web`만 본다.** `web/app/api/chat/route.ts`가 이미 서버 쪽에서 `BACKEND_URL`로 프록시하므로 compose에서는 `BACKEND_URL=http://api:8000`만 넣는다. API 포트를 밖으로 열 필요가 없고 CORS 문제도 없다.
- **기동 순서는 헬스체크로 보장한다.** `postgres`는 `pg_isready`, `api`는 기존 `/health`를 쓰고 `depends_on: condition: service_healthy`로 묶는다.
- **벡터스토어는 named volume.** 처음 뜰 때 250편을 임베딩하고 이후 재사용한다. CI 캐시 키(CSV + `imdb_data.py` 해시)와 같은 원리.
- **Python 이미지는 2단계 빌드.** `requirements.txt`가 전체 락이므로 의존성 층을 먼저 복사해 캐시를 살리고, 실행 이미지에는 가상환경과 코드만 남긴다.
- **Next.js는 `output: 'standalone'`.** `next.config.ts`에 한 줄 추가하면 `server.js` 하나로 실행돼 이미지가 작아진다.
- **선택 서비스는 profile로 분리.** n8n은 `docker compose --profile automation up`일 때만 뜬다.

### compose 뼈대

```yaml
services:
  postgres:
    image: postgres:16
    environment: {POSTGRES_USER: app, POSTGRES_PASSWORD: app, POSTGRES_DB: movies}
    volumes: [pg_data:/var/lib/postgresql/data]
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U app -d movies"]
      interval: 2s
      retries: 30

  api:
    build: .
    env_file: .env
    environment:
      DATABASE_URL: postgresql://app:app@postgres:5432/movies
    volumes: [vectorstore:/app/vectorstore]
    depends_on:
      postgres: {condition: service_healthy}
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"]
      interval: 5s
      retries: 30

  web:
    build: ./web
    environment:
      BACKEND_URL: http://api:8000
    ports: ["3000:3000"]
    depends_on:
      api: {condition: service_healthy}

volumes:
  pg_data:
  vectorstore:
```

### 완료 기준

- [x] 새로 clone한 폴더에서 `cp .env.example .env` → 키 3개 입력 → `docker compose up --wait` 후 `localhost:3000`에서 질문이 된다
- [x] `docker compose down` 후 다시 올려도 대화가 이어진다
- [x] README 맨 위 "빠른 시작"이 세 줄로 끝난다
- [x] CI에 `docker compose build` 단계를 추가한다

---

## 2. MCP 서버 분리

영화 데이터 도구를 독립 MCP 서버 `movie-data-mcp`로 옮기고, 에이전트는 MCP 클라이언트로 도구를 불러 쓴다.

### 왜

- 도구의 정의(이름·설명·JSON Schema)와 실행이 에이전트 프로세스 밖으로 나간다. 에이전트는 `tools/list`로 목록을 받고, 모델이 고른 호출을 `tools/call`로 보낸다. LLM 입장에서는 로컬 도구와 차이가 없다.
- 같은 서버를 Claude Desktop, Cursor 같은 다른 클라이언트에도 그대로 붙일 수 있다.
- KOBIS·Tavily 키는 MCP 서버만 갖고, 에이전트 컨테이너는 OpenAI 키만 가진다.

### 구성

| 항목 | 내용 |
| --- | --- |
| 서버 구현 | 공식 `mcp` Python SDK의 FastMCP |
| 도구 | `kobis_search`, `imdb_sql`, `imdb_search`. `web_search`는 범용이라 에이전트에 남겨도 된다 |
| 리소스 | `imdb://schema` (SQL 테이블 스키마) |
| 전송 방식 | compose 안에서는 streamable HTTP (`http://mcp:8001/mcp`), Claude Desktop용은 stdio. 실행 인자로 선택 |
| 클라이언트 | `langchain-mcp-adapters`의 `MultiServerMCPClient`. `lifespan`에서 `get_tools()`로 받아 `build_agent(tools=…)`에 넘긴다 |
| 디버깅 | MCP Inspector |

### 리팩터링 순서

1. 도구 본문을 `movie_tools/` 패키지의 순수 함수로 옮긴다. 에이전트와 MCP 서버가 같은 코드를 쓰게 하기 위해서다.
2. `mcp/server.py`에서 그 함수를 `@mcp.tool()`로 등록한다. `KobisInput` Pydantic 검증은 그대로 인자 모델로 쓴다.
3. `build_agent()`가 도구 목록을 인자로 받게 바꾼다. 평가는 로컬 도구로도, MCP 도구로도 돌릴 수 있게 둔다.
4. `server.py`의 `lifespan`에서 MCP 도구를 불러온다. MCP 서버가 응답하지 않으면 `/health`를 degraded로 보고한다.
5. README에 Claude Desktop 설정 예시와 사용 화면을 넣는다.

### 주의할 점

- **도구 이름을 바꾸지 않는다.** "검색 중" 표시, `tool_calls` 집계, 골든셋 `expected_tools`가 이름에 묶여 있다.
- **SQL 제한은 이미 충분하다.** `imdb_data.query_movies()`의 SQLite authorizer와 값 길이·30행 상한을 그대로 쓴다.
- **HTTP로 열 때는 노출 범위를 막는다.** compose 내부 네트워크에만 두고 포트를 열지 않는다. 밖으로 열어야 하면 Bearer 토큰과 Origin 검증을 넣는다.
- **버전 충돌을 먼저 확인한다.** `langgraph==1.0.1`, `langchain-core==1.2.26` 고정과 `langchain-mcp-adapters` 요구 범위가 맞는지 `scripts/gen_lock.py`로 락을 다시 만들며 확인한다.
- **네트워크 홉이 하나 늘어난다.** 전환 전후 도구 호출 지연을 trace로 비교해 기록한다.

### 구현하며 바뀐 점

- 패키지 이름은 `mcp/`가 아니라 `movie_mcp/`. `mcp/`는 공식 SDK를 가린다.
- `web_search`도 MCP로 옮겼다. 남기면 에이전트 컨테이너에 Tavily 키가 필요해 완료 기준과 어긋난다.
- MCP는 `MCP_URL`이 있을 때만 쓴다(compose). Railway 배포는 로컬 도구 그대로다.
- 도구당 약 280ms가 늘었다(호출마다 새 세션). 원인과 대안은 `docs/bug-log.md` B-013.

### 완료 기준

- [x] MCP 전환 전후로 골든셋 결과가 같다 (18/18 유지) — 로컬·MCP 모두 18/18, judge 평균 4.94
- [x] MCP Inspector와 Claude Desktop에서 `kobis_search`가 동작한다 — Inspector(stdio)로 실호출, Claude Desktop에서 박스오피스 질문 확인(2026-10-17)
- [x] FastMCP 인메모리 클라이언트로 도구 목록·스키마·잘못된 날짜 입력을 검증하는 단위 테스트가 있다 (`tests/mcp/`, 12개)
- [x] 에이전트 컨테이너 환경변수에 KOBIS·Tavily 키가 없다

---

## 3. Postgres 체크포인터 + 승인형 HITL

### 왜 Postgres로 바꾸는가

자동화가 들어오면 그래프를 시작하는 쪽(트리거)과 재개하는 쪽(승인 API)이 다르고, 둘이 같은 `thread_id`의 상태를 다룬다. 파일 하나에 쓰기 잠금이 걸리는 SQLite로는 이 구조가 불안정하다.

- `psycopg_pool.AsyncConnectionPool` + `AsyncPostgresSaver(pool)`을 `lifespan`에서 연다. `setup()`은 시작할 때 한 번.
- CLI(`imdb_rag.py`)와 평가는 지금처럼 SqliteSaver·MemorySaver를 쓴다. `build_agent(checkpointer)` 팩토리가 있어서 바꿀 곳은 `server.py`뿐이다.
- 기존 SQLite 대화는 옮기지 않고 README에 밝힌다.
- `langgraph-checkpoint-postgres`가 `langgraph-checkpoint==3.0.0` 고정과 맞는지 먼저 확인한다.

### HITL 두 종류

| 종류 | 언제 멈추나 | 예 |
| --- | --- | --- |
| 명확화 | 입력이 모호해 답을 못 할 때 | Movie Scene Finder의 `clarify` 노드 |
| 승인 | 되돌릴 수 없는 동작 직전 | 이 저장소의 브리핑 발송 전 검토 |

### interrupt 동작 원리와 함정

- `interrupt(payload)`를 부르면 그래프가 멈추고 상태가 체크포인터에 저장된다.
- 재개는 같은 `thread_id`로 `Command(resume=값)`을 보낸다. **interrupt를 부른 노드가 처음부터 다시 실행**되고, 이번에는 `interrupt()`가 resume 값을 돌려준다.
- 따라서 같은 노드 안에서 `interrupt()` 앞에 발송·과금 같은 부작용을 두지 않는다. 초안 생성, 승인, 발송을 노드로 나눈다.

### 승인 노드

```python
def request_approval(state: BriefingState) -> dict:
    decision = interrupt({"draft": state["draft"], "week": state["week"]})
    # decision = {"action": "approve" | "edit" | "reject", "text": str | None, "by": str}
    if decision["action"] == "reject":
        return {"status": "rejected", "decided_by": decision["by"]}
    final = decision["text"] if decision["action"] == "edit" else state["draft"]
    return {"status": "approved", "final_text": final, "decided_by": decision["by"]}
```

- **thread_id는 주차로 정한다** (`briefing-2026-W42`). 같은 주에 두 번 시작돼도 하나로 모인다.
- **승인 API는 인증한다.** `GET /briefings/pending`, `POST /briefings/{thread_id}/decision`에 관리자 토큰.
- **만료를 둔다.** 정해진 시간 안에 승인이 없으면 `expired`로 바꾸고 발송하지 않는다.
- **승인 화면은 웹이 기본.** Next.js `/admin/briefings`에 초안, 수정 입력칸, 승인·거절 버튼. Slack 버튼 승인은 공개 콜백 URL이 필요해 배포 환경 전용 선택 기능으로 둔다.

### `briefing_runs` 테이블

| 컬럼 | 용도 |
| --- | --- |
| `thread_id` (PK) | 주차 기반 스레드 키 |
| `status` | drafted → pending → approved / rejected / expired → sent / failed |
| `draft`, `final_text` | 생성안과 실제 발송본 |
| `decided_by`, `decided_at` | 누가 언제 승인했나 |
| `sent_at`, `delivery_id` | 발송 시각과 채널 쪽 메시지 ID |

### 구현하며 바뀐 점

- **Postgres는 compose에만** 붙였다(사용자 결정). `DATABASE_URL`이 있으면 Postgres, 없으면 SQLite. Railway는
  SQLite이고 브리핑은 꺼져 있다(503). 나중에 Railway에 Postgres를 추가하면 환경변수 하나로 바뀐다.
- 발송은 아직 기록만 한다(`LogSender`). 초안은 KOBIS 주간 원문. LLM 작성·검증·Discord는 4단계.
- 승인 화면은 계획대로 `/admin/briefings`. 관리자 토큰은 브라우저 탭 세션에만 두고 서버에 저장하지 않는다.
- `psycopg-binary`를 직접 적었다. `psycopg[binary]`의 extra는 락 생성기가 따라가지 않는다.
- Postgres 세이버는 3.0.4(3.0.5부터 `langgraph-checkpoint` 고정과 충돌).

### 완료 기준

- [x] 승인 대기 중에 `api` 컨테이너를 재시작해도 승인 후 정상 발송된다 — compose에서 `restart api` 후 웹으로 승인, `sent`
- [x] 같은 승인 요청을 두 번 보내도 한 번만 발송된다 — 두 번째는 409, 동시 승인·그래프 이중 재개도 1회
- [x] 거절·만료된 브리핑은 발송되지 않고 기록만 남는다
- [x] 위 세 경우가 실제 Postgres를 쓰는 통합 테스트로 검증된다 — `tests/integration/` 10개, CI `deps` 잡

---

## 4. 주간 박스오피스 브리핑 자동화

매주 월요일 오전에 지난주 박스오피스로 브리핑 초안을 만들고, 사람이 승인하면 Discord나 Slack으로 보낸다.

```mermaid
flowchart LR
  T[n8n 트리거<br/>매주 월요일] --> F[fetch_boxoffice<br/>MCP kobis_search]
  F --> C[compare_prev<br/>전주 대비 변동]
  C --> D[draft<br/>LLM 구조화 출력]
  D --> V{validate<br/>원자료와 숫자 대조}
  V -- 통과 --> A[request_approval<br/>interrupt, 상태 저장]
  V -- 불일치 --> X[실패 알림]
  A -- 승인 / 수정 --> S[deliver<br/>조건부 UPDATE 후 발송]
  A -- 거절 / 만료 --> R[기록만 남김]
```

### 그래프 노드

| 노드 | 하는 일 | LLM |
| --- | --- | --- |
| `fetch_boxoffice` | MCP `kobis_search`로 지난주(월~일) 주간 박스오피스 조회 | 아니오 |
| `compare_prev` | 전주와 비교해 신규 진입, 순위 변동, 관객 수 증감 계산 | 아니오 |
| `draft` | 비교 결과로 브리핑 작성 (Pydantic 구조화 출력) | 예 |
| `validate` | 날짜가 지난주인지, 순위·관객 수가 원자료와 일치하는지 코드로 검사 | 아니오 |
| `request_approval` | `interrupt`로 멈추고 승인 요청 알림 | 아니오 |
| `deliver` | 승인본 발송, `delivery_id` 기록 | 아니오 |

### 설계 원칙

- **LLM은 작성에만 쓴다.** 조회 대상은 매주 같으므로 도구 선택을 모델에 맡기지 않는다.
- **숫자는 모델이 만들지 못하게 한다.** `draft`에는 계산된 표를 넘기고 `validate`가 원자료와 대조한다. 불일치하면 한 번 다시 쓰고, 그래도 틀리면 발송하지 않고 실패 알림.
- **기존 사례와 연결.** "지난 주 박스오피스가 1년 전 데이터로 답한 문제"와 "집계 전 날짜 차단" 로직이 이 자동화의 정확성을 지킨다. KOBIS 주간 집계 확정 시각을 확인해 트리거 시각을 정하고, 집계 전이면 재시도 후 실패 알림.

### 트리거

| 방식 | 장점 | 단점 |
| --- | --- | --- |
| n8n Schedule → HTTP (1순위) | 실패 알림·채널 연동을 노드로 구성 | compose 컨테이너 추가, 워크플로 JSON 관리 |
| GitHub Actions cron | 설정이 가장 단순 | 지연 가능, 배포 URL·토큰 필요 |
| API 프로세스 안 APScheduler | 의존성 하나 | 워커마다 실행돼 중복 발생 (사용하지 않음) |

n8n은 시각에 맞춰 `POST /briefings/run`을 호출하고 실패 응답이면 알리기만 한다. 에이전트 실행, 승인 대기, 발송, 중복 방지는 모두 FastAPI가 맡는다.

### 멱등성

- `POST /briefings/run`은 이번 주 `thread_id`가 있으면 새로 시작하지 않고 기존 상태를 돌려준다.
- `deliver`는 `UPDATE briefing_runs SET status='sending' WHERE thread_id=… AND status='approved'`처럼 조건부로 상태를 바꾸고, 바뀐 행이 있을 때만 보낸다.
- 웹훅에는 중복 방지 키가 없으므로 이 상태 전이가 유일한 방어선이다.

### 발송 채널

- 기본: Discord 또는 Slack Incoming Webhook
- 승인 요청 알림: 같은 채널에 "승인 대기" 메시지와 `/admin/briefings` 링크
- 발송부는 인터페이스로 분리해 다른 채널 어댑터를 추가할 수 있게 한다

### 구현하며 바뀐 점

- **숫자는 모델이 아예 쓰지 않는다.** 계획은 "모델이 쓴 숫자를 원자료와 대조"였지만, 표는 코드가 원자료로 찍고 모델 글에는
  숫자 자체를 금지했다. 대조할 숫자가 없으니 틀릴 길도 없다. 검사(`validate_draft`)는 숫자·없는 순위·틀린 제목·'주말' 표현을 막는다.
- KOBIS 원본은 MCP **리소스**(`kobis://weekly/{날짜}`)로 받는다. 도구로 만들면 챗봇 에이전트의 도구 목록이 바뀐다.
- KOBIS의 `NEW`(개봉 첫 주)와 "전주 10위 밖에서 들어옴"을 구분했다(룩백: OLD인데 61위 → 6위).
- 실패한 주는 같은 주에 다시 트리거하면 다시 돈다(n8n이 월요일 10시·15시 두 번 부른다).
- n8n 2.43.7 고정. CLI 수동 실행은 task broker 포트를 바꿔야 한다(`N8N_RUNNERS_BROKER_PORT`).

### 완료 기준

- [x] `docker compose --profile automation up` 후 n8n 수동 실행으로 초안 → 승인 → Discord 수신까지 된다 —
      가짜 웹훅 서버로 전 과정(리뷰 알림·발송·실패 알림)을, 실제 Discord로 승인 요청 알림과 발송(메시지 ID 수신)을 확인(2026-10-17)
- [x] 초안에 원자료와 다른 숫자를 넣은 테스트에서 `validate`가 발송을 막는다 — 단위·통합 테스트(두 번 다 걸리면 failed)
- [x] 트리거를 연달아 두 번 보내도 브리핑이 하나만 생긴다 — 통합 테스트, n8n 재실행
- [x] n8n 워크플로 JSON을 저장소에 넣어 가져오기만 하면 재현된다 — 컨테이너 기동 때 import·publish

---

## 5. 평가·관측 보강

관측 도구는 LangSmith를 유지한다. 대신 새 기능마다 동작을 숫자로 보여 줄 평가를 추가한다.

### 골든셋 추가 항목

| 항목 | 검증 내용 | 방식 |
| --- | --- | --- |
| MCP 동등성 | 로컬 도구와 MCP 도구로 같은 18개 항목 결과가 같은지 | `run_evals.py`에 도구 공급 방식 옵션 |
| 브리핑 날짜 | 집계 기간이 "주어진 오늘의 직전 주"인지 | 코드 검사 (rubric에 날짜를 박지 않는 기존 원칙 유지) |
| 브리핑 숫자 | 순위·관객 수가 KOBIS 원자료와 일치하는지 | 저장해 둔 KOBIS 응답으로 재현 |
| 신규 진입 표시 | 전주에 없던 영화를 신규로 표시했는지 | 고정 입력(전주·금주 응답 쌍) |
| 집계 전 처리 | 집계 전 날짜로 실행하면 초안을 만들지 않는지 | 기존 차단 로직 재사용 |

저장한 응답으로 돌리는 브리핑 평가는 외부 API 비용이 없으므로 일반 CI에서 매번 실행한다. 실호출 평가는 지금처럼 수동 워크플로로 둔다.

### 운영 지표 (`briefing_runs`에서 계산)

| 지표 | 의미 |
| --- | --- |
| 수정 없이 승인된 비율 | 초안 품질 |
| `validate` 실패율 | 모델이 숫자를 틀리게 쓰는 빈도 |
| 승인까지 걸린 시간 | 만료 기준을 정하는 근거 |
| 브리핑 1건당 비용·지연 | LangSmith 트레이스 기준 |

브리핑 그래프 실행에는 `thread_id`, `trigger=n8n|manual`, `week` 메타데이터를 단다.

### 구현 결과 (2026-10-17)

| 항목 | 어디서 |
| --- | --- |
| MCP 동등성 | `run_evals --mcp-url` — 로컬·MCP 모두 18/18(2단계) |
| 브리핑 날짜 | `report.check_period` + `kobis_format.briefing_week` 단위 테스트, 기간이 다르면 `failed`(통합 테스트) |
| 브리핑 숫자 | `tests/fixtures`의 실제 KOBIS 응답으로 직접 계산한 값이 KOBIS의 `rankInten`·`audiChange`와 같은지(단위) |
| 신규 진입 표시 | 같은 고정 입력. 신작(`NEW`)과 순위권 진입(전주 10위 밖)을 구분 |
| 집계 전 처리 | KOBIS가 빈 목록을 주면 초안을 만들지 않음(통합), 대상 주는 항상 집계가 끝난 주(단위) |
| 초안 품질(LLM) | `python -m tests.evals.run_briefing` — 검사 통과율(과금, 수동) |

운영 지표는 `GET /briefings/metrics`와 관리자 화면 위쪽에 보인다. `briefing_runs`에 `pending_at`·`trigger`·`prep_seconds`·
`llm_tokens`를 더했다. 첫 실측: 초안까지 4.46초, 1,369토큰, LangSmith 비용 약 $0.002/건. LangSmith 트레이스는
`weekly_briefing` 이름과 `briefing` 태그로 찾고, 승인으로 재개한 실행은 `trigger=approval`이다.

---

## 일정과 위험 요소

| 단계 | 예상 기간 |
| --- | --- |
| 1. Compose | 2026-10-22 ~ 10-24 |
| 2. MCP | 2026-10-25 ~ 10-31 |
| 3. Postgres + 승인 HITL | 2026-11-01 ~ 11-07 |
| 4. 브리핑 자동화 + 평가 | 2026-11-08 ~ 11-14 |

| 위험 | 대응 |
| --- | --- |
| langgraph 고정 버전과 새 패키지 충돌 | 착수 첫날 락을 다시 만들어 확인. 안 되면 langgraph를 올리고 골든셋으로 회귀 확인 |
| KOBIS 주간 집계 확정이 트리거보다 늦음 | 확정 시각 확인 후 트리거를 그 뒤로. 재시도 1회 후 실패 알림 |
| n8n 설정 지연 | GitHub Actions cron으로 먼저 끝내고 나중에 교체 (트리거가 HTTP 호출 하나라 교체 쉬움) |

### 공통 규칙

- 기능마다 PR 하나, PR 설명에 "왜"를 적는다
- 단위 테스트와 CI 통과 후 병합
- README의 실행법·아키텍처 그림을 같은 PR에서 갱신
- 작업 중 만난 버그는 원인·해결·재발 방지를 기록
