# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 프로젝트 개요
IMDB Top 250 영화 PDF를 기반으로 한 AI 영화 전문가 챗봇. RAG(PDF 검색), KOBIS 한국 영화 데이터베이스 API, Tavily 웹 검색을 결합한 LangGraph 에이전트 구조. FastAPI 백엔드 + Next.js 프론트엔드로 웹 서비스 제공.

## 규칙 파일 색인

이 문서는 **항상 읽히는 요약**이다. 상세 규칙은 아래 파일에 나눠 두었고, 해당 영역을 건드릴 때
**작업 전에 그 파일을 먼저 읽을 것**. (`@`로 import하지 않는다 — 그러면 매 세션 전부 읽혀
분리한 의미가 없다.)

| 무엇을 고칠 때 | 읽을 파일 |
|---|---|
| KOBIS 6개 모드, 웹 검색 도구를 바꾼 이유, 도구 인자 검증 규칙 | `rules/tools.md` |
| 답변 출처 추출, 입력 검증, 헬스체크, 응답 캐시, 레이트리밋, 로깅 | `rules/server.md` |
| CI 잡을 그렇게 나눈 이유, 패키지 추가 절차, 배포 주의사항 | `rules/ci-deps.md` |
| 프론트엔드(`web/`) 레이아웃·모바일·프록시 에러 | `web/AGENTS.md` |
| 평가 러너와 골든셋 항목 추가 | `tests/evals/README.md` |
| 기술 스택 목록, 환경변수 전체, 사람이 읽는 프로젝트 소개 | `README.md` |

기술 스택과 `.env` 항목은 **README.md가 단일 출처**다. 여기에 복사해 두지 말 것 — 두 곳이
어긋나면 어느 쪽이 맞는지 알 수 없다.

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

## 도구 에러 계약

도구 내부 에러는 표준 포맷으로 LLM에 반환됩니다: **`[TOOL_ERROR code=<CODE>] <message>`**
- `MISSING_API_KEY` / `INVALID_DATE` / `INVALID_QUERY` / `MOVIE_NOT_FOUND` / `TIMEOUT` / `HTTP_ERROR` / `NETWORK_ERROR` / `PARSE_ERROR`
- 시스템 프롬프트(`agent.py`의 `SYSTEM_PROMPT`)에 이 코드를 보고 어떻게 행동할지 명시되어 있어, LLM이 도구를 우회(예: kobis 실패 → web_search) 하거나 사용자에게 솔직히 알릴 수 있음

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

## CI와 의존성 한눈에

잡을 그렇게 나눈 이유와 패키지 추가 절차는 `rules/ci-deps.md`.

| 파일 | 잡 | 내용 | 비용 |
|---|---|---|---|
| `ci.yml` | `python` | 구문 검사 + 단위 테스트 60개 (pytest만 설치) | **0** |
| `ci.yml` | `deps` | **프로덕션 의존성이 배포 환경에서 설치되는지** | **0** |
| `ci.yml` | `frontend` | ESLint + 프로덕션 빌드 | **0** |
| `evals.yml` | — | 에이전트 회귀 평가 14개 (`workflow_dispatch` 수동) | 발생 |

의존성은 **로컬과 Railway가 같은 것을 설치하도록** 네 파일로 고정돼 있다. 이 구조를 깨뜨리지 말 것.

| 파일 | 역할 |
|---|---|
| `.python-version` | `3.13`. pyenv(로컬)와 nixpacks(Railway)가 **같은 파일을 읽는다**. nixpacks 지원 상한이 3.13이므로 그 이상으로 올릴 수 없다. |
| `requirements.in` | 사람이 편집하는 **직접 의존성 13개** (프로덕션). |
| `requirements.txt` | `requirements.in`에서 생성된 **전체 의존성 락 78개**. 자동 생성물이므로 직접 편집 금지. nixpacks가 이 파일로 설치한다. |
| `requirements-dev.txt` | 개발 전용(pytest). **프로덕션 락과 분리**되어 Railway에는 설치되지 않는다. |

## 코드 규칙

- **순수 로직은 `kobis_format.py` / `sources.py`에 둔다.** `agent.py`는 import만 해도 벡터스토어를
  로드하고 OpenAI 클라이언트를 만든다(약 6초 + API 키 필요). 로직을 떼어놨기 때문에 단위 테스트가
  키 없이 0.1초에 끝나고, CI의 `python` 잡이 pytest만 설치하면 된다. **새 순수 함수도 이 두 모듈에
  넣을지 먼저 검토할 것.**
- **`print()` 금지.** `from logging_config import get_logger; log = get_logger("module_name")`.
- **예외를 로깅할 때 `str(e)`를 넣지 말 것.** KOBIS는 API 키를 쿼리 파라미터로 받는데 `requests`
  예외의 `str()`에는 요청 URL 전문이 들어간다. 실제로 키가 평문으로 기록되고 있었다.
  `error=type(e).__name__`으로 타입만 남긴다.
- **헬퍼에서 "못 찾음"을 `raise ValueError`로 신호하지 말 것.** `kobis_search`의
  `except (KeyError, ValueError, TypeError)`가 잡아서 `PARSE_ERROR`로 오분류한다. `None`이나 빈
  리스트를 반환하고 호출부에서 `tool_error()`를 반환한다.
- **단위 테스트는 docstring에 "어떤 실패를 막는지" 적는다.** `tests/unit/`의 테스트는 대부분 실제
  KOBIS·Tavily 응답에서 발견한 문제를 그대로 옮긴 것이다. 이유가 적혀 있지 않으면 나중에 누군가
  "불필요해 보인다"며 지운다.
- 사용자 질문 본문은 로그에 남기지 않는다(길이만). 검색어도 `query_len`만 남긴다.
- 에이전트 답변은 **한국어**, 모르는 내용은 모른다고 답한다. 영화와 무관한 질문은 거절한다
  (상세: 위 "프롬프트 규칙").

## 같이 고쳐야 하는 쌍

한쪽만 고치면 **에러 없이 조용히** 깨진다. 이름을 바꿀 때는 `git grep`으로 먼저 확인할 것.

| 이걸 바꾸면 | 이것도 함께 | 안 고치면 | 상세 |
|---|---|---|---|
| `server.py`의 센티넬(`…`) 추가 | `page.tsx`의 정규식 alternation | 신호 문자가 답변에 그대로 출력 | `rules/server.md` |
| `MAX_QUESTION_CHARS`(2000) | `server.py`와 `page.tsx` 양쪽 | 입력창과 서버 제한이 어긋남 | `rules/server.md` |
| `sources.format_web_results()` | `sources._web_sources()` | 출처 칩만 사라짐 | `rules/server.md` |
| 새 `tool_error` 코드 | `SYSTEM_PROMPT`의 `## 도구 에러 처리` | LLM이 대처 방법을 모름 | `rules/tools.md` |
| `imdb_tool`의 `response_format` | 되돌리지 말 것(`content_and_artifact`) | PDF 쪽번호 출처가 사라짐 | `rules/server.md` |
| `requirements.in` | `scripts/gen_lock.py` 재생성 + `ci.yml`의 import 목록 | CI 실패 또는 배포 불일치 | `rules/ci-deps.md` |
| 청크 파라미터(`chunk_size` 등) | `vectorstore/` 인덱스 삭제 + `evals.yml` 캐시 키 `v1`→`v2` | 예전 인덱스를 계속 사용 | 이 문서 |
| `SYSTEM_PROMPT`의 답변 범위 | 평가 `refusal-001`과 `refusal-002`를 **둘 다** 실행 | 과잉 거절을 놓침 | 이 문서 |

## 작업 순서

싼 검사부터 돌리고, 앞이 실패하면 뒤로 가지 않는다.

1. `pytest tests/unit/ -q` — 0.1초, 비용 0. **코드를 고쳤으면 항상 먼저.**
2. `compileall` — `agent.py`/`server.py`는 import에 API 키가 필요해 CI도 구문만 검사한다.
3. `web/`를 고쳤으면 `npm run lint` + `npm run build`.
4. 패키지를 건드렸으면 `scripts/gen_lock.py`로 락 재생성 (상세: `rules/ci-deps.md`).
5. 도구·프롬프트·모델을 고쳤으면 에이전트 평가. **과금된다** — 관련 항목(`--ids`)부터 돌리고
   프롬프트·모델 변경은 전체 14개를 돌린다.

현재 상태: 단위 테스트 60개 전부 통과, 평가 **14/14 전부 통과**.

문서를 고칠 때는 **숫자(테스트 개수·의존성 개수)가 여러 파일에 흩어져 있으니** 바뀌기 전 숫자로
`git grep`해서 전부 맞출 것.

## 배포 요약

- **프론트엔드**: Vercel (Root Directory `web`). 푸시하면 자동 배포.
- **백엔드**: Railway (`railway.toml`, nixpacks). `/health`가 200이어야 새 버전으로 교체되고,
  `/app/vectorstore` 볼륨에 인덱스와 대화 DB가 남는다.
- **CI와 배포는 연결돼 있지 않다.** GitHub Actions가 실패해도 배포는 진행된다.
- 배포가 안 되거나 옛 버전이 도는 경우의 확인 순서는 `rules/ci-deps.md`에 있다.
