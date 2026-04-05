# IMDB Top 250 AI 영화 챗봇

IMDB Top 250 영화 PDF 검색(RAG)과 Tavily 웹 검색을 결합한 LangGraph 에이전트 기반 영화 전문가 챗봇입니다.  
FastAPI 백엔드와 Next.js 16 프론트엔드로 웹 서비스를 제공합니다.

## 기술 스택

### 백엔드
- **LLM**: OpenAI `gpt-5.4-mini` (temperature=0)
- **Embeddings**: OpenAI `text-embedding-3-small`
- **Vector Store**: FAISS (로컬 캐시, 최초 1회 생성 후 재사용)
- **Framework**: LangChain + LangGraph
- **PDF Loader**: PyMuPDF
- **Web Search**: Tavily
- **API 서버**: FastAPI + uvicorn (스트리밍 응답)

### 프론트엔드
- **Framework**: Next.js 16 (App Router, TypeScript)
- **Styling**: Tailwind CSS

## 에이전트 파이프라인

1. FAISS 캐시(`vectorstore/`) 존재 시 로드, 없으면 PDF 파싱 후 생성 및 저장
   - 청크 분할: chunk_size=1500, chunk_overlap=150
2. Retriever(k=6), LLM, 도구 초기화
3. LangGraph 에이전트 생성 (`MemorySaver`로 대화 히스토리 유지)
4. 질문마다 `imdb_search` / `web_search` 도구 자동 선택

## 도구 동작 방식

| 도구 | 용도 |
|------|------|
| `imdb_search` | IMDB Top 250 PDF에서 영화 제목, 감독, 출연진, 평점 등 검색 |
| `web_search` | PDF에 없는 신작, 박스오피스, 최신 수상 내역 등 웹 검색 |

에이전트가 질문을 분석해 어떤 도구를 사용할지 자동으로 판단합니다.

## 설치 방법

```bash
# 백엔드
pip install langchain langchain-community langchain-openai langchain-tavily faiss-cpu pymupdf python-dotenv fastapi uvicorn

# 프론트엔드
cd web && npm install
```

## 환경 설정

루트의 `.env` 파일을 생성하고 API 키를 설정합니다:

```
OPENAI_API_KEY=your_openai_api_key
TAVILY_API_KEY=your_tavily_api_key
LANGCHAIN_TRACING_V2=true
LANGCHAIN_API_KEY=your_langsmith_api_key
LANGCHAIN_PROJECT=imdb-rag-chatbot
```

- Tavily API 키 발급: [app.tavily.com](https://app.tavily.com) (무료 플랜: 월 1,000회)
- LangSmith API 키 발급: [smith.langchain.com](https://smith.langchain.com)

`web/.env.local`은 기본값(`http://localhost:8000`)으로 설정되어 있으며, 배포 환경에서는 `BACKEND_URL`을 변경하세요.

## 실행 방법

### CLI 모드
```bash
python imdb_rag.py
```
- 질문을 입력한 뒤 Enter
- 같은 세션에서 이전 대화 맥락이 유지됩니다
- `q` 입력 시 종료

### 웹 모드 (터미널 2개)
```bash
# 터미널 1: FastAPI 서버 (포트 8000)
uvicorn server:app --reload

# 터미널 2: Next.js (포트 3000)
cd web && npm run dev
```
브라우저에서 `http://localhost:3000` 접속

## 특징

- 답변은 **한국어**로 출력, 모르는 내용은 모른다고 답변
- 벡터스토어 캐시로 재시작 시 임베딩 API 호출 없음
- 대화 히스토리 유지 — "그 영화의 감독은?" 같은 후속 질문 가능
- 스트리밍 응답 — 토큰 단위로 실시간 표시, 2분 타임아웃 자동 처리
- 마크다운 렌더링 — 제목, 목록, 굵은 글씨, 인라인 코드, 인용구 등 지원
- PDF에 없는 최신 정보도 웹 검색으로 보완
- LangSmith로 에이전트 실행 추적 가능

> **벡터스토어 파라미터 변경 시**: `vectorstore/` 폴더를 삭제하고 서버를 재시작하면 새 파라미터로 재생성됩니다.
