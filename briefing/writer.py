"""LLM이 브리핑의 글 부분(헤드라인·코멘트·요약)을 쓴다. 숫자는 쓰지 않는다 — 표는 report.render가 찍는다.

구조화 출력(Pydantic)으로 받아서 validate_draft가 그대로 검사한다. 검사에 걸리면 문제 문장을 붙여 한 번 더
쓰게 하고(graph의 draft ↔ validate), 그래도 걸리면 발송하지 않는다.
"""

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from briefing.report import MAX_COMMENT, MAX_HEADLINE, MAX_SUMMARY, facts_for_prompt


class Comment(BaseModel):
    rank: int = Field(description="표에 있는 순위")
    title: str = Field(description="표의 제목을 글자 그대로")
    comment: str = Field(description=f"한 문장, {MAX_COMMENT}자 이내, 숫자 금지")


class Draft(BaseModel):
    headline: str = Field(description=f"이번 주를 요약하는 한 문장, {MAX_HEADLINE}자 이내, 숫자 금지")
    comments: list[Comment] = Field(description="눈에 띄는 영화 2~4편")
    summary: str = Field(description=f"두 문장 이내, {MAX_SUMMARY}자 이내, 숫자 금지")


PROMPT = """당신은 한국 박스오피스 주간 브리핑을 쓰는 기자입니다. 아래 표만 보고 글을 씁니다.

규칙
- 한국어로 씁니다.
- **숫자를 쓰지 마세요.** 순위·관객 수·증감률은 표로 따로 붙습니다. "정상을 지켰다", "크게 늘었다", "순위권에 새로 들어왔다"처럼 씁니다.
- 표에 없는 사실(장르, 감독, 평가, 개봉 배경)은 지어내지 마세요. 표의 순위·관객 변화·신작·진입 여부만 근거로 삼습니다.
- 표는 **주간(월~일) 집계**입니다. '주말', '개봉 첫날'처럼 집계 단위가 다른 표현을 쓰지 마세요.
- 코멘트는 눈에 띄는 영화 2~4편에만 씁니다: 1위, 신작, 순위권 진입, 변동이 큰 영화.
- 제목은 표의 글자 그대로 옮깁니다(띄어쓰기·기호 포함).
- 헤드라인 {h}자, 코멘트 {c}자, 요약 {s}자를 넘기지 마세요."""


async def write_draft(llm, report: dict, feedback: list[str] | None = None) -> dict:
    messages = [
        SystemMessage(PROMPT.format(h=MAX_HEADLINE, c=MAX_COMMENT, s=MAX_SUMMARY)),
        HumanMessage(facts_for_prompt(report)),
    ]
    if feedback:
        messages.append(HumanMessage("이전 초안이 검사에 걸렸습니다. 아래를 고쳐 다시 쓰세요.\n- " + "\n- ".join(feedback)))
    draft = await llm.with_structured_output(Draft).ainvoke(messages)
    return draft.model_dump()
