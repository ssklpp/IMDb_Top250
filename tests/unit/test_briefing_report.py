"""briefing.report 단위 테스트 — 저장해 둔 실제 KOBIS 주간 응답 두 개(tests/fixtures)로 검증한다.

브리핑은 사람이 승인하지만, 승인하는 사람이 숫자를 원자료와 일일이 대조하지는 않는다. 그래서 숫자는 코드가 찍고
모델 글에는 숫자를 못 쓰게 했다. 여기서는 그 계산과 검증이 맞는지 본다. API·LLM은 부르지 않는다.
"""

import json
from pathlib import Path

from briefing.report import (
    DISCORD_LIMIT,
    change_label,
    check_period,
    compare,
    facts_for_prompt,
    render,
    validate_draft,
)
from kobis_format import parse_weekly

FIX = Path(__file__).resolve().parents[1] / "fixtures"


def load(d):
    return parse_weekly(json.loads((FIX / f"kobis_weekly_{d}.json").read_text(encoding="utf-8")))


THIS, PREV = load("20261011"), load("20261004")
REPORT = compare(THIS, PREV)
GOOD = {
    "headline": "암살자(들)이 정상을 지킨 가운데 룩백이 순위권으로 뛰어올랐다",
    "comments": [{"rank": 6, "title": "룩백", "comment": "전주 순위권 밖에서 단숨에 올라온 역주행"}],
    "summary": "상위권은 그대로였고 중하위권에 변화가 컸다.",
}


def by_title(t):
    return next(m for m in REPORT["items"] if m["title"] == t)


def test_KOBIS_응답을_읽는다():
    assert (THIS["start"], THIS["end"]) == ("2026-10-05", "2026-10-11")
    assert THIS["items"][0]["title"] == "암살자(들)" and THIS["items"][0]["audience"] == 759106
    assert len(THIS["items"]) == 10


def test_형태가_다른_응답은_None():
    """KOBIS가 에러 형식(faultInfo)이나 빈 목록을 주면 브리핑을 만들지 않는다."""
    assert parse_weekly({"faultInfo": {"message": "x"}}) is None
    assert parse_weekly({"boxOfficeResult": {"showRange": "20261005~20261011", "weeklyBoxOfficeList": []}}) is None


def test_직접_계산한_순위_변동이_KOBIS_값과_같다():
    """전주 비교를 직접 계산한다. 두 주 모두 10위 안에 있던 영화는 KOBIS가 준 rankInten과 같아야 한다 —
    다르면 계산이 틀렸거나 엉뚱한 주를 비교한 것이다."""
    both = [m for m in REPORT["items"] if m["prev_rank"] is not None]
    assert len(both) >= 5
    for m in both:
        assert m["rank_change"] == m["kobis_rank_change"], m["title"]


def test_관객_증감률이_KOBIS_값과_같다():
    raw = json.loads((FIX / "kobis_weekly_20261011.json").read_text(encoding="utf-8"))
    kobis = {m["movieCd"]: float(m["audiChange"]) for m in raw["boxOfficeResult"]["weeklyBoxOfficeList"]}
    for m in REPORT["items"]:
        if m["audience_change_pct"] is not None:
            assert abs(m["audience_change_pct"] - kobis[m["movie_cd"]]) <= 0.1, m["title"]


def test_신작과_순위권_진입을_구분한다():
    """룩백은 KOBIS상 OLD(개봉 첫 주 아님)지만 전주 10위 밖이었다. '신작'이라 쓰면 틀린 말이 된다."""
    assert change_label(by_title("입에 대한 앙케트")) == "신작"
    assert change_label(by_title("룩백")) == "순위권 진입"
    assert change_label(by_title("암살자(들)")) == "-"
    assert change_label(by_title("디거")) == "▲2"


def test_빠진_영화를_찾는다():
    assert set(REPORT["dropped"]) == {"가능한 사랑", "어벤져스: 엔드게임 앙코르"}


def test_전주가_없으면_비교_칸이_빈다():
    """전주 조회가 실패해도 이번 주 브리핑은 만들 수 있어야 한다. 대신 '진입'으로 잘못 표시하면 안 된다."""
    r = compare(THIS, None)
    assert all(m["prev_rank"] is None and not m["entered"] for m in r["items"])


def test_집계_기간이_대상_주와_다르면_막는다():
    """'지난 주'를 1년 전 데이터로 답한 적이 있다. KOBIS가 다른 주를 주면 브리핑을 보내지 않는다."""
    assert check_period(REPORT, "2026-10-05", "2026-10-11") is None
    assert "대상 주" in check_period(REPORT, "2025-10-06", "2025-10-12")


def test_정상_초안은_통과():
    assert validate_draft(GOOD, REPORT) == []


def test_모델_글에_숫자가_있으면_막는다():
    """숫자는 표에만 코드로 찍는다. 모델이 '70만 명'처럼 쓰면 틀려도 걸러낼 방법이 없다."""
    bad = {**GOOD, "summary": "암살자(들)이 70만 명을 넘겼다."}
    assert any("숫자" in p for p in validate_draft(bad, REPORT))


def test_주간_집계에_주말이라고_쓰면_막는다():
    """첫 실측에서 모델이 '주말 극장가에서 선두'라고 썼다. 표는 월~일 주간 집계라 틀린 말이다."""
    bad = {**GOOD, "comments": [{"rank": 1, "title": "암살자(들)", "comment": "주말 극장가에서 선두를 지켰다"}]}
    assert any("주말" in p for p in validate_draft(bad, REPORT))


def test_없는_순위나_틀린_제목은_막는다():
    bad = {**GOOD, "comments": [{"rank": 11, "title": "x", "comment": "좋다"}, {"rank": 6, "title": "룩 백", "comment": "좋다"}]}
    problems = validate_draft(bad, REPORT)
    assert any("11위" in p for p in problems) and any("'룩 백'" in p for p in problems)


def test_빈_헤드라인은_막는다():
    assert validate_draft({**GOOD, "headline": " "}, REPORT)


def test_최종_문장의_숫자는_원자료와_같다():
    text = render(REPORT, GOOD)
    assert "1위 암살자(들) — 759,106명 (누적 2,993,844명) -" in text
    assert "6위 룩백" in text and "순위권 진입" in text and "└ 전주 순위권 밖에서" in text
    assert "2026-10-05 ~ 2026-10-11" in text
    assert len(text) <= DISCORD_LIMIT


def test_모델에_주는_표에_비교_정보가_있다():
    facts = facts_for_prompt(REPORT)
    assert "6위 | 룩백" in facts and "순위권 진입" in facts and "가능한 사랑" in facts
