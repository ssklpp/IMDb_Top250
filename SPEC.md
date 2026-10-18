# SPEC: IMDB Top 250 AI 영화 챗봇

> 이 저장소가 **무엇을 어떻게 만들었는지** 적은 명세서다. 기능·데이터 모델·도구·그래프·API·평가의 기준이 여기 있고,
> 마지막 절에 시작부터 지금까지의 진행 단계를 적었다.
> 작업 규칙과 "같이 고쳐야 하는 쌍"은 `CLAUDE.md`, 영역별 상세 규칙은 `rules/`가 맡는다. 이 문서는 그 위의 전체 그림이다.
> **코드와 이 문서가 다르면 코드가 맞다.** 기능을 바꾸면 해당 절을 같은 PR에서 고친다.

---

## 0. 작업 규칙

`CLAUDE.md`의 규칙을 요약한다. 자세한 이유는 거기에 있다.

1. 코드를 고치면 `pytest tests/unit/ -q`부터 돌린다(키 없이 0.1초, 비용 0). 싼 검사가 실패하면 뒤 검사로 가지 않는다.
2. 순수 로직은 외부 의존이 없는 모듈(`kobis_format.py`, `sources.py`, `imdb_data.py`, `ratelimit.py`, `briefing/report.py`)에 둔다.
   `agent.py`는 import만 해도 OpenAI 클라이언트를 만든다.
3. 로그는 `logging_config.get_logger()`로만 남긴다. `print()` 금지(MCP stdio 모드에서는 stdout이 프로토콜 통로다).
4. 예외는 `error=type(e).__name__`으로만 기록한다. `str(e)`에는 KOBIS 키가 든 URL이 들어간다.
5. 사용자 질문·검색어 본문은 로그에 남기지 않는다(길이만). 웹훅 URL·`ADMIN_TOKEN`·`PROXY_SECRET`도 남기지 않는다.
6. 헬퍼는 "못 찾음"을 `raise ValueError`로 알리지 않는다. `None`을 돌려주고 호출부가 `tool_error()`를 반환한다.
7. 단위 테스트 docstring에는 "어떤 실패를 막는지" 적는다.
8. 도구·프롬프트·모델을 바꾸면 에이전트 평가를 돌린다. **과금되므로** 관련 항목(`--ids`)부터.
9. 패키지는 `requirements.in`만 고치고 `scripts/gen_lock.py`로 락을 다시 만든다. 두 파일은 같이 커밋한다.
10. 오류·버그·문제점은 고쳤든 아니든 `docs/bug-log.md`에 기록한다.
11. 기능마다 브랜치 → PR(제목·본문 한국어) → 사용자가 병합 → CI·배포·`/health` 확인(`release` 스킬).

---

## 1. 프로젝트 정의

**목표**: 영화 질문에 **근거와 출처를 붙여 한국어로** 답하는 웹 챗봇. 질문마다 LangGraph 에이전트가 도구 네 개 중 맞는 것을
골라 부른다 — IMDB Top 250 의미 검색, 같은 목록의 SQL 조회, KOBIS(한국 영화관입장권통합전산망) API, Tavily 웹 검색.
덧붙여 매주 박스오피스 브리핑을 쓰고 **사람이 승인해야** Discord로 보내는 자동화를 둔다.

**범위 (In scope)**
- IMDB Top 250 영화 250편(`imdb_top250.csv`)의 검색·정렬·집계
- 한국 개봉작 검색, 영화 상세정보, 일별·주간·주말·주중 박스오피스
- IMDB·KOBIS로 부족한 질문(해외 신작, 수상 내역)의 웹 검색
- 스트리밍 답변, 도구 상태 표시, 출처 칩, 대화 영속화(새로고침·서버 재시작 후에도)
- 영화와 무관한 질문의 거절, 대상이 모호한 질문의 되묻기
- 도구 네 개를 MCP 서버로도 제공(Claude Desktop 등 다른 클라이언트)
- 주간 박스오피스 브리핑: 초안 → 검사 → 사람 승인 → Discord 발송 (docker compose에서만)
- 단위 테스트, MCP 테스트, Postgres 통합 테스트, 에이전트 회귀 평가, 검색 적중률 평가, 브리핑 초안 평가

**제외 (Out of scope)**
- 로그인·개인화·사용자 관리
- 진행 중인 기간의 박스오피스(KOBIS가 집계를 마치기 전에는 주지 않는다)
- 하이브리드 검색·리랭킹(측정해 보니 약점이 언어 차이라 효과가 없다. §7.1)
- 배포 서버(Railway)의 Postgres와 브리핑. 배포는 SQLite이고 브리핑이 꺼져 있다
- 쓰기 도구(도구 네 개는 모두 조회라 MCP 재연결 시 다시 불러도 안전하다)

---

## 2. 기술 스택

패키지 목록과 버전은 `README.md`의 "기술 스택"과 `requirements.txt`가 단일 출처다. 여기에는 **고른 것과 이유**만 적는다.

| 영역 | 선택 | 이유 |
| --- | --- | --- |
| 언어 | Python 3.13 (`.python-version`), TypeScript | pyenv(로컬)와 nixpacks(Railway)가 같은 파일을 읽는다. nixpacks 상한이 3.13 |
| 의존성 | `requirements.in`(직접 의존성 18개) → `requirements.txt`(의존성 락 89개) | 로컬과 배포가 같은 것을 설치하게. 락은 Linux 기준 마커로 만든다 |
| LLM | OpenAI `gpt-5.4-mini`, temperature 0 | 에이전트·브리핑 글·평가 채점관 |
| 임베딩·검색 | `text-embedding-3-small` + FAISS(파일 인덱스) | 250편은 메모리에 다 들어간다. 별도 벡터 DB가 필요 없다 |
| 정렬·집계 | SQLite 메모리 DB(표준 라이브러리) | 벡터 검색은 8편 안에서만 고른다 |
| 에이전트 | LangChain `create_agent`(LangGraph) | 도구 선택은 모델이, 루프는 LangGraph가 |
| 체크포인터 | `AsyncSqliteSaver`(배포·로컬) / `AsyncPostgresSaver`(compose) | 트리거와 승인이 다른 요청이라 브리핑은 프로세스 밖 저장소가 필요하다 |
| 외부 API | KOBIS Open API, Tavily REST(직접 호출) | `TavilySearch` 래퍼는 도구 정의만 1,521토큰이었다 |
| MCP | 공식 `mcp` SDK의 FastMCP(서버), `langchain-mcp-adapters`(클라이언트) | 도구를 다른 클라이언트와 공유, KOBIS·Tavily 키를 MCP 컨테이너에만 |
| 백엔드 | FastAPI + uvicorn, Pydantic, slowapi, cachetools, tenacity, structlog | 스트리밍, 입력·인자 검증, 레이트리밋, 응답 캐시, 재시도, JSON 로그 |
| 프론트엔드 | Next.js 16(App Router), Tailwind CSS v4, react-markdown, next-themes | `route.ts`가 서버 쪽 프록시라 브라우저는 백엔드를 직접 보지 않는다 |
| 자동화 | n8n 2.43.7(트리거), Discord 웹훅(발송) | 트리거는 HTTP 호출 하나라 다른 스케줄러로 바꾸기 쉽다 |
| 관측 | LangSmith, structlog(JSON, `session_id`·`request_id` 자동 첨부) | |
| 배포 | Vercel(web), Railway(api, nixpacks + 영구 볼륨) / 로컬은 Docker Compose | CI와 배포는 연결돼 있지 않다 |

---

## 3. 저장소 구조

```
.
├── CLAUDE.md, SPEC.md, README.md
├── rules/                     # 영역별 상세 규칙: tools.md, server.md, ci-deps.md
├── docs/                      # bug-log.md(오류·버그 기록), roadmap.md(보강 계획)
├── imdb_top250.csv            # 데이터 원본 250편
├── imdb_data.py               # 순수: CSV → 검색 문서, 인덱스 지문, 읽기 전용 SQL 실행
├── kobis_format.py            # 순수: 영화 판별, 날짜 검증·집계 가능일, 응답 가공, 현재 날짜 문장, 브리핑 주차
├── sources.py                 # 순수: 도구 출력 → 출처 칩
├── ratelimit.py               # 순수: 레이트리밋 키(방문자 IP) 판정
├── logging_config.py          # structlog 설정 + ContextVar
├── movie_tools/               # 도구 4개 구현과 이름·설명 상수: imdb.py, kobis.py, web.py
├── movie_mcp/server.py        # FastMCP 서버. movie_tools를 등록만 한다 (`mcp/`로 이름 짓지 말 것: SDK를 가린다)
├── mcp_client.py              # MCP 세션 하나를 붙잡아 두고 끊기면 다시 연결
├── agent.py                   # LLM, SYSTEM_PROMPT, local_tools(), build_agent()
├── server.py                  # FastAPI: /api/chat(스트리밍), /health, 브리핑 라우터 연결
├── imdb_rag.py                # CLI
├── briefing/                  # 주간 브리핑: graph, store, report(순수), writer, discord, api
├── automation/n8n/            # weekly-briefing.json (n8n 워크플로, 저장소가 단일 출처)
├── docker/                    # api.Dockerfile(+ .dockerignore). 루트에 두면 Railway 배포가 깨진다
├── docker-compose.yml         # postgres, mcp, api, web, n8n(profile automation)
├── railway.toml               # 배포 설정, /health 헬스체크
├── scripts/                   # gen_lock.py, check_docs.py, trace.py
├── tests/
│   ├── unit/                  # 표준 라이브러리 모듈만 import (CI는 pytest만 설치)
│   ├── mcp/                   # MCP 서버를 메모리 안에서 띄우는 테스트 + mcp_client 재연결
│   ├── integration/           # 실제 Postgres로 브리핑 승인 흐름
│   ├── fixtures/              # 실제 KOBIS 주간 응답 두 주
│   └── evals/                 # 골든셋·검색 세트, run_evals / run_retrieval / run_briefing
├── web/                       # Next.js: app/page.tsx(채팅), app/admin/briefings(승인 화면), app/api/*(프록시)
└── .claude/skills/            # release, trace (gitignore 예외로 추적)
```

---

## 4. 환경 변수

값과 설명은 `.env.example`·`web/.env.example`이 단일 출처다. 여기서는 **누가 무엇을 쓰는지**만 정한다.

| 변수 | 필수 | 쓰는 곳 | 없으면 |
| --- | --- | --- | --- |
| `OPENAI_API_KEY` | 예 | api(에이전트), mcp(검색어 임베딩) | `/health` 503 |
| `KOBIS_API_KEY` | 아니오 | 도구가 도는 곳(로컬 경로는 api, compose는 mcp만) | 해당 도구만 `MISSING_API_KEY`, `/health` 200 degraded |
| `TAVILY_API_KEY` | 아니오 | 위와 같다 | 위와 같다 |
| `MCP_URL` | 아니오 | api | 도구를 같은 프로세스에서 실행(Railway·CLI) |
| `DATABASE_URL` | 아니오 | api | 체크포인터는 SQLite, 브리핑 꺼짐(`/briefings` 503) |
| `CHECKPOINT_DB_PATH` | 아니오 | api, CLI | `vectorstore/checkpoints.sqlite` (Railway 영구 볼륨 안) |
| `PROXY_SECRET` | 배포에서 예 | api, web(같은 값) | 접속 주소로 셈. 설정하면 프록시를 거치지 않은 `/api/chat`은 403 |
| `CORS_ORIGINS` | 배포에서 예 | api | |
| `ADMIN_TOKEN` | 브리핑에서 예 | api, n8n | 승인 API 503 |
| `DISCORD_WEBHOOK_URL` | 아니오 | api | 발송하지 않고 기록만 |
| `DISCORD_REVIEW_WEBHOOK_URL` | 아니오 | api, n8n | 승인 요청·실패 알림 없음 |
| `ADMIN_URL`, `BRIEFING_APPROVAL_TTL_HOURS` | 아니오 | api | 기본값(관리자 화면 주소, 48시간) |
| `MCP_ALLOWED_HOSTS` | 아니오 | mcp | Host 헤더 검사에 compose 서비스 이름(`mcp:*`)을 더한다 |
| `LANGCHAIN_TRACING_V2`, `LANGCHAIN_API_KEY`, `LANGCHAIN_PROJECT` | 아니오 | api | 추적 꺼짐 |
| `LOG_LEVEL`, `LOG_FORMAT` | 아니오 | 모든 프로세스 | `INFO`, `json` |
| `BACKEND_URL` | 예 | web | 로컬 `http://localhost:8000` |

compose는 서비스마다 필요한 키만 넣는다. KOBIS·Tavily 키는 `mcp`만 갖고, `api`는 OpenAI 키만 갖는다.

---

## 5. 데이터 모델

### 5.1 IMDB 원본과 검색 문서

`imdb_top250.csv`: `rank, title, year, rating, director, cast, storyline` 250행.

**영화 한 편 = 검색 문서 한 개**다(행이 130~380자라 나누지 않는다). 검색 도구는 본문(`page_content`)만 LLM에 넘기므로
답에 필요한 칸은 모두 본문에 넣는다. 메타데이터는 출처 칩용이다.

```
The Shawshank Redemption (1994)
IMDB Top 250 rank 1, rating 9.3
Director: Frank Darabont
Cast: Tim Robbins, Morgan Freeman, Bob Gunton
Storyline: A banker convicted of uxoricide ...
```
메타데이터: `{"rank": int, "title": str, "year": str}` (`imdb_data.movie_docs()`). 키를 바꾸면 `sources._imdb_sources()`도 함께.

### 5.2 FAISS 인덱스

파일 이름이 **임베딩할 내용의 지문**이다: `vectorstore/imdb_<해시 12자>.faiss`. 지문은 파일 바이트가 아니라 읽어낸 내용으로
만든다(git autocrlf로 OS마다 줄바꿈이 다르다).
- 같은 이름이 있으면 `FAISS.load_local()`(임베딩 호출 없음), 없으면 CSV → 문서 250개 → 임베딩 → 그 이름으로 저장.
- CSV나 변환 형식이 바뀌면 이름이 달라져 자동으로 새로 만든다. 손으로 지울 것이 없다.

### 5.3 SQL 테이블 (`imdb_sql`)

```sql
movies(rank INTEGER, title TEXT, year INTEGER, rating REAL, director TEXT, actors TEXT, storyline TEXT)
```
요청마다 메모리 DB에 만든다. CSV의 `cast`는 SQL 키워드라 `actors`로 바꾸고, 감독 `N/A`는 NULL로 넣는다.
스키마는 `imdb_data.SQL_SCHEMA` 한 곳에 있다(테이블 생성·도구 설명·에러 메시지·MCP 리소스가 같이 쓴다).

### 5.4 대화 체크포인트

- `thread_id` = 웹이 보낸 `session_id`(UUID, `^[A-Za-z0-9_-]+$`, 64자 이하). 체크포인터의 영구 키다.
- SQLite(`AsyncSqliteSaver`/CLI는 `SqliteSaver`) 또는 Postgres(`AsyncPostgresSaver`). 테이블은 세이버의 `setup()`이 만든다.
- Postgres 연결 풀: `autocommit=True, prepare_threshold=0, row_factory=dict_row`(`from_conn_string`과 같은 옵션).

### 5.5 브리핑 기록 (Postgres `briefing_runs`)

```sql
briefing_runs(
  thread_id TEXT PRIMARY KEY,            -- briefing-2026-W41 (ISO 주차)
  week_start DATE, week_end DATE, status TEXT,
  draft TEXT, final_text TEXT, decided_by TEXT, decided_at TIMESTAMPTZ,
  sent_at TIMESTAMPTZ, delivery_id TEXT, error TEXT,
  draft_attempts INT, pending_at TIMESTAMPTZ, trigger TEXT,   -- manual | n8n
  prep_seconds REAL, llm_tokens INT,
  created_at TIMESTAMPTZ, updated_at TIMESTAMPTZ
)
```

상태 전이(모두 조건부 UPDATE — 지금 상태가 맞을 때만 바뀐다):

```
drafted → pending → approved → sending → sent
              └→ rejected      └→ failed
pending (TTL 초과) → expired
초안 검사 2회 실패·집계 기간 불일치 → failed   (failed 주는 다시 트리거하면 처음부터)
```
`decide()`는 `pending`일 때만, `claim_send()`는 `approved → sending`을 따낸 쪽만 보낸다. 웹훅에는 중복 방지 키가 없어서 이것이
유일한 방어선이다.

### 5.6 브리핑 초안 스키마 (구조화 출력)

```python
class Comment(BaseModel):
    rank: int     # 표에 있는 순위
    title: str    # 표의 제목을 글자 그대로
    comment: str  # 한 문장, 120자 이내, 숫자 금지

class Draft(BaseModel):
    headline: str            # 80자 이내, 숫자 금지
    comments: list[Comment]  # 눈에 띄는 영화 2~4편
    summary: str             # 두 문장, 200자 이내, 숫자 금지
```
순위·관객 수·증감률은 모델이 쓰지 않는다. `briefing/report.render`가 KOBIS 원자료로 표를 찍는다.

---

## 6. 인덱싱과 빌드

이 프로젝트에는 오프라인 파이프라인이 따로 없다. 처음 필요할 때 만든다.

| 무엇 | 언제 | 처리 | 출력 |
| --- | --- | --- | --- |
| FAISS 인덱스 | 도구가 처음 쓰일 때(`movie_tools.imdb.load_vectorstore`) | §5.2 | `vectorstore/imdb_<해시>.faiss` |
| SQL 테이블 | `imdb_sql` 호출마다 | CSV → 메모리 DB | 없음(요청이 끝나면 사라짐) |
| 의존성 락 | `requirements.in`을 고쳤을 때 | `python scripts/gen_lock.py` (Linux `TARGET_ENV`로 마커 평가) | `requirements.txt` |
| Docker 이미지 | `docker compose up --build` | `docker/api.Dockerfile`은 `COPY . .`, 뺄 것은 `api.Dockerfile.dockerignore`. web은 Next standalone | 이미지 2종(api·mcp 공용, web) |

벡터스토어는 compose의 named volume(`movie_index`)과 Railway 볼륨(`/app/vectorstore`)에 남는다.

---

## 7. 도구 명세

도구 이름·설명·인자 설명은 `movie_tools/`의 상수 한 곳에 있다. 로컬 도구와 MCP 도구의 정의는 **한 글자도 같아야 한다**
(`tests/mcp/`가 JSON으로 비교한다). 상세 규칙은 `rules/tools.md`.

### 7.1 `imdb_search` — 의미 검색

- FAISS에서 `k=8`편. `response_format="content_and_artifact"` — 본문은 모델에, 문서 목록은 출처 칩에.
- 설명에 **"검색어는 영어로"**. 자료가 영어라 한국어 질문을 그대로 넣으면 한국어 제목 적중이 0.30이다. 이 문장을 넣고 실제
  에이전트 기준 Hit@8 0.97 → 1.00.
- BM25(하이브리드)를 넣지 않는다. 한국어 단어가 영어 문서에 없어 이 약점을 못 고친다.

### 7.2 `imdb_sql` — 정렬·필터·집계

모델이 쓴 SELECT 한 문장을 §5.3 테이블에 실행한다(`imdb_data.query_movies`). SQL은 모델이 쓰고 모델은 웹 검색 결과의
지시에 영향받을 수 있으므로 **방어는 코드에 둔다.**
- 요청마다 새 메모리 DB. `set_authorizer`로 SELECT·읽기·함수만 허용(INSERT·DROP·ATTACH·PRAGMA·재귀 CTE·`load_extension` 거부).
- 상한: SQL 2,000자, 값 1MB, 실행 단계(진행 콜백 2,000회), 결과 30행.
- 거부되면 `[TOOL_ERROR code=INVALID_QUERY]`에 SQLite 메시지와 스키마를 붙여 돌려준다. 모델이 고쳐서 다시 부른다.
- 공격 9가지를 `tests/unit/test_imdb_data.py`가 하나씩 고정한다.

**라우팅**: 정렬·필터·집계("평점 1위", "몇 편", "~년대")는 `imdb_sql`, 특정 작품·추천은 `imdb_search`(`SYSTEM_PROMPT`).

### 7.3 `kobis_search` — 한국 영화 데이터

인자(`KobisInput`, Pydantic): `search_type: Literal["movie","detail","daily","weekly","weekend","weekday"]`, `query: str`,
`open_start_dt`·`open_end_dt`(4자리 연도, 선택).

| 모드 | KOBIS 엔드포인트 | `query` |
| --- | --- | --- |
| `movie` | `searchMovieList` | 영화 제목 (후보 여러 편) |
| `detail` | `searchMovieList` → `searchMovieInfo` | 영화 제목 (8자리 숫자면 movieCd로 보고 1단계 생략) |
| `daily` | `searchDailyBoxOfficeList` | YYYYMMDD |
| `weekly` / `weekend` / `weekday` | `searchWeeklyBoxOfficeList` `weekGb=0/1/2` | YYYYMMDD (월~일 / 금~일 / 월~목) |

- **영화 판별(`pick_movie`)**: KOBIS 목록은 관련도순이 아니다. 정확 일치 → 개봉 완료작 → 한국 제작 → 최신 개봉일 순으로
  고르고, 나머지는 `(동명/유사 제목 후보: …)`로 덧붙인다.
- **날짜 검증**: 8자리 숫자이면서 실제 달력에 있는 날짜(`is_valid_date`). 아니면 `INVALID_DATE`.
- **집계 전 차단(`boxoffice_too_recent`)**: KOBIS는 진행 중인 기간을 주지 않는다. 일별은 어제까지, 주간 3종은 직전 일요일까지.
  그 뒤 날짜는 KOBIS를 부르지 않고 `NO_DATA`와 "연도는 그대로 두고 하루·일주일 이전으로" 지침을 돌려준다.
- **출력 상한**: 상세정보의 배우는 8명까지 `이름(배역)`, 스태프는 빼고, 회사는 제작사·배급사만.
- 재시도: `tenacity` 최대 3회(`RequestException`만), 타임아웃 10초.

### 7.4 `web_search` — Tavily

- REST를 `requests`로 직접 호출(`movie_tools/web.py`). 인자는 `query` 하나, 도구 정의 82토큰.
- 결과는 `sources.format_web_results()`가 title·url·content(본문 1,000자 상한)만 담은 JSON 문자열로 만든다. 출처 추출(`_web_sources`)이 같은 형식을 읽는다.
- 재시도: 최대 2회, 연결 문제(`Timeout`·`ConnectionError`)만. 타임아웃 15초.

### 7.5 도구 에러 계약

도구 내부 에러는 예외가 아니라 문자열로 모델에 돌아간다: **`[TOOL_ERROR code=<CODE>] <message>`**

| 코드 | 언제 | 모델의 행동(`SYSTEM_PROMPT` `## 도구 에러 처리`) |
| --- | --- | --- |
| `MISSING_API_KEY` | 키 없음 | 다른 도구로 우회하거나 솔직히 알림 |
| `INVALID_DATE` / `INVALID_QUERY` | 인자·SQL 오류 | 고쳐서 같은 도구로 재호출 |
| `NO_DATA` | 집계 전·빈 결과 | 연도를 바꾸지 말고 이전 날짜로 재호출 |
| `MOVIE_NOT_FOUND` | 영화 없음 | 다른 제목·`web_search` |
| `TIMEOUT` / `HTTP_ERROR` / `NETWORK_ERROR` / `PARSE_ERROR` | 외부 API 장애 | `web_search`로 대신 |

새 코드를 만들면 `SYSTEM_PROMPT`에도 대처법을 적는다.

### 7.6 출처 추출 (`sources.extract_sources`)

| 도구 | 출처를 어디서 | 칩 |
| --- | --- | --- |
| `imdb_search` | artifact의 문서 메타데이터(MCP 경로는 `structured_content.movies`) | `IMDB #<순위> <제목>`, 최대 4개 |
| `web_search` | content의 JSON에서 `results[].url` | 원문 링크 |
| `kobis_search` | URL 없음 | 기관 홈페이지 고정(`FIXED_SOURCES`) |
| `imdb_sql` | 표 문자열이라 영화로 나눌 수 없음 | `IMDB Top 250 목록 (SQL 조회)` 고정 |

`[TOOL_ERROR`로 시작하거나 `status="error"`인 결과에는 출처를 붙이지 않는다. MCP 결과의 content 블록은 `as_text()`로 문자열로 만든 뒤 읽는다.

### 7.7 MCP 서버 (`movie_mcp/server.py`)

| 항목 | 내용 |
| --- | --- |
| 실행 | `python -m movie_mcp.server`(stdio) / `--transport streamable-http --port 8001`(compose, `/mcp`) |
| 도구 | 위 네 개. 동기 도구는 `anyio.to_thread.run_sync`로 넘긴다 |
| 리소스 | `imdb://schema`, `kobis://weekly/{target_dt}`(브리핑용 주간 원본. 도구로 만들면 에이전트 도구 목록이 바뀐다) |
| 헬스체크 | `GET /health` (HTTP 모드) — 벡터스토어와 OpenAI 키 |
| 보안 | Host 헤더 검사(DNS 리바인딩 방어)를 켜 둔다. 포트는 compose 밖으로 열지 않는다 |
| 클라이언트 | `mcp_client.PersistentMcpSession` — 전용 백그라운드 태스크가 세션 하나를 열어 두고, 호출이 예외로 실패하면 새 세션으로 한 번만 다시 부른다 |

---

## 8. LangGraph 명세

### 8.1 챗봇 에이전트 — `build_agent(checkpointer, tools=None)`

- `langchain.agents.create_agent(model=gpt-5.4-mini, tools, system_prompt=SYSTEM_PROMPT, checkpointer)`. 상태는 메시지 목록이다.
- `tools`를 생략하면 `local_tools()`(이때 벡터스토어를 불러온다), MCP면 `PersistentMcpSession`이 받은 도구.
- 모듈 수준 `agent` 객체는 없다. 진입점마다 체크포인터가 달라 팩토리로 주입한다.

| 진입점 | 체크포인터 | 호출 |
| --- | --- | --- |
| `server.py` | `AsyncSqliteSaver` / `AsyncPostgresSaver` | `astream_events(version="v2")` (sync 세이버는 `aput`에서 실패) |
| `imdb_rag.py` | `SqliteSaver` | `invoke` |
| `tests/evals` | `MemorySaver` | 항목마다 새 thread |

**요청마다 넣는 메시지**: `[SystemMessage(format_date_context(today_kst())), HumanMessage(질문)]`. 날짜는 시스템 프롬프트에
넣지 않는다(서버가 며칠 떠 있으면 굳는다). `today_kst()`를 쓴다(Railway는 UTC).

**`SYSTEM_PROMPT` 절**: `## 답변 범위`(넓은 범위 정의 · 범위 밖이면 도구 없이 짧게 거절 · 모호하면 되묻기, 세 부분이 한 세트) ·
`## 도구 선택 기준` · `## 도구 에러 처리` · `## 출력 규칙`(한국어, 모르면 모른다, 박스오피스는 집계 기간을 첫 문장에,
사실은 매번 도구로 확인).

### 8.2 주간 브리핑 그래프 (`briefing/graph.py`)

```python
class BriefingState(TypedDict, total=False):
    thread_id: str          # briefing-YYYY-Www
    week_start: str
    week_end: str
    report: dict            # 두 주를 비교한 원자료(report.compare)
    draft: dict             # §5.6 Draft
    problems: list[str]     # validate_draft가 찾은 문제
    attempts: int
    failed: bool
    decision: str           # approve / edit / reject
```

| 노드 | 처리 | 모델 |
| --- | --- | --- |
| `fetch_boxoffice` | 대상 주와 전주 KOBIS 주간 원본(MCP 리소스, 없으면 같은 함수 직접). 집계 기간이 대상 주와 다르면 실패 | 없음 |
| `compare_prev` | 순위·관객 변동, 신작·진입 계산. 두 주 모두 10위 안이면 KOBIS `rankInten`·`audiChange`와 같아야 한다 | 없음 |
| `draft` | 구조화 출력으로 초안. 재시도면 문제 목록을 붙인다 | gpt-5.4-mini |
| `validate` | 숫자, 없는 순위, 틀린 제목, 길이, '주말'·'개봉 첫날' 같은 단위 오류 검사. 통과하면 `pending`으로 기록 | 없음 |
| `notify_reviewer` | 리뷰 웹훅에 승인 요청. 알림이 실패해도 승인 대기는 남는다(관리자 화면에서 보인다) | 없음 |
| `request_approval` | `interrupt()` → 결정을 받는다. **interrupt 앞에 부작용을 두지 않는다**(재개하면 노드가 처음부터 다시 돈다) | 없음 |
| `deliver` | `claim_send()`를 따낸 경우만 `report.render`로 표를 붙여 Discord 발송 | 없음 |

```
START → fetch_boxoffice → compare_prev → draft → validate
validate --(문제 없음)--> notify_reviewer → request_approval --(approve/edit)--> deliver → END
validate --(문제 있음, 첫 시도)--> draft
validate --(두 번째도 문제)--> END (failed)
request_approval --(reject/expired)--> END
```
체크포인터는 `AsyncPostgresSaver`, `thread_id`는 주차. 재개는 `Command(resume=결정)`. 실행에 `UsageMetadataCallbackHandler`를 붙여
토큰을 세고, LangSmith 실행 이름 `weekly_briefing`, 태그 `briefing`, 메타데이터 `thread_id`·`trigger`·`week`를 단다.

---

## 9. API 명세

### 9.1 백엔드 (FastAPI)

| 메서드·경로 | 요청 | 응답 |
| --- | --- | --- |
| `POST /api/chat` | `{"question": str (1~2,000자, 공백만 불가), "session_id"?: str}` | 텍스트 스트림(아래 센티넬 포함). 헤더 `X-Request-Id`, `X-Session-Id`, `X-Cache` |
| `GET /health` | - | 200 `ok` / 200 `degraded`(선택 키 없음) / 503 `unhealthy`(에이전트·벡터스토어·OpenAI 키, MCP면 도구 4개). 네트워크 호출 없음 |
| `POST /briefings/run?trigger=` | Bearer `ADMIN_TOKEN` | 그 주 브리핑 시작(이미 있으면 그대로, `failed`면 처음부터) |
| `GET /briefings` · `GET /briefings/metrics` · `GET /briefings/{thread_id}` | Bearer | 목록 · 운영 지표 · 한 건 (`/metrics`를 `/{thread_id}`보다 먼저 등록) |
| `POST /briefings/{thread_id}/decision` | `{"action": "approve"\|"edit"\|"reject", "text"?: 수정본, "by": 이름}` + Bearer | 결정 기록 후 그래프 재개 |

**스트림 센티넬** (본문 사이에 끼우는 한 줄, 프론트 정규식 `/\x1f((?:tool|error|sources):[^\n]*)\n/g`)

```
\x1ftool:<도구 이름>\n        도구 시작
\x1ftool:end\n               도구 끝
\x1fsources:<JSON 배열>\n     [{"tool","label","url"}]
\x1ferror:<CODE>|<메시지>\n   TIMEOUT / INTERNAL 등
```
센티넬을 추가하면 프론트 정규식의 alternation도 고친다.

**보호 장치**
- 레이트리밋: 방문자당 분당 10회(`/api/chat`). 키는 `PROXY_SECRET`이 맞을 때만 `X-Client-IP`, 아니면 접속 주소.
  `X-Forwarded-For`는 쓰지 않는다(Railway에서 요청마다 달라 제한이 안 걸렸다, B-009). 비밀값이 있는 서버는 직접 호출을 403.
- 요청 타임아웃 120초(`asyncio.timeout`). 입력 검증 실패는 422.
- 응답 캐시: `TTLCache(maxsize=256, ttl=3600)`, 키 `sha256(질문.strip().lower())`, **새 세션 질문만**, 에러는 캐시하지 않는다. 출처 센티넬도 캐시에 들어간다.

### 9.2 웹 프록시 (`web/app/api/`)

| 경로 | 동작 |
| --- | --- |
| `chat/route.ts` | `BACKEND_URL/api/chat`으로 스트림을 그대로 전달. `x-real-ip` → `X-Client-IP`, `PROXY_SECRET` → `X-Proxy-Secret`. 백엔드 오류를 `BACKEND_UNREACHABLE`·`RATE_LIMIT`·`INVALID_INPUT`·`BACKEND_ERROR`로 바꾼다 |
| `admin/briefings/[[...path]]/route.ts` | 허용한 브리핑 경로만 백엔드로 넘긴다 |

### 9.3 MCP

§7.7. 다른 클라이언트용 인터페이스이며, compose의 api도 같은 서버를 쓴다.

---

## 10. 프론트엔드 명세

상세는 `web/AGENTS.md`.

- `/` 채팅: 빈 화면에 예시 질문 4개, 입력창 `maxLength` 2,000(서버와 같은 값), 토큰 단위 스트리밍과 마크다운 렌더링.
- 도구 상태: "IMDB Top 250 검색 중..." / "한국 개봉 영화 검색 중..." / "웹 검색 중..."(도구 색으로 깜박임).
- 출처: 답변 아래 **입장권** 모양 칩. 종이색은 도구별(IMDB 노랑, KOBIS 분홍, 웹 초록). IMDB 칩은 라벨에서 순위를 꺼내 크게 쓴다(`stubParts()`).
- 에러: 코드별 아이콘·라벨(`errorLabel()`), 재시도 가능한 코드에만 "다시 시도".
- 영속화: `localStorage`에 `session_id`와 메시지. "새 대화"는 새 id를 만들고 메시지를 지운다.
- 다크 모드(next-themes), 복사 버튼, 모바일(`100dvh`, iOS 자동 줌 방지, 터치 영역 44px).
- `/admin/briefings`: 토큰 입력, 운영 지표, 브리핑 목록, 초안 보기·수정·승인·거절.

---

## 11. 평가 명세

테스트는 **비용으로 층을 나눈다.** 무료 검사는 모든 push·PR에서, LLM을 부르는 평가는 수동으로.

| 층 | 위치 | 내용 | 실행 | 비용 |
| --- | --- | --- | --- | --- |
| 단위 | `tests/unit/` | 순수 함수(영화 판별, 날짜, 출처, SQL 제한, 브리핑 비교·검사, 골든셋 근거) | CI `python` 잡 | 0 |
| MCP | `tests/mcp/` | 메모리 안 MCP 서버: 도구 정의 동일성, 입력 검증, 로그, 재연결 | CI `deps` 잡 | 0 |
| 통합 | `tests/integration/` | 실제 Postgres: 재시작 후 승인, 동시 승인, 거절·만료, 그래프 이중 재개 | CI `deps` 잡(서비스 컨테이너) | 0 |
| 문서 | `scripts/check_docs.py` | 문서의 숫자·로그 이벤트·제어 문자·참조 경로를 코드와 대조 | CI `python` 잡 | 0 |
| 에이전트 | `tests/evals/run_evals.py` | 골든셋: 도구 호출 + 키워드 + LLM-as-judge | 수동(`evals.yml`) | 발생 |
| 검색 | `tests/evals/run_retrieval.py` | 질문 50개 Hit@k·MRR (원문 기준 / `--actual` 실제 기준) | 수동 | 1센트 미만 |
| 브리핑 | `tests/evals/run_briefing.py` | 저장한 KOBIS 응답으로 초안 10~20번 → 첫 시도·최종 통과율, 걸린 이유 | 수동 | 발생(작은 모델) |

**골든셋 형식** (`tests/evals/golden_dataset.json`)

```json
{"id": "imdb-001", "question": "쇼생크 탈출의 감독은 누구야?",
 "expected_tools": ["imdb_search"], "expected_keywords": ["프랭크", "다라본트"], "expected_keywords_any": true,
 "corpus_evidence": ["The Shawshank Redemption", "Frank Darabont"],
 "rubric": "쇼생크 탈출(The Shawshank Redemption)의 감독 프랭크 다라본트(Frank Darabont)를 언급해야 한다."}
```

| 범주 | 개수 | 고정하는 것 |
| --- | --- | --- |
| `kobis-*` | 6 | 모드 선택, 상대 날짜(`kobis-006`: 1년 전 데이터 버그), 집계 기간 명시(`kobis-005`) |
| `imdb-*`, `korean-imdb-*` | 4 | 의미 검색, 한국어 질문 |
| `sql-*` | 2 | 정렬·집계 라우팅 |
| `refusal-*` | 2 | 거절해야 할 질문(001)과 거절하면 안 되는 배경지식 질문(002) |
| `multiturn-*`, `ambiguous-*`, `web-*`, `schema-*` | 4 | 다시 물어도 도구 호출, 되묻기, 웹 검색, 도구 인자 |

- 통과 조건: 기대 도구를 불렀고, 키워드가 있고, judge 점수 4점 이상. **judge 호출이 실패하면 실패**다(fail-closed).
- `imdb_search`를 기대하는 항목은 `corpus_evidence`를 적는다. 그 문자열이 코퍼스에 있는지 단위 테스트가 검사한다 —
  없으면 그 항목은 검색이 아니라 모델의 사전지식을 채점한다.
- 같은 날짜가 에이전트 입력과 judge 프롬프트에 모두 들어간다.
- 도구 경로 두 개 모두 평가한다: 기본(로컬)과 `--mcp-url http://localhost:8001/mcp`.

**현재 결과**: 단위 테스트 151개 전부 통과, 평가 **18/18 통과**(로컬·MCP 양쪽), 검색 실제 기준 Hit@8 1.00 / MRR 0.96,
브리핑 초안 최종 통과 10/10에 '주말' 오기 0번.

**규칙을 손볼 때 돌릴 항목**: 답변 범위 → `refusal-001`·`refusal-002`, 날짜 → `kobis-006`, 집계 기간 → `kobis-005`,
도구 라우팅 → `imdb-002`·`sql-001`·`sql-002`, 출처 유지 → `multiturn-001`, 검색어 → `run_retrieval --actual`.
통과율만 보지 말고 결과물을 직접 읽는다(브리핑 '주말' 오기는 10/10 통과 상태에서 읽다가 찾았다).

---

## 12. 진행 단계 (시작부터 지금까지)

각 단계의 완료 기준은 그 단계를 마칠 때 확인한 것이다. 숫자는 당시 값이다.

### Phase 0 — RAG 챗봇과 첫 배포 (2026-04-04 ~ 04-07)

- [x] PDF 기반 IMDB RAG + Tavily 웹 검색을 LangGraph 에이전트로
- [x] Next.js 프론트엔드 + FastAPI 백엔드, 스트리밍, 대화 히스토리, 벡터스토어 캐시, 응답 캐시, 도구 상태 표시
- [x] Railway(nixpacks)·Vercel 배포

**완료 기준**: 공개 URL에서 질문 → 스트리밍 답변.

### Phase 1 — KOBIS와 화면 (2026-04-11 ~ 04-24)

- [x] `kobis_search`(한국 개봉작·박스오피스)
- [x] 모바일 최적화, 다크 모드, 예시 질문, 레이트리밋, 스트리밍 에러 복구

**완료 기준**: 휴대폰에서 박스오피스 질문이 답해진다.

### Phase 2 — 에이전트 하네스 (2026-06-18)

- [x] 구조화 로깅, 도구 인자 검증(Pydantic), 외부 API 재시도, 표준 도구 에러, 골든셋 평가 러너

**완료 기준**: 평가 러너가 종료 코드로 통과·실패를 알린다.

### Phase 3 — 신뢰성과 품질 기반 (2026-09-12 ~ 09-19)

- [x] KOBIS 상세정보·주말·주중 모드, `pick_movie` 판별 규칙
- [x] `.python-version` + `requirements.in` → 락, API 키 로그 유출 차단
- [x] 출처 표시, SQLite 대화 영속화, 단위 테스트 도입, 영화 외 주제 거절(당시 평가 14/14)
- [x] 입력 검증, 의미 있는 헬스체크, JSON 로그의 트레이스백 보존
- [x] GitHub Actions(비용 0 검사와 과금 평가 분리), 배포 환경 의존성 설치 검증
- [x] `web_search`를 Tavily 직접 호출로(도구 정의 합계 2,132 → 692토큰)
- [x] 상대 기간 박스오피스가 1년 전 데이터를 답하던 문제(날짜 주입 + 집계 전 차단)
- [x] `CLAUDE.md` 분리(`rules/`), 프로젝트 스킬(release·trace), 문서-코드 대조 CI

**완료 기준**: CI 녹색, 평가 전부 통과, `kobis-006` 통과.

### Phase 4 — 데이터를 바로잡기 (2026-10-01 ~ 10-03)

- [x] 잘린 PDF를 CSV로 교체(영화 한 편 = 문서 한 개), `corpus_evidence` 검사 — 당시 평가는 근거 없이 15/15를 통과하고 있었다
- [x] `imdb_sql` 도구와 SQL 실행 제한
- [x] 같은 대화에서 다시 물어도 출처가 붙게(`multiturn-001`)
- [x] 검색 적중률 측정 → "검색어는 영어로"
- [x] 화면 리디자인(출처 입장권)

**완료 기준**: IMDB 항목의 근거가 코퍼스에 모두 있고, 실제 기준 Hit@8 1.00.

### Phase 5 — 에이전트 시스템과 자동화 (2026-10-16 ~ 10-17, `docs/roadmap.md`)

| 단계 | PR | 내용 | 완료 기준 (확인함) |
| --- | --- | --- | --- |
| 1. Docker Compose | #2, #3 | web·api 원클릭 실행, CI `docker` 잡 | 새 clone에서 키 3개 → `docker compose up --wait` → 질문 |
| 2. MCP 서버 | #5, #10 | `movie_tools/` 분리, FastMCP 서버, 세션 상주(281ms → 12ms) | MCP 경로 평가 전부 통과, Claude Desktop에서 `kobis_search` 동작 |
| 3. Postgres + 승인 HITL | #12 | `AsyncPostgresSaver`, `interrupt` 승인, `briefing_runs`, 관리자 화면 | 재시작 후 승인, 동시 승인 1회 발송, 거절 시 미발송을 통합 테스트로 |
| 4. 브리핑 자동화 | #13 | n8n 트리거, 숫자는 코드가, Discord 발송 | `--profile automation`에서 실제 Discord 수신, 중복 트리거 → 브리핑 하나 |
| (5절) 운영 지표 | #14 | `/briefings/metrics`, LangSmith 메타데이터 | 관리자 화면에 지표 표시 |

같은 기간에 고친 것: 배포 서버 레이트리밋(#7, #8), 박스오피스 집계 기간 명시(#11), KOBIS 검색어 로깅(#15). 기록은 `docs/bug-log.md`.

### 다음 단계 (하지 않은 것)

- 배포 서버에 Postgres를 붙여 브리핑을 켜기(지금은 compose만)
- 채팅의 지연·비용·에러율 집계 지표(브리핑에는 있다)
- 레이트리밋 상태가 프로세스 메모리에 있다 — 서버를 여러 개 띄우면 각자 센다
- 서킷 브레이커, 컨텍스트 압축

---

## 13. 비용 기준 (확인용)

- **도구 정의**: 네 개 합계 692토큰. LLM 호출마다 나가므로 도구를 추가하면 다시 잰다.
- **무료 한도**: Tavily 월 1,000회, KOBIS 하루 1,000회.
- **평가**: 단위·MCP·통합·문서 검사는 0. 검색 적중률은 임베딩만이라 1센트 미만. 에이전트 평가와 브리핑 평가는 과금 —
  관련 항목만(`--ids`) 먼저 돌린다.
- **재시도 설계**: Tavily는 호출마다 크레딧을 써서 연결 문제만 재시도한다. 응답 캐시는 새 세션의 같은 질문을 1시간 동안 다시 부르지 않는다.
- 레이트리밋(분당 10회)과 직접 호출 403이 비용 공격(분당 수백 번의 LLM 호출)을 막는 장치다.
