import { NextRequest } from "next/server";

const BACKEND_URL = process.env.BACKEND_URL ?? "http://localhost:8000";

export async function POST(req: NextRequest) {
  const { question, session_id } = await req.json();

  if (!question?.trim()) {
    return new Response(
      JSON.stringify({ code: "EMPTY_QUESTION", error: "질문이 비어있습니다." }),
      { status: 400, headers: { "Content-Type": "application/json" } }
    );
  }

  let res: Response;
  try {
    res = await fetch(`${BACKEND_URL}/api/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question, session_id }),
    });
  } catch {
    return new Response(
      JSON.stringify({
        code: "BACKEND_UNREACHABLE",
        error: "Python 서버에 연결할 수 없습니다. uvicorn server:app --reload 로 서버를 실행하세요.",
      }),
      { status: 503, headers: { "Content-Type": "application/json" } }
    );
  }

  if (res.status === 429) {
    return new Response(
      JSON.stringify({
        code: "RATE_LIMIT",
        error: "요청이 너무 많습니다 (분당 10회 제한). 잠시 후 다시 시도해주세요.",
      }),
      { status: 429, headers: { "Content-Type": "application/json" } }
    );
  }

  // 백엔드 Pydantic 검증 실패(질문 길이/빈 값, session_id 형식)는 422로 온다.
  // 그대로 BACKEND_ERROR로 넘기면 "서버 오류"로 보여 원인을 알 수 없다.
  if (res.status === 422) {
    return new Response(
      JSON.stringify({
        code: "INVALID_INPUT",
        error: "질문 형식이 올바르지 않습니다. 너무 길거나 비어있지 않은지 확인해주세요.",
      }),
      { status: 422, headers: { "Content-Type": "application/json" } }
    );
  }

  if (!res.ok) {
    return new Response(
      JSON.stringify({
        code: "BACKEND_ERROR",
        error: `서버 오류가 발생했습니다 (HTTP ${res.status}).`,
      }),
      { status: res.status, headers: { "Content-Type": "application/json" } }
    );
  }

  return new Response(res.body, {
    headers: {
      "Content-Type": "text/plain; charset=utf-8",
      "X-Request-Id": res.headers.get("X-Request-Id") ?? "",
      "X-Session-Id": res.headers.get("X-Session-Id") ?? "",
    },
  });
}
