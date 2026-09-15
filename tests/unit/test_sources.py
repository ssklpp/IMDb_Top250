"""sources.extract_sources 단위 테스트.

도구별 출력 형태가 전부 달라서(각각 실제 응답을 확인해 맞춤) 형태가 바뀌면
출처가 조용히 사라진다. 그 회귀를 잡는 것이 이 테스트의 목적이다.
"""

import json

import pytest

from sources import (
    KOBIS_HOME,
    MAX_SOURCES_PER_TOOL,
    WEB_MAX_CONTENT_CHARS,
    extract_sources,
    format_web_results,
)


class _Doc:
    """langchain Document 스텁 — metadata만 있으면 충분하다."""

    def __init__(self, metadata):
        self.metadata = metadata


class _ToolMessage:
    """langchain ToolMessage 스텁."""

    def __init__(self, content="", artifact=None):
        self.content = content
        self.artifact = artifact


class TestImdbSources:
    def test_PDF_쪽번호를_1기반으로_표시한다(self):
        """PyMuPDF의 page는 0-기반이라 그대로 쓰면 사용자에게 한 장 앞을 가리킨다."""
        out = extract_sources(
            "imdb_search",
            _ToolMessage(artifact=[_Doc({"page": 6}), _Doc({"page": 9})]),
        )
        assert len(out) == 1
        assert "p.7" in out[0]["label"]
        assert "p.10" in out[0]["label"]
        assert out[0]["url"] is None

    def test_중복_쪽번호는_한_번만_표시한다(self):
        """retriever가 k=8로 같은 페이지의 여러 청크를 반환하는 일이 흔하다."""
        docs = [_Doc({"page": 3}) for _ in range(8)]
        out = extract_sources("imdb_search", _ToolMessage(artifact=docs))
        assert out[0]["label"].count("p.4") == 1

    def test_쪽번호를_상한까지만_표시한다(self):
        docs = [_Doc({"page": i}) for i in range(20)]
        out = extract_sources("imdb_search", _ToolMessage(artifact=docs))
        assert out[0]["label"].count("p.") == MAX_SOURCES_PER_TOOL

    def test_artifact가_없으면_빈_결과(self):
        """response_format이 'content'로 되돌아가면 artifact가 None이 된다."""
        assert extract_sources("imdb_search", _ToolMessage(artifact=None)) == []

    def test_메타데이터에_page가_없으면_빈_결과(self):
        out = extract_sources("imdb_search", _ToolMessage(artifact=[_Doc({})]))
        assert out == []


class TestWebSources:
    @staticmethod
    def _tavily(results):
        return _ToolMessage(content=json.dumps({"query": "q", "results": results}))

    def test_URL과_제목을_뽑는다(self):
        """web_search는 artifact를 안 채우고 content에 JSON 문자열을 넣는다."""
        out = extract_sources(
            "web_search",
            self._tavily(
                [
                    {"url": "https://a.com", "title": "A 기사"},
                    {"url": "https://b.com", "title": "B 기사"},
                ]
            ),
        )
        assert [s["url"] for s in out] == ["https://a.com", "https://b.com"]
        assert out[0]["label"] == "A 기사"

    def test_제목이_비면_URL을_라벨로_쓴다(self):
        out = extract_sources("web_search", self._tavily([{"url": "https://a.com", "title": "  "}]))
        assert out[0]["label"] == "https://a.com"

    def test_같은_URL은_한_번만(self):
        out = extract_sources(
            "web_search",
            self._tavily([{"url": "https://a.com", "title": "x"}] * 3),
        )
        assert len(out) == 1

    def test_결과_개수를_상한까지만(self):
        results = [{"url": f"https://{i}.com", "title": f"t{i}"} for i in range(10)]
        out = extract_sources("web_search", self._tavily(results))
        assert len(out) == MAX_SOURCES_PER_TOOL

    def test_JSON이_아니면_빈_결과(self):
        """Tavily 응답 형태가 바뀌어도 예외 없이 출처만 생략해야 한다."""
        assert extract_sources("web_search", _ToolMessage(content="not json")) == []

    def test_results가_없으면_빈_결과(self):
        assert extract_sources("web_search", _ToolMessage(content='{"query":"q"}')) == []


class TestFormatWebResults:
    """web_search 도구가 LLM에 넘기는 형태. 출처 추출이 이 형태를 파싱한다."""

    # 실제 Tavily 응답에서 확인한 필드 구성
    RAW = {
        "query": "봉준호 신작",
        "follow_up_questions": None,
        "answer": None,
        "images": [],
        "results": [
            {
                "url": "https://a.com/news",
                "title": "봉준호 신작 소식",
                "content": "본문 발췌",
                "score": 0.91,
                "raw_content": None,
                "id": "r1",
            }
        ],
        "response_time": 1.23,
        "request_id": "abc",
    }

    def test_출처_추출과_형태가_맞물린다(self):
        """만드는 쪽과 읽는 쪽이 어긋나면 에러 없이 출처 칩만 사라진다. 그 회귀를 잡는다."""
        out = extract_sources("web_search", _ToolMessage(content=format_web_results(self.RAW)))
        assert out == [{"tool": "web_search", "label": "봉준호 신작 소식", "url": "https://a.com/news"}]

    def test_답변에_쓰이지_않는_필드는_버린다(self):
        """score·raw_content·images·response_time 등은 LLM 입력 토큰만 늘린다."""
        data = json.loads(format_web_results(self.RAW))
        assert list(data) == ["results"]
        assert list(data["results"][0]) == ["title", "url", "content"]

    def test_긴_본문은_상한까지_자른다(self):
        raw = {"results": [{"url": "https://a.com", "title": "t", "content": "가" * 5000}]}
        data = json.loads(format_web_results(raw))
        assert len(data["results"][0]["content"]) == WEB_MAX_CONTENT_CHARS

    def test_URL이_없는_결과는_건너뛴다(self):
        raw = {"results": [{"title": "URL 없음"}, {"url": "https://a.com", "title": "t"}]}
        data = json.loads(format_web_results(raw))
        assert [r["url"] for r in data["results"]] == ["https://a.com"]

    @pytest.mark.parametrize("raw", [{}, {"results": []}, {"results": "x"}, None, "not dict"])
    def test_쓸_결과가_없으면_None(self, raw):
        """None이면 도구가 '결과 없음' 문장을 반환한다. 빈 JSON을 LLM에 넘기지 않는다."""
        assert format_web_results(raw) is None


class TestKobisSources:
    def test_기관명을_고정_출처로_붙인다(self):
        """KOBIS 도구는 문자열만 반환해서 URL이 없다. 기관 홈페이지로 대체한다."""
        out = extract_sources("kobis_search", _ToolMessage(content="일별 박스오피스 ..."))
        assert len(out) == 1
        assert out[0]["url"] == KOBIS_HOME

    def test_도구가_에러를_반환하면_출처로_내보내지_않는다(self):
        """실패한 호출은 답변의 근거가 아니다. 출처로 표시하면 사용자를 오도한다."""
        err = "[TOOL_ERROR code=NETWORK_ERROR] KOBIS API 호출에 실패했습니다."
        assert extract_sources("kobis_search", _ToolMessage(content=err)) == []


class TestUnknownTool:
    def test_모르는_도구는_빈_결과(self):
        """도구가 추가돼도 출처 로직이 예외를 던지지 않고 조용히 건너뛴다."""
        assert extract_sources("brand_new_tool", _ToolMessage(content="x")) == []
