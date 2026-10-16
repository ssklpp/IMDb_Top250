"""web_search — Tavily REST API를 직접 호출한다. TavilySearch로 되돌리지 말 것(rules/tools.md)."""

import os

import requests
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from kobis_format import tool_error
from logging_config import get_logger
from sources import format_web_results

log = get_logger("movie_tools.web")

NAME = "web_search"
DESCRIPTION = "인터넷에서 최신 영화 정보를 검색합니다. IMDB 목록에 없는 신작, 박스오피스, 최신 수상 내역 등을 찾을 때 사용하세요."

TAVILY_URL = "https://api.tavily.com/search"
WEB_TIMEOUT = 15
WEB_MAX_ATTEMPTS = 2
WEB_MAX_RESULTS = 5


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


def web_search(query: str) -> str:
    """web_search 본체."""
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
