from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_community.document_loaders import PyMuPDFLoader
from langchain_community.vectorstores import FAISS
from langchain_core.tools.retriever import create_retriever_tool
from langchain_core.messages import HumanMessage
from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from langchain_tavily import TavilySearch
from langchain.agents import create_agent

# 에이전트 초기화 (서버 시작 시 1회)
print("에이전트 초기화 중...")

loader = PyMuPDFLoader("imdb_top250.pdf")
docs = loader.load()

text_splitter = RecursiveCharacterTextSplitter(chunk_size=1500, chunk_overlap=50)
split_documents = text_splitter.split_documents(docs)

embeddings = OpenAIEmbeddings(model="text-embedding-3-small")
vectorstore = FAISS.from_documents(documents=split_documents, embedding=embeddings)
retriever = vectorstore.as_retriever()

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

SYSTEM_PROMPT = """당신은 영화 전문가 AI 어시스턴트입니다.
먼저 imdb_search로 PDF 문서에서 정보를 찾고, 충분하지 않으면 web_search로 인터넷을 검색하세요.
반드시 한국어로 답변하세요. 모르는 내용은 모른다고 말하세요."""

agent = create_agent(
    model=llm,
    tools=[imdb_tool, web_tool],
    system_prompt=SYSTEM_PROMPT,
)

print("에이전트 초기화 완료!")

# FastAPI 앱
app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_methods=["POST"],
    allow_headers=["*"],
)

class QuestionRequest(BaseModel):
    question: str

@app.post("/api/chat")
async def chat(req: QuestionRequest):
    result = agent.invoke({"messages": [HumanMessage(content=req.question)]})
    answer = result["messages"][-1].content
    return {"answer": answer}
