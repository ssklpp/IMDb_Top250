from dotenv import load_dotenv

load_dotenv()

import os
import re
from datetime import datetime
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
KOBIS_BASE = "https://www.kobis.or.kr/kobisopenapi/webservice/rest"

# 박스오피스 계열 모드. daily는 별도 엔드포인트, 나머지는 weekGb로 구분.
BOXOFFICE_TYPES = ("daily", "weekly", "weekend", "weekday")
WEEK_GB = {"weekly": "0", "weekend": "1", "weekday": "2"}
BOXOFFICE_FALLBACK_LABEL = {
    "daily": "일별 박스오피스",
    "weekly": "주간 박스오피스",
    "weekend": "주말 박스오피스",
    "weekday": "주중 박스오피스",
}

# detail 모드 출력 상한. KOBIS는 actors를 90건, staffs를 625건까지 반환하므로
# 그대로 넣으면 LLM 컨텍스트를 잡아먹는다. staffs는 아예 제외한다.
DETAIL_MAX_ACTORS = 8
DETAIL_MAX_COMPANIES = 2
DETAIL_MAX_CANDIDATES = 3

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


def _fmt_date(v: str) -> str:
    """'20190530' → '2019-05-30'. 8자리가 아니면 원본 그대로."""
    return f"{v[:4]}-{v[4:6]}-{v[6:]}" if len(v) == 8 and v.isdigit() else v


def _is_valid_date(v: str) -> bool:
    """실제 달력에 존재하는 YYYYMMDD인지 검사.

    KOBIS는 '99999999' 같은 값에도 에러 대신 엉뚱한 데이터를 반환하므로
    호출 전에 걸러야 한다. 8자리 숫자 검사만으로는 부족하다.
    """
    try:
        datetime.strptime(v, "%Y%m%d")
        return True
    except ValueError:
        return False


def _format_show_range(show_range: str) -> str:
    """'20260831~20260906' → '2026-08-31~2026-09-06'. 시작=종료면 한 날짜로 축약."""
    parts = [p for p in show_range.split("~") if len(p) == 8]
    if not parts:
        return ""
    fmt = [_fmt_date(p) for p in parts]
    return fmt[0] if len(set(fmt)) == 1 else "~".join(fmt)


def _pick_movie(movies: list[dict], title: str) -> dict:
    """동명/유사 제목이 여러 건일 때 1건 선택.

    KOBIS 목록은 관련도순이 아니라서 1위가 정답이 아닌 경우가 많다
    ('기생충' → 1위가 '마약 기생충', '부산행' → 1위가 '부산행:익스텐디드').
    공백·대소문자 무시 정확 일치 → 개봉 완료작 → 한국 제작 → 최신 개봉일 순으로 좁힌다.
    한국 제작을 우선하는 이유는 KOBIS가 한국 영화 DB이고 이 챗봇이 국내 개봉작을
    다루기 때문이다. 이게 없으면 '올드보이'가 2003년 박찬욱판이 아니라
    2013년 스파이크 리 리메이크로 해석된다.
    max()는 동률 시 첫 항목을 주므로 openDt가 모두 비어도 API 순서로 폴백된다.
    """
    norm = title.replace(" ", "").lower()
    exact = [m for m in movies if m.get("movieNm", "").replace(" ", "").lower() == norm]
    pool = exact or movies
    released = [m for m in pool if m.get("prdtStatNm") == "개봉"]
    pool = released or pool
    korean = [m for m in pool if m.get("repNationNm") == "한국"]
    pool = korean or pool
    return max(pool, key=lambda m: m.get("openDt") or "")


def _format_movie_info(info: dict) -> str:
    """searchMovieInfo 응답을 LLM 친화 요약으로 축약.

    actors가 90건, staffs가 625건까지 올 수 있으므로 staffs는 제외하고
    actors는 DETAIL_MAX_ACTORS로 자른다. 없는 항목은 줄 자체를 생략한다.
    """
    lines: list[str] = []

    head = info.get("movieNm", "")
    if info.get("movieNmEn"):
        head += f" ({info['movieNmEn']})"
    if info.get("prdtYear"):
        head += f" / 제작연도 {info['prdtYear']}"
    lines.append(head)

    open_dt = _fmt_date(info.get("openDt", ""))
    if open_dt:
        stat = info.get("prdtStatNm", "")
        lines.append(f"- 개봉일: {open_dt}" + (f" ({stat})" if stat else ""))
    if info.get("showTm"):
        lines.append(f"- 상영시간: {info['showTm']}분")

    genres = ", ".join(g.get("genreNm", "") for g in info.get("genres", []))
    if genres:
        lines.append(f"- 장르: {genres}")
    nations = ", ".join(n.get("nationNm", "") for n in info.get("nations", []))
    if nations:
        lines.append(f"- 국가: {nations}")

    audits = info.get("audits", [])
    if audits and audits[0].get("watchGradeNm"):
        lines.append(f"- 관람등급: {audits[0]['watchGradeNm']}")

    directors = ", ".join(d.get("peopleNm", "") for d in info.get("directors", []))
    if directors:
        lines.append(f"- 감독: {directors}")

    actors = info.get("actors", [])
    if actors:
        names = []
        for a in actors[:DETAIL_MAX_ACTORS]:
            nm = a.get("peopleNm", "")
            cast = (a.get("cast") or "").strip()
            names.append(f"{nm}({cast})" if cast else nm)
        more = len(actors) - DETAIL_MAX_ACTORS
        lines.append(
            f"- 출연: {', '.join(names)}" + (f" 외 {more}명" if more > 0 else "")
        )

    parts: dict[str, list[str]] = {}
    for c in info.get("companys", []):
        parts.setdefault(c.get("companyPartNm", ""), []).append(c.get("companyNm", ""))
    for part in ("제작사", "배급사"):
        if parts.get(part):
            lines.append(f"- {part}: {', '.join(parts[part][:DETAIL_MAX_COMPANIES])}")

    show_types = list(
        dict.fromkeys(
            s.get("showTypeGroupNm", "")
            for s in info.get("showTypes", [])
            if s.get("showTypeGroupNm")
        )
    )
    if show_types:
        lines.append(f"- 상영타입: {', '.join(show_types)}")

    return "\n".join(lines)


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
        return _tool_error(
            "MISSING_API_KEY",
            "KOBIS_API_KEY 환경변수가 설정되지 않아 한국 영화 데이터에 접근할 수 없습니다. 대신 web_search를 시도해보세요.",
        )

    if search_type in BOXOFFICE_TYPES and not _is_valid_date(query):
        return _tool_error(
            "INVALID_DATE",
            f"박스오피스 조회 시 query는 실제 존재하는 YYYYMMDD 8자리 날짜여야 합니다. "
            f"받은 값 '{query}'은(는) 유효한 날짜가 아닙니다.",
        )

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
                log.info("kobis.empty_result", search_type=search_type, query=query)
                return f"{query} {label} 데이터가 없습니다."

            period = _format_show_range(result.get("showRange", "")) or query
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
                params = {"key": api_key, "movieNm": query, "itemPerPage": "10"}
                if open_start_dt:
                    params["openStartDt"] = open_start_dt
                if open_end_dt:
                    params["openEndDt"] = open_end_dt
                movies = (
                    _kobis_get(f"{KOBIS_BASE}/movie/searchMovieList.json", params)
                    .get("movieListResult", {})
                    .get("movieList", [])
                )
                if not movies:
                    log.info(
                        "kobis.movie_not_found",
                        search_type=search_type,
                        query=query,
                        stage="list",
                    )
                    return _tool_error(
                        "MOVIE_NOT_FOUND",
                        f"'{query}'에 해당하는 영화를 KOBIS에서 찾지 못했습니다. "
                        "제목 철자를 확인해 다시 호출하거나 web_search를 사용하세요.",
                    )

                picked = _pick_movie(movies, query)
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
                return _tool_error(
                    "MOVIE_NOT_FOUND",
                    f"영화코드 '{movie_cd}'의 상세정보를 가져오지 못했습니다. "
                    "search_type='movie'로 후보를 먼저 확인하세요.",
                )

            log.info(
                "kobis.success", search_type=search_type, items=1, movie_cd=movie_cd
            )
            return f"KOBIS 영화 상세정보\n\n{_format_movie_info(info)}{others_note}"

        else:  # movie list
            params = {"key": api_key, "movieNm": query, "itemPerPage": "10"}
            if open_start_dt:
                params["openStartDt"] = open_start_dt
            if open_end_dt:
                params["openEndDt"] = open_end_dt
            data = _kobis_get(f"{KOBIS_BASE}/movie/searchMovieList.json", params)
            movies = data.get("movieListResult", {}).get("movieList", [])
            if not movies:
                log.info("kobis.empty_result", search_type=search_type, query=query)
                return f"'{query}'에 대한 KOBIS 검색 결과가 없습니다."

            lines = [f"KOBIS 영화 검색 결과: '{query}'\n"]
            for m in movies[:5]:
                # searchMovieList 응답에는 배우 정보가 없다. 출연진은 search_type='detail'로.
                open_dt = _fmt_date(m.get("openDt", ""))
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
        log.warning(
            "kobis.network_error", search_type=search_type, error=type(e).__name__
        )
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
  - `movie`: 영화 목록 검색(여러 후보를 훑을 때). query에 영화 제목.
  - `detail`: 특정 영화 한 편의 상세정보 — 출연진과 배역, 관람등급, 상영시간, 장르, 제작/배급사. query에 **영화 제목**을 그대로 넣으면 됩니다(영화코드를 따로 찾을 필요 없음).
  - `daily`: 일별 박스오피스. query에 YYYYMMDD 8자리 날짜.
  - `weekly` / `weekend` / `weekday`: 각각 주간(월~일) / 주말(금~일) / 주중(월~목) 박스오피스. query에는 조회할 주에 속한 YYYYMMDD 날짜를 넣습니다. 사용자가 "주말 순위"를 물으면 `weekly`가 아니라 `weekend`를 쓰세요.
  - 출연진·배역·관람등급·러닝타임 질문에는 `movie`가 아니라 `detail`을 사용하세요.
- **web_search**: 위 두 도구로 부족할 때 — 최신 수상 내역, 해외 신작, 최신 뉴스 등.

## 도구 에러 처리
- 도구 응답이 `[TOOL_ERROR code=...]`로 시작하면 도구 실패를 의미합니다.
- `INVALID_DATE`/`VALIDATION_ERROR`: 인자를 고쳐서 같은 도구를 다시 호출하세요.
- `MISSING_API_KEY`/`TIMEOUT`/`NETWORK_ERROR`/`HTTP_ERROR`: 다른 도구(web_search 등)로 우회하세요.
- `MOVIE_NOT_FOUND`: 제목 철자를 고쳐 다시 호출하거나 search_type='movie'로 후보를 먼저 확인하세요. 그래도 없으면 web_search로 우회하세요.
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
