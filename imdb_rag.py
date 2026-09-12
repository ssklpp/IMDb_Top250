import uuid
from pathlib import Path

from langchain_core.messages import HumanMessage
from langgraph.checkpoint.sqlite import SqliteSaver

from agent import CHECKPOINT_DB_PATH, build_agent
from logging_config import configure_logging, get_logger, session_id_var

configure_logging()
log = get_logger("cli")


def main() -> None:
    thread_id = str(uuid.uuid4())
    session_id_var.set(thread_id)
    config = {"configurable": {"thread_id": thread_id}}

    Path(CHECKPOINT_DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    # CLI는 sync invoke를 쓰므로 동기 SqliteSaver를 쓴다.
    # 서버(AsyncSqliteSaver)와 같은 DB 파일을 공유한다.
    with SqliteSaver.from_conn_string(CHECKPOINT_DB_PATH) as checkpointer:
        agent = build_agent(checkpointer)
        log.info("cli.start", thread_id=thread_id)
        print("질문을 입력하세요. 종료하려면 'q'를 입력하세요.")

        while True:
            try:
                question = input("\n질문: ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\n종료합니다.")
                log.info("cli.exit", reason="interrupt")
                return

            if question.lower() == "q":
                log.info("cli.exit", reason="quit")
                print("종료합니다.")
                return
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


if __name__ == "__main__":
    main()
