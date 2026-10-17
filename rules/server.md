# 서버 규칙 (`server.py`, `sources.py`, `logging_config.py`)

> 출처 추출·입력 검증·헬스체크·캐시·로깅을 고칠 때 먼저 읽을 것.
> 스트리밍 센티넬 규약과 아키텍처 개요는 `CLAUDE.md`에 있다.

## 답변 출처 (`sources.py`)
도구마다 출처의 형태가 완전히 달라서 각각 따로 처리합니다. 실제 응답을 확인해 맞춘 것이라
라이브러리 응답 구조가 바뀌면 조용히 빈 출처가 되므로 `tests/unit/test_sources.py`로 고정해 두었습니다.

| 도구 | 출처를 어디서 얻는가 | 표시 |
|---|---|---|
| `imdb_search` | `ToolMessage.artifact`의 `list[Document]` → `metadata["rank"]`·`["title"]` | 영화마다 칩 하나(`IMDB #1 The Shawshank Redemption`), 검색 순서 유지, 최대 4개 |
| `web_search` | artifact가 없다. **`content`가 `format_web_results()`가 만든 JSON 문자열**이라 파싱해서 `results[].url` 추출 | 클릭 가능한 원문 링크 |
| `kobis_search` | 문자열만 반환해 URL이 없다 | 기관 홈페이지 고정 |
| `imdb_sql` | 표 형태 문자열이라 개별 영화로 나눌 수 없다 | `IMDB Top 250 목록 (SQL 조회)` 고정 |

URL 없는 도구의 고정 출처는 `sources.FIXED_SOURCES` 한 곳에 있다. `[TOOL_ERROR`로 시작하는 응답은
두 도구 모두 출처로 내보내지 않는다.

- `imdb_tool`은 `response_format="content_and_artifact"`로 생성해야 `artifact`가 채워집니다.
  기본값 `"content"`로 되돌리면 **IMDB 출처가 조용히 사라집니다.** 메타데이터 키는
  `imdb_data.movie_docs()`가 정한다. 키 이름을 바꾸면 이쪽도 함께.
- 출처는 **도구를 부른 차례에만** 붙는다. 같은 대화에서 이미 답한 사실을 다시 물으면 모델이 도구 없이
  답해 출처가 사라졌다 — `SYSTEM_PROMPT`에 "사실은 매번 도구로 확인" 규칙을 두고 평가 `multiturn-001`로 고정.
- 출처 칩은 "검색해 온 자료"이지 "답에 쓴 자료"가 아닙니다. 검색이 엉뚱한 영화를 가져오고 모델이
  사전지식으로 답하면 무관한 영화가 출처로 붙습니다. 검색 적중 여부는 평가에서 따로 봐야 합니다.
- KOBIS 응답이 `[TOOL_ERROR`로 시작하면 출처로 내보내지 않습니다. 실패한 호출은 답변의 근거가 아닙니다.
- 출처 센티넬은 응답 캐시에도 저장됩니다. 안 그러면 캐시 HIT일 때만 출처가 사라져 표시가 달라집니다.

응답 헤더에 `X-Request-Id`, `X-Session-Id`, `X-Cache`(HIT/MISS)가 포함됩니다.

## 응답 캐싱
`server.py`는 `cachetools.TTLCache(maxsize=256, ttl=3600)`을 사용합니다. 캐시 키는 `sha256(question.strip().lower())`. **새 세션(`session_id`가 요청에 없는 경우)에만** 캐시를 적용해 대화 컨텍스트가 있는 요청과 충돌하지 않습니다. 에러 응답은 캐시하지 않습니다.

## 사용자 입력 검증 (`QuestionRequest`)
`server.py`의 요청 모델이 Pydantic으로 입력을 제한합니다. 도구 인자(`KobisInput`)는 엄격히
검증하면서 정작 사용자 입력은 무검증이던 역전 구조를 바로잡은 것입니다.

- `question`: `min_length=1`, `max_length=MAX_QUESTION_CHARS`(2000), 그리고 `field_validator`로
  strip 후 빈 값 거부. **`min_length=1`만으로는 `"   "` 같은 공백 입력을 걸러내지 못합니다.**
- `session_id`: `max_length=64` + `^[A-Za-z0-9_-]+$`. 이 값이 그대로 `thread_id`가 되어
  체크포인터의 **영구 키**가 되므로 임의 문자열이 들어오면 안 됩니다.

검증 실패는 FastAPI가 **422**로 응답합니다. `web/app/api/chat/route.ts`가 이를 `INVALID_INPUT`
코드로 변환해 사용자에게 이유를 보여주고, 프론트는 재시도 버튼을 띄우지 않습니다
(같은 입력을 다시 보내도 실패하므로). 입력창에도 `maxLength`가 걸려 있어 정상 경로에서는
애초에 발생하지 않습니다 — **두 값(2000)을 바꿀 때는 양쪽을 함께 고쳐야 합니다.**

## 헬스체크 (`/health`)
의존성을 실제로 확인하고 **치명적 문제와 부분 장애를 구분**합니다.

| 구분 | 항목 | 실패 시 |
|---|---|---|
| 치명적 | `agent` 준비, `vectorstore` 비어있지 않음, `OPENAI_API_KEY` | **503** `unhealthy` |
| 선택 | `KOBIS_API_KEY`, `TAVILY_API_KEY` | 200 `degraded` |

KOBIS/Tavily 키가 없어도 503을 주면 안 됩니다. 해당 도구만 `[TOOL_ERROR]`를 반환하고
LLM이 다른 도구로 우회하므로 서비스는 계속 동작합니다. 여기서 503을 주면 Railway가
배포를 실패로 판정합니다.

**네트워크 호출은 하지 않습니다.** 헬스체크가 외부 API 장애에 물려 같이 죽으면
멀쩡한 서버가 재시작 루프에 빠집니다.

## 레이트 리미팅
`slowapi`로 `/api/chat` 엔드포인트에 방문자당 분당 10회 제한이 적용됩니다. 초과 시 429 응답을 반환합니다. `@limiter.limit("10/minute")` 데코레이터가 적용되며, FastAPI의 `Request` 객체가 첫 번째 파라미터로 필요합니다.

**누구로 셀지(`ratelimit.rate_limit_key`)가 핵심이다.** 예전에는 TCP 접속 주소(`get_remote_address`)로 셌는데,
배포 서버 앞의 Railway 프록시 주소가 요청마다 달라서 **같은 PC에서 15개를 동시에 보내도 429가 한 번도
나지 않았다**(로컬에서는 11번째부터 정확히 걸렸다. `docs/bug-log.md` B-009).

- **방문자 IP는 우리 프록시가 보낸 것만 믿는다.** `route.ts`가 `x-real-ip`(Vercel이 방문자 IP로 덮어쓰는 헤더)를
  `X-Client-IP`로, 공유 비밀값을 `X-Proxy-Secret`으로 보낸다. 백엔드는 `PROXY_SECRET`과 일치할 때만 믿는다
  (`hmac.compare_digest`). 비밀값 없이 믿으면 누구나 IP를 바꿔 가며 제한을 피한다.
- **`X-Forwarded-For`는 쓰지 않는다.** 맨 오른쪽 값을 "우리 프록시가 붙인 접속 주소"로 보고 한 번 배포했는데,
  Railway에서는 그 값도 요청마다 달라 직접 호출에 제한이 걸리지 않았다(실측). 왼쪽 값은 위조된다.
- **비밀값이 설정된 서버는 프록시를 거치지 않은 `/api/chat`을 403으로 거절한다**(`chat.forbidden_direct`).
  백엔드 주소로 바로 오는 요청은 방문자를 구분할 방법이 없어서, 받으면 제한 없는 LLM 호출 통로가 된다.
  `/health`는 막지 않는다(Railway 헬스체크·`check_deploy.py`가 쓴다).
- `PROXY_SECRET`은 **Railway와 Vercel에 같은 값**을 넣는다. 값이 어긋나면 웹 화면의 모든 질문이
  "서버 오류(HTTP 403)"가 된다. Vercel **Preview** 환경에도 넣어야 PR 미리보기에서 질문이 된다.
- 비밀값이 없는 환경(로컬·compose)은 TCP 접속 주소로 센다. 프록시가 하나라 모두 한 통이지만 사용자가 한 명이다.
- 로그(`chat.request`)에는 IP를 남기지 않고 `rate_key_source`(proxy·peer)만 남긴다.
- 제한 상태는 프로세스 메모리에 있다. 서버를 여러 개 띄우면 각자 따로 센다.
- **레이트리밋을 고치면 로컬이 아니라 배포 서버에서 동시 요청으로 확인한다.** 로컬에는 프록시가 없어서 이 버그가 안 보였다.

## 브리핑 승인 (`briefing/`, 보강 계획 3단계)

주간 박스오피스 브리핑을 **사람이 승인해야 발송**한다. 그래프는 `초안 → 승인 대기(interrupt) → 발송`.

- **Postgres(`DATABASE_URL`)가 있을 때만 켜진다.** 트리거하는 요청과 승인하는 요청이 다르고(때로는 다른 프로세스),
  그 사이에 서버가 재시작될 수 있어서 그래프 상태(체크포인터)와 승인 상태(`briefing_runs`)가 모두 프로세스 밖에 있어야
  한다. 지금은 compose만 Postgres다. Railway는 SQLite라 `/briefings`가 503 `BRIEFING_DISABLED`.
- **API**(`briefing/api.py`, 모두 `Authorization: Bearer <ADMIN_TOKEN>`): `POST /briefings/run`, `GET /briefings`,
  `GET /briefings/{thread_id}`, `POST /briefings/{thread_id}/decision`(`approve`·`edit`·`reject`). `ADMIN_TOKEN`이 없으면 503.
  웹 화면은 `/admin/briefings`, 프록시는 `web/app/api/admin/briefings/[[...path]]/route.ts`(허용 경로만 넘김).
- **thread_id는 주차**(`briefing-2026-W41`, `kobis_format.briefing_week`). 트리거가 두 번 와도 하나로 모인다.
- **중복 발송은 `briefing_runs`의 조건부 UPDATE가 막는다**(`store.py`). `decide()`는 `pending`일 때만,
  `claim_send()`는 `approved → sending`을 따낸 쪽만 보낸다. 웹훅에는 중복 방지 키가 없어서 이게 유일한 방어선이다.
  `claim_send`는 그래프가 두 번 재개될 때만 의미가 있다 — 그 경우를 직접 만드는 테스트가 있다(B-016).
- **interrupt 노드에는 부작용을 두지 않는다.** 재개되면 그 노드가 처음부터 다시 돈다. 초안 생성·발송은 다른 노드다.
- **만료**: 승인 대기가 `BRIEFING_APPROVAL_TTL_HOURS`(기본 48)를 넘기면 `expired`, 승인해도 보내지 않는다.
- **결정은 기록했는데 재개 전에 죽은 경우**: 같은 결정을 다시 보내면 재개만 한다(`briefing.resume_retry`).
- **그래프(4단계)**: `fetch_boxoffice → compare_prev → draft ⇄ validate → notify_reviewer → request_approval → deliver`.
  - KOBIS 원본은 **MCP 리소스** `kobis://weekly/{YYYYMMDD}`로 받는다(MCP가 없으면 같은 함수를 직접). 도구로 만들면 에이전트의
    도구 목록이 바뀌어 챗봇 평가가 흔들린다. 전주도 받아 `briefing/report.compare`가 순위·관객 변동을 직접 계산한다 —
    두 주 모두 10위 안이면 KOBIS의 `rankInten`·`audiChange`와 같아야 한다(단위 테스트).
  - **숫자는 모델이 쓰지 않는다.** LLM(`briefing/writer.py`, 구조화 출력)은 헤드라인·코멘트·요약만 쓰고, 표는
    `report.render`가 원자료로 찍는다. `validate_draft`가 모델 글의 숫자·없는 순위·틀린 제목·'주말' 같은 단위 오류를 막는다.
    걸리면 문제를 알려 한 번 더 쓰고(`draft_attempts`), 그래도 걸리면 `failed` — 발송되지 않는다.
  - 집계 기간이 대상 주와 다르면 `failed`(1년 전 데이터를 '지난 주'라고 답한 버그의 재발 방지).
  - **실패한 주는 다시 트리거하면 처음부터 다시 돈다**(월요일 아침 KOBIS 집계가 늦을 때). 승인 대기·발송된 주는 그대로.
- **발송**: `DISCORD_WEBHOOK_URL`이 있으면 Discord(`briefing/discord.py`), 없으면 기록만(`LogSender`). 멘션은 막는다
  (`allowed_mentions`). 웹훅 URL에 토큰이 있으므로 로그에 남기지 않는다. 승인 요청 알림은 `DISCORD_REVIEW_WEBHOOK_URL`.
- **운영 지표**: `GET /briefings/metrics`(관리자 화면 위쪽) — 수정 없이 승인된 비율, 초안 재작성률, 승인까지 시간, 초안 1건의
  시간·LLM 토큰. 토큰은 그래프 실행에 `UsageMetadataCallbackHandler`를 붙여 센다. `/metrics`는 `/{thread_id}`보다 **먼저**
  등록해야 한다(아니면 "metrics"가 thread_id로 잡힌다).
- **LangSmith**: 실행 이름 `weekly_briefing`, 태그 `briefing`, 메타데이터 `thread_id`·`trigger`(manual·n8n·approval)·`week`.
  `POST /briefings/run?trigger=n8n`으로 누가 시작했는지 남긴다.
- **트리거**: n8n(`docker compose --profile automation up`, `automation/n8n/weekly-briefing.json`). 월요일 10시·15시에
  `POST /briefings/run`, 응답이 200이 아니거나 `failed`면 리뷰 웹훅으로 알린다. 수동 실행:
  `docker compose exec -e N8N_RUNNERS_BROKER_PORT=5690 n8n n8n execute --id WeeklyBriefing01`
  (포트를 바꾸지 않으면 실행 중인 서버와 task broker 포트가 겹친다).
- 통합 테스트는 실제 Postgres로 돈다(`tests/integration/`, CI `deps` 잡의 서비스 컨테이너).
- **Windows에서 직접 띄울 때**: psycopg async는 Windows 기본 이벤트 루프(Proactor)에서 돌지 않는다. Postgres를 쓸 때는
  compose(리눅스)로 띄운다. 테스트는 `SelectorEventLoop`로 돈다.

## 구조화 로깅 (Observability)
`logging_config.py`가 `structlog` 기반 JSON 로거를 제공합니다. `configure_logging()`은 `server.py` / `imdb_rag.py` / `movie_mcp/server.py` 진입점에서 한 번 호출됩니다.
- **ContextVar**: `session_id_var`, `request_id_var`가 모든 로그에 자동 첨부됨. `server.py`의 `/api/chat` 핸들러가 요청마다 두 값을 바인딩
- **환경변수**:
  - `LOG_LEVEL` (기본 `INFO`)
  - `LOG_FORMAT=json` (기본, 프로덕션용) / `LOG_FORMAT=console` (개발 시 컬러 출력)
- **로그 이벤트** (코드에 있는 전체 목록 — 새 이벤트를 추가하면 여기도 갱신):
  - 요청: `chat.request`(rate_key_source), `chat.forbidden_direct`(비밀값 없는 직접 호출 거절), `chat.cache_hit`, `chat.tool_start`, `chat.tool_end`, `chat.sources`, `chat.done`(elapsed_s, tool_calls), `chat.timeout`, `chat.unhandled_error`
  - 서버: `startup.checkpointer_ready`(db=postgres 또는 SQLite 경로), `startup.mcp_tools`(MCP_URL일 때 받은 도구 이름), `shutdown.checkpointer_closed`, `health.not_ok`, `vectorstore.cache_hit`, `vectorstore.build_start`, `vectorstore.build_done`
  - 브리핑: `startup.briefing_ready`(channel·review_notice), `briefing.start`(retry), `briefing.prev_week_missing`, `briefing.validated`(attempt·problems), `briefing.failed`(reason), `briefing.pending`, `briefing.notify_failed`, `briefing.decided`, `briefing.resume_retry`, `briefing.send_skipped`(이미 다른 실행이 보냄), `briefing.sent`(chars·delivery_id), `briefing.send_failed`
  - KOBIS 원본: `kobis.weekly_raw`(브리핑용 주간 원본 조회)
  - MCP 클라이언트(`mcp_client.py`): `mcp.session_open`, `mcp.session_error`(error=타입), `mcp.reconnect`(서버 재시작 등으로 호출이 실패해 새 세션으로 다시 부름)
  - MCP 서버(`movie_mcp/server.py`): `mcp.start`(transport). 도구 이벤트(kobis·web·imdb·vectorstore)는 그 프로세스에서 같은 이름으로 찍힌다
  - KOBIS: `kobis.request`, `kobis.success`, `kobis.detail_resolve`(query/candidates/movie_cd/movie_nm), `kobis.movie_not_found`(stage=list|info), `kobis.too_recent`(호출 전 차단), `kobis.empty_result`, `kobis.no_api_key`, `kobis.timeout`, `kobis.http_error`, `kobis.network_error`, `kobis.parse_error`
  - 웹 검색: `web.request`(query_len), `web.success`, `web.empty_result`, `web.no_api_key`, `web.timeout`, `web.http_error`, `web.network_error`, `web.parse_error`
  - IMDB SQL: `imdb.sql_request`(query_len — SQL 본문은 남기지 않음), `imdb.sql_done`(rows), `imdb.sql_rejected`(error=예외 타입)
  - CLI: `cli.start`, `cli.exit`, `cli.invoke_error`
- 모든 로그는 `stderr`로 출력되어 uvicorn 표준 로그와 섞이지 않음. **MCP stdio 모드에서는 stdout이 프로토콜 통로**라 이게 필수다 — stdout에 로그나 `print()`가 섞이면 Claude Desktop 같은 클라이언트가 메시지를 해석하지 못한다

`print()` 사용은 금지. 새 코드는 `from logging_config import get_logger; log = get_logger("module_name")`을 사용해야 합니다.

> **프로세서 체인을 수정할 때 주의**: JSON 경로에는 `format_exc_info`가 렌더러 **앞에** 있어야 합니다.
> `JSONRenderer`는 `exc_info`를 해석하지 못해 `"exc_info": true` 한 줄만 남기고 트레이스백을
> 통째로 버립니다. 반대로 `ConsoleRenderer`는 예외를 자체 처리하므로 console 경로에
> `format_exc_info`를 넣으면 그 처리를 가로챕니다. 그래서 두 경로를 분기해 둡니다.
> 이걸 놓치면 `log.exception()`이 로컬(console)에서는 멀쩡하고 프로덕션(json)에서만
> 스택을 잃는, 가장 추적하기 어려운 형태가 됩니다.

> **예외를 로깅할 때 `str(e)`를 그대로 넣지 말 것.** KOBIS는 API 키를 쿼리 파라미터로 받는데 `requests` 예외의 `str()`에는 요청 URL 전문이 들어간다. 실제로 `error=str(e)`가 네트워크 장애 시 키를 평문으로 기록하고 있었고, 지금은 `error=type(e).__name__`으로 바꿔 타입만 남긴다. 외부 API 예외를 새로 다룰 때도 같은 원칙을 지킬 것.
