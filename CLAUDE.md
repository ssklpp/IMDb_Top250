# IMDB RAG Project

## 프로젝트 개요
IMDB Top 250 영화 PDF를 기반으로 한 RAG(Retrieval-Augmented Generation) 챗봇.

## 파일 구조
- `imdb_rag.py` — 메인 RAG 파이프라인 및 대화 루프
- `imdb_top250_20251025_154422.pdf` — IMDB Top 250 영화 데이터 소스

## 기술 스택
- **LLM**: OpenAI `gpt-5.4-mini` (temperature=0)
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

## 프롬프트 규칙
- 질문은 영어로 이해
- 답변은 **한국어**로 출력
- 모르는 내용은 모른다고 답변

## 실행 방법
```bash
python imdb_rag.py
```
- 질문 입력 후 Enter
- `q` 입력 시 종료

## 환경 설정
`.env` 파일에 OpenAI API 키 설정 필요:
```
OPENAI_API_KEY=your_api_key_here
```
