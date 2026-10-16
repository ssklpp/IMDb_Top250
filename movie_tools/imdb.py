"""IMDB Top 250 도구: imdb_search(벡터 검색)와 imdb_sql(SQLite SELECT).

벡터스토어는 처음 필요할 때 만든다(load_vectorstore). import만으로는 OpenAI 키가 필요 없어서
MCP 서버의 단위 테스트가 키 없이 돈다. 서버들은 기동할 때 미리 불러 첫 질문이 느려지지 않게 한다.
"""

from pathlib import Path

from langchain_community.vectorstores import FAISS
from langchain_core.documents import Document
from langchain_openai import OpenAIEmbeddings

from imdb_data import CSV_PATH, SQL_SCHEMA, SqlRejected, corpus_digest, movie_docs, query_movies
from kobis_format import tool_error
from logging_config import get_logger

log = get_logger("movie_tools.imdb")

# 저장소 루트 기준 절대 경로. MCP 서버는 Claude Desktop이 어느 폴더에서 띄울지 모르므로
# 작업 폴더에 기대지 않는다. Railway·Docker에서는 /app/vectorstore(영구 볼륨)다.
VECTORSTORE_PATH = Path(__file__).resolve().parent.parent / "vectorstore"
SEARCH_K = 8

SEARCH_NAME = "imdb_search"
SEARCH_DESCRIPTION = (
    "IMDB Top 250 목록에서 영화 정보를 검색합니다. 영화 제목, 감독, 출연진, 평점 등을 찾을 때 사용하세요. "
    "자료가 영어라 검색어는 영어로 쓰세요(쇼생크 탈출 → The Shawshank Redemption). "
    "제목을 모르는 줄거리 묘사도 영어 키워드로 바꿔 먼저 여기서 찾아보세요(요리하는 쥐 → rat who cooks)."
)
# "영어로" 문장은 검색 적중률 측정의 결과다(tests/evals/README.md). 모델은 대개 제목을 스스로
# 영어로 바꾸지만, 제목을 모르는 한국어 줄거리 묘사는 그대로 넘겨 못 찾거나 웹 검색으로 우회했다.
# 고치면 python -m tests.evals.run_retrieval --actual 로 다시 잴 것.
SEARCH_QUERY_DESCRIPTION = "query to look up in retriever"

# 벡터 검색(imdb_search)은 의미가 가까운 8편만 가져와서 정렬·집계를 못 한다. "평점 1위"를 물으면
# 검색된 8편 안에서 최고를 골라 틀린다. 같은 데이터를 SQL로 조회하는 도구를 따로 둔다.
# 실행 제한(읽기 전용, 상한)은 imdb_data.query_movies()에 있다.
SQL_NAME = "imdb_sql"
SQL_DESCRIPTION = (
    "IMDB Top 250 목록에 SQLite SELECT 한 문장을 실행합니다. 평점·순위·연도로 정렬하거나 "
    "거르는 질문, 개수를 세는 질문에 쓰세요(예: 평점 1위, 1990년대 영화 수, 특정 감독 작품 중 최고 평점). "
    f"테이블: {SQL_SCHEMA}. 값은 영어이고 비영어권 영화는 원제입니다(기생충=Gisaengchung). "
    "director·actors는 쉼표로 구분된 이름이라 LIKE '%Nolan%'처럼 찾으세요."
)

_vectorstore: FAISS | None = None


def load_vectorstore() -> FAISS:
    """인덱스를 불러오거나 처음이면 만든다. 두 번째 호출부터는 같은 객체를 준다.

    인덱스 이름에 임베딩할 내용의 지문을 넣는다. CSV나 변환 형식(imdb_data.py)이 바뀌면
    이름이 달라져 자동으로 새로 만든다. Railway 볼륨은 재배포해도 남기 때문에, 고정된
    이름(index.faiss)을 쓰던 시절에는 옛 인덱스가 계속 쓰였다(로컬 72청크, 배포 87청크).
    """
    global _vectorstore
    if _vectorstore is not None:
        return _vectorstore

    embeddings = OpenAIEmbeddings(model="text-embedding-3-small")
    docs = movie_docs()
    index_name = f"imdb_{corpus_digest(docs)}"

    if (VECTORSTORE_PATH / f"{index_name}.faiss").exists():
        log.info("vectorstore.cache_hit", path=str(VECTORSTORE_PATH), index=index_name)
        _vectorstore = FAISS.load_local(
            str(VECTORSTORE_PATH),
            embeddings,
            index_name=index_name,
            allow_dangerous_deserialization=True,
        )
    else:
        log.info("vectorstore.build_start", source=CSV_PATH.name, index=index_name)
        documents = [Document(page_content=text, metadata=meta) for text, meta in docs]
        _vectorstore = FAISS.from_documents(documents=documents, embedding=embeddings)
        _vectorstore.save_local(str(VECTORSTORE_PATH), index_name=index_name)
        log.info("vectorstore.build_done", docs=len(documents))
    return _vectorstore


def vector_count() -> int:
    """헬스체크용. 아직 불러오지 않았으면 0."""
    if _vectorstore is None:
        return 0
    try:
        return int(_vectorstore.index.ntotal)
    except Exception:
        return 0


def get_retriever():
    return load_vectorstore().as_retriever(search_kwargs={"k": SEARCH_K})


def search_movies(query: str) -> tuple[str, list[dict]]:
    """MCP의 imdb_search. (모델에 줄 본문, 출처용 영화 목록)을 준다.

    본문은 로컬 도구(create_retriever_tool)와 같은 형태 — 문서 본문을 빈 줄로 잇는다.
    영화 목록은 sources._imdb_sources()가 읽어 출처 칩을 만든다.
    """
    docs = get_retriever().invoke(query)
    text = "\n\n".join(d.page_content for d in docs)
    movies = [
        {"rank": d.metadata.get("rank"), "title": d.metadata.get("title"), "year": d.metadata.get("year")}
        for d in docs
    ]
    return text, movies


def run_sql(query: str) -> str:
    log.info("imdb.sql_request", query_len=len(query))
    try:
        text, rows = query_movies(query)
    except SqlRejected as e:
        log.info("imdb.sql_rejected", error=type(e.__cause__ or e).__name__)
        return tool_error("INVALID_QUERY", f"{e} 테이블: {SQL_SCHEMA}")
    log.info("imdb.sql_done", rows=rows)
    return text
