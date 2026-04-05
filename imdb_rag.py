import uuid
from langchain_core.messages import HumanMessage
from agent import agent

thread_id = str(uuid.uuid4())
config = {"configurable": {"thread_id": thread_id}}

print("질문을 입력하세요. 종료하려면 'q'를 입력하세요.")
while True:
    question = input("\n질문: ").strip()
    if question.lower() == "q":
        print("종료합니다.")
        break
    if not question:
        continue
    result = agent.invoke(
        {"messages": [HumanMessage(content=question)]},
        config=config,
    )
    print(f"\n답변: {result['messages'][-1].content}")
