# IMDB Top 250 AI 영화 챗봇

https://im-db-top250.vercel.app/

IMDB Top 250 영화 PDF 검색(RAG), KOBIS 한국 영화 데이터베이스, Tavily 웹 검색을 결합한 LangGraph 에이전트 기반 영화 전문가 챗봇입니다.  
FastAPI 백엔드와 Next.js 16 프론트엔드로 웹 서비스를 제공합니다.

**품질 지표** — 단위 테스트 51개 통과 (0.1초) · 에이전트 회귀 평가 14/14 통과 · 의존성 79개 전체 고정

---

## 해결한 문제들

기능을 붙이는 것보다, 실제로 부딪힌 문제를 어떻게 진단하고 고쳤는지가 이 프로젝트의 핵심입니다.

### 1. "올드보이"가 엉뚱한 영화로 검색되던 문제

KOBIS 영화 목록 API는 검색 결과를 **관련도순으로 주지 않습니다.** `기생충`을 검색하면 1위가 `마약 기생충`, `부산행`은 1위가 `부산행:익스텐디드`였습니다. 단순히 첫 결과를 쓰면 틀린 영화를 답하게 됩니다.

정확 일치를 우선하도록 고쳤더니 이번엔 `올드보이`에서 새 문제가 나왔습니다. 2003년 박찬욱 작품과 2013년 스파이크 리 리메이크가 **둘 다 정확히 일치**해서, 최신 개봉일 기준으로는 리메이크가 선택됐습니다.

최종적으로 **정확 일치 → 개봉 완료작 → 한국 제작 → 최신 개봉일** 순의 판별 규칙을 세웠습니다. KOBIS가 한국 영화 데이터베이스라는 도메인 특성을 근거로 삼은 결정입니다. 선택되지 않은 후보는 답변 말미에 함께 표시해 사용자가 다른 작품을 의도했다면 되물을 수 있게 했습니다.
→ [`kobis_format.py`](kobis_format.py)의 `pick_movie()`, 판별 규칙은 [단위 테스트](tests/unit/test_kobis_format.py)로 고정

### 2. API 키가 로그에 평문으로 남던 문제

네트워크 예외를 `log.warning(..., error=str(e))`로 기록하고 있었는데, KOBIS는 API 키를 **쿼리 파라미터**로 받고 `requests` 예외 메시지에는 요청 URL 전문이 들어갑니다. 즉 KOBIS 쪽 장애가 한 번만 나도 키가 로그에 그대로 찍히는 구조였습니다.

```
HTTPSConnectionPool(...): Max retries exceeded with
url: /.../searchMovieList.json?key=<실제_API_키>&movieNm=...
```

재현 테스트로 확인한 뒤 `error=type(e).__name__`으로 바꿔 예외 타입만 남기도록 수정했습니다.

### 3. 로컬과 배포 환경이 다른 소프트웨어를 돌리던 문제

`requirements.txt`가 직접 의존성 13개만 고정하고 전이 의존성은 열어둔 상태였습니다. 실측해보니:

| | 로컬 | Railway |
|---|---|---|
| Python | 3.14 | **3.11** (버전 핀이 없어 nixpacks 기본값) |
| langchain-core | 1.2.26 | **1.6.2** |
| langsmith | 0.4.38 | **0.12.4** |

로컬 검증이 배포본을 전혀 보증하지 못하는 상태였습니다. `.python-version`으로 인터프리터를 고정하고, `requirements.in`(직접 의존성) → `requirements.txt`(전체 79개 락) 구조로 바꿔 양쪽이 같은 것을 설치하게 했습니다.

### 4. 단위 테스트가 즉시 찾아낸 날짜 검증 버그

`is_valid_date('2026091')`이 `True`를 반환하고 있었습니다. Python의 `strptime`은 제로 패딩에 관대해서 7자리 문자열을 `2026-09-01`로 파싱합니다. 8자리 날짜만 받아야 하는 함수가 7자리를 통과시키고 있었던 것입니다. 테스트를 붙이자마자 드러난 버그입니다.

---

## 기술 스택

### 백엔드
- **Python**: 3.13 (`.python-version`으로 로컬·배포 환경 통일)
- **LLM**: OpenAI `gpt-5.4-mini` (temperature=0)
- **Embeddings**: OpenAI `text-embedding-3-small`
- **Vector Store**: FAISS (로컬 캐시, 최초 1회 생성 후 재사용)
- **Framework**: LangChain + LangGraph
- **PDF Loader**: PyMuPDF
- **Korean Movie DB**: KOBIS (영화관입장권통합전산망) Open API
- **Web Search**: Tavily
- **API 서버**: FastAPI + uvicorn (스트리밍 응답)
- **Rate Limiting**: slowapi (IP당 분당 10회)
- **Tool Validation**: Pydantic 스키마 + Literal 타입 (도구 인자 검증)
- **Retry**: tenacity (외부 API 호출 지수 백오프 재시도)
- **Logging**: structlog (JSON 구조화 로그 + session_id/request_id 추적)
- **대화 영속화**: LangGraph `AsyncSqliteSaver` (서버 재시작 후에도 대화 유지)
- **테스트**: pytest 단위 테스트 51개 + 골든 데이터셋 회귀 평가 14개 (LLM-as-judge)

### 프론트엔드
- **Framework**: Next.js 16 (App Router, TypeScript)
- **Styling**: Tailwind CSS v4
- **Dark Mode**: next-themes (시스템 설정 연동, 수동 토글)

## 에이전트 파이프라인

1. FAISS 캐시(`vectorstore/`) 존재 시 로드, 없으면 PDF 파싱 후 생성 및 저장
   - 청크 분할: chunk_size=800, chunk_overlap=100
2. Retriever(k=8), LLM, 도구 초기화
3. `build_agent(checkpointer)`로 에이전트 생성 — 진입점마다 다른 체크포인터를 주입합니다
   (웹 서버는 `AsyncSqliteSaver`, CLI는 `SqliteSaver`, 평가는 `MemorySaver`)
4. 질문마다 `imdb_search` / `kobis_search` / `web_search` 도구 자동 선택
5. 도구 실행 결과에서 출처를 추출해 답변과 함께 스트리밍

## 도구 동작 방식

| 도구 | 용도 |
|------|------|
| `imdb_search` | IMDB Top 250 PDF에서 영화 제목, 감독, 출연진, 평점 등 검색 |
| `kobis_search` | KOBIS API로 한국 개봉작, 영화 상세정보, 일별/주간/주말/주중 박스오피스 검색 |
| `web_search` | 위 두 도구로 충분하지 않을 때 인터넷 검색 (해외 신작, 수상 내역 등) |

에이전트가 질문을 분석해 어떤 도구를 사용할지 자동으로 판단합니다.

`kobis_search`는 여섯 가지 모드를 지원합니다:
- `movie`: 영화 제목으로 국내 개봉 정보 검색 (여러 후보 나열)
- `detail`: 영화 한 편의 상세정보 — 상영시간, 관람등급, 배역별 출연진, 장르, 제작사/배급사
- `daily`: 특정 날짜(YYYYMMDD)의 일별 박스오피스 Top 10
- `weekly`: 주간(월~일) 박스오피스 Top 10
- `weekend`: 주말(금~일) 박스오피스 Top 10
- `weekday`: 주중(월~목) 박스오피스 Top 10

> `detail` 모드는 KOBIS 영화코드가 필요한 API를 감싸고 있지만, 영화 제목만 주면 내부에서 코드 조회까지 자동으로 처리합니다. 동명 영화가 여러 편일 때는 정확 일치 → 개봉작 → 한국 제작 → 최신 개봉일 순으로 고르고, 나머지 후보를 답변 말미에 함께 안내합니다.

## 설치 방법

백엔드는 **Python 3.13**을 사용합니다 (`.python-version`으로 고정, Railway도 동일 버전을 씁니다).

```bash
# 백엔드 — 가상환경 생성 후 설치
py -3.13 -m venv .venv
.venv/Scripts/activate                  # Windows. macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
pip install -r requirements-dev.txt     # 테스트를 돌리려면 (pytest)

# 프론트엔드
cd web && npm install
```

> `requirements.txt`는 전체 의존성 79개를 고정한 **자동 생성 락 파일**입니다. 직접 편집하지 말고 `requirements.in`을 수정한 뒤 `python scripts/gen_lock.py`로 재생성하세요 (자세한 내용은 `CLAUDE.md`의 "의존성 관리" 참고).

## 환경 설정

루트의 `.env` 파일을 생성하고 API 키를 설정합니다:

```
OPENAI_API_KEY=your_openai_api_key
TAVILY_API_KEY=your_tavily_api_key
KOBIS_API_KEY=your_kobis_api_key
LANGCHAIN_TRACING_V2=true
LANGCHAIN_API_KEY=your_langsmith_api_key
LANGCHAIN_PROJECT=imdb-rag-chatbot

# 로깅 (옵션)
LOG_LEVEL=INFO          # DEBUG/INFO/WARNING/ERROR
LOG_FORMAT=json         # json(프로덕션) / console(개발용 컬러 출력)
```

- Tavily API 키 발급: [app.tavily.com](https://app.tavily.com) (무료 플랜: 월 1,000회)
- KOBIS API 키 발급: [kobis.or.kr](https://www.kobis.or.kr) → 마이페이지 → 오픈API → 키 발급/관리 (무료, 1,000 요청/일)
- LangSmith API 키 발급: [smith.langchain.com](https://smith.langchain.com)

`web/.env.local`은 기본값(`http://localhost:8000`)으로 설정되어 있으며, 배포 환경에서는 `BACKEND_URL`을 변경하세요.

환경변수 템플릿: `.env.example`, `web/.env.example` 참고

## 배포

Vercel(프론트엔드) + Railway(백엔드) 조합으로 배포합니다.

### Railway (백엔드)
1. GitHub 레포 연결 → 환경변수 설정 (`.env.example` 참고)
2. `CORS_ORIGINS`에 Vercel 도메인 설정
3. Volumes에서 `/app/vectorstore` 마운트 (영구 저장)

> **Python 버전**: `.python-version`(3.13)을 nixpacks가 자동으로 인식합니다. 이 파일이 없으면 nixpacks 기본값인 3.11로 빌드되어 로컬과 달라지므로 삭제하지 마세요.

> **주의**: `requirements.in`에 `requests`를 직접 명시하지 마세요. `langchain-community`의 transitive dependency로 자동 설치되며, 별도로 고정하면 의존성 충돌이 발생합니다. (락 파일인 `requirements.txt`에는 자동으로 포함됩니다)

### Vercel (프론트엔드)
1. GitHub 레포 연결 → Root Directory: `web`
2. Environment Variables: `BACKEND_URL` = Railway 백엔드 URL
3. 환경변수 변경 시 Redeploy 필요

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

### 테스트

두 층으로 나뉩니다. **단위 테스트를 먼저 돌리고**, 에이전트 동작까지 봐야 할 때 평가를 돌립니다.

```bash
# 단위 테스트 — 순수 함수만 검증. API 호출 없음, 0.1초, 비용 0
pytest tests/unit/ -q
```
`kobis_format.py`(영화 판별·응답 가공)와 `sources.py`(출처 추출)의 로직을 검증합니다.
두 모듈 모두 외부 의존이 없어 빠르고 CI에 그대로 붙일 수 있습니다.

```bash
# 에이전트 회귀 평가 — 실제 LLM/도구를 호출하므로 비용과 시간이 든다
python -m tests.evals.run_evals --skip-judge   # judge 채점만 생략
python -m tests.evals.run_evals                # LLM-as-judge 포함
```
골든 데이터셋 14개에 대해 **도구 호출이 기대대로인지 + 키워드가 답변에 있는지**를 확인하고,
judge 모드에서는 rubric 기준 1~5점 채점까지 합니다. 종료 코드 0이면 전체 통과라 CI에 바로 쓸 수 있습니다.

> judge 호출이 실패하면 해당 항목을 **통과가 아니라 실패로** 처리합니다. 예전에는 점수가 비면
> 자동 통과되어 평가가 조용히 무력화되는 구조였습니다.

## 특징

- 답변은 **한국어**로 출력, 모르는 내용은 모른다고 답변
- **답변 범위 제한** — 영화·영상 콘텐츠 밖의 질문(음식, 날씨, 코딩 등)은 거절하고 영화 질문을 유도합니다. 다만 "인터스텔라 블랙홀 묘사가 과학적으로 맞아?" 같은 작품 배경지식은 범위 안으로 두어 과잉 거절을 막았고, 양쪽 방향을 평가 항목으로 고정했습니다
- 벡터스토어 캐시로 재시작 시 임베딩 API 호출 없음
- **답변 출처 표시** — 어느 근거로 답했는지 함께 보여줍니다. IMDB 자료는 PDF 쪽번호, 웹 검색은 클릭 가능한 원문 링크, KOBIS는 기관 출처로 표기해 사용자가 사실 여부를 직접 확인할 수 있습니다
- **대화 영속화** — 새로고침해도, 서버가 재시작돼도 대화가 이어집니다. 프론트는 `localStorage`에 세션과 메시지를 보존하고, 백엔드는 SQLite 체크포인터에 대화 상태를 저장합니다
- 대화 히스토리 유지 — "그 영화의 감독은?" 같은 후속 질문 가능
- 스트리밍 응답 — 토큰 단위로 실시간 표시, 2분 타임아웃 자동 처리
- **에러 분류 + 다시 시도 버튼** — TIMEOUT/RATE_LIMIT/BACKEND_UNREACHABLE/INTERNAL/NETWORK 등 코드별 아이콘과 메시지, 재시도 가능한 에러에는 버튼 표시
- **영화 상세정보** — 제목만 주면 KOBIS 영화코드를 자동 해석해 상영시간·관람등급·배역별 출연진·제작사/배급사 조회 (동명 영화는 한국 제작·정확 일치 우선으로 판별)
- **도구 입력 검증** — Pydantic 스키마로 `kobis_search` 인자 검증, 잘못된 값 시 LLM이 자가 정정 후 재호출. 박스오피스 날짜는 실제 달력에 존재하는지까지 확인
- **외부 API 자동 재시도** — KOBIS 호출 실패 시 tenacity로 최대 3회 지수 백오프 재시도, 영구 실패 시 `[TOOL_ERROR code=...]` 표준 포맷으로 LLM에 반환 → web_search 등으로 폴백 유도
- **도구 상태 표시** — 에이전트가 IMDB 검색 / 한국 개봉 영화 검색 / 웹 검색 중일 때 UI에 실시간 표시
- **구조화 로깅** — structlog 기반 JSON 로그, 모든 이벤트에 `session_id`/`request_id` 자동 첨부 (`LOG_FORMAT=console`로 개발용 컬러 출력 가능)
- **응답 헤더 추적** — `X-Request-Id`, `X-Session-Id`, `X-Cache`(HIT/MISS) 헤더로 요청 추적 가능
- **테스트 두 층** — 순수 함수 단위 테스트 51개(0.1초, 비용 0)와 에이전트 회귀 평가 14개(LLM-as-judge)를 분리 운영. 테스트를 붙이기 위해 순수 로직을 `kobis_format.py`/`sources.py`로 분리해 `agent.py`의 무거운 초기화 없이 검증합니다
- **응답 캐싱** — 동일 질문 반복 시 TTLCache(1시간)로 API 비용 절감
- **레이트 리미팅** — IP당 분당 10회 제한, 초과 시 429 응답
- **예시 질문** — 빈 화면에 클릭 가능한 예시 질문 4개 표시
- **다크 모드** — 헤더 토글 버튼, 시스템 설정 자동 연동, 새로고침 후 유지
- **새 대화 버튼** — 헤더에서 클릭 한 번으로 세션 초기화
- **복사 버튼** — 데스크톱 hover / 모바일 항상 표시, 클립보드 복사
- **모바일 최적화** — 가상 키보드 대응(`dvh`), iOS 자동 줌 방지, 터치 타겟 44px 확보
- 마크다운 렌더링 — 제목, 목록, 굵은 글씨, 인라인 코드, 인용구 등 지원
- PDF에 없는 최신 정보도 웹 검색으로 보완
- LangSmith로 에이전트 실행 추적 가능

> **벡터스토어 파라미터 변경 시**: `vectorstore/` 폴더를 삭제하고 서버를 재시작하면 새 파라미터로 재생성됩니다.
