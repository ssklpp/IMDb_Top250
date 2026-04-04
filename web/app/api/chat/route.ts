import { NextRequest, NextResponse } from "next/server";

export async function POST(req: NextRequest) {
  const { question } = await req.json();

  if (!question?.trim()) {
    return NextResponse.json({ error: "질문이 비어있습니다." }, { status: 400 });
  }

  const res = await fetch("http://localhost:8000/api/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ question }),
  });

  if (!res.ok) {
    return NextResponse.json({ error: "서버 오류가 발생했습니다." }, { status: 500 });
  }

  const data = await res.json();
  return NextResponse.json({ answer: data.answer });
}
