"""도구 실행 결과에서 답변 근거(출처)를 뽑아낸다.

RAG 답변이 어디서 왔는지 사용자가 확인할 수 있어야 환각과 사실을 구분할 수 있다.
도구마다 출처의 형태가 완전히 달라서 각각 따로 처리한다:

- imdb_search : ToolMessage.artifact 가 list[Document]. PDF 쪽번호를 쓴다.
                (agent.py에서 response_format="content_and_artifact"로 설정해야
                 artifact가 채워진다. 기본값 "content"면 메타데이터가 버려진다)
- web_search  : content가 format_web_results()가 만든 JSON 문자열이다.
                파싱해서 실제 URL을 꺼낸다 — 유일하게 클릭 가능한 출처다.
                만드는 쪽과 읽는 쪽이 어긋나면 출처가 조용히 사라지므로 같은 모듈에 둔다.
- kobis_search: 문자열만 반환하므로 URL이 없다. 기관명을 고정 출처로 붙인다.

외부 의존이 없는 순수 함수라 tests/unit에서 API 호출 없이 검증한다.
"""

import json

KOBIS_HOME = "https://www.kobis.or.kr"

MAX_SOURCES_PER_TOOL = 4

# 검색 결과 하나의 본문 상한. Tavily basic 검색의 content는 보통 짧은 발췌지만,
# 가끔 긴 본문이 섞이면 결과 5개가 그대로 LLM 입력 토큰이 된다.
WEB_MAX_CONTENT_CHARS = 1000


def format_web_results(raw) -> str | None:
    """Tavily 검색 응답을 LLM에 넘길 JSON 문자열로 줄인다. 쓸 결과가 없으면 None.

    답변에 쓰이는 title·url·content만 남기고 score·raw_content·images·
    response_time·request_id 같은 필드는 버린다. 결과 형태는 _web_sources()가
    파싱하는 형태({"results": [{"title", "url", ...}]})와 반드시 같아야 한다.
    """
    results = raw.get("results") if isinstance(raw, dict) else None
    if not isinstance(results, list):
        return None

    slim: list[dict] = []
    for r in results:
        if not isinstance(r, dict):
            continue
        url = r.get("url")
        if not isinstance(url, str) or not url:
            continue
        title = r.get("title") if isinstance(r.get("title"), str) else ""
        content = r.get("content") if isinstance(r.get("content"), str) else ""
        slim.append(
            {"title": title, "url": url, "content": content[:WEB_MAX_CONTENT_CHARS]}
        )

    if not slim:
        return None
    return json.dumps({"results": slim}, ensure_ascii=False)


def _imdb_sources(artifact) -> list[dict]:
    if not isinstance(artifact, list):
        return []
    pages: list[int] = []
    for doc in artifact:
        meta = getattr(doc, "metadata", None)
        if not isinstance(meta, dict):
            continue
        page = meta.get("page")
        if isinstance(page, int) and page not in pages:
            pages.append(page)
    if not pages:
        return []
    # PyMuPDF의 page는 0-기반이라 사람이 읽는 쪽번호로 1을 더한다.
    shown = sorted(pages)[:MAX_SOURCES_PER_TOOL]
    label = "IMDB Top 250 자료 " + ", ".join(f"p.{p + 1}" for p in shown)
    return [{"tool": "imdb_search", "label": label, "url": None}]


def _web_sources(content) -> list[dict]:
    if not isinstance(content, str):
        return []
    try:
        data = json.loads(content)
    except (ValueError, TypeError):
        return []
    results = data.get("results") if isinstance(data, dict) else None
    if not isinstance(results, list):
        return []

    out: list[dict] = []
    seen: set[str] = set()
    for r in results:
        if not isinstance(r, dict):
            continue
        url = r.get("url")
        if not isinstance(url, str) or not url or url in seen:
            continue
        seen.add(url)
        title = r.get("title")
        label = title.strip() if isinstance(title, str) and title.strip() else url
        out.append({"tool": "web_search", "label": label, "url": url})
        if len(out) >= MAX_SOURCES_PER_TOOL:
            break
    return out


def extract_sources(tool_name: str, output) -> list[dict]:
    """on_tool_end 이벤트의 output에서 출처 목록을 만든다.

    반환 형태: [{"tool": str, "label": str, "url": str | None}, ...]
    추출할 게 없으면 빈 리스트를 준다(출처 UI를 그리지 않는다).
    """
    if tool_name == "imdb_search":
        return _imdb_sources(getattr(output, "artifact", None))
    if tool_name == "web_search":
        content = getattr(output, "content", output)
        return _web_sources(content)
    if tool_name == "kobis_search":
        content = getattr(output, "content", output)
        # 도구가 에러를 반환했으면 근거가 아니므로 출처로 내보내지 않는다.
        if isinstance(content, str) and content.startswith("[TOOL_ERROR"):
            return []
        return [
            {
                "tool": "kobis_search",
                "label": "영화진흥위원회 KOBIS 오픈API",
                "url": KOBIS_HOME,
            }
        ]
    return []
