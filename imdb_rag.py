import uuid

from langchain_core.messages import HumanMessage

from agent import agent
from logging_config import configure_logging, get_logger, session_id_var

configure_logging()
log = get_logger("cli")

thread_id = str(uuid.uuid4())
session_id_var.set(thread_id)
config = {"configurable": {"thread_id": thread_id}}

log.info("cli.start", thread_id=thread_id)
print("질문을 입력하세요. 종료하려면 'q'를 입력하세요.")
while True:
    question = input("\n질문: ").strip()
    if question.lower() == "q":
        log.info("cli.exit")
        print("종료합니다.")
        break
    if not question:
        continue
    try:
        result = agent.invoke(
            {"messages": [HumanMessage(content=question)]},
            config=config,
        )
        print(f"\n답변: {result['messages'][-1].content}")
    except Exception:
        log.exception("cli.invoke_error")
        print("\n오류가 발생했습니다. 다시 시도해주세요.")
