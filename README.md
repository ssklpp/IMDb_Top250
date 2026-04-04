# IMDB Top 250 RAG Chatbot

IMDB Top 250 영화 데이터를 기반으로 한 RAG(Retrieval-Augmented Generation) 챗봇입니다.

## 기술 스택

- **LLM**: OpenAI `gpt-5.4-mini`
- **Embeddings**: OpenAI `text-embedding-3-small`
- **Vector Store**: FAISS
- **Framework**: LangChain
- **PDF Loader**: PyMuPDF

## RAG 파이프라인

1. PDF 문서 로드 (`PyMuPDFLoader`)
2. 문서 분할 (`RecursiveCharacterTextSplitter`, chunk_size=1500, chunk_overlap=50)
3. 임베딩 생성 (`OpenAIEmbeddings`)
4. FAISS 벡터스토어 생성
5. Retriever 생성
6. 프롬프트 구성 → LLM 호출 → 답변 출력

## 설치 방법

```bash
pip install langchain langchain-community langchain-openai faiss-cpu pymupdf python-dotenv
```

## 환경 설정

`.env` 파일을 생성하고 OpenAI API 키를 설정합니다:

```
OPENAI_API_KEY=your_api_key_here
```

## 실행 방법

```bash
python imdb_rag.py
```

- 질문을 입력한 뒤 Enter
- `q` 입력 시 종료

## 특징

- 질문은 영어로 이해
- 답변은 **한국어**로 출력
- 모르는 내용은 모른다고 답변
