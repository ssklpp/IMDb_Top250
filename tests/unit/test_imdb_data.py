"""imdb_data 단위 테스트 + 골든셋 근거 검사.

마지막 클래스가 핵심이다. PDF가 잘려 코퍼스에 'Shawshank'가 한 번도 없었는데도 평가는
15/15 통과했다. 모델이 사전지식으로 답하고 키워드 검사가 그걸 통과시켰기 때문이다.
평가는 "답이 맞는가"만 보고 "답이 코퍼스에서 왔는가"는 보지 않는다. 그 구멍을 여기서 막는다.
"""

import json
from pathlib import Path

import pytest

from imdb_data import (
    CSV_PATH,
    SQL_MAX_CHARS,
    SQL_MAX_ROWS,
    SqlRejected,
    corpus_digest,
    movie_docs,
    query_movies,
)

GOLDEN_PATH = Path(__file__).resolve().parents[2] / "tests" / "evals" / "golden_dataset.json"

DOCS = movie_docs()
BY_RANK = {meta["rank"]: (text, meta) for text, meta in DOCS}


class TestMovieDocs:
    def test_250편을_순위대로_읽는다(self):
        """CSV는 크롤러가 BOM을 붙여 저장했다. utf-8로 읽으면 첫 칼럼이 '\\ufeffrank'가 돼 KeyError가 난다."""
        assert [meta["rank"] for _, meta in DOCS] == list(range(1, 251))

    def test_HTML_엔티티를_복원한다(self):
        """크롤링 결과에 'Schindler&apos;s List'가 그대로 들어 있다. 그대로 두면 'Schindler's'로 검색이 안 된다."""
        text, meta = BY_RANK[7]
        assert meta["title"] == "Schindler's List"
        assert "&apos;" not in text

    def test_검색_본문에_답에_필요한_칸이_모두_들어간다(self):
        """검색 도구는 page_content만 LLM에 넘긴다. 감독을 메타데이터에만 두면 모델이 볼 수 없다."""
        text, _ = BY_RANK[1]
        for expected in ("The Shawshank Redemption", "1994", "rank 1", "9.3", "Frank Darabont", "Morgan Freeman", "uxoricide"):
            assert expected in text

    def test_N_A인_칸은_본문에_넣지_않는다(self):
        """237위 'The Wizard of Oz'의 감독이 'N/A'다. 'Director: N/A'를 넣으면 모델이 감독 미상이라고 답한다."""
        text, _ = BY_RANK[237]
        assert "N/A" not in text
        assert "Director" not in text

    def test_제목의_숫자를_연도로_읽지_않았다(self):
        """크롤러가 제목 앞 숫자를 연도로 읽어 '2001: A Space Odyssey'가 2001년, '1917'이 1917년으로
        들어와 있었다(실제 1968, 2019). "2001년 영화"를 물으면 큐브릭 영화가 나왔다. 크롤링을 다시
        가져올 때 같은 오류가 섞이면 여기서 걸린다."""
        assert BY_RANK[100][1]["year"] == "1968"
        assert BY_RANK[121][1]["year"] == "2019"
        suspicious = [m["title"] for _, m in DOCS if m["title"][:4].isdigit() and m["title"][:4] == m["year"]]
        assert suspicious == []

    def test_메타데이터는_출처_칩이_쓰는_형태다(self):
        """sources._imdb_sources()가 rank와 title로 칩을 만든다. 키가 바뀌면 칩만 조용히 사라진다."""
        _, meta = BY_RANK[1]
        assert meta == {"rank": 1, "title": "The Shawshank Redemption", "year": "1994"}


class TestCorpusDigest:
    def test_한_칸만_바뀌어도_지문이_바뀐다(self):
        """지문이 그대로면 옛 인덱스를 계속 쓴다. 고정 이름을 쓰던 시절 배포만 87청크로 남아 있었다."""
        changed = [(text, dict(meta)) for text, meta in DOCS]
        changed[0][1]["year"] = "1995"
        assert corpus_digest(changed) != corpus_digest(DOCS)

    def test_줄바꿈_방식이_달라도_지문이_같다(self, tmp_path):
        """git autocrlf로 Windows는 CRLF, Railway는 LF로 받는다. 바이트로 해시하면 배포 때마다 다시 임베딩한다."""
        raw = CSV_PATH.read_text(encoding="utf-8-sig").replace("\r\n", "\n")
        lf, crlf = tmp_path / "lf.csv", tmp_path / "crlf.csv"
        lf.write_bytes(raw.encode("utf-8"))
        crlf.write_bytes(raw.replace("\n", "\r\n").encode("utf-8"))
        assert corpus_digest(movie_docs(lf)) == corpus_digest(movie_docs(crlf))


def _rows(sql):
    text, _ = query_movies(sql)
    return [line.split(" | ") for line in text.splitlines()[1:]]


class TestQueryMovies:
    def test_평점_1위를_정렬로_찾는다(self):
        """벡터 검색은 8편 안에서 골라 'The Dark Knight'(9.1)라고 답했다. 정답은 250편 전체의 최댓값이다."""
        assert _rows("SELECT title, rating FROM movies ORDER BY rating DESC LIMIT 1") == [["The Shawshank Redemption", "9.3"]]

    def test_집계를_한다(self):
        """'몇 편' 같은 질문은 검색된 문서를 세어서는 답할 수 없다(k=8이 상한)."""
        assert _rows("SELECT COUNT(*) FROM movies WHERE year BETWEEN 1990 AND 1999") == [["39"]]

    def test_평점과_연도는_숫자로_비교된다(self):
        """TEXT로 넣으면 '9' > '8.8'이 사전순으로 비교돼 정렬이 틀린다."""
        assert _rows("SELECT typeof(rating), typeof(year), typeof(rank) FROM movies LIMIT 1") == [["real", "integer", "integer"]]

    def test_배우_칼럼을_SELECT할_수_있다(self):
        """CSV 칼럼명 'cast'는 SQL 키워드라 SELECT title, cast FROM movies가 syntax error였다. 'actors'로 바꿨다."""
        assert _rows("SELECT title, actors FROM movies WHERE rank = 1") == [["The Shawshank Redemption", "Tim Robbins, Morgan Freeman, Bob Gunton"]]

    def test_N_A_감독은_NULL이다(self):
        """'N/A' 문자열로 두면 COUNT(DISTINCT director)나 감독별 집계에 'N/A'라는 감독이 생긴다."""
        assert _rows("SELECT title FROM movies WHERE director IS NULL") == [["The Wizard of Oz"]]

    def test_결과가_없으면_영어로_찾으라고_안내한다(self):
        """가장 흔한 실패는 LIKE '%놀란%'처럼 한국어로 찾는 것이다. 빈 결과만 주면 모델이 '없다'고 답한다."""
        text, n = query_movies("SELECT title FROM movies WHERE director LIKE '%놀란%'")
        assert n == 0
        assert "영어" in text

    def test_결과를_상한까지만_돌려준다(self):
        """SELECT * FROM movies 한 번에 250행이 LLM 입력 토큰이 되지 않게 자른다."""
        text, n = query_movies("SELECT title FROM movies")
        assert n == SQL_MAX_ROWS
        assert "LIMIT" in text

    @pytest.mark.parametrize(
        "sql",
        [
            "INSERT INTO movies(title) VALUES ('x')",
            "DROP TABLE movies",
            "PRAGMA table_info(movies)",
            "SELECT 1; DROP TABLE movies",
            "SELECT length(randomblob(1000000000))",
            "SELECT COUNT(*) FROM movies a, movies b, movies c",
            "WITH RECURSIVE r(n) AS (SELECT 1 UNION ALL SELECT n + 1 FROM r) SELECT n FROM r",
            "SELECT load_extension('x')",
            "",
        ],
        ids=["insert", "drop", "pragma", "두_문장", "메모리_폭탄", "3중_조인", "무한_재귀", "확장_로드", "빈_문자열"],
    )
    def test_읽기_외의_SQL은_거부한다(self, sql):
        """SQL은 모델이 쓴다. 웹 검색 결과에 섞인 지시로 모델이 무엇을 쓸지 모르므로 실행 전에 막는다.
        메모리 폭탄(1GB 할당)과 3중 조인(1,500만 행)은 서버를 멈출 수 있다."""
        with pytest.raises(SqlRejected):
            query_movies(sql)

    def test_ATTACH로_파일을_만들지_못한다(self, tmp_path):
        """ATTACH DATABASE는 없는 파일을 새로 만든다. 서버 디스크에 임의 파일이 생기면 안 된다."""
        target = tmp_path / "evil.db"
        with pytest.raises(SqlRejected):
            query_movies(f"ATTACH DATABASE '{target}' AS e")
        assert not target.exists()

    def test_너무_긴_SQL은_실행하지_않는다(self):
        with pytest.raises(SqlRejected):
            query_movies("SELECT 1" + " " * SQL_MAX_CHARS)


GOLDEN = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
SEARCH_ITEMS = [it for it in GOLDEN if "imdb_search" in it.get("expected_tools", [])]
EVIDENCE_ITEMS = [it for it in GOLDEN if "corpus_evidence" in it]


class TestGoldenCorpus:
    def test_검사할_항목이_있다(self):
        """아래 parametrize가 0건이면 테스트가 통과한 것처럼 보이지만 아무것도 검사하지 않는다."""
        assert SEARCH_ITEMS and EVIDENCE_ITEMS

    @pytest.mark.parametrize("item", SEARCH_ITEMS, ids=lambda it: it["id"])
    def test_imdb_search_항목은_근거를_적는다(self, item):
        """imdb_search를 기대하는 항목은 답의 근거가 코퍼스에 있어야 한다.

        없으면 그 항목은 검색이 아니라 모델의 사전지식을 채점한다. 잘린 PDF 시절 imdb-001~003이
        정확히 그 상태로 통과했고, 답변에는 PDF 쪽번호 출처까지 붙어 거짓 인용이 됐다.
        (imdb_sql 항목은 강제하지 않는다. "1990년대 몇 편"의 답 39는 이 CSV에서만 나오는 값이라
        모델이 기억으로 맞힐 수 없고, 키워드 검사가 곧 도구를 썼다는 증거다.)
        """
        assert item.get("corpus_evidence"), f"{item['id']}: corpus_evidence가 없다. 코퍼스에서 답을 찾을 수 있다는 근거를 적을 것"

    @pytest.mark.parametrize("item", EVIDENCE_ITEMS, ids=lambda it: it["id"])
    def test_근거가_실제로_코퍼스에_있다(self, item):
        corpus = "\n\n".join(text for text, _ in DOCS)
        missing = [e for e in item["corpus_evidence"] if e not in corpus]
        assert not missing, f"{item['id']}: 코퍼스에 없음 {missing}"
