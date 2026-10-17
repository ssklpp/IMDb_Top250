# 도구 규칙 (`movie_tools/`의 4개 도구)

> `imdb_search` / `imdb_sql` / `kobis_search` / `web_search`를 고칠 때 먼저 읽을 것.
> 에러 계약(`[TOOL_ERROR code=…]`)과 코드 목록은 `CLAUDE.md`에 있다.

## IMDB 도구 두 개 — 검색과 SQL
같은 250편을 두 방식으로 조회한다. 데이터 변환과 SQL 실행은 `imdb_data.py`(순수 함수)에 있다.

| 도구 | 방식 | 맞는 질문 | 못 하는 것 |
|---|---|---|---|
| `imdb_search` | 벡터 검색, 의미가 가까운 8편 | "쇼생크 감독", "놀란 영화 추천", 줄거리·분위기 | 정렬·집계. 8편 안에서만 고른다 |
| `imdb_sql` | 모델이 쓴 SELECT를 SQLite에서 실행 | "평점 1위", "1990년대 몇 편", "감독별 작품 수" | 의미 검색("우울할 때 볼 영화") |

**왜 둘인가**: CSV로 바꾼 뒤 "평점이 가장 높은 영화는?"에 모델이 검색된 8편 안에서 최고를 골라
'The Dark Knight'(9.1)라고 답했다. 정답 쇼생크(9.3)가 그 8편에 없었다. 예전 PDF 시절엔 검색 결과가
쓸모없어서 모델이 기억으로 맞혔기 때문에 드러나지 않았다. 라우팅 규칙은 `SYSTEM_PROMPT`의
`## 도구 선택 기준`에 있고 평가 `imdb-002`·`sql-001`·`sql-002`가 고정한다.

**검색어 언어** — 자료가 영어라 `imdb_search` 설명에 "검색어는 영어로"를 적었다. 검색 적중률을 재 보니 사용자
질문을 그대로 넣으면 한국어 제목은 10개 중 3개만 찾았다. 모델은 대개 제목을 스스로 번역하지만, 제목을 모르는
줄거리 묘사("요리하는 쥐")는 한국어 그대로 넘겨 놓쳤다. 이 문장을 넣고 실제 기준 적중률 0.97 → 1.00.
하이브리드 검색(BM25)은 이 문제를 못 고친다 — 한국어 단어가 영어 문서에 없다. 수치는 `tests/evals/README.md`.

**SQL 실행 제한** (`imdb_data.query_movies`) — SQL은 모델이 쓰고, 모델은 웹 검색 결과에 섞인
지시(프롬프트 인젝션)에 영향받을 수 있다. 그래서 방어를 프롬프트가 아니라 코드에 둔다.
- 요청마다 새 **메모리 DB**. 파일도, 요청 사이 공유 상태도 없다(스레드 문제도 없다).
- `set_authorizer`로 **SELECT·읽기·함수만 허용**. INSERT·DROP·ATTACH(파일 생성)·PRAGMA·재귀 CTE·
  `load_extension`이 실행 전에 거부된다.
- 값 길이 1MB 상한(`randomblob(1e9)`로 1GB 할당 차단), 실행 단계 상한(3중 조인 1,500만 행 차단),
  결과 30행 상한, SQL 2,000자 상한.
- 거부되면 `[TOOL_ERROR code=INVALID_QUERY]`에 SQLite 메시지와 스키마를 붙여 돌려준다. 프롬프트의
  기존 규칙대로 모델이 고쳐서 다시 호출한다.
- 위 공격 9가지를 `tests/unit/test_imdb_data.py`가 하나씩 고정한다. **새 허용 동작을 추가할 때는 거기에
  거부 사례도 같이 추가할 것.**

스키마는 `imdb_data.SQL_SCHEMA` 한 곳에 있고 테이블 생성·도구 설명·에러 메시지가 모두 그걸 쓴다.
CSV의 `cast` 칼럼은 **`actors`**로 바꿨다 — `cast`는 SQL 키워드라 `SELECT title, cast FROM movies`가
syntax error였다. 감독 `N/A`는 NULL로 넣는다(감독별 집계에 'N/A'라는 감독이 생기지 않게).

## KOBIS 도구 모드 (`search_type`)
`kobis_search`는 6개 모드를 지원합니다. 모두 KOBIS 오픈API 실호출로 검증되었습니다.

| 모드 | 엔드포인트 | query 의미 |
|---|---|---|
| `movie` | `movie/searchMovieList.json` | 영화 제목 (여러 후보 나열) |
| `detail` | `movie/searchMovieList` → `movie/searchMovieInfo.json` | 영화 제목 (상세정보 1편) |
| `daily` | `boxoffice/searchDailyBoxOfficeList.json` | YYYYMMDD |
| `weekly` | `boxoffice/searchWeeklyBoxOfficeList.json` `weekGb=0` | YYYYMMDD (주간 월~일) |
| `weekend` | 같은 URL, `weekGb=1` | YYYYMMDD (주말 금~일) |
| `weekday` | 같은 URL, `weekGb=2` | YYYYMMDD (주중 월~목) |

**KOBIS는 진행 중인 기간을 주지 않습니다.** 일별은 다음 날, 주간·주말·주중은 해당 주가 끝난 뒤에
공개됩니다(실호출로 확인). 이를 두 겹으로 처리합니다.

1. **호출 전 차단** — `is_valid_date()`를 통과한 날짜를 `boxoffice_too_recent()`(`kobis_format.py`)가
   검사해, 집계 전이면 KOBIS를 부르지 않고 `[TOOL_ERROR code=NO_DATA]`를 반환합니다. 메시지에
   조회 가능한 가장 최근 날짜를 함께 줍니다. 기준은 `latest_boxoffice_date()`:
   - 일별 → 어제
   - 주간·주말·주중 → 직전 일요일(이번 주는 아직 집계 전). 월요일이면 바로 전날이 지난 주에 속한다.
2. **빈 결과 안전망** — 1을 통과했는데도 KOBIS가 빈 목록을 주면 역시 `NO_DATA`로 반환합니다.

두 경우 모두 "연도는 그대로 두고 하루/일주일 이전으로 재호출하라"는 지침을 붙입니다. 이 지침이
없을 때 LLM이 연도만 1년 낮춰 재호출해 작년 데이터를 "지난 주"라고 답한 적이 있습니다.
LLM의 날짜 계산은 실행마다 흔들리므로(같은 질문에 `20260911`이나 `20260914`) 프롬프트만으로는
부족하다고 보고 도구 쪽에 1을 두었습니다.

박스오피스 4종은 단일 분기로 통합되어 있고, 헤더 라벨은 하드코딩이 아니라 응답의 `boxOfficeResult.boxofficeType`을 사용합니다(KOBIS가 "주말 박스오피스" 같은 한글 라벨을 직접 제공).

**`detail` 모드의 2단계 조회**: `searchMovieInfo`는 `movieCd`(영화코드)로만 조회되고 영화명으로는 안 됩니다. 그래서 `detail`은 내부에서 목록조회 → movieCd 해석 → 상세조회를 자동 처리하며, LLM은 영화명으로 한 번만 호출하면 됩니다. query가 8자리 숫자면 movieCd로 간주해 1단계를 건너뜁니다.

**`pick_movie()`의 해석 규칙** (`kobis_format.py`) — KOBIS 목록은 관련도순이 아니라서 1위가 정답이 아닌 경우가 많습니다("기생충" 검색 시 1위가 "마약 기생충"). 다음 순서로 좁힙니다:
1. 공백·대소문자 무시 **정확 일치**
2. `prdtStatNm == "개봉"` (개봉 완료작)
3. `repNationNm == "한국"` (한국 제작 — 이게 없으면 "올드보이"가 2003년 박찬욱판이 아니라 2013년 스파이크 리 리메이크로 해석됨)
4. 최신 `openDt`

선택되지 않은 후보는 출력 말미에 `(동명/유사 제목 후보: ...)`로 덧붙여 LLM이 오선택을 인지할 수 있게 합니다.

**출력 상한** — KOBIS 상세정보는 `actors`를 90건, `staffs`를 625건까지 반환합니다. `staffs`는 전량 제외(VFX 아티스트·투자 등 크루 노이즈), `actors`는 `DETAIL_MAX_ACTORS=8`건까지만 `이름(배역)` 형태로 렌더링하고 나머지는 "외 N명"으로 요약합니다. `companys`는 `companyPartNm`으로 제작사·배급사만 필터합니다.

## 웹 검색 도구 (`web_search`)
`langchain_tavily.TavilySearch`를 쓰지 않고 `movie_tools/web.py`의 `_tavily_search()`가 Tavily REST API를
`requests`로 직접 호출한다. **`TavilySearch`로 되돌리지 말 것.** 바꾼 이유가 전부 실측으로 확인된 문제다.

| 문제 (TavilySearch) | 실측 | 지금 |
|---|---|---|
| LLM에 파라미터 9개(`include_domains`, `time_range` 등)를 노출 | 도구 정의 **1,521토큰**. LLM 호출마다 전송되고 질문 하나에 호출이 2번이라 약 3,000토큰이 매번 나감 | `query` 하나, **82토큰** (도구 정의 합계 2,132 → 692) |
| 결과 dict를 통째로 문자열화 (`score`, `raw_content`, `images`, `request_id` …) | 결과 **1,546토큰** | `format_web_results()`가 title·url·content만, 본문 1,000자 상한 → **1,059토큰** |
| 생성 시점에 `TAVILY_API_KEY` 필수 | 키가 없으면 **`agent.py` import부터 실패** — 헬스체크의 "선택 키" 설계와 모순 | 키가 없으면 도구만 `MISSING_API_KEY` 반환 |
| 동기 경로 `requests.post`에 timeout 없음 | 응답이 멈추면 요청 제한(120초)까지 붙잡힘 | `WEB_TIMEOUT=15`, 연결 문제만 2회 재시도 |

- **기존 도구를 새 `@tool` 안에서 `invoke()`로 감싸는 방식도 안 된다.** 중첩 실행이 도구 이벤트로
  잡혀 `\x1ftool:` 센티넬과 `tool_calls` 집계가 두 번씩 나간다. 그래서 래핑이 아니라 직접 호출이다.
- 결과 형태는 `sources.py`의 `format_web_results()`(만드는 쪽)와 `_web_sources()`(읽는 쪽)가 공유한다.
  한쪽만 바꾸면 에러 없이 출처 칩만 사라지므로 두 함수를 같은 모듈에 두고 왕복 테스트로 고정했다.
- 검색어는 사용자 질문에서 파생되므로 로그에는 `query_len`만 남긴다.

## 도구 입력 검증
> 에러 포맷(`[TOOL_ERROR code=…]`)과 코드 목록은 `CLAUDE.md`에 있다.
`kobis_search`는 Pydantic `KobisInput` 스키마(`movie_tools/kobis.py`)로 인자를 검증합니다. MCP 경로에서는 `movie_mcp/server.py`가 같은 모델을 거쳐 field_validator까지 돌린다.
- `search_type`: `Literal["movie", "detail", "daily", "weekly", "weekend", "weekday"]` — 잘못된 값 시 LangChain이 `ValidationError`를 ToolMessage로 변환해 LLM에 반환 → LLM이 자가 정정 후 재호출
- `open_start_dt` / `open_end_dt`: `field_validator`로 4자리 숫자 검증
- 박스오피스 모드의 `query`: `is_valid_date()`(`kobis_format.py`)로 **8자리 숫자이면서 실제 달력에 존재하는 날짜**인지 검증. 두 조건을 모두 봐야 합니다 — KOBIS가 `99999999`에도 에러 대신 엉뚱한 데이터를 반환하고, 반대로 `strptime`만 쓰면 제로 패딩에 관대해서 7자리 `'2026091'`을 2026-09-01로 통과시킵니다(실제로 있었던 버그). `detail`은 제목을 받으므로 이 검증 대상이 아닙니다.

> 날짜 검증을 Pydantic `model_validator`로 옮기지 말 것. `search_type` 의존 규칙이라 교차 필드 검증이 필요한데, 옮기면 에러 표면이 `[TOOL_ERROR code=INVALID_DATE]`에서 Pydantic `ValidationError`로 바뀌어 이 문서와 `SYSTEM_PROMPT`의 서술이 전부 어긋납니다.

> **헬퍼에서 "못 찾음"을 `raise ValueError`로 신호하지 말 것.** `kobis_search`의 `except (KeyError, ValueError, TypeError)`가 잡아서 `PARSE_ERROR`로 오분류합니다. `None`/빈 dict를 반환하고 호출부에서 `tool_error()`를 반환하세요. 같은 이유로 헬퍼는 `_kobis_get`의 예외를 잡지 않고 그대로 전파시켜 기존 4종 except가 처리하게 합니다.

## 신뢰성 (Retry / Fallback)
- `_kobis_get()`은 `tenacity`로 KOBIS API 호출을 최대 3회까지 지수 백오프로 재시도(`RequestException`만 대상)
- `_tavily_search()`는 **최대 2회, 연결 문제(`Timeout`/`ConnectionError`)만** 재시도. Tavily는 호출마다 크레딧을 쓰고 HTTP 오류는 다시 보내도 같기 때문
- 도구별 타임아웃: `KOBIS_TIMEOUT=10s`, `WEB_TIMEOUT=15s`. 전체 요청 타임아웃: `REQUEST_TIMEOUT_S=120` (`server.py`)
- 폴백은 명시적 코드가 아닌 LLM 판단으로 처리됨 — 도구 에러 메시지에 "web_search로 대신 시도해보세요" 같은 힌트를 포함

## MCP 서버 (`movie_mcp/server.py`)

도구 4개를 FastMCP 서버로 내보낸다. 구현은 `movie_tools/`이고 서버는 등록만 한다.

| 항목 | 내용 |
|---|---|
| 실행 | `python -m movie_mcp.server` (stdio, Claude Desktop용) / `--transport streamable-http --port 8001` (compose) |
| 도구 | `imdb_search`, `imdb_sql`, `kobis_search`, `web_search` — 이름·설명·인자 스키마가 로컬 도구와 **완전히 같다** |
| 리소스 | `imdb://schema` (`imdb_data.SQL_SCHEMA`) |
| 헬스체크 | `GET /health` — 벡터스토어와 OpenAI 키가 준비돼야 200 (HTTP 모드에서만) |
| 키 | OPENAI(검색어 임베딩), KOBIS, TAVILY. 에이전트 쪽(compose의 api)은 OpenAI 키만 갖는다 |

**왜 두 경로 모두 18/18이어야 하나** — MCP로 바꿔도 모델이 받는 입력이 같아야 같은 도구를 고른다.
그래서 이름·설명·인자 설명을 `movie_tools`의 상수 한 곳에 두고, `tests/mcp/`가 로컬과 MCP의 도구 정의를
JSON 그대로 비교한다. 처음 옮길 때 `kobis_search` 설명이 docstring 들여쓰기 4칸만큼 달랐다 — 눈으로는
안 보이는 차이다. 바꾼 뒤 평가는 `run_evals`와 `run_evals --mcp-url …`로 양쪽 다 돌린다.

**MCP를 거치면 결과 형태가 달라진다** (`langchain-mcp-adapters` 0.3.0)
- `ToolMessage.content`가 문자열이 아니라 블록 목록(`[{"type": "text", "text": …}]`)이다. `sources.py`는
  읽기 전에 `_as_text()`로 문자열로 만든다. 안 그러면 웹 출처가 사라지고 에러 응답에도 고정 출처가 붙는다.
- `imdb_search`의 artifact는 `list[Document]`가 아니라 `{"structured_content": {"movies": [{rank, title, year}]}}`다.
  서버가 `CallToolResult(content=본문, structuredContent=영화 목록)`으로 보낸다 — 모델은 본문만 본다.
- 인자 검증 실패는 `isError` 결과 → `status="error"` ToolMessage로 모델에 간다(로컬의 ValidationError와 같은 동작).

**구현 주의**
- **동기 도구는 스레드로 넘긴다**(`anyio.to_thread.run_sync`). FastMCP는 동기 함수를 이벤트 루프에서 그대로
  돌려서, KOBIS 응답을 기다리는 동안 다른 요청이 멈춘다.
- **Host 헤더 검사를 끄지 않는다.** FastMCP는 host가 127.0.0.1일 때만 DNS 리바인딩 방어를 켠다. 0.0.0.0으로
  띄우면 꺼지므로 직접 켜고, compose 서비스 이름만 `MCP_ALLOWED_HOSTS=mcp:*`로 더한다. 포트는 밖으로 열지 않는다.
- **`.env`는 저장소 경로로 읽는다.** Claude Desktop은 서버를 어느 폴더에서 띄울지 모르고, 부모 환경변수도 대부분
  넘기지 않는다(MCP Inspector도 마찬가지였다). 벡터스토어 경로도 같은 이유로 저장소 기준 절대 경로다.
- **로그는 stderr.** stdio 모드에서 stdout은 프로토콜 통로다.

**지연과 세션** (같은 PC, 중앙값) — 어댑터 기본(`get_tools()`)은 도구 호출마다 세션을 새로 열어서(초기화 왕복 포함)
`imdb_sql` 3ms → 281ms, `imdb_search` 156ms → 436ms였다. 지금은 `mcp_client.PersistentMcpSession`이 세션 하나를 붙잡아
두고 모든 호출이 같이 쓴다 → 12ms·169ms. `docs/bug-log.md` B-013.
- **세션은 전용 백그라운드 태스크가 열고 닫는다.** MCP SDK 연결은 anyio 태스크 그룹이라 요청 태스크에서 열고 lifespan에서
  닫으면 "다른 태스크에서 cancel scope를 나갔다" 에러가 난다.
- **호출이 예외로 실패하면 새 세션으로 한 번만 다시 부른다.** 도구 자체의 실패는 예외가 아니라 isError 결과로 오므로, 예외는
  연결 문제다(MCP 서버 재시작 등). 도구 4개는 모두 조회라 다시 불러도 안전하다 — **쓰기 도구를 추가하면 이 재시도를 다시 볼 것.**
- 재시작 시험: compose에서 `docker compose restart mcp` 후에도 웹 질문이 이어졌다(로그 `mcp.reconnect` 1번).
- `run_evals --mcp-url`은 여전히 호출마다 세션을 연다(평가 속도는 중요하지 않다).
