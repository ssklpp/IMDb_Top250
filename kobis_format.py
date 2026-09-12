"""KOBIS 응답 가공과 도구 에러 포맷 — 순수 함수만 모은 모듈.

`agent.py`에서 분리한 이유: agent.py는 import만 해도 벡터스토어를 로드하고
OpenAI 클라이언트를 생성한다. 그 부작용 때문에 단위 테스트가 느려지고
API 키를 요구하게 되므로, 외부 의존이 없는 로직을 여기로 옮겼다.
이 모듈은 표준 라이브러리만 사용하므로 `pytest tests/unit/`가 1초 안에 끝난다.
"""

from datetime import datetime

# detail 모드 출력 상한. KOBIS는 actors를 90건, staffs를 625건까지 반환하므로
# 그대로 넣으면 LLM 컨텍스트를 잡아먹는다. staffs는 아예 제외한다.
DETAIL_MAX_ACTORS = 8
DETAIL_MAX_COMPANIES = 2
DETAIL_MAX_CANDIDATES = 3


def tool_error(code: str, message: str) -> str:
    """LLM에 반환되는 표준 에러 포맷. LLM이 코드를 보고 재시도/우회 결정 가능."""
    return f"[TOOL_ERROR code={code}] {message}"


def fmt_date(v: str) -> str:
    """'20190530' → '2019-05-30'. 8자리 숫자가 아니면 원본 그대로."""
    return f"{v[:4]}-{v[4:6]}-{v[6:]}" if len(v) == 8 and v.isdigit() else v


def is_valid_date(v: str) -> bool:
    """실제 달력에 존재하는 YYYYMMDD 8자리인지 검사.

    두 가지를 모두 봐야 한다:
    - 8자리 숫자인가 — strptime만으로는 부족하다. strptime은 제로 패딩에 관대해서
      '2026091'(7자리)을 2026-09-01로 받아준다.
    - 달력에 존재하는가 — 8자리 검사만으로는 부족하다. KOBIS는 '99999999' 같은
      값에도 에러 대신 엉뚱한 데이터를 반환하므로 호출 전에 걸러야 한다.
    """
    if len(v) != 8 or not v.isdigit():
        return False
    try:
        datetime.strptime(v, "%Y%m%d")
        return True
    except ValueError:
        return False


def format_show_range(show_range: str) -> str:
    """'20260831~20260906' → '2026-08-31~2026-09-06'. 시작=종료면 한 날짜로 축약."""
    parts = [p for p in show_range.split("~") if len(p) == 8]
    if not parts:
        return ""
    fmt = [fmt_date(p) for p in parts]
    return fmt[0] if len(set(fmt)) == 1 else "~".join(fmt)


def pick_movie(movies: list[dict], title: str) -> dict:
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


def format_movie_info(info: dict) -> str:
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

    open_dt = fmt_date(info.get("openDt", ""))
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
