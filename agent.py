from dotenv import load_dotenv

load_dotenv()

import os
import requests
from langchain_core.tools import tool
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_community.document_loaders import PyMuPDFLoader
from langchain_community.vectorstores import FAISS
from langchain_core.tools.retriever import create_retriever_tool
from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from langchain_tavily import TavilySearch
from langchain.agents import create_agent
from langgraph.checkpoint.memory import MemorySaver

VECTORSTORE_PATH = "vectorstore"

embeddings = OpenAIEmbeddings(model="text-embedding-3-small")

if os.path.exists(f"{VECTORSTORE_PATH}/index.faiss"):
    print("벡터스토어 캐시 로드 중...")
    vectorstore = FAISS.load_local(
        VECTORSTORE_PATH, embeddings, allow_dangerous_deserialization=True
    )
else:
    print("벡터스토어 생성 중 (최초 1회)...")
    loader = PyMuPDFLoader("imdb_top250.pdf")
    docs = loader.load()
    text_splitter = RecursiveCharacterTextSplitter(chunk_size=800, chunk_overlap=100)
    split_documents = text_splitter.split_documents(docs)
    vectorstore = FAISS.from_documents(documents=split_documents, embedding=embeddings)
    vectorstore.save_local(VECTORSTORE_PATH)

retriever = vectorstore.as_retriever(search_kwargs={"k": 8})
llm = ChatOpenAI(model_name="gpt-5.4-mini", temperature=0)

imdb_tool = create_retriever_tool(
    retriever,
    name="imdb_search",
    description="IMDB Top 250 PDF에서 영화 정보를 검색합니다. 영화 제목, 감독, 출연진, 평점 등을 찾을 때 사용하세요.",
)

web_tool = TavilySearch(
    max_results=5,
    name="web_search",
    description="인터넷에서 최신 영화 정보를 검색합니다. PDF에 없는 신작, 박스오피스, 최신 수상 내역 등을 찾을 때 사용하세요.",
)


@tool
def kobis_search(
    query: str,
    open_start_dt: str = "",
    open_end_dt: str = "",
    search_type: str = "movie",
) -> str:
    """한국 영화관입장권통합전산망(KOBIS)에서 한국 개봉 영화 정보를 검색합니다.
    한국 박스오피스, 한국 개봉작, 국내 상영 영화, 일별/주간 박스오피스를 찾을 때 사용하세요.
    query: 영화 제목 또는 날짜(YYYYMMDD 형식, 박스오피스 조회 시).
    open_start_dt: 개봉 시작 연도 (예: '2024'), open_end_dt: 개봉 종료 연도 (예: '2024').
    search_type: 'movie'(영화 목록), 'daily'(일별 박스오피스), 'weekly'(주간 박스오피스)."""
    api_key = os.environ.get("KOBIS_API_KEY")
    if not api_key:
        return "KOBIS_API_KEY 환경변수가 설정되지 않았습니다."

    try:
        if search_type == "daily":
            resp = requests.get(
                "https://www.kobis.or.kr/kobisopenapi/webservice/rest/boxoffice/searchDailyBoxOfficeList.json",
                params={"key": api_key, "targetDt": query},
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
            items = data.get("boxOfficeResult", {}).get("dailyBoxOfficeList", [])
            if not items:
                return f"{query} 일별 박스오피스 데이터가 없습니다."
            lines = [f"일별 박스오피스 ({query})\n"]
            for m in items[:10]:
                lines.append(
                    f"{m['rank']}위. {m['movieNm']} — 관객수: {int(m['audiCnt']):,}명 / 누적: {int(m['audiAcc']):,}명 (개봉일: {m.get('openDt', '-')})"
                )
            return "\n".join(lines)

        elif search_type == "weekly":
            resp = requests.get(
                "https://www.kobis.or.kr/kobisopenapi/webservice/rest/boxoffice/searchWeeklyBoxOfficeList.json",
                params={"key": api_key, "targetDt": query, "weekGb": "0"},
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
            items = data.get("boxOfficeResult", {}).get("weeklyBoxOfficeList", [])
            if not items:
                return f"{query} 주간 박스오피스 데이터가 없습니다."
            show_range = data.get("boxOfficeResult", {}).get("showRange", "")
            lines = [f"주간 박스오피스 ({show_range})\n"]
            for m in items[:10]:
                lines.append(
                    f"{m['rank']}위. {m['movieNm']} — 관객수: {int(m['audiCnt']):,}명 / 누적: {int(m['audiAcc']):,}명"
                )
            return "\n".join(lines)

        else:  # movie list
            params = {"key": api_key, "movieNm": query, "itemPerPage": "10"}
            if open_start_dt:
                params["openStartDt"] = open_start_dt
            if open_end_dt:
                params["openEndDt"] = open_end_dt
            resp = requests.get(
                "https://www.kobis.or.kr/kobisopenapi/webservice/rest/movie/searchMovieList.json",
                params=params,
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
            movies = data.get("movieListResult", {}).get("movieList", [])
            if not movies:
                return f"'{query}'에 대한 KOBIS 검색 결과가 없습니다."

            lines = [f"KOBIS 영화 검색 결과: '{query}'\n"]
            for m in movies[:5]:
                open_dt = m.get("openDt", "")
                if len(open_dt) == 8:
                    open_dt = f"{open_dt[:4]}-{open_dt[4:6]}-{open_dt[6:]}"
                directors = ", ".join(
                    d.get("peopleNm", "") for d in m.get("directors", [])
                )
                actors = ", ".join(
                    a.get("peopleNm", "") for a in m.get("actors", [])[:3]
                )
                entry = f"- 제목: {m.get('movieNm', '')}"
                if m.get("movieNmEn"):
                    entry += f" ({m['movieNmEn']})"
                if open_dt:
                    entry += f"\n  개봉일: {open_dt}"
                if m.get("genreAlt"):
                    entry += f"\n  장르: {m['genreAlt']}"
                if m.get("nationAlt"):
                    entry += f"\n  국가: {m['nationAlt']}"
                if directors:
                    entry += f"\n  감독: {directors}"
                if actors:
                    entry += f"\n  출연: {actors}"
                lines.append(entry)
            return "\n".join(lines)

    except requests.RequestException as e:
        return f"KOBIS API 호출 실패: {e}"


SYSTEM_PROMPT = """당신은 영화 전문가 AI 어시스턴트입니다.
도구 선택 기준:
- imdb_search: IMDB Top 250에 대한 질문 (평점, 명작, 고전 등)
- kobis_search: 한국 개봉 영화, 한국 박스오피스, 국내 상영작 검색. search_type으로 'movie'(영화 검색), 'daily'(일별 박스오피스, query=YYYYMMDD), 'weekly'(주간 박스오피스, query=YYYYMMDD) 구분.
- web_search: 위 두 도구로 충분하지 않을 때 (최신 수상 내역, 해외 신작 등)
반드시 한국어로 답변하세요. 모르는 내용은 모른다고 말하세요."""

agent = create_agent(
    model=llm,
    tools=[imdb_tool, web_tool, kobis_search],
    system_prompt=SYSTEM_PROMPT,
    checkpointer=MemorySaver(),
)
