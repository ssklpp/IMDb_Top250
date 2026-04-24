# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 프로젝트 개요
IMDB Top 250 영화 PDF를 기반으로 한 AI 영화 전문가 챗봇. RAG(PDF 검색), KOBIS 한국 영화 데이터베이스 API, Tavily 웹 검색을 결합한 LangGraph 에이전트 구조. FastAPI 백엔드 + Next.js 프론트엔드로 웹 서비스 제공.

## 실행 명령어

### 백엔드 (FastAPI)
```bash
uvicorn server:app --reload        # 웹 서버 (포트 8000)
python imdb_rag.py                 # CLI 모드 (q 입력 시 종료)
```

### 프론트엔드 (Next.js)
```bash
cd web && npm run dev              # 개발 서버 (포트 3000)
cd web && npm run build            # 프로덕션 빌드
cd web && npm run lint             # ESLint 검사
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
agent.py      ← 공통: vectorstore 캐시, LLM, tools, agent 초기화
imdb_rag.py   ← CLI 루프만 (thread_id로 대화 히스토리 유지)
server.py     ← FastAPI 앱만 (StreamingResponse로 토큰 단위 전송)
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
- 에러 발생 시: `\x1ferror:<메시지>\n` 센티넬을 전송 (타임아웃/예외 모두 동일 형식)

프론트엔드는 청크마다 정규식(`/\x1f((?:tool|error):[^\n]*)\n/g`)으로 센티넬을 추출합니다. `tool:` 센티넬은 도구 상태를 표시하고, `error:` 센티넬은 빨간 에러 버블로 렌더링합니다. 나머지 텍스트는 답변에 추가됩니다. `asyncio.timeout(120)`으로 2분 초과 시 자동 종료됩니다.

### 응답 캐싱
`server.py`는 `cachetools.TTLCache(maxsize=256, ttl=3600)`을 사용합니다. 캐시 키는 `sha256(question.strip().lower())`. **새 세션(`session_id`가 요청에 없는 경우)에만** 캐시를 적용해 대화 컨텍스트가 있는 요청과 충돌하지 않습니다. 에러 응답은 캐시하지 않습니다.

### 레이트 리미팅
`slowapi`로 `/api/chat` 엔드포인트에 IP당 분당 10회 제한이 적용됩니다. 초과 시 429 응답을 반환합니다. `@limiter.limit("10/minute")` 데코레이터가 적용되며, FastAPI의 `Request` 객체가 첫 번째 파라미터로 필요합니다.

### 프론트엔드 레이아웃
`page.tsx`는 `h-[100dvh]` + `header / main(flex-1 overflow-y-auto) / footer` 구조입니다. `h-[100dvh]`(dynamic viewport height)를 사용해 모바일 가상 키보드가 열려도 레이아웃이 올바르게 유지됩니다. 입력 폼은 항상 footer에 고정됩니다. 메시지 목록은 `id`(UUID) 기반 key를 사용하며, 스크롤은 새 메시지 추가 시에만 실행됩니다(`messages.length` 의존). 가상 키보드 열림/닫힘 시에도 `visualViewport` resize 이벤트로 마지막 메시지가 보이도록 스크롤합니다.

헤더 우측에 **다크 모드 토글**(해/달 아이콘)과 **새 대화** 버튼이 있습니다. 다크 모드는 `next-themes`로 관리하며 시스템 설정을 기본값으로 사용하고 새로고침 후에도 유지됩니다. `web/app/providers.tsx`에 `ThemeProvider`가 정의되어 있으며 `layout.tsx`에서 감쌉니다. Tailwind v4 class 기반 다크 모드는 `globals.css`의 `@variant dark (&:where(.dark, .dark *));`로 설정합니다.

`messages.length === 0`일 때 main 영역에 예시 질문 버튼 4개가 표시됩니다. 클릭 시 `submitQuestion(text)`을 직접 호출해 바로 전송됩니다.

AI 응답 버블에는:
- **도구 상태 표시**: 도구 실행 중 버블 상단에 "IMDB Top 250 검색 중..." / "한국 개봉 영화 검색 중..." / "웹 검색 중..." 표시 (파란 펄스 점)
- **에러 버블**: `isError: true`인 메시지는 빨간(`bg-red-50 dark:bg-red-900/30`) 버블로 표시. 복사 버튼 미표시.
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

## 기술 스택
- **LLM**: OpenAI `gpt-5.4-mini` (temperature=0)
- **Embeddings**: OpenAI `text-embedding-3-small`
- **Vector Store**: FAISS (`langchain_community.vectorstores`)
- **Agent**: `langchain.agents.create_agent` (LangGraph 기반, `MemorySaver` checkpointer)
- **PDF Loader**: PyMuPDF (`langchain_community.document_loaders.PyMuPDFLoader`)
- **Korean Movie DB**: KOBIS Open API (`requests`, 영화 목록/일별/주간 박스오피스)
- **Web Search**: Tavily (`langchain_tavily.TavilySearch`, max_results=5)
- **Cache**: `cachetools.TTLCache` (maxsize=256, TTL 1시간)
- **Rate Limiting**: `slowapi` (IP당 분당 10회, `/api/chat` 엔드포인트)
- **API 서버**: FastAPI + uvicorn, CORS는 `CORS_ORIGINS` 환경변수로 설정 (기본값: `http://localhost:3000`)
- **Frontend**: Next.js 16 (App Router, TypeScript, Tailwind CSS v4)
- **Dark Mode**: `next-themes` (class 전략, 시스템 설정 연동)
- **Markdown**: `react-markdown`

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
