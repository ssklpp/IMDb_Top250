from dotenv import load_dotenv

load_dotenv()

import os
import re
from typing import Literal

import requests
from langchain.agents import create_agent
from langchain_community.vectorstores import FAISS
from langchain_core.documents import Document
from langchain_core.tools import tool
from langchain_core.tools.retriever import create_retriever_tool
from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from langgraph.checkpoint.memory import MemorySaver
from pydantic import BaseModel, Field, field_validator
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from imdb_data import (
    CSV_PATH,
    SQL_SCHEMA,
    SqlRejected,
    corpus_digest,
    movie_docs,
    query_movies,
)
from kobis_format import (
    DETAIL_MAX_CANDIDATES,
    boxoffice_too_recent,
    fmt_date,
    format_movie_info,
    format_show_range,
    is_valid_date,
    pick_movie,
    today_kst,
    tool_error,
)
from logging_config import get_logger
from sources import format_web_results

log = get_logger("agent")

VECTORSTORE_PATH = "vectorstore"

# 대화 체크포인트 DB. 기본값을 vectorstore/ 아래로 둔 이유는 Railway에서 이미
# /app/vectorstore가 영구 볼륨으로 마운트돼 있어 추가 설정 없이 재시작 후에도
# 대화가 보존되기 때문이다. 다른 위치를 쓰려면 CHECKPOINT_DB_PATH로 덮어쓴다.
CHECKPOINT_DB_PATH = os.environ.get(
    "CHECKPOINT_DB_PATH", f"{VECTORSTORE_PATH}/checkpoints.sqlite"
)

KOBIS_TIMEOUT = 10
KOBIS_MAX_ATTEMPTS = 3
KOBIS_BASE = "https://www.kobis.or.kr/kobisopenapi/webservice/rest"

TAVILY_URL = "https://api.tavily.com/search"
WEB_TIMEOUT = 15
WEB_MAX_ATTEMPTS = 2
WEB_MAX_RESULTS = 5

# 박스오피스 계열 모드. daily는 별도 엔드포인트, 나머지는 weekGb로 구분.
BOXOFFICE_TYPES = ("daily", "weekly", "weekend", "weekday")
WEEK_GB = {"weekly": "0", "weekend": "1", "weekday": "2"}
BOXOFFICE_FALLBACK_LABEL = {
    "daily": "일별 박스오피스",
    "weekly": "주간 박스오피스",
    "weekend": "주말 박스오피스",
    "weekday": "주중 박스오피스",
}

embeddings = OpenAIEmbeddings(model="text-embedding-3-small")

# 인덱스 이름에 임베딩할 내용의 지문을 넣는다. CSV나 변환 형식(imdb_data.py)이 바뀌면
# 이름이 달라져 자동으로 새로 만든다. Railway 볼륨은 재배포해도 남기 때문에, 고정된
# 이름(index.faiss)을 쓰던 시절에는 옛 인덱스가 계속 쓰였다(로컬 72청크, 배포 87청크).
_movie_docs = movie_docs()
INDEX_NAME = f"imdb_{corpus_digest(_movie_docs)}"

if os.path.exists(f"{VECTORSTORE_PATH}/{INDEX_NAME}.faiss"):
    log.info("vectorstore.cache_hit", path=VECTORSTORE_PATH, index=INDEX_NAME)
    vectorstore = FAISS.load_local(
        VECTORSTORE_PATH,
        embeddings,
        index_name=INDEX_NAME,
        allow_dangerous_deserialization=True,
    )
else:
    log.info("vectorstore.build_start", source=CSV_PATH.name, index=INDEX_NAME)
    docs = [Document(page_content=text, metadata=meta) for text, meta in _movie_docs]
    vectorstore = FAISS.from_documents(documents=docs, embedding=embeddings)
    vectorstore.save_local(VECTORSTORE_PATH, index_name=INDEX_NAME)
    log.info("vectorstore.build_done", docs=len(docs))

retriever = vectorstore.as_retriever(search_kwargs={"k": 8})
llm = ChatOpenAI(model_name="gpt-5.4-mini", temperature=0)

imdb_tool = create_retriever_tool(
    retriever,
    name="imdb_search",
    description=(
        "IMDB Top 250 목록에서 영화 정보를 검색합니다. 영화 제목, 감독, 출연진, 평점 등을 찾을 때 사용하세요. "
        "자료가 영어라 검색어는 영어로 쓰세요(쇼생크 탈출 → The Shawshank Redemption). "
        "제목을 모르는 줄거리 묘사도 영어 키워드로 바꿔 먼저 여기서 찾아보세요(요리하는 쥐 → rat who cooks)."
    ),
    # "영어로" 문장은 검색 적중률 측정의 결과다(tests/evals/README.md). 모델은 대개 제목을 스스로
    # 영어로 바꾸지만, 제목을 모르는 한국어 줄거리 묘사는 그대로 넘겨 못 찾거나 웹 검색으로 우회했다.
    # 고치면 python -m tests.evals.run_retrieval --actual 로 다시 잴 것.
    # content만 쓰면 검색된 Document의 메타데이터(순위·제목)가 버려진다.
    # 출처 표시에 필요하므로 artifact로 원본 Document를 함께 받는다.
    response_format="content_and_artifact",
)


# 벡터 검색(imdb_search)은 의미가 가까운 8편만 가져와서 정렬·집계를 못 한다. "평점 1위"를 물으면
# 검색된 8편 안에서 최고를 골라 틀린다. 같은 데이터를 SQL로 조회하는 도구를 따로 둔다.
# 실행 제한(읽기 전용, 상한)은 imdb_data.query_movies()에 있다.
@tool(
    description=(
        "IMDB Top 250 목록에 SQLite SELECT 한 문장을 실행합니다. 평점·순위·연도로 정렬하거나 "
        "거르는 질문, 개수를 세는 질문에 쓰세요(예: 평점 1위, 1990년대 영화 수, 특정 감독 작품 중 최고 평점). "
        f"테이블: {SQL_SCHEMA}. 값은 영어이고 비영어권 영화는 원제입니다(기생충=Gisaengchung). "
        "director·actors는 쉼표로 구분된 이름이라 LIKE '%Nolan%'처럼 찾으세요."
    )
)
def imdb_sql(query: str) -> str:
    log.info("imdb.sql_request", query_len=len(query))
    try:
        text, rows = query_movies(query)
    except SqlRejected as e:
        log.info("imdb.sql_rejected", error=type(e.__cause__ or e).__name__)
        return tool_error("INVALID_QUERY", f"{e} 테이블: {SQL_SCHEMA}")
    log.info("imdb.sql_done", rows=rows)
    return text


# web_search는 langchain_tavily.TavilySearch를 쓰지 않고 Tavily API를 직접 호출한다.
# - TavilySearch는 LLM에 파라미터 9개(include_domains, time_range 등)를 노출해 도구 정의만
#   1,521토큰이었다. 도구 정의는 LLM 호출마다 전송되고 질문 하나에 호출이 2번이라,
#   약 3,000토큰이 쓰지도 않는 설명문에 나갔다. query 하나만 받게 줄였다.
# - TavilySearch는 생성 시점에 TAVILY_API_KEY를 요구해 키가 없으면 import부터 실패했다.
#   헬스체크는 이 키를 선택 사항(degraded)으로 설계했는데 실제로는 서버가 뜨지 않았다.
# - 동기 경로의 requests.post에 timeout이 없었다.
# 기존 도구를 이 함수 안에서 invoke()로 감싸면 안 된다. 중첩 도구 이벤트가 발생해
# "검색 중" 표시와 tool_calls 집계가 두 번씩 잡힌다.
@retry(
    # Tavily는 호출마다 크레딧을 쓴다. HTTP 오류(401·429 등)는 다시 보내도 결과가 같으므로
    # 연결 문제만, 그리고 KOBIS(3회)보다 적게 재시도한다.
    retry=retry_if_exception_type((requests.Timeout, requests.ConnectionError)),
    stop=stop_after_attempt(WEB_MAX_ATTEMPTS),
    wait=wait_exponential(multiplier=0.5, min=0.5, max=2),
    reraise=True,
)
def _tavily_search(api_key: str, query: str) -> dict:
    resp = requests.post(
        TAVILY_URL,
        json={"query": query, "max_results": WEB_MAX_RESULTS, "search_depth": "basic"},
        # 키는 헤더로 보내므로 KOBIS와 달리 예외 메시지(URL)에 섞이지 않는다.
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=WEB_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()


@tool
def web_search(query: str) -> str:
    """인터넷에서 최신 영화 정보를 검색합니다. IMDB 목록에 없는 신작, 박스오피스, 최신 수상 내역 등을 찾을 때 사용하세요."""
    api_key = os.environ.get("TAVILY_API_KEY")
    if not api_key:
        log.warning("web.no_api_key")
        return tool_error(
            "MISSING_API_KEY",
            "TAVILY_API_KEY 환경변수가 설정되지 않아 웹 검색을 할 수 없습니다. "
            "imdb_search나 kobis_search로 확인할 수 있는 범위에서 답하세요.",
        )

    query = query.strip()
    if not query:
        return tool_error("INVALID_QUERY", "검색어가 비어 있습니다. 검색어를 넣어 다시 호출하세요.")

    # 검색어는 사용자 질문에서 파생되므로 본문 대신 길이만 남긴다.
    log.info("web.request", query_len=len(query))
    try:
        raw = _tavily_search(api_key, query)
    except requests.Timeout as e:
        log.warning("web.timeout", error=type(e).__name__)
        return tool_error("TIMEOUT", "웹 검색 응답 시간이 초과되었습니다. 잠시 후 다시 시도하세요.")
    except requests.HTTPError as e:
        status = e.response.status_code if e.response is not None else None
        log.warning("web.http_error", status=status)
        return tool_error("HTTP_ERROR", f"웹 검색 API HTTP 오류 ({status or 'unknown'}).")
    # requests.JSONDecodeError는 RequestException의 하위 클래스라 먼저 잡아야 한다.
    except requests.JSONDecodeError as e:
        log.warning("web.parse_error", error=type(e).__name__)
        return tool_error("PARSE_ERROR", "웹 검색 응답을 해석하지 못했습니다.")
    except requests.RequestException as e:
        log.warning("web.network_error", error=type(e).__name__)
        return tool_error("NETWORK_ERROR", "웹 검색 API 호출에 실패했습니다.")

    formatted = format_web_results(raw)
    if formatted is None:
        log.info("web.empty_result")
        return f"'{query}'에 대한 웹 검색 결과가 없습니다."

    log.info("web.success", items=len(raw.get("results", [])))
    return formatted


class KobisInput(BaseModel):
    """KOBIS 검색 인자. LLM이 잘못된 포맷을 보낼 경우 검증 에러로 자동 재호출 유도."""

    query: str = Field(
        ...,
        min_length=1,
        description=(
            "search_type에 따라 의미가 달라집니다. "
            "'movie'/'detail' → 영화 제목 (예: '기생충'). "
            "'daily'/'weekly'/'weekend'/'weekday' → YYYYMMDD 8자리 날짜 (예: '20260906'). "
            "detail 모드에서 8자리 숫자를 넣으면 KOBIS 영화코드(movieCd)로 간주하므로 제목을 그대로 넣으세요."
        ),
    )
    open_start_dt: str = Field(
        "",
        description="개봉 시작 연도 4자리 (예: '2024'). movie/detail 검색에만 사용.",
    )
    open_end_dt: str = Field(
        "",
        description="개봉 종료 연도 4자리 (예: '2024'). movie/detail 검색에만 사용.",
    )
    search_type: Literal[
        "movie", "detail", "daily", "weekly", "weekend", "weekday"
    ] = Field(
        "movie",
        description=(
            "'movie'=영화 목록 검색(여러 후보), "
            "'detail'=영화 한 편의 상세정보(출연진·배역·관람등급·상영시간·제작/배급사), "
            "'daily'=일별 박스오피스, "
            "'weekly'=주간(월~일), 'weekend'=주말(금~일), 'weekday'=주중(월~목) 박스오피스."
        ),
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


def _search_movie_list(
    api_key: str, query: str, open_start_dt: str, open_end_dt: str
) -> list[dict]:
    """영화명으로 목록을 조회한다. movie/detail 모드가 공유한다.

    결과가 없으면 빈 리스트를 준다(예외로 신호하지 않는다 — PARSE_ERROR로 오분류됨).
    _kobis_get의 네트워크 예외는 잡지 않고 호출부의 except로 전파시킨다.
    """
    params = {"key": api_key, "movieNm": query, "itemPerPage": "10"}
    if open_start_dt:
        params["openStartDt"] = open_start_dt
    if open_end_dt:
        params["openEndDt"] = open_end_dt
    return (
        _kobis_get(f"{KOBIS_BASE}/movie/searchMovieList.json", params)
        .get("movieListResult", {})
        .get("movieList", [])
    )


@tool(args_schema=KobisInput)
def kobis_search(
    query: str,
    open_start_dt: str = "",
    open_end_dt: str = "",
    search_type: str = "movie",
) -> str:
    """한국 영화관입장권통합전산망(KOBIS)에서 한국 개봉 영화 정보를 검색합니다.
    한국 박스오피스, 한국 개봉작, 국내 상영 영화, 영화 상세정보(출연진·배역·관람등급·상영시간)를 찾을 때 사용하세요.
    search_type='detail'은 영화 제목만 주면 내부에서 영화코드 조회까지 한 번에 처리하므로 두 번 호출할 필요가 없습니다.
    박스오피스 조회(daily/weekly/weekend/weekday) 시 query는 YYYYMMDD 형식이어야 하며,
    영화 검색 시 open_start_dt/open_end_dt는 4자리 연도여야 합니다."""
    api_key = os.environ.get("KOBIS_API_KEY")
    if not api_key:
        log.warning("kobis.no_api_key")
        return tool_error(
            "MISSING_API_KEY",
            "KOBIS_API_KEY 환경변수가 설정되지 않아 한국 영화 데이터에 접근할 수 없습니다. 대신 web_search를 시도해보세요.",
        )

    if search_type in BOXOFFICE_TYPES:
        if not is_valid_date(query):
            return tool_error(
                "INVALID_DATE",
                f"박스오피스 조회 시 query는 실제 존재하는 YYYYMMDD 8자리 날짜여야 합니다. "
                f"받은 값 '{query}'은(는) 유효한 날짜가 아닙니다.",
            )
        # 아직 집계 전인 날짜는 호출하지 않고 바로 막는다. KOBIS를 한 번 다녀와야
        # 알 수 있던 것을 왕복 없이 알려준다.
        too_recent = boxoffice_too_recent(search_type, query, today_kst())
        if too_recent:
            log.info("kobis.too_recent", search_type=search_type, query=query)
            return tool_error("NO_DATA", too_recent)

    log.info(
        "kobis.request",
        search_type=search_type,
        query=query,
        open_start=open_start_dt or None,
        open_end=open_end_dt or None,
    )

    try:
        if search_type in BOXOFFICE_TYPES:
            if search_type == "daily":
                url = f"{KOBIS_BASE}/boxoffice/searchDailyBoxOfficeList.json"
                params = {"key": api_key, "targetDt": query}
                list_key = "dailyBoxOfficeList"
            else:
                url = f"{KOBIS_BASE}/boxoffice/searchWeeklyBoxOfficeList.json"
                params = {
                    "key": api_key,
                    "targetDt": query,
                    "weekGb": WEEK_GB[search_type],
                }
                list_key = "weeklyBoxOfficeList"

            result = _kobis_get(url, params).get("boxOfficeResult", {})
            items = result.get(list_key, [])
            # KOBIS가 '주말 박스오피스' 같은 한글 라벨을 직접 주므로 그대로 쓴다.
            label = result.get("boxofficeType") or BOXOFFICE_FALLBACK_LABEL[search_type]
            if not items:
                # 진행 중인 기간을 물으면 KOBIS는 빈 목록을 준다. 그냥 문장으로 돌려주면
                # 에러 계약 밖이라 LLM이 임의로 복구한다 — 실제로 연도를 1년 낮춰
                # 재호출해서 작년 데이터를 "지난 주"라고 답한 적이 있다.
                log.info("kobis.empty_result", search_type=search_type, query=query)
                return tool_error(
                    "NO_DATA",
                    f"{fmt_date(query)} {label} 데이터가 아직 없습니다. "
                    "KOBIS는 일별은 다음 날, 주간·주말·주중은 해당 주가 끝난 뒤에 제공합니다. "
                    "연도는 그대로 두고 하루(일별) 또는 일주일(주간·주말·주중) 이전 날짜로 다시 호출하세요.",
                )

            period = format_show_range(result.get("showRange", "")) or query
            lines = [f"{label} ({period})\n"]
            for m in items[:10]:
                line = (
                    f"{m['rank']}위. {m['movieNm']} — "
                    f"관객수: {int(m['audiCnt']):,}명 / 누적: {int(m['audiAcc']):,}명"
                )
                if search_type == "daily" and m.get("openDt"):
                    line += f" (개봉일: {m['openDt']})"
                lines.append(line)
            log.info("kobis.success", search_type=search_type, items=len(items))
            return "\n".join(lines)

        elif search_type == "detail":
            # 8자리 숫자는 movieCd로 간주해 1단계를 건너뛴다.
            movie_cd = query if re.fullmatch(r"\d{8}", query) else None
            others_note = ""

            if movie_cd is None:
                movies = _search_movie_list(api_key, query, open_start_dt, open_end_dt)
                if not movies:
                    log.info(
                        "kobis.movie_not_found",
                        search_type=search_type,
                        query=query,
                        stage="list",
                    )
                    return tool_error(
                        "MOVIE_NOT_FOUND",
                        f"'{query}'에 해당하는 영화를 KOBIS에서 찾지 못했습니다. "
                        "제목 철자를 확인해 다시 호출하거나 web_search를 사용하세요.",
                    )

                picked = pick_movie(movies, query)
                movie_cd = picked.get("movieCd", "")
                log.info(
                    "kobis.detail_resolve",
                    query=query,
                    candidates=len(movies),
                    movie_cd=movie_cd,
                    movie_nm=picked.get("movieNm"),
                )
                others = [m for m in movies if m.get("movieCd") != movie_cd][
                    :DETAIL_MAX_CANDIDATES
                ]
                if others:
                    cand = ", ".join(
                        f"{m.get('movieNm', '')}({m.get('prdtYear', '') or '연도미상'})"
                        for m in others
                    )
                    others_note = f"\n\n(동명/유사 제목 후보: {cand})"

            info = (
                _kobis_get(
                    f"{KOBIS_BASE}/movie/searchMovieInfo.json",
                    {"key": api_key, "movieCd": movie_cd},
                )
                .get("movieInfoResult", {})
                .get("movieInfo", {})
            )
            # 잘못된 movieCd는 HTTP 200에 전 필드 null로 돌아온다. 예외가 아니라 값으로 판별.
            if not info or not info.get("movieNm"):
                log.info(
                    "kobis.movie_not_found",
                    search_type=search_type,
                    query=query,
                    movie_cd=movie_cd,
                    stage="info",
                )
                return tool_error(
                    "MOVIE_NOT_FOUND",
                    f"영화코드 '{movie_cd}'의 상세정보를 가져오지 못했습니다. "
                    "search_type='movie'로 후보를 먼저 확인하세요.",
                )

            log.info(
                "kobis.success", search_type=search_type, items=1, movie_cd=movie_cd
            )
            return f"KOBIS 영화 상세정보\n\n{format_movie_info(info)}{others_note}"

        else:  # movie list
            movies = _search_movie_list(api_key, query, open_start_dt, open_end_dt)
            if not movies:
                log.info("kobis.empty_result", search_type=search_type, query=query)
                return f"'{query}'에 대한 KOBIS 검색 결과가 없습니다."

            lines = [f"KOBIS 영화 검색 결과: '{query}'\n"]
            for m in movies[:5]:
                # searchMovieList 응답에는 배우 정보가 없다. 출연진은 search_type='detail'로.
                open_dt = fmt_date(m.get("openDt", ""))
                directors = ", ".join(
                    d.get("peopleNm", "") for d in m.get("directors", [])
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
                lines.append(entry)
            log.info("kobis.success", search_type=search_type, items=len(movies))
            return "\n".join(lines)

    # 주의: requests 예외의 str()에는 요청 URL 전문이 들어가고 KOBIS는 API 키를
    # 쿼리 파라미터로 받으므로, str(e)를 로깅하면 키가 평문으로 남는다. 타입만 기록한다.
    except requests.Timeout as e:
        log.warning("kobis.timeout", search_type=search_type, error=type(e).__name__)
        return tool_error(
            "TIMEOUT",
            "KOBIS API 응답 시간이 초과되었습니다. 잠시 후 다시 시도하거나 web_search를 사용하세요.",
        )
    except requests.HTTPError as e:
        log.warning(
            "kobis.http_error",
            search_type=search_type,
            status=e.response.status_code if e.response is not None else None,
        )
        return tool_error(
            "HTTP_ERROR",
            f"KOBIS API HTTP 오류 ({e.response.status_code if e.response is not None else 'unknown'}). web_search로 대신 시도해보세요.",
        )
    except requests.RequestException as e:
        log.warning(
            "kobis.network_error", search_type=search_type, error=type(e).__name__
        )
        return tool_error(
            "NETWORK_ERROR",
            "KOBIS API 호출에 실패했습니다. web_search로 대신 시도해보세요.",
        )
    except (KeyError, ValueError, TypeError) as e:
        log.exception("kobis.parse_error", search_type=search_type)
        return tool_error(
            "PARSE_ERROR",
            f"KOBIS 응답 파싱 실패: {e}. web_search로 대신 시도해보세요.",
        )


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

TOOLS = [imdb_tool, imdb_sql, web_search, kobis_search]


def build_agent(checkpointer=None):
    """체크포인터를 주입받아 에이전트를 만든다.

    호출부마다 필요한 체크포인터가 다르기 때문에 팩토리로 분리했다:
    - `server.py`: AsyncSqliteSaver — astream_events가 async라 sync 세이버는
      aput에서 NotImplementedError를 던진다. 파일에 저장해 재시작 후에도 대화가 유지된다.
    - `imdb_rag.py`: SqliteSaver — CLI는 sync invoke를 쓴다. 같은 DB 파일을 공유한다.
    - `tests/evals`: MemorySaver — 항목마다 새 thread_id를 쓰므로 영속화가 불필요하고,
      평가가 실제 대화 DB를 건드리지 않게 격리한다.

    checkpointer를 생략하면 MemorySaver를 쓴다(프로세스 종료 시 소멸).
    """
    if checkpointer is None:
        checkpointer = MemorySaver()
    return create_agent(
        model=llm,
        tools=TOOLS,
        system_prompt=SYSTEM_PROMPT,
        checkpointer=checkpointer,
    )
