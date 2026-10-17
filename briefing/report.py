"""브리핑의 숫자 부분 — 전주 비교, LLM 초안 검증, 최종 문장 조립. 표준 라이브러리만 쓰는 순수 함수라
tests/unit에서 저장해 둔 KOBIS 응답(tests/fixtures)으로 검증한다.

원칙: **숫자는 모델이 쓰지 않는다.** 순위·관객 수·증감은 이 모듈이 원자료로 계산해 그대로 찍고, 모델은
헤드라인과 짧은 코멘트만 쓴다. 모델 글에는 숫자를 아예 못 쓰게 한다(validate_draft). 그래야 "70만 명"처럼
그럴듯하지만 틀린 숫자가 발송될 길이 없다. 이 챗봇은 실제로 1년 전 박스오피스를 "지난 주"라고 답한 적이 있다.
"""

import re

DISCORD_LIMIT = 2000  # Discord 메시지 한 개의 글자 상한
MAX_HEADLINE = 80
MAX_COMMENT = 120
MAX_SUMMARY = 200
_DIGIT = re.compile(r"\d")
# 표는 주간(월~일) 집계다. 모델이 "주말 극장가"처럼 단위가 다른 틀을 지어낸 적이 있다(실측, 10번 중 1번).
_WRONG_UNIT = re.compile(r"주말|개봉 첫날|하루 만에")


def compare(this_week: dict, prev_week: dict | None) -> dict:
    """이번 주 각 영화에 전주 대비 정보를 붙인다. prev_week가 없으면(전주 조회 실패) 비교 칸이 비어 있다.

    entered: 전주 10위 안에 없다가 들어옴. 개봉 첫 주(new_release)와 다르다 — 재개봉·역주행도 여기 들어간다.
    rank_change: 양수면 오름(전주 순위 - 이번 순위). KOBIS의 rankInten과 같아야 한다(테스트가 확인).
    """
    prev = {m["movie_cd"]: m for m in (prev_week or {}).get("items", [])}
    items = []
    for m in this_week["items"]:
        p = prev.get(m["movie_cd"])
        items.append({
            **m,
            "prev_rank": p["rank"] if p else None,
            "rank_change": p["rank"] - m["rank"] if p else None,
            "audience_change_pct": round((m["audience"] - p["audience"]) / p["audience"] * 100, 1)
            if p and p["audience"] else None,
            "entered": prev_week is not None and p is None,
        })
    this_codes = {m["movie_cd"] for m in this_week["items"]}
    dropped = [m["title"] for m in (prev_week or {}).get("items", []) if m["movie_cd"] not in this_codes]
    return {"start": this_week["start"], "end": this_week["end"], "items": items, "dropped": dropped}


def change_label(m: dict) -> str:
    if m["new_release"]:
        return "신작"
    if m["entered"]:
        return "순위권 진입"
    if m["rank_change"] is None:
        return ""
    if m["rank_change"] > 0:
        return f"▲{m['rank_change']}"
    if m["rank_change"] < 0:
        return f"▼{-m['rank_change']}"
    return "-"


def facts_for_prompt(report: dict) -> str:
    """모델에 줄 표. 모델은 이걸 읽고 코멘트만 쓴다."""
    lines = [f"집계 기간: {report['start']} ~ {report['end']}"]
    for m in report["items"]:
        pct = m["audience_change_pct"]
        lines.append(
            f"{m['rank']}위 | {m['title']} | 관객 {m['audience']:,}명 | 누적 {m['audience_acc']:,}명 | "
            f"전주 대비 {change_label(m) or '정보 없음'}" + (f" | 관객 증감 {pct:+.1f}%" if pct is not None else "")
        )
    if report["dropped"]:
        lines.append("전주 10위 안에 있다가 빠진 영화: " + ", ".join(report["dropped"]))
    return "\n".join(lines)


def check_period(report: dict, week_start: str, week_end: str) -> str | None:
    """KOBIS가 준 집계 기간이 브리핑 대상 주와 같은지. 다르면 이유를 준다."""
    if (report["start"], report["end"]) != (week_start, week_end):
        return f"집계 기간이 {report['start']}~{report['end']}로, 대상 주 {week_start}~{week_end}와 다릅니다."
    return None


def validate_draft(draft: dict, report: dict) -> list[str]:
    """모델 초안의 문제 목록. 비어 있으면 통과. 문제 문장은 그대로 모델에 되돌려 고치게 한다."""
    problems: list[str] = []
    titles = {m["rank"]: m["title"] for m in report["items"]}

    def check_text(label: str, text, limit: int):
        if not isinstance(text, str) or not text.strip():
            problems.append(f"{label}이(가) 비어 있습니다.")
            return
        if len(text) > limit:
            problems.append(f"{label}이(가) {limit}자를 넘습니다.")
        if _DIGIT.search(text):
            problems.append(f"{label}에 숫자가 있습니다. 숫자는 표에만 쓰니 글에서는 빼세요: {text!r}")
        if _WRONG_UNIT.search(text):
            problems.append(f"{label}에 주간 집계와 맞지 않는 표현이 있습니다('주말' 등): {text!r}")

    check_text("헤드라인", draft.get("headline"), MAX_HEADLINE)
    check_text("요약", draft.get("summary"), MAX_SUMMARY)
    for c in draft.get("comments") or []:
        rank, title = c.get("rank"), c.get("title")
        if rank not in titles:
            problems.append(f"{rank}위는 이번 주 순위에 없습니다.")
        elif title != titles[rank]:
            problems.append(f"{rank}위 제목이 '{title}'인데 원자료는 '{titles[rank]}'입니다.")
        check_text(f"{rank}위 코멘트", c.get("comment"), MAX_COMMENT)
    return problems


def render(report: dict, draft: dict) -> str:
    """발송할 최종 문장. 숫자는 전부 여기서 원자료로 찍는다."""
    comments = {c["rank"]: c["comment"].strip() for c in draft.get("comments") or []}
    lines = [f"**주간 박스오피스** {report['start']} ~ {report['end']}", draft["headline"].strip(), ""]
    for m in report["items"]:
        label = change_label(m)
        line = f"{m['rank']}위 {m['title']} — {m['audience']:,}명 (누적 {m['audience_acc']:,}명)"
        if label:
            line += f" {label}"
        lines.append(line)
        if m["rank"] in comments:
            lines.append(f"  └ {comments[m['rank']]}")
    lines += ["", draft["summary"].strip(), "", "자료: 영화진흥위원회 KOBIS"]
    text = "\n".join(lines)
    return text if len(text) <= DISCORD_LIMIT else text[: DISCORD_LIMIT - 1] + "…"
