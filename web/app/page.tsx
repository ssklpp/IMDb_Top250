"use client";

import { useState, useRef, useEffect } from "react";
import { useTheme } from "next-themes";
import ReactMarkdown from "react-markdown";

interface Source {
  tool: string;
  label: string;
  url: string | null;
}

interface Message {
  id: string;
  question: string;
  answer: string;
  toolStatus?: string | null;
  isError?: boolean;
  errorCode?: string | null;
  retryable?: boolean;
  sources?: Source[];
}

const SESSION_KEY = "movie-chat-session-id";
const MESSAGES_KEY = "movie-chat-messages";
// localStorage는 보통 5MB 제한이라 무한히 쌓지 않는다.
const MAX_STORED_MESSAGES = 50;
// server.py의 MAX_QUESTION_CHARS와 맞춘다. 백엔드가 422로 거절하기 전에 입력 단계에서 막는다.
const MAX_QUESTION_CHARS = 2000;

type Tone = "imdb" | "kobis" | "web";

// where: 이 질문의 답이 어디서 오는지. 입장권과 같은 색 표시로 미리 보여준다.
const EXAMPLE_QUESTIONS: { q: string; tone: Tone; where: string }[] = [
  { q: "IMDB Top 250 평점 1위 영화는?", tone: "imdb", where: "IMDB 순위표" },
  { q: "어제 한국 박스오피스 1위는?", tone: "kobis", where: "영화진흥위원회" },
  { q: "크리스토퍼 놀란 감독 영화 추천해줘", tone: "imdb", where: "IMDB Top 250" },
  { q: "인터스텔라에 대해 설명해줘", tone: "imdb", where: "IMDB Top 250" },
];

function sourceTone(tool: string): Tone {
  if (tool === "imdb_search" || tool === "imdb_sql") return "imdb";
  if (tool === "kobis_search") return "kobis";
  return "web";
}

// runRequest가 만든 상태 문구에서 도구 색을 고른다
function statusTone(status: string): Tone | "none" {
  if (status.includes("IMDB")) return "imdb";
  if (status.includes("한국")) return "kobis";
  if (status.includes("웹")) return "web";
  return "none";
}

function hostOf(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return url;
  }
}

// 입장권 한 장에 들어갈 내용. 라벨 형식은 sources.py가 정한다(IMDB는 "IMDB #<순위> <제목>").
function stubParts(s: Source): { big: string; unit?: string; small?: boolean; title: string; sub: string } {
  if (s.tool === "imdb_search") {
    const m = s.label.match(/^IMDB #(\d+)\s+(.+)$/);
    if (m) return { big: m[1], unit: "위", title: m[2], sub: "IMDB Top 250" };
    return { big: "IMDB", small: true, title: s.label, sub: "IMDB Top 250" };
  }
  if (s.tool === "imdb_sql") {
    return { big: "250", unit: "편", title: "IMDB Top 250 전체 목록", sub: "순위·평점 조회" };
  }
  if (s.tool === "kobis_search") {
    return { big: "KOBIS", small: true, title: "영화진흥위원회", sub: "한국 개봉·흥행 기록" };
  }
  return { big: "웹", title: s.label, sub: s.url ? hostOf(s.url) : "웹 검색" };
}

const RETRYABLE_CODES = new Set([
  "TIMEOUT",
  "INTERNAL",
  "BACKEND_UNREACHABLE",
  "BACKEND_ERROR",
  "NETWORK",
]);

function errorLabel(code: string | null | undefined, fallback: string): string {
  switch (code) {
    case "TIMEOUT":
      return "⏱ 응답 시간 초과: " + fallback;
    case "RATE_LIMIT":
      return "🚦 " + fallback;
    case "BACKEND_UNREACHABLE":
      return "🔌 " + fallback;
    case "BACKEND_ERROR":
      return "⚠ " + fallback;
    case "INTERNAL":
      return "⚠ " + fallback;
    case "NETWORK":
      return "🌐 " + fallback;
    // 입력 자체가 잘못된 경우라 재시도 대상이 아니다(RETRYABLE_CODES에 없음).
    case "INVALID_INPUT":
    case "EMPTY_QUESTION":
      return "✏️ " + fallback;
    default:
      return fallback;
  }
}

export default function Home() {
  const [question, setQuestion] = useState("");
  const [messages, setMessages] = useState<Message[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [copiedId, setCopiedId] = useState<string | null>(null);
  const { theme, setTheme } = useTheme();
  const sessionId = useRef<string | null>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const prevCountRef = useRef(0);
  const [restored, setRestored] = useState(false);

  // 세션 ID를 localStorage에 보존해 새로고침해도 같은 대화를 이어간다.
  // 렌더 중이 아니라 호출 시점에 읽으므로 SSR에서 localStorage를 건드리지 않는다.
  const getSessionId = (): string => {
    if (sessionId.current) return sessionId.current;
    let id = "";
    try {
      id = localStorage.getItem(SESSION_KEY) ?? "";
      if (!id) {
        id = crypto.randomUUID();
        localStorage.setItem(SESSION_KEY, id);
      }
    } catch {
      // 사생활 보호 모드 등 localStorage 차단 환경 — 세션이 유지되지 않을 뿐 동작은 한다
      id = crypto.randomUUID();
    }
    sessionId.current = id;
    return id;
  };

  // 마운트 시 이전 대화 복원. 백엔드는 SQLite 체크포인터로 맥락을 기억하므로,
  // 화면만 비어 있으면 "봇은 기억하는데 나는 안 보이는" 상태가 된다.
  useEffect(() => {
    try {
      const raw = localStorage.getItem(MESSAGES_KEY);
      if (raw) {
        const parsed = JSON.parse(raw);
        if (Array.isArray(parsed)) setMessages(parsed);
      }
    } catch {
      // 저장된 형식이 깨졌으면 빈 대화로 시작한다
    }
    setRestored(true);
  }, []);

  // 복원이 끝나기 전에 저장하면 빈 배열로 덮어써서 대화가 날아간다.
  useEffect(() => {
    if (!restored) return;
    try {
      // 진행 중 상태(toolStatus)는 저장하지 않는다. 요청 도중 새로고침하면
      // "검색 중..."이 멈춘 채로 복원되기 때문이다.
      const persistable = messages
        .slice(-MAX_STORED_MESSAGES)
        .map((m) => ({ ...m, toolStatus: null }));
      localStorage.setItem(MESSAGES_KEY, JSON.stringify(persistable));
    } catch {
      // 용량 초과 등 — 저장만 실패하고 대화는 계속된다
    }
  }, [messages, restored]);

  useEffect(() => {
    if (messages.length > prevCountRef.current) {
      messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
      prevCountRef.current = messages.length;
    }
  }, [messages.length]);

  useEffect(() => {
    const handleResize = () => {
      messagesEndRef.current?.scrollIntoView({ behavior: "instant" });
    };
    window.visualViewport?.addEventListener("resize", handleResize);
    return () => window.visualViewport?.removeEventListener("resize", handleResize);
  }, []);

  const handleNewConversation = () => {
    setMessages([]);
    setQuestion("");
    const fresh = crypto.randomUUID();
    sessionId.current = fresh;
    prevCountRef.current = 0;
    try {
      localStorage.setItem(SESSION_KEY, fresh);
      localStorage.removeItem(MESSAGES_KEY);
    } catch {
      // localStorage 차단 환경 — 메모리상으로는 이미 새 대화로 전환됐다
    }
  };

  const handleCopy = async (msgId: string, text: string) => {
    try {
      await navigator.clipboard.writeText(text);
      setCopiedId(msgId);
      setTimeout(() => setCopiedId(null), 1500);
    } catch {
      // Clipboard API 미지원 환경 무시
    }
  };

  // 메시지 하나만 갱신한다. 이전 값이 필요하면(답변 이어붙이기 등) 함수를 넘긴다.
  const updateMessage = (
    msgId: string,
    patch: Partial<Message> | ((m: Message) => Partial<Message>)
  ) => {
    setMessages((prev) =>
      prev.map((m) =>
        m.id === msgId ? { ...m, ...(typeof patch === "function" ? patch(m) : patch) } : m
      )
    );
  };

  const setError = (msgId: string, code: string, message: string) => {
    updateMessage(msgId, {
      isError: true,
      answer: errorLabel(code, message),
      errorCode: code,
      retryable: RETRYABLE_CODES.has(code),
      toolStatus: null,
    });
  };

  const runRequest = async (msgId: string, q: string) => {
    updateMessage(msgId, {
      answer: "",
      isError: false,
      errorCode: null,
      retryable: false,
      toolStatus: null,
      sources: [],
    });
    setIsLoading(true);

    try {
      const res = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question: q, session_id: getSessionId() }),
      });

      if (!res.ok || !res.body) {
        const data = await res.json().catch(() => ({}));
        setError(msgId, data.code ?? "BACKEND_ERROR", data.error ?? "오류가 발생했습니다.");
        return;
      }

      const reader = res.body.getReader();
      const decoder = new TextDecoder();

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        const chunk = decoder.decode(value, { stream: true });

        const displayText = chunk.replace(/\x1f((?:tool|error|sources):[^\n]*)\n/g, (_, payload) => {
          if (payload === "tool:end") {
            updateMessage(msgId, { toolStatus: null });
          } else if (payload.startsWith("sources:")) {
            try {
              const incoming: Source[] = JSON.parse(payload.slice(8));
              updateMessage(msgId, (m) => {
                // 도구가 여러 번 호출되면 출처도 여러 번 온다. URL+라벨로 중복 제거.
                const seen = new Set((m.sources ?? []).map((s) => `${s.url}|${s.label}`));
                const merged = [...(m.sources ?? [])];
                for (const s of incoming) {
                  const key = `${s.url}|${s.label}`;
                  if (!seen.has(key)) {
                    seen.add(key);
                    merged.push(s);
                  }
                }
                return { sources: merged };
              });
            } catch {
              // 출처 파싱 실패는 답변 자체에 영향을 주지 않으므로 무시한다
            }
          } else if (payload.startsWith("tool:")) {
            const toolName = payload.slice(5);
            const label =
              toolName === "imdb_search"  ? "IMDB Top 250 검색 중..." :
              toolName === "imdb_sql"     ? "IMDB Top 250 순위·평점 조회 중..." :
              toolName === "kobis_search" ? "한국 개봉 영화 검색 중..." :
              toolName === "web_search"   ? "웹 검색 중..." :
              `${toolName} 실행 중...`;
            updateMessage(msgId, { toolStatus: label });
          } else if (payload.startsWith("error:")) {
            const raw = payload.slice(6);
            const sep = raw.indexOf("|");
            const code = sep === -1 ? "INTERNAL" : raw.slice(0, sep);
            const message = sep === -1 ? raw || "오류가 발생했습니다." : raw.slice(sep + 1);
            setError(msgId, code, message);
          }
          return "";
        });

        if (displayText) {
          updateMessage(msgId, (m) => ({ answer: m.answer + displayText }));
        }
      }

      updateMessage(msgId, { toolStatus: null });
    } catch {
      setError(msgId, "NETWORK", "서버에 연결할 수 없습니다. Python 서버가 실행 중인지 확인하세요.");
    } finally {
      setIsLoading(false);
    }
  };

  const submitQuestion = async (q: string) => {
    if (!q.trim() || isLoading) return;
    const msgId = crypto.randomUUID();
    setQuestion("");
    setMessages((prev) => [...prev, { id: msgId, question: q, answer: "", toolStatus: null }]);
    await runRequest(msgId, q);
  };

  const handleRetry = async (msg: Message) => {
    if (isLoading) return;
    await runRequest(msg.id, msg.question);
  };

  const handleSubmit = (e: React.SyntheticEvent) => {
    e.preventDefault();
    submitQuestion(question.trim());
  };

  return (
    <div className="flex flex-col h-screen h-[100dvh] bg-page text-ink">
      <header className="shrink-0 border-b border-line">
        <div className="mx-auto w-full max-w-2xl flex items-center gap-1 px-4 py-2">
          <h1 className="flex-1 font-display text-lg font-bold tracking-tight">영화 챗봇</h1>
          {/* 다크 모드 토글 */}
          <button
            onClick={() => setTheme(theme === "dark" ? "light" : "dark")}
            className="min-h-[44px] min-w-[44px] flex items-center justify-center rounded-lg text-ink-soft hover:text-ink hover:bg-line/60 focus-visible:outline-2 focus-visible:outline-ink transition-colors"
            title="다크 모드 전환"
            aria-label="다크 모드 전환"
          >
            {theme === "dark" ? (
              <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
                  d="M12 3v1m0 16v1m9-9h-1M4 12H3m15.364-6.364l-.707.707M6.343 17.657l-.707.707M17.657 17.657l-.707-.707M6.343 6.343l-.707-.707M12 5a7 7 0 000 14A7 7 0 0012 5z" />
              </svg>
            ) : (
              <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
                  d="M20.354 15.354A9 9 0 018.646 3.646 9.003 9.003 0 0012 21a9.003 9.003 0 008.354-5.646z" />
              </svg>
            )}
          </button>
          <button
            onClick={handleNewConversation}
            disabled={isLoading}
            className="min-h-[44px] min-w-[44px] px-3 py-2 rounded-lg text-sm text-ink-soft hover:text-ink hover:bg-line/60 focus-visible:outline-2 focus-visible:outline-ink disabled:opacity-40 transition-colors"
          >
            새 대화
          </button>
        </div>
      </header>

      <main className="flex-1 overflow-y-auto px-4 py-6">
        {messages.length === 0 ? (
          <div className="mx-auto w-full max-w-2xl flex flex-col justify-center min-h-full pb-8">
            <h2 className="font-display text-[2.25rem] sm:text-5xl font-bold leading-[1.15] tracking-tight">
              어떤 영화가<br />궁금하세요?
            </h2>
            <p className="mt-4 max-w-[34em] text-ink-soft leading-relaxed">
              IMDB Top 250과 한국 박스오피스, 웹을 찾아 답하고, 어디서 찾았는지 답 아래에 함께 붙여 드려요.
            </p>
            <ul className="mt-8 border-t border-line">
              {EXAMPLE_QUESTIONS.map(({ q, tone, where }) => (
                <li key={q} className="border-b border-line">
                  <button
                    onClick={() => submitQuestion(q)}
                    disabled={isLoading}
                    className="w-full min-h-[52px] flex items-center gap-3 py-3 text-left hover:bg-line/40 focus-visible:outline-2 focus-visible:outline-ink disabled:opacity-40 transition-colors"
                  >
                    <span className="flex-1">{q}</span>
                    <span className="flex items-center gap-1.5 text-xs text-ink-soft whitespace-nowrap">
                      <span className="swatch" data-tone={tone} data-static />
                      {where}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          </div>
        ) : (
          <div className="mx-auto w-full max-w-2xl flex flex-col gap-10">
            {messages.map((msg) => (
              <div key={msg.id} className="flex flex-col gap-4">
                {/* 사용자 질문 */}
                <div className="self-end max-w-[85%] sm:max-w-[80%] px-4 py-2.5 rounded-2xl rounded-br-md bg-ink text-page whitespace-pre-wrap break-words">
                  {msg.question}
                </div>

                {/* AI 응답 — 말풍선 없이 읽는 글로 둔다 */}
                <div className="group flex flex-col gap-3 min-h-[44px]">
                  {/* 도구 상태 표시 */}
                  {msg.toolStatus && (
                    <div className="flex items-center gap-2 text-sm text-ink-soft">
                      <span className="swatch" data-tone={statusTone(msg.toolStatus)} />
                      {msg.toolStatus}
                    </div>
                  )}

                  {msg.answer ? (
                    msg.isError ? (
                      <div className="flex flex-col gap-2 border-l-[3px] border-stamp pl-3 py-1 text-stamp">
                        <span>{msg.answer}</span>
                        {msg.retryable && (
                          <button
                            onClick={() => handleRetry(msg)}
                            disabled={isLoading}
                            className="self-start min-h-[36px] text-sm px-3 rounded-lg border border-stamp/60 hover:bg-stamp/10 focus-visible:outline-2 focus-visible:outline-stamp disabled:opacity-40 transition-colors"
                          >
                            다시 시도
                          </button>
                        )}
                      </div>
                    ) : (
                      <div className="leading-[1.75] break-words">
                        <ReactMarkdown
                          components={{
                            p: ({ children }) => <p className="mb-3 last:mb-0">{children}</p>,
                            ul: ({ children }) => <ul className="list-disc pl-5 mb-3 space-y-1">{children}</ul>,
                            ol: ({ children }) => <ol className="list-decimal pl-5 mb-3 space-y-1">{children}</ol>,
                            li: ({ children }) => <li className="pl-0.5">{children}</li>,
                            strong: ({ children }) => <strong className="font-semibold">{children}</strong>,
                            h1: ({ children }) => <h1 className="font-display text-xl font-bold mt-4 mb-2 first:mt-0">{children}</h1>,
                            h2: ({ children }) => <h2 className="font-display text-lg font-bold mt-4 mb-2 first:mt-0">{children}</h2>,
                            h3: ({ children }) => <h3 className="font-semibold mt-3 mb-1 first:mt-0">{children}</h3>,
                            code: ({ children }) => <code className="bg-line rounded px-1 font-mono text-[0.875em]">{children}</code>,
                            blockquote: ({ children }) => <blockquote className="border-l-2 border-line pl-3 text-ink-soft">{children}</blockquote>,
                            hr: () => <hr className="my-4 border-line" />,
                          }}
                        >
                          {msg.answer}
                        </ReactMarkdown>
                      </div>
                    )
                  ) : (
                    !msg.toolStatus && (
                      <span className="flex gap-1 items-center h-6" aria-label="답변 준비 중">
                        <span className="w-1.5 h-1.5 bg-ink-soft rounded-full animate-bounce [animation-delay:0ms]" />
                        <span className="w-1.5 h-1.5 bg-ink-soft rounded-full animate-bounce [animation-delay:150ms]" />
                        <span className="w-1.5 h-1.5 bg-ink-soft rounded-full animate-bounce [animation-delay:300ms]" />
                      </span>
                    )
                  )}

                  {/* 출처 — 답변이 어느 도구/문서에서 나왔는지 입장권으로 붙인다 */}
                  {!msg.isError && msg.answer && msg.sources && msg.sources.length > 0 && (
                    <div className="mt-1">
                      <div className="text-xs text-ink-soft mb-2">출처</div>
                      <ul className="flex flex-wrap gap-2">
                        {msg.sources.map((s, i) => {
                          const p = stubParts(s);
                          const inner = (
                            <>
                              <span className="stub-head">
                                <span className="flex items-baseline gap-px">
                                  <span className={p.small ? "text-[0.7rem]" : "text-xl"}>{p.big}</span>
                                  {p.unit && <span className="text-[0.7rem]">{p.unit}</span>}
                                </span>
                              </span>
                              <span className="stub-body">
                                <span className="truncate text-[0.8rem] font-semibold leading-snug">{p.title}</span>
                                <span className="truncate text-[0.7rem] opacity-70 leading-snug">{p.sub}</span>
                              </span>
                            </>
                          );
                          const style = { animationDelay: `${i * 70}ms` };
                          return (
                            <li key={`${s.url ?? s.label}-${i}`} className="min-w-0">
                              {s.url ? (
                                <a
                                  href={s.url}
                                  target="_blank"
                                  rel="noopener noreferrer"
                                  className="stub"
                                  data-tone={sourceTone(s.tool)}
                                  style={style}
                                  title={s.label}
                                >
                                  {inner}
                                </a>
                              ) : (
                                <span className="stub" data-tone={sourceTone(s.tool)} style={style} title={s.label}>
                                  {inner}
                                </span>
                              )}
                            </li>
                          );
                        })}
                      </ul>
                    </div>
                  )}

                  {/* 복사 버튼 (응답 완료 후, 에러가 아닐 때) */}
                  {msg.answer && !msg.isError && (
                    <button
                      onClick={() => handleCopy(msg.id, msg.answer)}
                      className="self-start -ml-2 opacity-0 group-hover:opacity-100 focus-visible:opacity-100 [@media(hover:none)]:opacity-100 transition-opacity flex items-center gap-1 text-xs text-ink-soft hover:text-ink px-2 py-1 rounded"
                      title="복사"
                    >
                      {copiedId === msg.id ? (
                        <>
                          <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
                          </svg>
                          복사됨
                        </>
                      ) : (
                        <>
                          <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
                              d="M8 16H6a2 2 0 01-2-2V6a2 2 0 012-2h8a2 2 0 012 2v2m-6 12h8a2 2 0 002-2v-8a2 2 0 00-2-2h-8a2 2 0 00-2 2v8a2 2 0 002 2z" />
                          </svg>
                          복사
                        </>
                      )}
                    </button>
                  )}
                </div>
              </div>
            ))}
            <div ref={messagesEndRef} />
          </div>
        )}
      </main>

      <footer className="shrink-0 px-4 py-3 sm:py-4 border-t border-line">
        <form onSubmit={handleSubmit} className="mx-auto w-full max-w-2xl flex gap-2">
          <input
            type="text"
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            maxLength={MAX_QUESTION_CHARS}
            placeholder="영화에 대해 물어보세요"
            aria-label="질문"
            className="flex-1 min-w-0 px-4 py-2.5 sm:py-3 text-base sm:text-sm rounded-xl border border-line bg-surface text-ink placeholder:text-ink-soft/70 focus:outline-none focus:border-ink transition-colors"
          />
          <button
            type="submit"
            disabled={!question.trim() || isLoading}
            className="px-5 py-2 sm:py-3 rounded-xl bg-ink text-page font-medium hover:opacity-90 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ink disabled:opacity-30 disabled:cursor-not-allowed transition-opacity min-w-[72px]"
          >
            {isLoading ? (
              <span className="flex items-center justify-center" aria-label="답변 중">
                <svg className="animate-spin w-4 h-4" fill="none" viewBox="0 0 24 24">
                  <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                  <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z" />
                </svg>
              </span>
            ) : "보내기"}
          </button>
        </form>
      </footer>
    </div>
  );
}
