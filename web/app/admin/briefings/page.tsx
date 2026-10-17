"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

// 주간 박스오피스 브리핑 승인 화면. 백엔드 /briefings(관리자 토큰 필요)를 /api/admin/briefings로 프록시해 쓴다.
// Postgres가 없는 서버(지금의 Railway)에서는 백엔드가 503 BRIEFING_DISABLED를 준다.

interface Briefing {
  thread_id: string;
  week_start: string;
  week_end: string;
  status: string;
  draft: string | null;
  final_text: string | null;
  decided_by: string | null;
  decided_at: string | null;
  sent_at: string | null;
  delivery_id: string | null;
  error: string | null;
  created_at: string;
}

interface Metrics {
  total: number;
  by_status: Record<string, number>;
  approved_unedited_ratio: number | null;
  redraft_ratio: number | null;
  hours_to_decision_avg: number | null;
  hours_to_decision_max: number | null;
  prep_seconds_avg: number | null;
  llm_tokens_avg: number | null;
}

const pct = (v: number | null) => (v === null ? "기록 없음" : `${Math.round(v * 100)}%`);
const num = (v: number | null, unit: string) => (v === null ? "기록 없음" : `${v}${unit}`);

const TOKEN_KEY = "briefing-admin-token"; // 탭을 닫으면 사라지게 sessionStorage에 둔다
const NAME_KEY = "briefing-admin-name";

const STATUS_LABEL: Record<string, string> = {
  drafted: "초안 작성 중",
  pending: "승인 대기",
  approved: "승인됨",
  sending: "보내는 중",
  sent: "보냄",
  rejected: "거절됨",
  expired: "만료됨",
  failed: "실패",
};

function read(storage: () => Storage, key: string): string {
  try {
    return storage().getItem(key) ?? "";
  } catch {
    return "";
  }
}

function write(storage: () => Storage, key: string, value: string) {
  try {
    storage().setItem(key, value);
  } catch {
    // 저장이 막힌 환경 — 이번 화면에서만 쓴다
  }
}

export default function BriefingAdmin() {
  const [token, setToken] = useState("");
  const [name, setName] = useState("");
  const [briefings, setBriefings] = useState<Briefing[] | null>(null);
  const [metrics, setMetrics] = useState<Metrics | null>(null);
  const [edits, setEdits] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    // localStorage/sessionStorage는 브라우저에만 있어서 마운트 후에 읽는다
    setToken(read(() => sessionStorage, TOKEN_KEY));
    setName(read(() => localStorage, NAME_KEY));
  }, []);

  const call = useCallback(
    async (path: string, body?: object) => {
      const res = await fetch(`/api/admin/briefings${path}`, {
        method: body ? "POST" : "GET",
        headers: { "Content-Type": "application/json", "x-admin-token": token },
        body: body ? JSON.stringify(body) : undefined,
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.error ?? `요청이 실패했습니다 (HTTP ${res.status}).`);
      return data;
    },
    [token]
  );

  const load = useCallback(async () => {
    if (!token) return;
    setError(null);
    try {
      const [list, m] = await Promise.all([call(""), call("/metrics")]);
      setBriefings(list.briefings);
      setMetrics(m.metrics);
    } catch (e) {
      setBriefings(null);
      setError((e as Error).message);
    }
  }, [call, token]);

  useEffect(() => {
    load();
  }, [load]);

  const act = async (fn: () => Promise<unknown>) => {
    setBusy(true);
    setError(null);
    try {
      await fn();
      await load();
    } catch (e) {
      setError((e as Error).message);
      await load();
    } finally {
      setBusy(false);
    }
  };

  const decide = (b: Briefing, action: "approve" | "edit" | "reject") =>
    act(() =>
      call(`/${b.thread_id}/decision`, { action, text: edits[b.thread_id] ?? null, by: name.trim() })
    );

  return (
    <div className="min-h-[100dvh] bg-page text-ink">
      <header className="border-b border-line">
        <div className="mx-auto w-full max-w-2xl flex items-center gap-3 px-4 py-3">
          <h1 className="flex-1 font-display text-lg font-bold">브리핑 승인</h1>
          <Link href="/" className="text-sm text-ink-soft hover:text-ink">챗봇으로</Link>
        </div>
      </header>

      <main className="mx-auto w-full max-w-2xl px-4 py-6 flex flex-col gap-6">
        <section className="flex flex-col gap-3">
          <p className="text-sm text-ink-soft leading-relaxed">
            지난주 박스오피스 브리핑 초안을 확인하고 승인하면 발송됩니다. 승인하지 않은 초안은 48시간 뒤 만료됩니다.
          </p>
          <div className="grid gap-2 sm:grid-cols-2">
            <label className="flex flex-col gap-1 text-sm">
              관리자 토큰
              <input
                type="password"
                value={token}
                onChange={(e) => {
                  setToken(e.target.value);
                  write(() => sessionStorage, TOKEN_KEY, e.target.value);
                }}
                className="px-3 py-2 rounded-lg border border-line bg-surface text-base sm:text-sm"
                autoComplete="off"
              />
            </label>
            <label className="flex flex-col gap-1 text-sm">
              승인자 이름
              <input
                value={name}
                maxLength={64}
                onChange={(e) => {
                  setName(e.target.value);
                  write(() => localStorage, NAME_KEY, e.target.value);
                }}
                className="px-3 py-2 rounded-lg border border-line bg-surface text-base sm:text-sm"
              />
            </label>
          </div>
          <div className="flex gap-2">
            <button
              onClick={() => act(() => call("/run", {}))}
              disabled={!token || busy}
              className="min-h-[44px] px-4 rounded-lg bg-ink text-page text-sm font-medium disabled:opacity-30"
            >
              이번 주 브리핑 만들기
            </button>
            <button
              onClick={load}
              disabled={!token || busy}
              className="min-h-[44px] px-4 rounded-lg border border-line text-sm disabled:opacity-30"
            >
              새로고침
            </button>
          </div>
          {error && <p className="border-l-[3px] border-stamp pl-3 text-sm text-stamp">{error}</p>}
        </section>

        {metrics && metrics.total > 0 && (
          // 운영 지표(docs/roadmap.md 5절). 초안 품질과 만료 기준을 정하는 근거다.
          <section className="grid grid-cols-2 sm:grid-cols-4 gap-x-4 gap-y-3 border-t border-line pt-4 text-sm">
            <div>
              <div className="text-ink-soft text-xs">수정 없이 승인</div>
              <div className="font-semibold">{pct(metrics.approved_unedited_ratio)}</div>
            </div>
            <div>
              <div className="text-ink-soft text-xs">초안 재작성</div>
              <div className="font-semibold">{pct(metrics.redraft_ratio)}</div>
            </div>
            <div>
              <div className="text-ink-soft text-xs">승인까지 평균</div>
              <div className="font-semibold">{num(metrics.hours_to_decision_avg, "시간")}</div>
            </div>
            <div>
              <div className="text-ink-soft text-xs">초안 1건 토큰</div>
              <div className="font-semibold">{num(metrics.llm_tokens_avg, "")}</div>
            </div>
            <p className="col-span-full text-xs text-ink-soft">
              전체 {metrics.total}건 중 보냄 {metrics.by_status.sent}, 거절 {metrics.by_status.rejected}, 만료{" "}
              {metrics.by_status.expired}, 실패 {metrics.by_status.failed}
            </p>
          </section>
        )}

        {briefings && briefings.length === 0 && (
          <p className="text-sm text-ink-soft">아직 브리핑이 없습니다. 이번 주 브리핑을 만들어 보세요.</p>
        )}

        {briefings?.map((b) => {
          const pending = b.status === "pending";
          const text = edits[b.thread_id] ?? b.draft ?? "";
          const edited = edits[b.thread_id] !== undefined && edits[b.thread_id] !== b.draft;
          return (
            <article key={b.thread_id} className="flex flex-col gap-3 border-t border-line pt-4">
              <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
                <h2 className="font-display font-bold">
                  {b.week_start} ~ {b.week_end}
                </h2>
                <span className="text-sm text-ink-soft">{STATUS_LABEL[b.status] ?? b.status}</span>
              </div>

              {pending ? (
                <textarea
                  value={text}
                  onChange={(e) => setEdits({ ...edits, [b.thread_id]: e.target.value })}
                  rows={12}
                  maxLength={4000}
                  className="w-full p-3 rounded-lg border border-line bg-surface text-base sm:text-sm leading-relaxed"
                />
              ) : (
                (b.final_text ?? b.draft) && (
                  <pre className="whitespace-pre-wrap text-sm leading-relaxed text-ink-soft">
                    {b.final_text ?? b.draft}
                  </pre>
                )
              )}

              {b.error && <p className="text-sm text-stamp">{b.error}</p>}
              {b.decided_by && (
                <p className="text-xs text-ink-soft">
                  {b.decided_by}님이 {b.decided_at?.slice(0, 16).replace("T", " ")}에 결정
                  {b.delivery_id && `, 발송 기록 ${b.delivery_id}`}
                </p>
              )}

              {pending && (
                <div className="flex flex-wrap gap-2">
                  <button
                    onClick={() => decide(b, edited ? "edit" : "approve")}
                    disabled={busy || !name.trim() || !text.trim()}
                    className="min-h-[44px] px-4 rounded-lg bg-ink text-page text-sm font-medium disabled:opacity-30"
                  >
                    {edited ? "수정본으로 승인하고 보내기" : "승인하고 보내기"}
                  </button>
                  <button
                    onClick={() => decide(b, "reject")}
                    disabled={busy || !name.trim()}
                    className="min-h-[44px] px-4 rounded-lg border border-stamp/60 text-stamp text-sm disabled:opacity-30"
                  >
                    거절
                  </button>
                  {!name.trim() && <span className="self-center text-xs text-ink-soft">승인자 이름을 적어 주세요.</span>}
                </div>
              )}
            </article>
          );
        })}
      </main>
    </div>
  );
}
