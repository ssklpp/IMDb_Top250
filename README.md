# IMDB Top 250 AI 영화 챗봇

IMDB Top 250 영화 PDF 검색(RAG)과 Tavily 웹 검색을 결합한 LangGraph 에이전트 기반 영화 전문가 챗봇입니다.  
FastAPI 백엔드와 Next.js 16 프론트엔드로 웹 서비스를 제공합니다.

## 기술 스택

### 백엔드
- **LLM**: OpenAI `gpt-5.4-mini` (temperature=0)
- **Embeddings**: OpenAI `text-embedding-3-small`
- **Vector Store**: FAISS
- **Framework**: LangChain + LangGraph
- **PDF Loader**: PyMuPDF
- **Web Search**: Tavily
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
8. LangGraph 에이전트 생성 → 질문에 따라 도구 자동 선택

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

`.env` 파일을 생성하고 API 키를 설정합니다:

```
OPENAI_API_KEY=your_openai_api_key
TAVILY_API_KEY=your_tavily_api_key
LANGCHAIN_TRACING_V2=true
LANGCHAIN_API_KEY=your_langsmith_api_key
LANGCHAIN_PROJECT=imdb-rag-chatbot
```

- Tavily API 키 발급: [app.tavily.com](https://app.tavily.com) (무료 플랜: 월 1,000회)
- LangSmith API 키 발급: [smith.langchain.com](https://smith.langchain.com)

## 실행 방법

### CLI 모드
```bash
python imdb_rag.py
```
- 질문을 입력한 뒤 Enter
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

- 답변은 **한국어**로 출력
- PDF에 없는 최신 정보도 웹 검색으로 보완
- LangSmith로 에이전트 실행 추적 가능
- 모르는 내용은 모른다고 답변
