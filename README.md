# IMDB Top 250 AI 영화 챗봇

IMDB Top 250 영화 PDF 검색(RAG)과 Tavily 웹 검색을 결합한 LangGraph 에이전트 기반 영화 전문가 챗봇입니다.

## 기술 스택

- **LLM**: OpenAI `gpt-5.4-mini` (temperature=0)
- **Embeddings**: OpenAI `text-embedding-3-small`
- **Vector Store**: FAISS
- **Framework**: LangChain + LangGraph
- **PDF Loader**: PyMuPDF
- **Web Search**: Tavily

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
pip install langchain langchain-community langchain-openai langchain-tavily faiss-cpu pymupdf python-dotenv
```

## 환경 설정

`.env` 파일을 생성하고 API 키를 설정합니다:

```
OPENAI_API_KEY=your_openai_api_key
TAVILY_API_KEY=your_tavily_api_key
```

- Tavily API 키 발급: [app.tavily.com](https://app.tavily.com) (무료 플랜: 월 1,000회)

## 실행 방법

```bash
python imdb_rag.py
```

- 질문을 입력한 뒤 Enter
- `q` 입력 시 종료

## 특징

- 답변은 **한국어**로 출력
- PDF에 없는 최신 정보도 웹 검색으로 보완
- 모르는 내용은 모른다고 답변
