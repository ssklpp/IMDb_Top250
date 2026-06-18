# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 프로젝트 개요
IMDB Top 250 영화 PDF를 기반으로 한 AI 영화 전문가 챗봇. RAG(PDF 검색), KOBIS 한국 영화 데이터베이스 API, Tavily 웹 검색을 결합한 LangGraph 에이전트 구조. FastAPI 백엔드 + Next.js 프론트엔드로 웹 서비스 제공.

## 실행 명령어

### 백엔드 (FastAPI)
```bash
uvicorn server:app --reload                          # 웹 서버 (포트 8000)
python imdb_rag.py                                   # CLI 모드 (q 입력 시 종료)
LOG_FORMAT=console uvicorn server:app --reload       # 개발용 컬러 로그 (기본은 JSON)
```

### 프론트엔드 (Next.js)
```bash
cd web && npm run dev              # 개발 서버 (포트 3000)
cd web && npm run build            # 프로덕션 빌드
cd web && npm run lint             # ESLint 검사
```

### 평가 (Evals)
```bash
python -m tests.evals.run_evals --skip-judge        # 도구/키워드 검증만 (빠름, OpenAI 비용 0)
python -m tests.evals.run_evals                     # LLM-as-judge 포함 (OpenAI 비용 발생)
python -m tests.evals.run_evals --ids imdb-001      # 특정 항목만
```

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
에이전트 초기화 코드는 `agent.py`에만 존재하며, `server.py`와 `imdb_rag.py` 모두 여기서 `agent`를 import합니다.

```
logging_config.py ← 공통: structlog JSON 로거 + ContextVar(session_id/request_id)
agent.py          ← 공통: vectorstore 캐시, LLM, tools(Pydantic 스키마 검증, tenacity 재시도), agent 초기화
imdb_rag.py       ← CLI 루프만 (thread_id로 대화 히스토리 유지)
server.py         ← FastAPI 앱만 (StreamingResponse, request_id 발급, 에러 코드 분류)
tests/evals/      ← 골든 데이터셋 + LLM-as-judge 평가 러너
```

### 벡터스토어 캐시
`agent.py` 시작 시 `vectorstore/index.faiss` 존재 여부를 확인합니다.
- **존재**: `FAISS.load_local()`로 즉시 로드 (임베딩 API 호출 없음)
- **없음**: PDF 파싱 → 청크 분할(chunk_size=800, chunk_overlap=100) → 임베딩 생성 → `vectorstore.save_local()`로 저장

Retriever는 `search_kwargs={"k": 8}`으로 쿼리당 8개 청크를 반환합니다.
청크 파라미터를 변경할 경우 `vectorstore/` 폴더를 삭제하고 재시작해야 반영됩니다.

### 대화 히스토리
에이전트에 `MemorySaver` checkpointer가 설정되어 있습니다. 각 세션은 `thread_id`(UUID)로 구분됩니다.
- **CLI**: `imdb_rag.py` 실행 시 새 `thread_id` 생성, 프로세스 종료까지 유지
- **웹**: 브라우저 탭 로드 시 `crypto.randomUUID()`로 생성, 모든 요청에 `session_id`로 전달

### 스트리밍
`server.py`는 `agent.astream_events(version="v2")`로 이벤트를 구독합니다.
- `on_chat_model_stream`: `AIMessageChunk.content`를 그대로 전송
- `on_tool_start` / `on_tool_end`: `\x1ftool:<name>\n` / `\x1ftool:end\n` 형태의 센티넬 라인을 텍스트 스트림에 삽입
- 에러 발생 시: **`\x1ferror:<code>|<메시지>\n`** 센티넬을 전송. `code`는 `TIMEOUT` / `INTERNAL` 등 (`server._error_sentinel()` 참고)

프론트엔드는 청크마다 정규식(`/\x1f((?:tool|error):[^\n]*)\n/g`)으로 센티넬을 추출합니다. `tool:` 센티넬은 도구 상태를 표시하고, `error:` 센티넬은 `code|message`로 split하여 빨간 에러 버블 + 코드별 아이콘/라벨로 렌더링합니다. 나머지 텍스트는 답변에 추가됩니다. `asyncio.timeout(120)`으로 2분 초과 시 자동 종료됩니다.

응답 헤더에 `X-Request-Id`, `X-Session-Id`, `X-Cache`(HIT/MISS)가 포함됩니다.

### 응답 캐싱
`server.py`는 `cachetools.TTLCache(maxsize=256, ttl=3600)`을 사용합니다. 캐시 키는 `sha256(question.strip().lower())`. **새 세션(`session_id`가 요청에 없는 경우)에만** 캐시를 적용해 대화 컨텍스트가 있는 요청과 충돌하지 않습니다. 에러 응답은 캐시하지 않습니다.

### 레이트 리미팅
`slowapi`로 `/api/chat` 엔드포인트에 IP당 분당 10회 제한이 적용됩니다. 초과 시 429 응답을 반환합니다. `@limiter.limit("10/minute")` 데코레이터가 적용되며, FastAPI의 `Request` 객체가 첫 번째 파라미터로 필요합니다.

### 구조화 로깅 (Observability)
`logging_config.py`가 `structlog` 기반 JSON 로거를 제공합니다. `configure_logging()`은 `server.py` / `imdb_rag.py` 진입점에서 한 번 호출됩니다.
- **ContextVar**: `session_id_var`, `request_id_var`가 모든 로그에 자동 첨부됨. `server.py`의 `/api/chat` 핸들러가 요청마다 두 값을 바인딩
- **환경변수**:
  - `LOG_LEVEL` (기본 `INFO`)
  - `LOG_FORMAT=json` (기본, 프로덕션용) / `LOG_FORMAT=console` (개발 시 컬러 출력)
- **로그 이벤트 예시**: `vectorstore.cache_hit`, `chat.request`, `chat.tool_start`, `chat.tool_end`, `chat.done`(elapsed_s, tool_calls), `chat.cache_hit`, `chat.timeout`, `kobis.request`, `kobis.success`, `kobis.timeout`
- 모든 로그는 `stderr`로 출력되어 uvicorn 표준 로그와 섞이지 않음

`print()` 사용은 금지. 새 코드는 `from logging_config import get_logger; log = get_logger("module_name")`을 사용해야 합니다.

### 도구 입력 검증 및 에러 포맷
`kobis_search`는 Pydantic `KobisInput` 스키마(`agent.py`)로 인자를 검증합니다.
- `search_type`: `Literal["movie", "daily", "weekly"]` — 잘못된 값 시 LangChain이 `ValidationError`를 ToolMessage로 변환해 LLM에 반환 → LLM이 자가 정정 후 재호출
- `open_start_dt` / `open_end_dt`: `field_validator`로 4자리 숫자 검증
- 박스오피스 모드의 `query`: 8자리 숫자(YYYYMMDD)인지 추가 검증

도구 내부 에러는 표준 포맷으로 LLM에 반환됩니다: **`[TOOL_ERROR code=<CODE>] <message>`**
- `MISSING_API_KEY` / `INVALID_DATE` / `TIMEOUT` / `HTTP_ERROR` / `NETWORK_ERROR` / `PARSE_ERROR`
- 시스템 프롬프트(`agent.py`의 `SYSTEM_PROMPT`)에 이 코드를 보고 어떻게 행동할지 명시되어 있어, LLM이 도구를 우회(예: kobis 실패 → web_search) 하거나 사용자에게 솔직히 알릴 수 있음

### 신뢰성 (Retry / Fallback)
- `_kobis_get()`은 `tenacity`로 KOBIS API 호출을 최대 3회까지 지수 백오프로 재시도(`RequestException`만 대상)
- 도구별 타임아웃: `KOBIS_TIMEOUT=10s`. 전체 요청 타임아웃: `REQUEST_TIMEOUT_S=120` (`server.py`)
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

### 평가 (Evals)
`tests/evals/`에 골든 데이터셋 기반 회귀 평가가 있습니다.
- `golden_dataset.json` — 항목당 `id`/`question`/`expected_tools`/`expected_keywords`(`_any`)/`rubric`
- `run_evals.py` — 각 질문을 `agent.astream_events`로 실행하며 도구 호출 추적 + 키워드 매칭 + (옵션) LLM-as-judge 채점
- 실행:
  - `python -m tests.evals.run_evals` (judge 포함, OpenAI 비용 발생)
  - `python -m tests.evals.run_evals --skip-judge` (도구/키워드만, 비용 0)
  - `python -m tests.evals.run_evals --ids imdb-001 kobis-002` (특정 항목만)
- 종료 코드: 전체 통과 시 0, 하나라도 실패 시 1 → CI 회귀 차단용
- 새 항목 추가 시 `expected_keywords_any`(any/all)와 `rubric` 작성을 잊지 말 것

## 기술 스택
- **LLM**: OpenAI `gpt-5.4-mini` (temperature=0)
- **Embeddings**: OpenAI `text-embedding-3-small`
- **Vector Store**: FAISS (`langchain_community.vectorstores`)
- **Agent**: `langchain.agents.create_agent` (LangGraph 기반, `MemorySaver` checkpointer)
- **PDF Loader**: PyMuPDF (`langchain_community.document_loaders.PyMuPDFLoader`)
- **Korean Movie DB**: KOBIS Open API (`requests`, 영화 목록/일별/주간 박스오피스)
- **Web Search**: Tavily (`langchain_tavily.TavilySearch`, max_results=5)
- **Tool Input Validation**: Pydantic `BaseModel` + `field_validator` (`Literal` 타입 강제)
- **Retry**: `tenacity` (지수 백오프, KOBIS 호출 3회 재시도)
- **Logging**: `structlog` (JSON 출력, `ContextVar`로 session_id/request_id 자동 첨부)
- **Cache**: `cachetools.TTLCache` (maxsize=256, TTL 1시간)
- **Rate Limiting**: `slowapi` (IP당 분당 10회, `/api/chat` 엔드포인트)
- **API 서버**: FastAPI + uvicorn, CORS는 `CORS_ORIGINS` 환경변수로 설정 (기본값: `http://localhost:3000`)
- **Frontend**: Next.js 16 (App Router, TypeScript, Tailwind CSS v4)
- **Dark Mode**: `next-themes` (class 전략, 시스템 설정 연동)
- **Markdown**: `react-markdown`
- **Evals**: 자체 구현 — `tests/evals/run_evals.py` (golden dataset + LLM-as-judge)

## Next.js 16 주의사항
Next.js 16은 이전 버전과 API, 파일 구조가 다릅니다. `web/` 코드 수정 시 반드시 `web/node_modules/next/dist/docs/`의 가이드를 참고하세요 (`web/AGENTS.md` 참조).

## 프롬프트 규칙
- 에이전트 답변은 **한국어**로 출력
- 모르는 내용은 모른다고 답변

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
