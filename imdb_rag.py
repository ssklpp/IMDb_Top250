# API 키를 환경변수로 관리하기 위한 설정 파일
from dotenv import load_dotenv

# API 키 정보 로드
load_dotenv()

from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_community.document_loaders import PyMuPDFLoader
from langchain_community.vectorstores import FAISS
from langchain_core.tools.retriever import create_retriever_tool
from langchain_core.messages import HumanMessage
from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from langchain_tavily import TavilySearch
from langchain.agents import create_agent

# 단계 1: 문서 로드(Load Documents)
loader = PyMuPDFLoader("imdb_top250.pdf")
docs = loader.load()

# 단계 2: 문서 분할(Split Documents)
text_splitter = RecursiveCharacterTextSplitter(chunk_size=1500, chunk_overlap=50)

split_documents = text_splitter.split_documents(docs)

# 단계 3: 임베딩(Embedding) 생성
embeddings = OpenAIEmbeddings(model="text-embedding-3-small")

# 단계 4: DB 생성(Create DB) 및 저장
# 벡터스토어를 생성합니다.
vectorstore = FAISS.from_documents(documents=split_documents, embedding=embeddings)

# 단계 5: 검색기(Retriever) 생성
# 문서에 포함되어 있는 정보를 검색하고 생성합니다.
retriever = vectorstore.as_retriever()

# 단계 6: 언어모델(LLM) 생성
llm = ChatOpenAI(model_name="gpt-5.4-mini", temperature=0)

# 단계 7: 도구(Tools) 생성
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

# 단계 8: 에이전트 생성
SYSTEM_PROMPT = """당신은 영화 전문가 AI 어시스턴트입니다.
먼저 imdb_search로 PDF 문서에서 정보를 찾고, 충분하지 않으면 web_search로 인터넷을 검색하세요.
반드시 한국어로 답변하세요. 모르는 내용은 모른다고 말하세요."""

agent = create_agent(
    model=llm,
    tools=[imdb_tool, web_tool],
    system_prompt=SYSTEM_PROMPT,
)

# 에이전트 실행 루프
print("질문을 입력하세요. 종료하려면 'q'를 입력하세요.")
while True:
    question = input("\n질문: ").strip()
    if question.lower() == "q":
        print("종료합니다.")
        break
    if not question:
        continue
    result = agent.invoke({"messages": [HumanMessage(content=question)]})
    print(f"\n답변: {result['messages'][-1].content}")
