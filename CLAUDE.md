# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 프로젝트 개요
IMDB Top 250 영화 PDF를 기반으로 한 AI 영화 전문가 챗봇. RAG(PDF 검색), KOBIS 한국 영화 데이터베이스 API, Tavily 웹 검색을 결합한 LangGraph 에이전트 구조. FastAPI 백엔드 + Next.js 프론트엔드로 웹 서비스 제공.

## 실행 명령어

### 백엔드 (FastAPI)
가상환경을 먼저 활성화한다. Python은 `.python-version`(3.13)으로 고정돼 있다.
```bash
.venv/Scripts/activate                               # Windows (PowerShell: .venv\Scripts\Activate.ps1)
uvicorn server:app --reload                          # 웹 서버 (포트 8000)
python imdb_rag.py                                   # CLI 모드 (q 입력 시 종료)
LOG_FORMAT=console uvicorn server:app --reload       # 개발용 컬러 로그 (기본은 JSON)
```

venv가 없으면 생성:
```bash
py -3.13 -m venv .venv                               # pyenv 사용 시 .python-version이 3.13을 선택해줌
.venv/Scripts/python -m pip install -r requirements.txt
```

### 프론트엔드 (Next.js)
```bash
cd web && npm run dev              # 개발 서버 (포트 3000)
cd web && npm run build            # 프로덕션 빌드
cd web && npm run lint             # ESLint 검사
```

### 테스트
```bash
pytest tests/unit/ -q                               # 단위 테스트 (API 호출 없음, 0.1초, 비용 0)
python -m tests.evals.run_evals --skip-judge        # 에이전트 평가, judge 채점만 생략
python -m tests.evals.run_evals                     # LLM-as-judge 포함
python -m tests.evals.run_evals --ids imdb-001      # 특정 항목만
```

**코드를 고쳤으면 `pytest tests/unit/`를 먼저 돌릴 것.** 0.1초면 끝나고 비용이 없다.
평가(`tests/evals`)는 실제 LLM·도구를 호출하므로 `--skip-judge`를 줘도 비용이 든다.

### CI (`.github/workflows/`)

| 파일 | 잡 | 내용 | 비용 |
|---|---|---|---|
| `ci.yml` | `python` | 구문 검사 + 단위 테스트 60개 (pytest만 설치) | **0** |
| `ci.yml` | `deps` | **프로덕션 의존성이 배포 환경에서 설치되는지** | **0** |
| `ci.yml` | `frontend` | ESLint + 프로덕션 빌드 | **0** |
| `evals.yml` | — | 에이전트 회귀 평가 14개 (`workflow_dispatch` 수동) | 발생 |

### `deps` 잡이 막는 것
Railway 배포 실패를 **푸시 시점에** 잡는다. 과거에 배포본이 7커밋 뒤처진 채로 돌고 있었는데,
빌드가 실패해도 이전 Active 배포가 유지되어 겉으로는 정상으로 보였다.

- `ubuntu-latest` + `.python-version`(3.13) — **nixpacks가 쓰는 것과 같은 조건**
- `pip install -r requirements.txt` — Railway가 실행하는 것과 같은 명령
- 서드파티 19개를 실제로 import — 설치 성공과 사용 가능은 다르다.
  특히 `AsyncSqliteSaver`는 `langgraph-checkpoint-sqlite`라는 **별도 패키지**라 빠지기 쉽다.
- `requirements.in`과 락의 드리프트 검사 — `.in`에 추가하고 `gen_lock.py`를 잊는 실수를 막는다

### 두 워크플로를 나눈 기준은 비용
`ci.yml`은 **외부 API를 전혀 호출하지 않아** 시크릿 없이 돌고 포크 PR에서도 안전하다.
`evals.yml`만 실제 LLM을 부르므로 수동 실행이다.

`python` 잡이 수십 초에 끝나는 건 단위 테스트 대상(`kobis_format.py`, `sources.py`)이
표준 라이브러리만 쓰도록 분리돼 있어 **pytest만 설치하면 되기 때문**이다.
반면 `deps` 잡은 락 78개를 전부 설치하므로 몇 분 걸린다 — 그래서 잡을 나눴다.
병렬로 돌아 전체 소요 시간은 크게 늘지 않는다.

`agent.py`/`server.py`는 import만 해도 벡터스토어 빌드와 API 키를 요구해 CI에서 실행할 수
없다. `python` 잡은 `compileall`로 구문 오류만 잡고, `deps` 잡은 서드파티를 직접 import해
패키지가 실제로 쓸 수 있는 상태인지 확인한다.

주의사항:
- **`.python-version`을 `setup-python`이 읽는다.** 로컬·Railway·CI가 같은 파일 하나를 본다.
- `evals.yml`은 `vectorstore`를 **PDF 해시로 캐시**한다. 청크 파라미터(`chunk_size` 등)를
  바꾸면 캐시가 낡으므로 캐시 키의 `vectorstore-v1`을 `v2`로 올릴 것.
- 평가를 자동 실행하려면 `evals.yml`의 주석 처리된 `push`/`schedule` 트리거를 풀면 되지만,
  **실행마다 과금된다.**

## 의존성 관리

**Python 버전과 패키지 버전이 로컬과 Railway에서 동일하도록 고정돼 있다.** 이 구조를 깨뜨리지 말 것.

| 파일 | 역할 |
|---|---|
| `.python-version` | `3.13`. pyenv(로컬)와 nixpacks(Railway)가 **같은 파일을 읽는다**. nixpacks 지원 상한이 3.13이므로 그 이상으로 올릴 수 없다. |
| `requirements.in` | 사람이 편집하는 **직접 의존성 13개** (프로덕션). |
| `requirements.txt` | `requirements.in`에서 생성된 **전체 의존성 락 78개**. 자동 생성물이므로 직접 편집 금지. nixpacks가 이 파일로 설치한다. |
| `requirements-dev.txt` | 개발 전용(pytest). **프로덕션 락과 분리**되어 Railway에는 설치되지 않는다. |

### 패키지를 추가·변경할 때
1. `requirements.in`(프로덕션) 또는 `requirements-dev.txt`(개발 도구)를 수정한다.
2. venv에서 설치하고 락을 재생성한다:
   ```bash
   .venv/Scripts/python -m pip install -r requirements.in
   .venv/Scripts/python scripts/gen_lock.py
   ```
3. `pytest tests/unit/ -q`로 먼저 확인하고, 필요하면 `python -m tests.evals.run_evals --skip-judge`까지 돌린다.
4. `requirements.in`과 `requirements.txt`를 **함께** 커밋한다.

> **`pip freeze > requirements.txt`를 쓰지 말 것.** venv에는 개발 도구(pytest 등)도 설치돼
> 있어서 freeze를 그대로 쓰면 프로덕션 락에 개발 의존성이 섞여 Railway까지 실려간다.
> `scripts/gen_lock.py`는 `requirements.in`의 의존성 트리만 추적하므로 이 문제가 없다.

### 왜 이렇게 하는가
이전에는 `requirements.txt`가 직접 의존성만 고정하고 transitive를 열어둬서, 배포할 때마다 다른 버전이 깔렸다. 실측 당시 로컬은 `langchain-core 1.2.26`/`langsmith 0.4.38`이었지만 Railway는 `1.6.2`/`0.12.4`를 설치하고 있었고, Python도 로컬 3.14 대 Railway 3.11(nixpacks 기본값)로 갈려 있었다. 로컬 검증이 배포본을 보증하지 못하는 상태였다.

`pip freeze`를 전역 환경에서 돌리면 무관한 패키지가 섞이므로 **반드시 venv 안에서** 실행할 것.

## 아키텍처

### 요청 흐름
```
브라우저 → Next.js (port 3000)
         → /api/chat (route.ts, 내부 프록시)
         → FastAPI (port 8000, /api/chat)
         → LangGraph 에이전트
         → imdb_search (FAISS) / kobis_search (KOBIS API) / web_search (Tavily)
```

`web/app/api/chat/route.ts`는 순수 프록시로, 스트리밍 응답을 그대로 브라우저에 전달합니다. FastAPI 주소는 `web/.env.local`의 `BACKEND_URL`로 설정합니다.

### 공유 모듈 구조

```
logging_config.py ← 공통: structlog JSON 로거 + ContextVar(session_id/request_id)
kobis_format.py   ← 순수 함수: 영화 판별(pick_movie), 날짜/응답 가공. 외부 의존 없음
sources.py        ← 순수 함수: 도구 출력에서 답변 출처 추출. 외부 의존 없음
agent.py          ← vectorstore 캐시, LLM, tools, build_agent() 팩토리
imdb_rag.py       ← CLI 루프 (sync SqliteSaver)
server.py         ← FastAPI 앱 (lifespan에서 AsyncSqliteSaver 준비, 스트리밍/에러 분류)
scripts/gen_lock.py ← requirements.in → requirements.txt 락 생성
tests/unit/       ← pytest 단위 테스트 (kobis_format, sources)
tests/evals/      ← 골든 데이터셋 + LLM-as-judge 평가 러너
```

**`kobis_format.py`와 `sources.py`를 `agent.py`에서 분리한 이유**: `agent.py`는 import만 해도
벡터스토어를 로드하고 OpenAI 클라이언트를 만든다(약 6초 + API 키 필요). 순수 로직을 떼어내니
단위 테스트가 0.1초에 끝나고 키 없이도 돌아간다. **새로 순수 함수를 만들 때도 이 두 모듈에
넣을지 먼저 검토할 것** — 테스트 가능성이 크게 달라진다.

### 에이전트 생성 — `build_agent(checkpointer)`
모듈 수준 `agent` 객체는 없다. 진입점마다 필요한 체크포인터가 달라서 팩토리로 주입한다.

| 진입점 | 체크포인터 | 이유 |
|---|---|---|
| `server.py` | `AsyncSqliteSaver` | `astream_events`가 async라 **sync 세이버는 `aput`에서 `NotImplementedError`를 던진다** |
| `imdb_rag.py` | `SqliteSaver` | CLI는 sync `invoke`를 쓴다. 서버와 같은 DB 파일을 공유 |
| `tests/evals` | `MemorySaver`(기본값) | 항목마다 새 thread라 영속화가 불필요하고, 실제 대화 DB를 건드리지 않게 격리 |

### 벡터스토어 캐시
`agent.py` 시작 시 `vectorstore/index.faiss` 존재 여부를 확인합니다.
- **존재**: `FAISS.load_local()`로 즉시 로드 (임베딩 API 호출 없음)
- **없음**: PDF 파싱 → 청크 분할(chunk_size=800, chunk_overlap=100) → 임베딩 생성 → `vectorstore.save_local()`로 저장

Retriever는 `search_kwargs={"k": 8}`으로 쿼리당 8개 청크를 반환합니다.
청크 파라미터를 변경할 경우 `vectorstore/` 폴더를 삭제하고 재시작해야 반영됩니다.

### 대화 영속화
각 세션은 `thread_id`(UUID)로 구분되며, **SQLite 체크포인터에 저장되어 프로세스가 재시작돼도 유지됩니다.**

- **DB 경로**: `CHECKPOINT_DB_PATH` 환경변수, 기본값 `vectorstore/checkpoints.sqlite`.
  기본값을 `vectorstore/` 아래로 둔 이유는 Railway에서 이미 `/app/vectorstore`가 영구 볼륨으로
  마운트돼 있어 **추가 설정 없이** 재시작 후에도 대화가 보존되기 때문입니다.
- **웹**: 프론트가 `localStorage`에 `session_id`와 메시지 목록을 저장합니다. 새로고침해도
  같은 대화를 이어가고 화면도 복원됩니다. 백엔드만 영속화하면 "봇은 기억하는데 화면은 빈" 상태가 됩니다.
- **CLI**: 실행 시 새 `thread_id` 생성. 서버와 같은 DB 파일을 공유합니다.
- **새 대화** 버튼은 새 `thread_id`를 발급하고 `localStorage`의 메시지를 지웁니다.

> `MemorySaver`는 LangGraph 문서가 "디버깅/테스트 전용"이라고 명시한 클래스이고 정리 로직이
> 없어 방문자당 세션이 영구 잔류합니다. 그래서 프로덕션 경로에서는 쓰지 않습니다.

### 스트리밍
`server.py`는 `agent.astream_events(version="v2")`로 이벤트를 구독합니다.
- `on_chat_model_stream`: `AIMessageChunk.content`를 그대로 전송
- `on_tool_start` / `on_tool_end`: `\x1ftool:<name>\n` / `\x1ftool:end\n` 형태의 센티넬 라인을 텍스트 스트림에 삽입
- `on_tool_end`에서 출처 추출 시: **`\x1fsources:<JSON 배열>\n`**. `sources.extract_sources()`가
  `[{"tool","label","url"}]`을 만듭니다
- 에러 발생 시: **`\x1ferror:<code>|<메시지>\n`** 센티넬을 전송. `code`는 `TIMEOUT` / `INTERNAL` 등 (`server._error_sentinel()` 참고)

프론트엔드는 청크마다 정규식(`/\x1f((?:tool|error|sources):[^\n]*)\n/g`)으로 센티넬을 추출합니다. `tool:` 센티넬은 도구 상태를 표시하고, `sources:` 센티넬은 답변 하단의 출처 칩으로, `error:` 센티넬은 `code|message`로 split하여 빨간 에러 버블 + 코드별 아이콘/라벨로 렌더링합니다. 나머지 텍스트는 답변에 추가됩니다. `asyncio.timeout(120)`으로 2분 초과 시 자동 종료됩니다.

> **센티넬을 추가할 때는 프론트 정규식의 alternation도 함께 고쳐야 합니다.** 안 고치면 새 센티넬이
> 본문 텍스트로 그대로 화면에 출력됩니다.

### 답변 출처 (`sources.py`)
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

### 응답 캐싱
`server.py`는 `cachetools.TTLCache(maxsize=256, ttl=3600)`을 사용합니다. 캐시 키는 `sha256(question.strip().lower())`. **새 세션(`session_id`가 요청에 없는 경우)에만** 캐시를 적용해 대화 컨텍스트가 있는 요청과 충돌하지 않습니다. 에러 응답은 캐시하지 않습니다.

### 사용자 입력 검증 (`QuestionRequest`)
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

### 헬스체크 (`/health`)
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

### 레이트 리미팅
`slowapi`로 `/api/chat` 엔드포인트에 IP당 분당 10회 제한이 적용됩니다. 초과 시 429 응답을 반환합니다. `@limiter.limit("10/minute")` 데코레이터가 적용되며, FastAPI의 `Request` 객체가 첫 번째 파라미터로 필요합니다.

### 구조화 로깅 (Observability)
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

### KOBIS 도구 모드 (`search_type`)
`kobis_search`는 6개 모드를 지원합니다. 모두 KOBIS 오픈API 실호출로 검증되었습니다.

| 모드 | 엔드포인트 | query 의미 |
|---|---|---|
| `movie` | `movie/searchMovieList.json` | 영화 제목 (여러 후보 나열) |
| `detail` | `movie/searchMovieList` → `movie/searchMovieInfo.json` | 영화 제목 (상세정보 1편) |
| `daily` | `boxoffice/searchDailyBoxOfficeList.json` | YYYYMMDD |
| `weekly` | `boxoffice/searchWeeklyBoxOfficeList.json` `weekGb=0` | YYYYMMDD (주간 월~일) |
| `weekend` | 같은 URL, `weekGb=1` | YYYYMMDD (주말 금~일) |
| `weekday` | 같은 URL, `weekGb=2` | YYYYMMDD (주중 월~목) |

박스오피스 4종은 단일 분기로 통합되어 있고, 헤더 라벨은 하드코딩이 아니라 응답의 `boxOfficeResult.boxofficeType`을 사용합니다(KOBIS가 "주말 박스오피스" 같은 한글 라벨을 직접 제공).

**`detail` 모드의 2단계 조회**: `searchMovieInfo`는 `movieCd`(영화코드)로만 조회되고 영화명으로는 안 됩니다. 그래서 `detail`은 내부에서 목록조회 → movieCd 해석 → 상세조회를 자동 처리하며, LLM은 영화명으로 한 번만 호출하면 됩니다. query가 8자리 숫자면 movieCd로 간주해 1단계를 건너뜁니다.

**`pick_movie()`의 해석 규칙** (`kobis_format.py`) — KOBIS 목록은 관련도순이 아니라서 1위가 정답이 아닌 경우가 많습니다("기생충" 검색 시 1위가 "마약 기생충"). 다음 순서로 좁힙니다:
1. 공백·대소문자 무시 **정확 일치**
2. `prdtStatNm == "개봉"` (개봉 완료작)
3. `repNationNm == "한국"` (한국 제작 — 이게 없으면 "올드보이"가 2003년 박찬욱판이 아니라 2013년 스파이크 리 리메이크로 해석됨)
4. 최신 `openDt`

선택되지 않은 후보는 출력 말미에 `(동명/유사 제목 후보: ...)`로 덧붙여 LLM이 오선택을 인지할 수 있게 합니다.

**출력 상한** — KOBIS 상세정보는 `actors`를 90건, `staffs`를 625건까지 반환합니다. `staffs`는 전량 제외(VFX 아티스트·투자 등 크루 노이즈), `actors`는 `DETAIL_MAX_ACTORS=8`건까지만 `이름(배역)` 형태로 렌더링하고 나머지는 "외 N명"으로 요약합니다. `companys`는 `companyPartNm`으로 제작사·배급사만 필터합니다.

### 웹 검색 도구 (`web_search`)
`langchain_tavily.TavilySearch`를 쓰지 않고 `agent.py`의 `_tavily_search()`가 Tavily REST API를
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

### 도구 입력 검증 및 에러 포맷
`kobis_search`는 Pydantic `KobisInput` 스키마(`agent.py`)로 인자를 검증합니다.
- `search_type`: `Literal["movie", "detail", "daily", "weekly", "weekend", "weekday"]` — 잘못된 값 시 LangChain이 `ValidationError`를 ToolMessage로 변환해 LLM에 반환 → LLM이 자가 정정 후 재호출
- `open_start_dt` / `open_end_dt`: `field_validator`로 4자리 숫자 검증
- 박스오피스 모드의 `query`: `is_valid_date()`(`kobis_format.py`)로 **8자리 숫자이면서 실제 달력에 존재하는 날짜**인지 검증. 두 조건을 모두 봐야 합니다 — KOBIS가 `99999999`에도 에러 대신 엉뚱한 데이터를 반환하고, 반대로 `strptime`만 쓰면 제로 패딩에 관대해서 7자리 `'2026091'`을 2026-09-01로 통과시킵니다(실제로 있었던 버그). `detail`은 제목을 받으므로 이 검증 대상이 아닙니다.

> 날짜 검증을 Pydantic `model_validator`로 옮기지 말 것. `search_type` 의존 규칙이라 교차 필드 검증이 필요한데, 옮기면 에러 표면이 `[TOOL_ERROR code=INVALID_DATE]`에서 Pydantic `ValidationError`로 바뀌어 이 문서와 `SYSTEM_PROMPT`의 서술이 전부 어긋납니다.

도구 내부 에러는 표준 포맷으로 LLM에 반환됩니다: **`[TOOL_ERROR code=<CODE>] <message>`**
- `MISSING_API_KEY` / `INVALID_DATE` / `INVALID_QUERY` / `MOVIE_NOT_FOUND` / `TIMEOUT` / `HTTP_ERROR` / `NETWORK_ERROR` / `PARSE_ERROR`
- 시스템 프롬프트(`agent.py`의 `SYSTEM_PROMPT`)에 이 코드를 보고 어떻게 행동할지 명시되어 있어, LLM이 도구를 우회(예: kobis 실패 → web_search) 하거나 사용자에게 솔직히 알릴 수 있음

> **헬퍼에서 "못 찾음"을 `raise ValueError`로 신호하지 말 것.** `kobis_search`의 `except (KeyError, ValueError, TypeError)`가 잡아서 `PARSE_ERROR`로 오분류합니다. `None`/빈 dict를 반환하고 호출부에서 `tool_error()`를 반환하세요. 같은 이유로 헬퍼는 `_kobis_get`의 예외를 잡지 않고 그대로 전파시켜 기존 4종 except가 처리하게 합니다.

### 신뢰성 (Retry / Fallback)
- `_kobis_get()`은 `tenacity`로 KOBIS API 호출을 최대 3회까지 지수 백오프로 재시도(`RequestException`만 대상)
- `_tavily_search()`는 **최대 2회, 연결 문제(`Timeout`/`ConnectionError`)만** 재시도. Tavily는 호출마다 크레딧을 쓰고 HTTP 오류는 다시 보내도 같기 때문
- 도구별 타임아웃: `KOBIS_TIMEOUT=10s`, `WEB_TIMEOUT=15s`. 전체 요청 타임아웃: `REQUEST_TIMEOUT_S=120` (`server.py`)
- 폴백은 명시적 코드가 아닌 LLM 판단으로 처리됨 — 도구 에러 메시지에 "web_search로 대신 시도해보세요" 같은 힌트를 포함

### 프론트엔드 레이아웃
`page.tsx`는 `h-[100dvh]` + `header / main(flex-1 overflow-y-auto) / footer` 구조입니다. `h-[100dvh]`(dynamic viewport height)를 사용해 모바일 가상 키보드가 열려도 레이아웃이 올바르게 유지됩니다. 입력 폼은 항상 footer에 고정됩니다. 메시지 목록은 `id`(UUID) 기반 key를 사용하며, 스크롤은 새 메시지 추가 시에만 실행됩니다(`messages.length` 의존). 가상 키보드 열림/닫힘 시에도 `visualViewport` resize 이벤트로 마지막 메시지가 보이도록 스크롤합니다.

헤더 우측에 **다크 모드 토글**(해/달 아이콘)과 **새 대화** 버튼이 있습니다. 다크 모드는 `next-themes`로 관리하며 시스템 설정을 기본값으로 사용하고 새로고침 후에도 유지됩니다. `web/app/providers.tsx`에 `ThemeProvider`가 정의되어 있으며 `layout.tsx`에서 감쌉니다. Tailwind v4 class 기반 다크 모드는 `globals.css`의 `@variant dark (&:where(.dark, .dark *));`로 설정합니다.

`messages.length === 0`일 때 main 영역에 예시 질문 버튼 4개가 표시됩니다. 클릭 시 `submitQuestion(text)`을 직접 호출해 바로 전송됩니다.

AI 응답 버블에는:
- **도구 상태 표시**: 도구 실행 중 버블 상단에 "IMDB Top 250 검색 중..." / "한국 개봉 영화 검색 중..." / "웹 검색 중..." 표시 (파란 펄스 점)
- **에러 버블**: `isError: true`인 메시지는 빨간(`bg-red-50 dark:bg-red-900/30`) 버블로 표시. 복사 버튼 미표시. 에러 코드별 아이콘/라벨이 `errorLabel()` 함수로 부여됨 (`⏱ TIMEOUT`, `🚦 RATE_LIMIT`, `🔌 BACKEND_UNREACHABLE`, `⚠ INTERNAL/BACKEND_ERROR`, `🌐 NETWORK`)
- **다시 시도 버튼**: 재시도 가능한 코드(`RETRYABLE_CODES = TIMEOUT/INTERNAL/BACKEND_UNREACHABLE/BACKEND_ERROR/NETWORK`)에만 표시. `RATE_LIMIT`은 표시하지 않음. `handleRetry()`가 `runRequest()`를 같은 질문으로 재호출
- **복사 버튼**: 데스크톱에서는 hover 시, 모바일(터치 기기)에서는 항상 버블 하단에 표시. 클릭 후 1.5초간 "복사됨" 피드백

### 모바일 최적화
- **뷰포트**: `h-[100dvh]`로 가상 키보드 대응 (`h-screen` 폴백 포함)
- **반응형 타이포그래피**: 헤더 제목 `text-2xl sm:text-4xl`, 입력창 `text-base sm:text-sm` (iOS 자동 줌 방지)
- **터치 타겟**: "새 대화" / 다크 모드 토글 버튼 `min-h-[44px] min-w-[44px]` (WCAG 최소 터치 영역)
- **말풍선 너비**: 모바일 `max-w-[85%]`, 데스크톱 `sm:max-w-[80%]`
- **복사 버튼**: `[@media(hover:none)]:opacity-100`으로 터치 기기에서 항상 표시
- **패딩**: footer/input/button 모바일 `py-2`, 데스크톱 `sm:py-3`

### 마크다운 렌더링
에이전트 답변은 `react-markdown`으로 렌더링됩니다. `components` prop으로 Tailwind 클래스를 직접 지정합니다. 지원 요소: `p`, `ul`, `ol`, `li`, `strong`, `h1`–`h3`, `code`(인라인), `blockquote`, `hr`.

### 프론트엔드 프록시 에러 (route.ts)
`web/app/api/chat/route.ts`는 백엔드 호출 결과를 JSON 에러로 변환합니다:
- 빈 질문 → 400 `{code: "EMPTY_QUESTION"}`
- 백엔드 도달 불가 → 503 `{code: "BACKEND_UNREACHABLE"}`
- 백엔드 429 → 429 `{code: "RATE_LIMIT"}`
- 기타 백엔드 비-2xx → `{code: "BACKEND_ERROR"}`
- 정상 → 백엔드 스트림 + `X-Request-Id`/`X-Session-Id` 헤더 패스스루

프론트엔드(`page.tsx`)는 이 `code`로 라벨/재시도 가능 여부를 결정합니다.

### 테스트 두 층

**1. 단위 테스트 — `tests/unit/`** (pytest, API 호출 없음, 0.1초)
`kobis_format.py`와 `sources.py`의 순수 로직을 검증합니다. 각 테스트는 **어떤 실패를 막는지**
docstring에 적혀 있습니다(대부분 실제 KOBIS 응답에서 발견한 문제를 그대로 옮긴 것).
`requirements-dev.txt`의 pytest가 필요합니다.

**2. 에이전트 회귀 평가 — `tests/evals/`** (실제 LLM·도구 호출, 비용 발생)
- `golden_dataset.json` — 항목당 `id`/`question`/`expected_tools`/`expected_keywords`(`_any`)/`rubric`
- `run_evals.py` — 각 질문을 `astream_events`로 실행하며 도구 호출 추적 + 키워드 매칭 + (옵션) LLM-as-judge 채점
- 종료 코드: 전체 통과 시 0, 하나라도 실패 시 1 → CI 회귀 차단용
- 새 항목 추가 시 `expected_keywords_any`(any/all)와 `rubric` 작성을 잊지 말 것
- **judge 호출이 실패하면 해당 항목은 실패 처리됩니다.** 예전에는 `judge_score`가 `None`으로
  남아 자동 통과되면서 평가가 조용히 무력화되는 fail-open 구조였습니다.
- `--skip-judge`는 채점 비용만 없앨 뿐 에이전트 본체는 실제로 호출됩니다. **완전 무료가 아닙니다.**

현재 상태: 단위 테스트 60개 전부 통과, 평가 **14/14 전부 통과**.

## 기술 스택
- **LLM**: OpenAI `gpt-5.4-mini` (temperature=0)
- **Embeddings**: OpenAI `text-embedding-3-small`
- **Vector Store**: FAISS (`langchain_community.vectorstores`)
- **Agent**: `langchain.agents.create_agent` (LangGraph 기반, `MemorySaver` checkpointer)
- **PDF Loader**: PyMuPDF (`langchain_community.document_loaders.PyMuPDFLoader`)
- **Korean Movie DB**: KOBIS Open API (`requests`, 영화 목록/영화 상세정보/일별·주간·주말·주중 박스오피스)
- **Web Search**: Tavily REST API 직접 호출 (`requests`, max_results=5). `langchain-tavily`는 쓰지 않는다 — "웹 검색 도구" 참고
- **Tool Input Validation**: Pydantic `BaseModel` + `field_validator` (`Literal` 타입 강제)
- **Retry**: `tenacity` (지수 백오프, KOBIS 호출 3회 재시도)
- **Logging**: `structlog` (JSON 출력, `ContextVar`로 session_id/request_id 자동 첨부)
- **Cache**: `cachetools.TTLCache` (maxsize=256, TTL 1시간)
- **Rate Limiting**: `slowapi` (IP당 분당 10회, `/api/chat` 엔드포인트)
- **API 서버**: FastAPI + uvicorn, CORS는 `CORS_ORIGINS` 환경변수로 설정 (기본값: `http://localhost:3000`)
- **Frontend**: Next.js 16 (App Router, TypeScript, Tailwind CSS v4)
- **Dark Mode**: `next-themes` (class 전략, 시스템 설정 연동)
- **Markdown**: `react-markdown`
- **대화 영속화**: `langgraph-checkpoint-sqlite` (서버 `AsyncSqliteSaver` / CLI `SqliteSaver`)
- **테스트**: pytest 단위 테스트 + 자체 구현 평가 러너 (golden dataset + LLM-as-judge)

## Next.js 16 주의사항
Next.js 16은 이전 버전과 API, 파일 구조가 다릅니다. `web/` 코드 수정 시 반드시 `web/node_modules/next/dist/docs/`의 가이드를 참고하세요 (`web/AGENTS.md` 참조).

## 프롬프트 규칙
- 에이전트 답변은 **한국어**로 출력
- 모르는 내용은 모른다고 답변
- **답변 범위 제한** — 영화·영상 콘텐츠와 무관한 질문(음식, 날씨, 코딩 등)은 도구를 호출하지 않고 거절한 뒤 영화 질문을 유도

### 답변 범위 규칙을 수정할 때 주의할 것
`SYSTEM_PROMPT`의 `## 답변 범위` 섹션은 **세 부분이 한 세트**다. 하나만 고치면 경계가 무너진다.

1. **범위를 넓게 정의** — "영화 정보"만 적으면 배경지식 질문("인터스텔라 블랙홀이 과학적으로 맞아?")까지 거절된다. 작품 배경지식·영화인·관람 정보를 범위 안에 명시해 둔 이유다.
2. **범위 밖일 때의 행동 지정** — 도구를 호출하지 말 것, 한두 문장으로 짧게 거절할 것. 이게 없으면 거절하면서도 `web_search`를 호출한다.
3. **모호함은 거절이 아니라 되묻기** — "그 영화 어땠어?"는 영화 관련이지만 대상이 불분명한 경우다. 이 예외를 빼면 `ambiguous-001`이 거절로 처리되어 깨진다.

평가가 **양쪽 방향**을 모두 고정한다. `refusal-001`은 거절해야 할 질문을, `refusal-002`는
거절하면 안 되는 질문(영화 배경지식)을 검증한다. 규칙을 손보면 **둘 다** 돌려볼 것.

## 환경 설정
루트 `.env` (`.env.example` 참고):
```
OPENAI_API_KEY=your_openai_api_key
TAVILY_API_KEY=your_tavily_api_key
KOBIS_API_KEY=your_kobis_api_key
LANGCHAIN_TRACING_V2=true
LANGCHAIN_API_KEY=your_langsmith_api_key
LANGCHAIN_PROJECT=imdb-rag-chatbot

# 프로덕션 배포 시: Vercel 도메인으로 변경
CORS_ORIGINS=http://localhost:3000

# 로깅 (옵션)
LOG_LEVEL=INFO          # DEBUG/INFO/WARNING/ERROR
LOG_FORMAT=json         # json(기본, 프로덕션) / console(개발)
```

`web/.env.local` (`web/.env.example` 참고):
```
BACKEND_URL=http://localhost:8000
```

## 배포 (Vercel + Railway)

### Railway (백엔드)
- `railway.toml`에 시작 명령 및 헬스체크 설정 포함
- 환경변수: `.env.example` 참고, `CORS_ORIGINS`는 Vercel 도메인으로 설정
- 볼륨 마운트: `/app/vectorstore` (vectorstore 영구 저장)
- 백엔드 URL: `https://imdbtop250-production.up.railway.app`

#### Railway 배포 주의사항
- **`requests`를 requirements.txt에 직접 명시하지 말 것**: `langchain-community`가 `requests>=2.32.5`를 요구하므로 버전을 고정하면 의존성 충돌이 발생한다. `requests`는 transitive dependency로 자동 설치된다.
- **빌드 실패 시 Active 배포는 유지됨**: 새 빌드가 실패해도 이전 버전이 계속 Active 상태로 서비스된다. Deployments 탭에서 각 배포의 커밋 해시를 확인해 실제로 어떤 버전이 빌드되었는지 확인할 것.
- **캐시로 인해 최신 커밋이 빌드되지 않을 경우**: Deployments 탭에서 실패한 배포를 Redeploy하면 그 커밋 기준으로 재시도한다. 최신 커밋으로 빌드하려면 GitHub push로 새 배포를 트리거해야 한다.

### Vercel (프론트엔드)
- Root Directory: `web`
- 환경변수: `BACKEND_URL` = Railway 백엔드 URL
- `web/.env.local`은 로컬 전용, Vercel에는 대시보드에서 직접 설정
