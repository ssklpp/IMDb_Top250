# 서버 규칙 (`server.py`, `sources.py`, `logging_config.py`)

> 출처 추출·입력 검증·헬스체크·캐시·로깅을 고칠 때 먼저 읽을 것.
> 스트리밍 센티넬 규약과 아키텍처 개요는 `CLAUDE.md`에 있다.

## 답변 출처 (`sources.py`)
도구마다 출처의 형태가 완전히 달라서 각각 따로 처리합니다. 실제 응답을 확인해 맞춘 것이라
라이브러리 응답 구조가 바뀌면 조용히 빈 출처가 되므로 `tests/unit/test_sources.py`로 고정해 두었습니다.

| 도구 | 출처를 어디서 얻는가 | 표시 |
|---|---|---|
| `imdb_search` | `ToolMessage.artifact`의 `list[Document]` → `metadata["page"]` | PDF 쪽번호 (0-기반이라 +1) |
| `web_search` | artifact가 없다. **`content`가 `format_web_results()`가 만든 JSON 문자열**이라 파싱해서 `results[].url` 추출 | 클릭 가능한 원문 링크 |
| `kobis_search` | 문자열만 반환해 URL이 없다 | 기관 홈페이지 고정 |

- `imdb_tool`은 `response_format="content_and_artifact"`로 생성해야 `artifact`가 채워집니다.
  기본값 `"content"`로 되돌리면 **쪽번호 출처가 조용히 사라집니다.**
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
`slowapi`로 `/api/chat` 엔드포인트에 IP당 분당 10회 제한이 적용됩니다. 초과 시 429 응답을 반환합니다. `@limiter.limit("10/minute")` 데코레이터가 적용되며, FastAPI의 `Request` 객체가 첫 번째 파라미터로 필요합니다.

## 구조화 로깅 (Observability)
`logging_config.py`가 `structlog` 기반 JSON 로거를 제공합니다. `configure_logging()`은 `server.py` / `imdb_rag.py` 진입점에서 한 번 호출됩니다.
- **ContextVar**: `session_id_var`, `request_id_var`가 모든 로그에 자동 첨부됨. `server.py`의 `/api/chat` 핸들러가 요청마다 두 값을 바인딩
- **환경변수**:
  - `LOG_LEVEL` (기본 `INFO`)
  - `LOG_FORMAT=json` (기본, 프로덕션용) / `LOG_FORMAT=console` (개발 시 컬러 출력)
- **로그 이벤트 예시**: `vectorstore.cache_hit`, `chat.request`, `chat.tool_start`, `chat.tool_end`, `chat.done`(elapsed_s, tool_calls), `chat.cache_hit`, `chat.timeout`, `kobis.request`, `kobis.success`, `kobis.timeout`, `kobis.detail_resolve`(query/candidates/movie_cd/movie_nm), `kobis.movie_not_found`(stage=list|info)
- 모든 로그는 `stderr`로 출력되어 uvicorn 표준 로그와 섞이지 않음

`print()` 사용은 금지. 새 코드는 `from logging_config import get_logger; log = get_logger("module_name")`을 사용해야 합니다.

> **프로세서 체인을 수정할 때 주의**: JSON 경로에는 `format_exc_info`가 렌더러 **앞에** 있어야 합니다.
> `JSONRenderer`는 `exc_info`를 해석하지 못해 `"exc_info": true` 한 줄만 남기고 트레이스백을
> 통째로 버립니다. 반대로 `ConsoleRenderer`는 예외를 자체 처리하므로 console 경로에
> `format_exc_info`를 넣으면 그 처리를 가로챕니다. 그래서 두 경로를 분기해 둡니다.
> 이걸 놓치면 `log.exception()`이 로컬(console)에서는 멀쩡하고 프로덕션(json)에서만
> 스택을 잃는, 가장 추적하기 어려운 형태가 됩니다.

> **예외를 로깅할 때 `str(e)`를 그대로 넣지 말 것.** KOBIS는 API 키를 쿼리 파라미터로 받는데 `requests` 예외의 `str()`에는 요청 URL 전문이 들어간다. 실제로 `error=str(e)`가 네트워크 장애 시 키를 평문으로 기록하고 있었고, 지금은 `error=type(e).__name__`으로 바꿔 타입만 남긴다. 외부 API 예외를 새로 다룰 때도 같은 원칙을 지킬 것.
