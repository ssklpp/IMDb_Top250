"use client";

import { useState, useRef, useEffect } from "react";
import { useTheme } from "next-themes";
import ReactMarkdown from "react-markdown";

interface Message {
  id: string;
  question: string;
  answer: string;
  toolStatus?: string | null;
  isError?: boolean;
}

const EXAMPLE_QUESTIONS = [
  "IMDB Top 250 평점 1위 영화는?",
  "오늘 한국 박스오피스 1위는?",
  "크리스토퍼 놀란 감독 영화 추천해줘",
  "인터스텔라에 대해 설명해줘",
];

export default function Home() {
  const [question, setQuestion] = useState("");
  const [messages, setMessages] = useState<Message[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [copiedId, setCopiedId] = useState<string | null>(null);
  const { theme, setTheme } = useTheme();
  const sessionId = useRef<string>(crypto.randomUUID());
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const prevCountRef = useRef(0);

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
    sessionId.current = crypto.randomUUID();
    prevCountRef.current = 0;
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

  const submitQuestion = async (q: string) => {
    if (!q.trim() || isLoading) return;

    const msgId = crypto.randomUUID();
    setQuestion("");
    setIsLoading(true);
    setMessages((prev) => [...prev, { id: msgId, question: q, answer: "", toolStatus: null }]);

    try {
      const res = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question: q, session_id: sessionId.current }),
      });

      if (!res.ok || !res.body) {
        const data = await res.json().catch(() => ({}));
        setMessages((prev) =>
          prev.map((m) => m.id === msgId ? { ...m, isError: true, answer: data.error ?? "오류가 발생했습니다." } : m)
        );
        return;
      }

      const reader = res.body.getReader();
      const decoder = new TextDecoder();

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        const chunk = decoder.decode(value, { stream: true });

        const displayText = chunk.replace(/\x1f((?:tool|error):[^\n]*)\n/g, (_, payload) => {
          if (payload === "tool:end") {
            setMessages((prev) =>
              prev.map((m) => m.id === msgId ? { ...m, toolStatus: null } : m)
            );
          } else if (payload.startsWith("tool:")) {
            const toolName = payload.slice(5);
            const label =
              toolName === "imdb_search"  ? "IMDB Top 250 검색 중..." :
              toolName === "kobis_search" ? "한국 개봉 영화 검색 중..." :
              toolName === "web_search"   ? "웹 검색 중..." :
              `${toolName} 실행 중...`;
            setMessages((prev) =>
              prev.map((m) => m.id === msgId ? { ...m, toolStatus: label } : m)
            );
          } else if (payload.startsWith("error:")) {
            const errorMsg = payload.slice(6) || "오류가 발생했습니다.";
            setMessages((prev) =>
              prev.map((m) => m.id === msgId ? { ...m, isError: true, answer: errorMsg, toolStatus: null } : m)
            );
          }
          return "";
        });

        if (displayText) {
          setMessages((prev) =>
            prev.map((m) => m.id === msgId ? { ...m, answer: m.answer + displayText } : m)
          );
        }
      }

      setMessages((prev) =>
        prev.map((m) => m.id === msgId ? { ...m, toolStatus: null } : m)
      );
    } catch {
      setMessages((prev) =>
        prev.map((m) =>
          m.id === msgId
            ? { ...m, isError: true, answer: "서버에 연결할 수 없습니다. Python 서버가 실행 중인지 확인하세요." }
            : m
        )
      );
    } finally {
      setIsLoading(false);
    }
  };

  const handleSubmit = (e: React.SyntheticEvent) => {
    e.preventDefault();
    submitQuestion(question.trim());
  };

  return (
    <div className="flex flex-col h-screen h-[100dvh] bg-white dark:bg-gray-900 text-gray-900 dark:text-white">
      <header className="shrink-0 flex items-center px-4 pt-6 pb-4">
        <div className="flex-1" />
        <div className="flex flex-col items-center">
          <h1 className="text-2xl sm:text-4xl font-bold tracking-tight mb-1">영화 챗봇</h1>
          <p className="text-gray-500 dark:text-gray-400 text-sm">
            AI 영화 전문가에게 무엇이든 물어보세요
          </p>
        </div>
        <div className="flex-1 flex justify-end items-center gap-1">
          {/* 다크 모드 토글 */}
          <button
            onClick={() => setTheme(theme === "dark" ? "light" : "dark")}
            className="min-h-[44px] min-w-[44px] flex items-center justify-center rounded-lg text-gray-500 dark:text-gray-400 hover:text-gray-800 dark:hover:text-white hover:bg-gray-100 dark:hover:bg-gray-800 transition-colors"
            title="다크 모드 전환"
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
            className="min-h-[44px] min-w-[44px] px-3 py-2 rounded-lg text-sm text-gray-500 dark:text-gray-400 hover:text-gray-800 dark:hover:text-white hover:bg-gray-100 dark:hover:bg-gray-800 disabled:opacity-40 transition-colors"
          >
            새 대화
          </button>
        </div>
      </header>

      <main className="flex-1 overflow-y-auto px-4 py-4">
        {messages.length === 0 ? (
          <div className="mx-auto w-full max-w-2xl flex flex-col items-center justify-center h-full gap-4 pb-8">
            <p className="text-sm text-gray-400 dark:text-gray-500">예시 질문으로 시작해보세요</p>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 w-full">
              {EXAMPLE_QUESTIONS.map((q) => (
                <button
                  key={q}
                  onClick={() => submitQuestion(q)}
                  disabled={isLoading}
                  className="text-left px-4 py-3 rounded-xl border border-gray-200 dark:border-gray-700 text-sm text-gray-600 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-800 hover:border-gray-300 dark:hover:border-gray-600 disabled:opacity-40 transition-colors"
                >
                  {q}
                </button>
              ))}
            </div>
          </div>
        ) : (
          <div className="mx-auto w-full max-w-2xl flex flex-col gap-6">
            {messages.map((msg) => (
              <div key={msg.id} className="flex flex-col gap-2">
                {/* 사용자 질문 */}
                <div className="self-end max-w-[85%] sm:max-w-[80%] px-4 py-3 rounded-2xl bg-blue-600 text-white text-sm">
                  {msg.question}
                </div>

                {/* AI 응답 */}
                <div className="self-start max-w-[85%] sm:max-w-[80%] relative group">
                  <div className={`px-4 py-3 rounded-2xl text-sm leading-relaxed min-h-[44px] ${
                    msg.isError
                      ? "bg-red-50 dark:bg-red-900/30 text-red-700 dark:text-red-300"
                      : "bg-gray-100 dark:bg-gray-800 text-gray-900 dark:text-white"
                  }`}>
                    {/* 도구 상태 표시 */}
                    {msg.toolStatus && (
                      <div className="flex items-center gap-1.5 text-xs text-blue-500 mb-2">
                        <span className="w-1.5 h-1.5 bg-blue-400 rounded-full animate-pulse" />
                        {msg.toolStatus}
                      </div>
                    )}

                    {msg.answer ? (
                      <ReactMarkdown
                        components={{
                          p: ({ children }) => <p className="mb-2 last:mb-0">{children}</p>,
                          ul: ({ children }) => <ul className="list-disc pl-4 mb-2 space-y-1">{children}</ul>,
                          ol: ({ children }) => <ol className="list-decimal pl-4 mb-2 space-y-1">{children}</ol>,
                          li: ({ children }) => <li>{children}</li>,
                          strong: ({ children }) => <strong className="font-semibold">{children}</strong>,
                          h1: ({ children }) => <h1 className="text-base font-bold mb-1">{children}</h1>,
                          h2: ({ children }) => <h2 className="text-sm font-bold mb-1">{children}</h2>,
                          h3: ({ children }) => <h3 className="text-sm font-semibold mb-1">{children}</h3>,
                          code: ({ children }) => <code className="bg-gray-200 dark:bg-gray-700 rounded px-1 font-mono text-xs">{children}</code>,
                          blockquote: ({ children }) => <blockquote className="border-l-2 border-gray-400 pl-3 text-gray-600 dark:text-gray-300 italic">{children}</blockquote>,
                          hr: () => <hr className="my-2 border-gray-300 dark:border-gray-600" />,
                        }}
                      >
                        {msg.answer}
                      </ReactMarkdown>
                    ) : (
                      !msg.toolStatus && (
                        <span className="flex gap-1 items-center">
                          <span className="w-1.5 h-1.5 bg-gray-400 rounded-full animate-bounce [animation-delay:0ms]" />
                          <span className="w-1.5 h-1.5 bg-gray-400 rounded-full animate-bounce [animation-delay:150ms]" />
                          <span className="w-1.5 h-1.5 bg-gray-400 rounded-full animate-bounce [animation-delay:300ms]" />
                        </span>
                      )
                    )}
                  </div>

                  {/* 복사 버튼 (응답 완료 후, 에러가 아닐 때) */}
                  {msg.answer && !msg.isError && (
                    <button
                      onClick={() => handleCopy(msg.id, msg.answer)}
                      className="absolute -bottom-6 right-0 opacity-0 group-hover:opacity-100 [@media(hover:none)]:opacity-100 transition-opacity flex items-center gap-1 text-xs text-gray-400 dark:text-gray-500 hover:text-gray-600 dark:hover:text-gray-300 px-2 py-1 rounded"
                      title="복사"
                    >
                      {copiedId === msg.id ? (
                        <>
                          <svg className="w-3.5 h-3.5 text-green-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
                          </svg>
                          <span className="text-green-500">복사됨</span>
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

      <footer className="shrink-0 px-4 py-3 sm:py-4 bg-white dark:bg-gray-900 border-t border-gray-100 dark:border-gray-700">
        <form onSubmit={handleSubmit} className="mx-auto w-full max-w-2xl flex gap-2">
          <input
            type="text"
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            placeholder="영화에 대해 질문해보세요..."
            className="flex-1 px-4 py-2 sm:py-3 text-base sm:text-sm rounded-xl border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-white placeholder-gray-400 dark:placeholder-gray-500 focus:outline-none focus:ring-2 focus:ring-blue-500 transition-colors"
          />
          <button
            type="submit"
            disabled={!question.trim() || isLoading}
            className="px-5 py-2 sm:py-3 rounded-xl bg-blue-600 hover:bg-blue-700 disabled:bg-gray-300 dark:disabled:bg-gray-700 text-white font-medium transition-colors disabled:cursor-not-allowed min-w-[72px]"
          >
            {isLoading ? (
              <span className="flex items-center justify-center">
                <svg className="animate-spin w-4 h-4" fill="none" viewBox="0 0 24 24">
                  <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                  <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z" />
                </svg>
              </span>
            ) : "전송"}
          </button>
        </form>
      </footer>
    </div>
  );
}
