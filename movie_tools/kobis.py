"""kobis_search — 영화진흥위원회 KOBIS 오픈API(6개 모드). 규칙은 rules/tools.md."""

import os
import re
from typing import Literal

import requests
from pydantic import BaseModel, Field, field_validator
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

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

log = get_logger("movie_tools.kobis")

NAME = "kobis_search"
# 로컬 도구와 MCP 도구가 같은 설명을 쓴다. 원래 @tool 함수의 docstring이었다.
DESCRIPTION = """\
한국 영화관입장권통합전산망(KOBIS)에서 한국 개봉 영화 정보를 검색합니다.
한국 박스오피스, 한국 개봉작, 국내 상영 영화, 영화 상세정보(출연진·배역·관람등급·상영시간)를 찾을 때 사용하세요.
search_type='detail'은 영화 제목만 주면 내부에서 영화코드 조회까지 한 번에 처리하므로 두 번 호출할 필요가 없습니다.
박스오피스 조회(daily/weekly/weekend/weekday) 시 query는 YYYYMMDD 형식이어야 하며,
영화 검색 시 open_start_dt/open_end_dt는 4자리 연도여야 합니다."""

SearchType = Literal["movie", "detail", "daily", "weekly", "weekend", "weekday"]

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
    search_type: SearchType = Field(
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


def kobis_search(
    query: str,
    open_start_dt: str = "",
    open_end_dt: str = "",
    search_type: str = "movie",
) -> str:
    """kobis_search 본체. 인자는 KobisInput으로 이미 검증된 값이라고 가정한다."""
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


def fetch_weekly_raw(target_dt: str) -> dict:
    """주간(월~일) 박스오피스 원본 응답. 브리핑이 쓴다(kobis_format.parse_weekly로 읽는다).

    에이전트 도구(kobis_search)는 사람이 읽을 문장을 주지만 브리핑은 숫자를 직접 계산해야 해서 원본이 필요하다.
    키가 없거나 날짜가 틀리면 ValueError, 네트워크 오류는 requests 예외 그대로 — 호출부가 실패로 기록한다.
    """
    api_key = os.environ.get("KOBIS_API_KEY")
    if not api_key:
        raise ValueError("KOBIS_API_KEY가 없습니다.")
    if not is_valid_date(target_dt):
        raise ValueError(f"날짜 형식이 아닙니다: {target_dt!r}")
    log.info("kobis.weekly_raw", target_dt=target_dt)
    return _kobis_get(
        f"{KOBIS_BASE}/boxoffice/searchWeeklyBoxOfficeList.json",
        {"key": api_key, "targetDt": target_dt, "weekGb": WEEK_GB["weekly"]},
    )

