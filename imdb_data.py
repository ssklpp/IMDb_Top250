"""IMDB Top 250 CSV를 벡터 검색용 문서로 바꾼다.

원래는 같은 데이터를 표로 출력한 PDF를 PyMuPDF로 읽어 800자씩 잘랐다. 그런데 그 PDF는
칸 너비에서 글자를 잘라 그린 것이라 'The Shawshank Redemption'이 'The Shaws',
'Frank Darabont'가 'Frank Dara'로 추출됐다. 코퍼스에 'Shawshank'가 한 번도 없었고,
IMDB 평가 항목은 모델의 사전지식으로 통과하면서 PDF 쪽번호 출처까지 달고 있었다.
글자가 그려지기 전에 잘린 것이라 어떤 파서로 읽어도 복원되지 않아 원본 CSV로 바꿨다.

영화 한 편 = 문서 한 개. 행이 130~380자라 나눌 필요가 없고, 나누면 한 영화의 감독과
줄거리가 다른 조각으로 흩어진다.

같은 데이터를 SQL로도 조회한다(query_movies). 벡터 검색은 의미가 가까운 8편만 가져오므로
"평점 1위", "1990년대 몇 편" 같은 정렬·집계 질문에 답할 수 없다. CSV로 바꾼 뒤 평가를 돌리자
모델이 검색된 8편 안에서 최고 평점을 골라 'The Dark Knight'라고 틀리게 답했다.

외부 의존이 없는 순수 함수라 tests/unit에서 API 키 없이 검증한다.
"""

import csv
import hashlib
import html
import json
import sqlite3
from pathlib import Path

CSV_PATH = Path(__file__).resolve().parent / "imdb_top250.csv"

# 도구 설명과 에러 메시지에 그대로 넣는다. 모델이 칼럼 이름·타입을 보고 SQL을 쓴다.
# CSV의 'cast'를 'actors'로 바꿨다. cast는 SQL 키워드라 SELECT title, cast FROM movies가
# CAST(...) 문법으로 해석돼 syntax error가 난다.
SQL_SCHEMA = "movies(rank INTEGER, title TEXT, year INTEGER, rating REAL, director TEXT, actors TEXT, storyline TEXT)"
SQL_MAX_ROWS = 30
SQL_MAX_CHARS = 2000
# SQLite 가상 머신 명령 1,000개마다 진행 콜백이 불린다. 250행 테이블의 정상 질의는 수십 번이면
# 끝나고, 3중 조인(250³=1,500만 행) 같은 폭주만 이 상한에 걸린다.
SQL_MAX_PROGRESS_CALLS = 2_000
# 문자열·BLOB 길이 상한. randomblob(1000000000)으로 서버 메모리를 1GB 잡지 못하게 한다.
SQL_MAX_VALUE_BYTES = 1_000_000

_ALLOWED_ACTIONS = {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION}


class SqlRejected(Exception):
    """모델이 고쳐서 다시 보낼 수 있는 SQL 문제. 메시지가 그대로 모델에게 간다."""


def _rows(path: Path) -> list[dict]:
    # utf-8-sig: 크롤러가 BOM을 붙여 저장했다. utf-8로 읽으면 첫 칼럼명이 '﻿rank'가 된다.
    # html.unescape: 제목에 'Schindler&apos;s List' 같은 엔티티가 그대로 들어 있다.
    with open(path, encoding="utf-8-sig", newline="") as f:
        return [{k: html.unescape(v).strip() for k, v in r.items()} for r in csv.DictReader(f)]


def movie_docs(path: Path = CSV_PATH) -> list[tuple[str, dict]]:
    """(검색 본문, 메타데이터) 목록. 메타데이터는 출처 칩(sources._imdb_sources)이 쓴다."""
    return [(_text(r), {"rank": int(r["rank"]), "title": r["title"], "year": r["year"]}) for r in _rows(path)]


def query_movies(sql: str, path: Path = CSV_PATH) -> tuple[str, int]:
    """모델이 쓴 SELECT 한 문장을 영화 250편 테이블에 실행한다. (결과 텍스트, 행 수).

    웹 검색 결과에 섞인 지시(프롬프트 인젝션)로 모델이 무엇을 쓸지 모르므로 방어는 여기서 한다.
    - 요청마다 새 메모리 DB. 파일도, 요청 사이에 공유되는 상태도 없다.
    - authorizer가 SELECT·읽기·함수만 허용한다. INSERT·ATTACH·PRAGMA 등은 실행 전에 거부된다.
    - 값 길이와 실행 단계에 상한을 둔다. 결과는 SQL_MAX_ROWS행까지만 돌려준다.
    """
    if len(sql) > SQL_MAX_CHARS:
        raise SqlRejected(f"SQL이 너무 깁니다({len(sql)}자). {SQL_MAX_CHARS}자 이하로 쓰세요.")

    conn = sqlite3.connect(":memory:")
    try:
        conn.execute(f"CREATE TABLE {SQL_SCHEMA}")
        conn.executemany(
            "INSERT INTO movies VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                (int(r["rank"]), r["title"], int(r["year"]), float(r["rating"]),
                 None if r["director"] in ("", "N/A") else r["director"], r["cast"], r["storyline"])
                for r in _rows(path)
            ],
        )
        # 데이터를 넣은 뒤에 잠근다. 순서를 바꾸면 위의 CREATE·INSERT부터 거부된다.
        conn.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, SQL_MAX_VALUE_BYTES)
        conn.set_authorizer(lambda action, *_: sqlite3.SQLITE_OK if action in _ALLOWED_ACTIONS else sqlite3.SQLITE_DENY)
        calls = 0

        def over_budget() -> bool:
            nonlocal calls
            calls += 1
            return calls > SQL_MAX_PROGRESS_CALLS  # True를 돌려주면 SQLite가 실행을 중단한다

        conn.set_progress_handler(over_budget, 1000)

        try:
            cur = conn.execute(sql)
            rows = cur.fetchmany(SQL_MAX_ROWS + 1)
        except sqlite3.Error as e:
            hint = " 조인을 줄이거나 조건을 좁히세요." if calls > SQL_MAX_PROGRESS_CALLS else ""
            raise SqlRejected(f"SQL 실행 실패: {str(e).rstrip('.')}.{hint}") from e
        if cur.description is None:
            raise SqlRejected("SELECT 문 하나를 보내세요.")
        columns = [d[0] for d in cur.description]
    finally:
        conn.close()

    if not rows:
        # 가장 흔한 원인은 한국어 이름으로 찾는 것이다(WHERE director LIKE '%놀란%').
        return "조건에 맞는 영화가 없습니다. 제목·인명은 영어로(비영어권 영화는 원제로) 저장돼 있습니다.", 0
    truncated = len(rows) > SQL_MAX_ROWS
    rows = rows[:SQL_MAX_ROWS]
    lines = [" | ".join(columns)]
    lines += [" | ".join("" if v is None else str(v) for v in r) for r in rows]
    if truncated:
        lines.append(f"(앞 {SQL_MAX_ROWS}행만 표시. 조건을 좁히거나 LIMIT을 쓰세요)")
    return "\n".join(lines), len(rows)


def _text(m: dict) -> str:
    # 검색 도구는 page_content만 LLM에 넘긴다. 메타데이터에만 두면 모델이 볼 수 없으므로
    # 답에 필요한 칸은 전부 본문에 넣는다.
    lines = [f"{m['title']} ({m['year']})", f"IMDB Top 250 rank {m['rank']}, rating {m['rating']}"]
    for label, key in (("Director", "director"), ("Cast", "cast"), ("Storyline", "storyline")):
        if m[key] not in ("", "N/A"):
            lines.append(f"{label}: {m[key]}")
    return "\n".join(lines)


def corpus_digest(docs: list[tuple[str, dict]]) -> str:
    """임베딩할 내용의 지문. 인덱스 파일 이름에 넣어, CSV나 변환 형식이 바뀌면 옛 인덱스를
    재사용하지 않고 새로 만들게 한다.

    파일 바이트가 아니라 읽어낸 내용으로 만든다. git(core.autocrlf)이 Windows에서는 CRLF,
    Railway(Linux)에서는 LF로 꺼내서 바이트 해시는 환경마다 달라진다.
    """
    return hashlib.sha256(json.dumps(docs, ensure_ascii=False).encode("utf-8")).hexdigest()[:12]
