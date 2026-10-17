import { NextRequest } from "next/server";

const BACKEND_URL = process.env.BACKEND_URL ?? "http://localhost:8000";

// 관리자 화면(/admin/briefings) → 백엔드 /briefings 프록시. 관리자 토큰은 브라우저가 x-admin-token으로 보내고
// 여기서 Authorization 헤더로 바꿔 넘긴다. 토큰을 서버에 저장하지 않는다 — 입력한 사람만 쓴다.
// 허용하는 경로만 넘긴다: "" · "run" · "<thread_id>" · "<thread_id>/decision"
const SEGMENT = /^[A-Za-z0-9_-]{1,64}$/;

function backendPath(path: string[] | undefined): string | null {
  const parts = path ?? [];
  if (parts.length > 2 || !parts.every((p) => SEGMENT.test(p))) return null;
  if (parts.length === 2 && parts[1] !== "decision") return null;
  return parts.length ? `/briefings/${parts.join("/")}` : "/briefings";
}

async function forward(req: NextRequest, path: string[] | undefined, method: "GET" | "POST") {
  const target = backendPath(path);
  if (!target) {
    return Response.json({ code: "NOT_FOUND", error: "잘못된 경로입니다." }, { status: 404 });
  }
  const token = req.headers.get("x-admin-token") ?? "";
  try {
    const res = await fetch(`${BACKEND_URL}${target}`, {
      method,
      headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
      body: method === "POST" ? await req.text() : undefined,
      cache: "no-store",
    });
    const data = await res.json().catch(() => ({}));
    // FastAPI의 HTTPException은 {"detail": {...}}로 감싸 준다. 화면에서는 code/error만 쓴다.
    return Response.json(data.detail ?? data, { status: res.status });
  } catch {
    return Response.json(
      { code: "BACKEND_UNREACHABLE", error: "백엔드에 연결할 수 없습니다." },
      { status: 503 }
    );
  }
}

export async function GET(req: NextRequest, { params }: { params: Promise<{ path?: string[] }> }) {
  return forward(req, (await params).path, "GET");
}

export async function POST(req: NextRequest, { params }: { params: Promise<{ path?: string[] }> }) {
  return forward(req, (await params).path, "POST");
}
