from dotenv import load_dotenv

load_dotenv()

import os

from langchain.agents import create_agent
from langchain_core.tools import StructuredTool
from langchain_core.tools.retriever import create_retriever_tool
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import MemorySaver

from logging_config import get_logger
from movie_tools import imdb, kobis, web

log = get_logger("agent")

# 대화 체크포인트 DB. 기본값을 vectorstore/ 아래로 둔 이유는 Railway에서 이미
# /app/vectorstore가 영구 볼륨으로 마운트돼 있어 추가 설정 없이 재시작 후에도
# 대화가 보존되기 때문이다. 다른 위치를 쓰려면 CHECKPOINT_DB_PATH로 덮어쓴다.
CHECKPOINT_DB_PATH = os.environ.get("CHECKPOINT_DB_PATH", "vectorstore/checkpoints.sqlite")

llm = ChatOpenAI(model_name="gpt-5.4-mini", temperature=0)


def local_tools() -> list:
    """같은 프로세스에서 실행하는 도구 4개. 벡터스토어를 이때 불러온다(없으면 임베딩).

    MCP를 쓰면(server.py의 MCP_URL) 이 함수를 부르지 않는다 — 그러면 에이전트 쪽에는
    벡터스토어도 KOBIS·Tavily 키도 필요 없다. 구현과 설명은 movie_tools가 갖고 있어서
    MCP 서버(movie_mcp/server.py)와 모델이 받는 도구 정의가 같다.
    """
    imdb_tool = create_retriever_tool(
        imdb.get_retriever(),
        name=imdb.SEARCH_NAME,
        description=imdb.SEARCH_DESCRIPTION,
        # content만 쓰면 검색된 Document의 메타데이터(순위·제목)가 버려진다.
        # 출처 표시에 필요하므로 artifact로 원본 Document를 함께 받는다.
        response_format="content_and_artifact",
    )
    imdb_sql = StructuredTool.from_function(
        imdb.run_sql, name=imdb.SQL_NAME, description=imdb.SQL_DESCRIPTION
    )
    web_search = StructuredTool.from_function(
        web.web_search, name=web.NAME, description=web.DESCRIPTION
    )
    kobis_search = StructuredTool.from_function(
        kobis.kobis_search,
        name=kobis.NAME,
        description=kobis.DESCRIPTION,
        args_schema=kobis.KobisInput,
    )
    return [imdb_tool, imdb_sql, web_search, kobis_search]


SYSTEM_PROMPT = """당신은 영화 전문가 AI 어시스턴트입니다.

## 답변 범위
영화·영상 콘텐츠와 무관한 질문에는 답하지 마세요.

- **범위 안**: 영화 정보, 감독·배우·제작진, 박스오피스, 평점, 추천, 시상식, 영화사,
  특정 작품의 배경지식(과학 고증, 원작, 제작 비화 등), 관람 정보
- **범위 밖**: 음식·맛집, 날씨, 코딩, 건강, 금융, 시사 등 영화와 연결되지 않는 주제

범위 밖 질문에는 **도구를 호출하지 말고**, 한두 문장으로 영화 전문 어시스턴트임을 밝힌 뒤
영화 관련 질문을 유도하세요.
예: "저는 영화 전문 어시스턴트라 점심 메뉴는 도와드리기 어렵습니다. 대신 오늘 볼 만한 영화를 추천해드릴까요?"

**모호한 질문은 거절이 아니라 되묻기입니다.** "그 영화 어땠어?"처럼 영화 관련이지만
대상이 불분명하면 거절하지 말고 어떤 작품인지 물어보세요.

## 도구 선택 기준
- **영화 사실은 매번 도구로 확인하세요.** 감독·출연진·평점·순위·개봉일·박스오피스 같은 사실을 답할 때는 이전 대화에 같은 내용이 있어도 이번 질문에서 도구를 다시 호출하세요. 답변의 출처는 도구를 호출한 차례에만 표시되므로, 이전 대화를 기억해 답하면 근거 없는 답처럼 보입니다. 단, 범위 밖 질문·되묻기·직전 답변을 요약하거나 다듬어 달라는 요청에는 도구가 필요 없습니다.
- **imdb_search**: IMDB Top 250의 명작/고전 영화 정보 (평점, 감독, 출연진, 줄거리). 한국 영화여도 Top 250에 포함된 작품은 여기서 먼저 찾으세요.
- **imdb_sql**: IMDB Top 250을 **정렬·필터·집계**할 때 — "평점이 가장 높은", "몇 편", "~년대", "감독별". imdb_search는 질문과 비슷한 영화 8편만 가져오므로 이런 질문에 쓰면 그 8편 안에서 고르게 되어 틀립니다.
- **kobis_search**: 한국 개봉 영화, 한국 박스오피스, 국내 상영작 검색. search_type 사용법:
  - `movie`: 영화 목록 검색(여러 후보를 훑을 때). query에 영화 제목.
  - `detail`: 특정 영화 한 편의 상세정보 — 출연진과 배역, 관람등급, 상영시간, 장르, 제작/배급사. query에 **영화 제목**을 그대로 넣으면 됩니다(영화코드를 따로 찾을 필요 없음).
  - `daily`: 일별 박스오피스. query에 YYYYMMDD 8자리 날짜.
  - `weekly` / `weekend` / `weekday`: 각각 주간(월~일) / 주말(금~일) / 주중(월~목) 박스오피스. query에는 조회할 주에 속한 YYYYMMDD 날짜를 넣습니다. 사용자가 "주말 순위"를 물으면 `weekly`가 아니라 `weekend`를 쓰세요.
  - **상대적인 기간은 대화 첫머리에 주어진 오늘 날짜를 기준으로 계산하세요.** "어제"=오늘-1일, "지난 주"/"지난 주말"=오늘-7일이 속한 주입니다.
  - **KOBIS는 진행 중인 기간을 제공하지 않습니다.** 일별은 어제까지, 주간·주말·주중은 지난 주까지만 조회됩니다. 오늘 날짜로 박스오피스를 조회하지 마세요.
  - **박스오피스를 답할 때는 도구 결과에 나온 집계 기간(예: 2026-09-04~2026-09-06)을 답변 첫 문장에 밝히세요.** 사용자가 어느 날·어느 주의 순위인지 확인할 수 있어야 합니다.
  - 출연진·배역·관람등급·러닝타임 질문에는 `movie`가 아니라 `detail`을 사용하세요.
- **web_search**: 위 도구들로 부족할 때 — 최신 수상 내역, 해외 신작, 최신 뉴스 등.

## 도구 에러 처리
- 도구 응답이 `[TOOL_ERROR code=...]`로 시작하면 도구 실패를 의미합니다.
- `INVALID_DATE`/`INVALID_QUERY`, 또는 인자 검증 오류(허용되지 않는 search_type, 4자리가 아닌 연도 등): 인자를 고쳐서 같은 도구를 다시 호출하세요.
- `MISSING_API_KEY`/`TIMEOUT`/`NETWORK_ERROR`/`HTTP_ERROR`/`PARSE_ERROR`: 다른 도구(web_search 등)로 우회하세요.
- `NO_DATA`: 아직 집계되지 않은 기간입니다. **연도를 바꾸지 말고** 하루(일별) 또는 일주일(주간·주말·주중) 이전 날짜로 다시 호출하세요. 그래도 없으면 사용자에게 알리세요.
- `MOVIE_NOT_FOUND`: 제목 철자를 고쳐 다시 호출하거나 search_type='movie'로 후보를 먼저 확인하세요. 그래도 없으면 web_search로 우회하세요.
- 모든 도구가 실패하면 사용자에게 솔직하게 알리세요.

## 출력 규칙
- 한국어로 답변하세요.
- 마크다운 형식 사용: 영화 제목은 **굵게**, 목록은 `-`로.
- 모르는 내용은 추측하지 말고 "확인되지 않습니다"라고 답하세요.
- 답변은 3~6 문장 또는 짧은 목록 형태로 간결하게 유지하세요.
- 도구 호출 결과를 그대로 복사하지 말고, 사용자 질문에 맞게 요약/재구성하세요."""


def build_agent(checkpointer=None, tools=None):
    """체크포인터와 도구를 주입받아 에이전트를 만든다.

    호출부마다 필요한 체크포인터가 다르기 때문에 팩토리로 분리했다:
    - `server.py`: AsyncSqliteSaver — astream_events가 async라 sync 세이버는
      aput에서 NotImplementedError를 던진다. 파일에 저장해 재시작 후에도 대화가 유지된다.
    - `imdb_rag.py`: SqliteSaver — CLI는 sync invoke를 쓴다. 같은 DB 파일을 공유한다.
    - `tests/evals`: MemorySaver — 항목마다 새 thread_id를 쓰므로 영속화가 불필요하고,
      평가가 실제 대화 DB를 건드리지 않게 격리한다.

    checkpointer를 생략하면 MemorySaver를 쓴다(프로세스 종료 시 소멸).
    tools를 생략하면 local_tools()를 쓴다. MCP 서버의 도구를 쓰려면 MultiServerMCPClient의
    get_tools() 결과를 넘긴다(server.py, run_evals.py --mcp-url).
    """
    if checkpointer is None:
        checkpointer = MemorySaver()
    if tools is None:
        tools = local_tools()
    return create_agent(
        model=llm,
        tools=tools,
        system_prompt=SYSTEM_PROMPT,
        checkpointer=checkpointer,
    )
