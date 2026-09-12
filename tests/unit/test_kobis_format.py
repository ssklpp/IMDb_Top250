"""kobis_format 순수 함수 단위 테스트.

외부 API도 LLM도 호출하지 않으므로 1초 안에 끝나고 API 키가 필요 없다.
(대조: tests/evals는 에이전트를 실제로 호출해 비용과 시간이 든다)

대부분의 케이스는 실제 KOBIS 응답에서 발견한 문제를 그대로 옮긴 것이다.
각 테스트가 어떤 실패를 막는지 docstring에 적어두었다.
"""

import pytest

from kobis_format import (
    DETAIL_MAX_ACTORS,
    fmt_date,
    format_movie_info,
    format_show_range,
    is_valid_date,
    pick_movie,
    tool_error,
)


class TestToolError:
    def test_표준_포맷을_따른다(self):
        """SYSTEM_PROMPT가 이 포맷을 파싱하도록 지시하므로 형태가 바뀌면 안 된다."""
        assert tool_error("TIMEOUT", "시간 초과") == "[TOOL_ERROR code=TIMEOUT] 시간 초과"


class TestFmtDate:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("20190530", "2019-05-30"),
            ("20260101", "2026-01-01"),
        ],
    )
    def test_8자리_숫자를_하이픈_형식으로(self, raw, expected):
        assert fmt_date(raw) == expected

    @pytest.mark.parametrize("raw", ["", "2019", "2019053", "abcdefgh", "개봉예정"])
    def test_8자리_숫자가_아니면_원본_유지(self, raw):
        """KOBIS는 openDt를 빈 문자열로 주는 경우가 흔하다. 그대로 통과해야 한다."""
        assert fmt_date(raw) == raw


class TestIsValidDate:
    @pytest.mark.parametrize("raw", ["20260901", "20240229", "19000101"])
    def test_실제_존재하는_날짜(self, raw):
        assert is_valid_date(raw) is True

    @pytest.mark.parametrize(
        "raw,why",
        [
            ("99999999", "KOBIS가 에러 대신 엉뚱한 데이터를 반환하던 값"),
            ("20261301", "13월"),
            ("20260931", "9월 31일"),
            ("20230229", "평년의 2월 29일"),
            ("2026091", "7자리"),
            ("abc", "숫자가 아님"),
            ("", "빈 문자열"),
        ],
    )
    def test_존재하지_않는_날짜는_거부(self, raw, why):
        """8자리 숫자 검사만으로는 '99999999'를 못 걸러서 실제로 통과시켰던 회귀."""
        assert is_valid_date(raw) is False, why


class TestFormatShowRange:
    def test_기간을_하이픈_형식으로(self):
        assert format_show_range("20260831~20260906") == "2026-08-31~2026-09-06"

    def test_시작과_종료가_같으면_한_날짜로_축약(self):
        """일별 박스오피스는 showRange가 같은 날짜 두 번이라 중복 표기를 피한다."""
        assert format_show_range("20260901~20260901") == "2026-09-01"

    @pytest.mark.parametrize("raw", ["", "invalid", "~"])
    def test_파싱_불가하면_빈_문자열(self, raw):
        assert format_show_range(raw) == ""


class TestPickMovie:
    """KOBIS 목록은 관련도순이 아니라서 1위가 정답이 아닌 경우가 많다.

    아래 케이스는 전부 실제 API 응답에서 확인한 것이다.
    """

    def test_정확_일치가_부분_일치를_이긴다(self):
        """'기생충' 검색 시 KOBIS 1위는 '마약 기생충'이었다."""
        movies = [
            {"movieNm": "마약 기생충", "movieCd": "20207361", "prdtStatNm": "기타"},
            {
                "movieNm": "기생충",
                "movieCd": "20183782",
                "prdtStatNm": "개봉",
                "openDt": "20190530",
                "repNationNm": "한국",
            },
            {"movieNm": "사랑하는 기생충", "movieCd": "20236433", "prdtStatNm": "개봉"},
        ]
        assert pick_movie(movies, "기생충")["movieCd"] == "20183782"

    def test_접미사가_붙은_제목보다_정확_일치_우선(self):
        """'부산행' 검색 시 1위는 '부산행:익스텐디드'였다."""
        movies = [
            {"movieNm": "부산행:익스텐디드", "movieCd": "20200846", "prdtStatNm": "개봉"},
            {
                "movieNm": "부산행",
                "movieCd": "20156564",
                "prdtStatNm": "개봉",
                "openDt": "20160720",
                "repNationNm": "한국",
            },
        ]
        assert pick_movie(movies, "부산행")["movieCd"] == "20156564"

    def test_정확_일치가_둘이면_한국_제작을_고른다(self):
        """이 규칙이 없으면 '올드보이'가 박찬욱(2003)이 아니라 스파이크 리(2013)로 해석된다.

        최신 개봉일만 보면 미국 리메이크(2014 국내개봉)가 이겨버린다.
        """
        movies = [
            {
                "movieNm": "올드보이",
                "movieCd": "20030350",
                "prdtStatNm": "개봉",
                "openDt": "20031121",
                "repNationNm": "한국",
            },
            {
                "movieNm": "올드보이",
                "movieCd": "20136853",
                "prdtStatNm": "개봉",
                "openDt": "20140116",
                "repNationNm": "미국",
            },
        ]
        assert pick_movie(movies, "올드보이")["movieCd"] == "20030350"

    def test_개봉작을_미개봉작보다_우선(self):
        movies = [
            {"movieNm": "영화", "movieCd": "1", "prdtStatNm": "기타"},
            {"movieNm": "영화", "movieCd": "2", "prdtStatNm": "개봉", "openDt": "20200101"},
        ]
        assert pick_movie(movies, "영화")["movieCd"] == "2"

    def test_공백과_대소문자를_무시하고_일치시킨다(self):
        movies = [
            {"movieNm": "기생충 스페셜", "movieCd": "1", "prdtStatNm": "개봉"},
            {"movieNm": "Old Boy", "movieCd": "2", "prdtStatNm": "개봉"},
        ]
        assert pick_movie(movies, "oldboy")["movieCd"] == "2"

    def test_정확_일치가_없으면_전체에서_고른다(self):
        """후보가 하나도 정확히 안 맞아도 빈 결과를 내지 않고 최선을 고른다."""
        movies = [
            {"movieNm": "전혀 다른 영화", "movieCd": "1", "prdtStatNm": "개봉", "openDt": "20200101"},
        ]
        assert pick_movie(movies, "기생충")["movieCd"] == "1"

    def test_개봉일이_모두_비어도_예외가_나지_않는다(self):
        """max()의 key가 None이면 TypeError가 난다. 빈 문자열 폴백이 있어야 한다."""
        movies = [
            {"movieNm": "영화", "movieCd": "1", "prdtStatNm": "기타"},
            {"movieNm": "영화", "movieCd": "2", "prdtStatNm": "기타"},
        ]
        assert pick_movie(movies, "영화")["movieCd"] == "1"


class TestFormatMovieInfo:
    @staticmethod
    def _info(**overrides):
        base = {
            "movieNm": "기생충",
            "movieNmEn": "PARASITE",
            "prdtYear": "2019",
            "openDt": "20190530",
            "prdtStatNm": "개봉",
            "showTm": "131",
            "genres": [{"genreNm": "드라마"}],
            "nations": [{"nationNm": "한국"}],
            "audits": [{"watchGradeNm": "15세이상관람가"}],
            "directors": [{"peopleNm": "봉준호"}],
            "actors": [{"peopleNm": "송강호", "cast": "기택"}],
            "companys": [
                {"companyNm": "(주)바른손이앤에이", "companyPartNm": "제작사"},
                {"companyNm": "(주)씨제이이엔엠", "companyPartNm": "배급사"},
            ],
        }
        base.update(overrides)
        return base

    def test_핵심_정보를_포함한다(self):
        out = format_movie_info(self._info())
        for expected in [
            "기생충 (PARASITE)",
            "131분",
            "15세이상관람가",
            "봉준호",
            "송강호(기택)",
            "2019-05-30",
        ]:
            assert expected in out

    def test_배우가_많으면_잘라내고_나머지는_개수로_요약한다(self):
        """KOBIS는 actors를 90건까지 반환한다. 그대로 넣으면 LLM 컨텍스트를 잡아먹는다."""
        actors = [{"peopleNm": f"배우{i}", "cast": f"배역{i}"} for i in range(90)]
        out = format_movie_info(self._info(actors=actors))

        assert "배우0(배역0)" in out
        assert f"배우{DETAIL_MAX_ACTORS - 1}" in out
        assert f"배우{DETAIL_MAX_ACTORS}(" not in out
        assert f"외 {90 - DETAIL_MAX_ACTORS}명" in out

    def test_staffs는_출력에_넣지_않는다(self):
        """staffs는 625건까지 오고 'VFX 아티스트' 같은 잡무 위주라 답변 가치가 없다."""
        staffs = [{"peopleNm": "홍길동", "staffRoleNm": "VFX 아티스트"}] * 625
        out = format_movie_info(self._info(staffs=staffs))
        assert "홍길동" not in out
        assert "VFX" not in out

    def test_제작사와_배급사만_남기고_나머지_역할은_제외(self):
        """companys에는 '제공', '해외세일즈사', '가치봄 제작처' 등 노이즈가 섞여 있다."""
        companys = [
            {"companyNm": "제작사A", "companyPartNm": "제작사"},
            {"companyNm": "배급사B", "companyPartNm": "배급사"},
            {"companyNm": "해외세일즈C", "companyPartNm": "해외세일즈사"},
            {"companyNm": "가치봄D", "companyPartNm": "가치봄 제작처"},
        ]
        out = format_movie_info(self._info(companys=companys))
        assert "제작사A" in out
        assert "배급사B" in out
        assert "해외세일즈C" not in out
        assert "가치봄D" not in out

    def test_배역명이_없으면_이름만_표기한다(self):
        out = format_movie_info(self._info(actors=[{"peopleNm": "공유", "cast": ""}]))
        assert "공유" in out
        assert "공유()" not in out

    def test_없는_필드는_줄_자체를_생략한다(self):
        out = format_movie_info({"movieNm": "제목만"})
        assert out == "제목만"

    def test_상영타입은_중복을_제거한다(self):
        show_types = [
            {"showTypeGroupNm": "2D"},
            {"showTypeGroupNm": "2D"},
            {"showTypeGroupNm": "IMAX"},
        ]
        out = format_movie_info(self._info(showTypes=show_types))
        assert "2D, IMAX" in out
