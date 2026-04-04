# IMDB RAG Project

## 프로젝트 개요
IMDB Top 250 영화 PDF를 기반으로 한 AI 영화 전문가 챗봇. RAG(PDF 검색)와 Tavily 웹 검색을 결합한 LangGraph 에이전트 구조. FastAPI 백엔드 + Next.js 프론트엔드로 웹 서비스 제공.

## 파일 구조
- `imdb_rag.py` — CLI용 에이전트 파이프라인 및 대화 루프
- `server.py` — FastAPI 백엔드 서버 (웹 연동용)
- `imdb_top250.pdf` — IMDB Top 250 영화 데이터 소스
- `vectorstore/` — FAISS 인덱스 캐시 (index.faiss, index.pkl)
- `.env` — API 키 설정 파일
- `web/` — Next.js 16 프론트엔드

## 기술 스택

### 백엔드
- **LLM**: OpenAI `gpt-5.4-mini` (temperature=0)
- **Embeddings**: OpenAI `text-embedding-3-small`
- **Vector Store**: FAISS
- **Framework**: LangChain + LangGraph
- **PDF Loader**: PyMuPDF
- **Web Search**: Tavily (`langchain-tavily`)
- **Agent**: `langchain.agents.create_agent` (LangGraph 기반)
- **API 서버**: FastAPI + uvicorn

### 프론트엔드
- **Framework**: Next.js 16 (App Router, TypeScript)
- **Styling**: Tailwind CSS

## 에이전트 파이프라인
1. PDF 문서 로드 (`PyMuPDFLoader`)
2. 문서 분할 (`RecursiveCharacterTextSplitter`, chunk_size=1500, chunk_overlap=50)
3. 임베딩 생성 (`OpenAIEmbeddings`)
4. FAISS 벡터스토어 생성
5. Retriever 생성
6. LLM 생성
7. 도구 생성: `imdb_search` (PDF 검색) + `web_search` (Tavily 웹 검색)
8. 에이전트 생성 (`create_agent`) → 질문에 따라 도구 자동 선택

## 도구 사용 전략 (에이전트 동작)
- **imdb_search**: IMDB Top 250 PDF에서 영화 정보 검색 (제목, 감독, 출연진, 평점 등)
- **web_search**: PDF에 없는 정보 검색 — 신작 영화, 박스오피스, 최신 수상 내역 등
- 에이전트가 질문을 분석해 어떤 도구를 쓸지 자동 판단 (OpenAI function calling)

## 프롬프트 규칙
- 답변은 **한국어**로 출력
- 모르는 내용은 모른다고 답변

## 실행 방법

### CLI 모드
```bash
python imdb_rag.py
```
- 질문 입력 후 Enter, `q` 입력 시 종료

### 웹 모드 (터미널 2개)
```bash
# 터미널 1: FastAPI 서버
uvicorn server:app --reload

# 터미널 2: Next.js
cd web && npm run dev
```
- 브라우저에서 http://localhost:3000 접속

## 환경 설정
`.env` 파일에 아래 API 키 설정 필요:
```
OPENAI_API_KEY=your_openai_api_key
TAVILY_API_KEY=your_tavily_api_key
LANGCHAIN_TRACING_V2=true
LANGCHAIN_API_KEY=your_langsmith_api_key
LANGCHAIN_PROJECT=imdb-rag-chatbot
```
- Tavily 무료 플랜: [app.tavily.com](https://app.tavily.com) (월 1,000회)
- LangSmith 트레이싱: [smith.langchain.com](https://smith.langchain.com)
