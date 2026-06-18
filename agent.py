from dotenv import load_dotenv

load_dotenv()

import os
import re
from typing import Literal

import requests
from langchain.agents import create_agent
from langchain_community.document_loaders import PyMuPDFLoader
from langchain_community.vectorstores import FAISS
from langchain_core.tools import tool
from langchain_core.tools.retriever import create_retriever_tool
from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from langchain_tavily import TavilySearch
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langgraph.checkpoint.memory import MemorySaver
from pydantic import BaseModel, Field, field_validator
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from logging_config import get_logger

log = get_logger("agent")

VECTORSTORE_PATH = "vectorstore"
KOBIS_TIMEOUT = 10
KOBIS_MAX_ATTEMPTS = 3

embeddings = OpenAIEmbeddings(model="text-embedding-3-small")

if os.path.exists(f"{VECTORSTORE_PATH}/index.faiss"):
    log.info("vectorstore.cache_hit", path=VECTORSTORE_PATH)
    vectorstore = FAISS.load_local(
        VECTORSTORE_PATH, embeddings, allow_dangerous_deserialization=True
    )
else:
    log.info("vectorstore.build_start", pdf="imdb_top250.pdf")
    loader = PyMuPDFLoader("imdb_top250.pdf")
    docs = loader.load()
    text_splitter = RecursiveCharacterTextSplitter(chunk_size=800, chunk_overlap=100)
    split_documents = text_splitter.split_documents(docs)
    vectorstore = FAISS.from_documents(documents=split_documents, embedding=embeddings)
    vectorstore.save_local(VECTORSTORE_PATH)
    log.info("vectorstore.build_done", chunks=len(split_documents))

retriever = vectorstore.as_retriever(search_kwargs={"k": 8})
llm = ChatOpenAI(model_name="gpt-5.4-mini", temperature=0)

imdb_tool = create_retriever_tool(
    retriever,
    name="imdb_search",
    description="IMDB Top 250 PDF에서 영화 정보를 검색합니다. 영화 제목, 감독, 출연진, 평점 등을 찾을 때 사용하세요.",
)

web_tool = TavilySearch(
    max_results=5,
    name="web_search",
    description="인터넷에서 최신 영화 정보를 검색합니다. PDF에 없는 신작, 박스오피스, 최신 수상 내역 등을 찾을 때 사용하세요.",
)


class KobisInput(BaseModel):
    """KOBIS 검색 인자. LLM이 잘못된 포맷을 보낼 경우 검증 에러로 자동 재호출 유도."""

    query: str = Field(
        ...,
        min_length=1,
        description="영화 제목. 박스오피스 조회 시에는 YYYYMMDD 형식의 날짜 (예: '20260617').",
    )
    open_start_dt: str = Field(
        "",
        description="개봉 시작 연도 4자리 (예: '2024'). 영화 검색에만 사용.",
    )
    open_end_dt: str = Field(
        "",
        description="개봉 종료 연도 4자리 (예: '2024'). 영화 검색에만 사용.",
    )
    search_type: Literal["movie", "daily", "weekly"] = Field(
        "movie",
        description="'movie'=영화 목록 검색, 'daily'=일별 박스오피스(query=YYYYMMDD), 'weekly'=주간 박스오피스(query=YYYYMMDD).",
    )

    @field_validator("open_start_dt", "open_end_dt")
    @classmethod
    def _validate_year(cls, v: str) -> str:
        if v and not re.fullmatch(r"\d{4}", v):
            raise ValueError("연도는 4자리 숫자여야 합니다 (예: '2024').")
        return v

    @field_validator("query")
    @classmethod
    def _validate_query(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("query는 비어있을 수 없습니다.")
        return v


def _tool_error(code: str, message: str) -> str:
    """LLM에 반환되는 표준 에러 포맷. LLM이 코드를 보고 재시도/우회 결정 가능."""
    return f"[TOOL_ERROR code={code}] {message}"


@retry(
    retry=retry_if_exception_type(requests.RequestException),
    stop=stop_after_attempt(KOBIS_MAX_ATTEMPTS),
    wait=wait_exponential(multiplier=0.5, min=0.5, max=4),
    reraise=True,
)
def _kobis_get(url: str, params: dict) -> dict:
    resp = requests.get(url, params=params, timeout=KOBIS_TIMEOUT)
    resp.raise_for_status()
    return resp.json()


@tool(args_schema=KobisInput)
def kobis_search(
    query: str,
    open_start_dt: str = "",
    open_end_dt: str = "",
    search_type: str = "movie",
) -> str:
    """한국 영화관입장권통합전산망(KOBIS)에서 한국 개봉 영화 정보를 검색합니다.
    한국 박스오피스, 한국 개봉작, 국내 상영 영화, 일별/주간 박스오피스를 찾을 때 사용하세요.
    박스오피스 조회 시 query는 YYYYMMDD 형식이어야 하며, 영화 검색 시 open_start_dt/open_end_dt는 4자리 연도여야 합니다."""
    api_key = os.environ.get("KOBIS_API_KEY")
    if not api_key:
        log.warning("kobis.no_api_key")
        return _tool_error(
            "MISSING_API_KEY",
            "KOBIS_API_KEY 환경변수가 설정되지 않아 한국 영화 데이터에 접근할 수 없습니다. 대신 web_search를 시도해보세요.",
        )

    if search_type in ("daily", "weekly") and not re.fullmatch(r"\d{8}", query):
        return _tool_error(
            "INVALID_DATE",
            f"박스오피스 조회 시 query는 YYYYMMDD 8자리 숫자여야 합니다. 받은 값: '{query}'",
        )

    log.info(
        "kobis.request",
        search_type=search_type,
        query=query,
        open_start=open_start_dt or None,
        open_end=open_end_dt or None,
    )

    try:
        if search_type == "daily":
            data = _kobis_get(
                "https://www.kobis.or.kr/kobisopenapi/webservice/rest/boxoffice/searchDailyBoxOfficeList.json",
                {"key": api_key, "targetDt": query},
            )
            items = data.get("boxOfficeResult", {}).get("dailyBoxOfficeList", [])
            if not items:
                log.info("kobis.empty_result", search_type=search_type, query=query)
                return f"{query} 일별 박스오피스 데이터가 없습니다."
            lines = [f"일별 박스오피스 ({query})\n"]
            for m in items[:10]:
                lines.append(
                    f"{m['rank']}위. {m['movieNm']} — 관객수: {int(m['audiCnt']):,}명 / 누적: {int(m['audiAcc']):,}명 (개봉일: {m.get('openDt', '-')})"
                )
            log.info("kobis.success", search_type=search_type, items=len(items))
            return "\n".join(lines)

        elif search_type == "weekly":
            data = _kobis_get(
                "https://www.kobis.or.kr/kobisopenapi/webservice/rest/boxoffice/searchWeeklyBoxOfficeList.json",
                {"key": api_key, "targetDt": query, "weekGb": "0"},
            )
            items = data.get("boxOfficeResult", {}).get("weeklyBoxOfficeList", [])
            if not items:
                log.info("kobis.empty_result", search_type=search_type, query=query)
                return f"{query} 주간 박스오피스 데이터가 없습니다."
            show_range = data.get("boxOfficeResult", {}).get("showRange", "")
            lines = [f"주간 박스오피스 ({show_range})\n"]
            for m in items[:10]:
                lines.append(
                    f"{m['rank']}위. {m['movieNm']} — 관객수: {int(m['audiCnt']):,}명 / 누적: {int(m['audiAcc']):,}명"
                )
            log.info("kobis.success", search_type=search_type, items=len(items))
            return "\n".join(lines)

        else:  # movie list
            params = {"key": api_key, "movieNm": query, "itemPerPage": "10"}
            if open_start_dt:
                params["openStartDt"] = open_start_dt
            if open_end_dt:
                params["openEndDt"] = open_end_dt
            data = _kobis_get(
                "https://www.kobis.or.kr/kobisopenapi/webservice/rest/movie/searchMovieList.json",
                params,
            )
            movies = data.get("movieListResult", {}).get("movieList", [])
            if not movies:
                log.info("kobis.empty_result", search_type=search_type, query=query)
                return f"'{query}'에 대한 KOBIS 검색 결과가 없습니다."

            lines = [f"KOBIS 영화 검색 결과: '{query}'\n"]
            for m in movies[:5]:
                open_dt = m.get("openDt", "")
                if len(open_dt) == 8:
                    open_dt = f"{open_dt[:4]}-{open_dt[4:6]}-{open_dt[6:]}"
                directors = ", ".join(
                    d.get("peopleNm", "") for d in m.get("directors", [])
                )
                actors = ", ".join(
                    a.get("peopleNm", "") for a in m.get("actors", [])[:3]
                )
                entry = f"- 제목: {m.get('movieNm', '')}"
                if m.get("movieNmEn"):
                    entry += f" ({m['movieNmEn']})"
                if open_dt:
                    entry += f"\n  개봉일: {open_dt}"
                if m.get("genreAlt"):
                    entry += f"\n  장르: {m['genreAlt']}"
                if m.get("nationAlt"):
                    entry += f"\n  국가: {m['nationAlt']}"
                if directors:
                    entry += f"\n  감독: {directors}"
                if actors:
                    entry += f"\n  출연: {actors}"
                lines.append(entry)
            log.info("kobis.success", search_type=search_type, items=len(movies))
            return "\n".join(lines)

    except requests.Timeout as e:
        log.warning("kobis.timeout", search_type=search_type, error=str(e))
        return _tool_error(
            "TIMEOUT",
            "KOBIS API 응답 시간이 초과되었습니다. 잠시 후 다시 시도하거나 web_search를 사용하세요.",
        )
    except requests.HTTPError as e:
        log.warning(
            "kobis.http_error",
            search_type=search_type,
            status=e.response.status_code if e.response is not None else None,
        )
        return _tool_error(
            "HTTP_ERROR",
            f"KOBIS API HTTP 오류 ({e.response.status_code if e.response is not None else 'unknown'}). web_search로 대신 시도해보세요.",
        )
    except requests.RequestException as e:
        log.warning("kobis.network_error", search_type=search_type, error=str(e))
        return _tool_error(
            "NETWORK_ERROR",
            "KOBIS API 호출에 실패했습니다. web_search로 대신 시도해보세요.",
        )
    except (KeyError, ValueError, TypeError) as e:
        log.exception("kobis.parse_error", search_type=search_type)
        return _tool_error(
            "PARSE_ERROR",
            f"KOBIS 응답 파싱 실패: {e}. web_search로 대신 시도해보세요.",
        )


SYSTEM_PROMPT = """당신은 영화 전문가 AI 어시스턴트입니다.

## 도구 선택 기준
- **imdb_search**: IMDB Top 250의 명작/고전 영화 정보 (평점, 감독, 출연진, 줄거리). 한국 영화여도 Top 250에 포함된 작품은 여기서 먼저 찾으세요.
- **kobis_search**: 한국 개봉 영화, 한국 박스오피스, 국내 상영작 검색. search_type 사용법:
  - `movie`: 영화 목록 검색. query에 영화 제목.
  - `daily`: 일별 박스오피스. query에 YYYYMMDD 8자리 날짜.
  - `weekly`: 주간 박스오피스. query에 YYYYMMDD 8자리 날짜.
- **web_search**: 위 두 도구로 부족할 때 — 최신 수상 내역, 해외 신작, 최신 뉴스 등.

## 도구 에러 처리
- 도구 응답이 `[TOOL_ERROR code=...]`로 시작하면 도구 실패를 의미합니다.
- `INVALID_DATE`/`VALIDATION_ERROR`: 인자를 고쳐서 같은 도구를 다시 호출하세요.
- `MISSING_API_KEY`/`TIMEOUT`/`NETWORK_ERROR`/`HTTP_ERROR`: 다른 도구(web_search 등)로 우회하세요.
- 모든 도구가 실패하면 사용자에게 솔직하게 알리세요.

## 출력 규칙
- 한국어로 답변하세요.
- 마크다운 형식 사용: 영화 제목은 **굵게**, 목록은 `-`로.
- 모르는 내용은 추측하지 말고 "확인되지 않습니다"라고 답하세요.
- 답변은 3~6 문장 또는 짧은 목록 형태로 간결하게 유지하세요.
- 도구 호출 결과를 그대로 복사하지 말고, 사용자 질문에 맞게 요약/재구성하세요."""

agent = create_agent(
    model=llm,
    tools=[imdb_tool, web_tool, kobis_search],
    system_prompt=SYSTEM_PROMPT,
    checkpointer=MemorySaver(),
)
