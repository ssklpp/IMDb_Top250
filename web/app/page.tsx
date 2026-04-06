"use client";

import { useState, useRef, useEffect } from "react";
import ReactMarkdown from "react-markdown";

interface Message {
  id: string;
  question: string;
  answer: string;
  toolStatus?: string | null;
}

export default function Home() {
  const [question, setQuestion] = useState("");
  const [messages, setMessages] = useState<Message[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [copiedId, setCopiedId] = useState<string | null>(null);
  const sessionId = useRef<string>(crypto.randomUUID());
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const prevCountRef = useRef(0);

  useEffect(() => {
    if (messages.length > prevCountRef.current) {
      messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
      prevCountRef.current = messages.length;
    }
  }, [messages.length]);

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

  const handleSubmit = async (e: React.SyntheticEvent) => {
    e.preventDefault();
    if (!question.trim() || isLoading) return;

    const q = question.trim();
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
          prev.map((m) => m.id === msgId ? { ...m, answer: data.error ?? "오류가 발생했습니다." } : m)
        );
        return;
      }

      const reader = res.body.getReader();
      const decoder = new TextDecoder();

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        const chunk = decoder.decode(value, { stream: true });

        // 센티넬 이벤트(\x1ftool:...\n)를 추출하고 표시 텍스트에서 제거
        const displayText = chunk.replace(/\x1f(tool:[^\n]*)\n/g, (_, payload) => {
          if (payload === "tool:end") {
            setMessages((prev) =>
              prev.map((m) => m.id === msgId ? { ...m, toolStatus: null } : m)
            );
          } else if (payload.startsWith("tool:")) {
            const toolName = payload.slice(5);
            const label =
              toolName === "imdb_search" ? "IMDB Top 250 검색 중..." :
              toolName === "web_search"  ? "웹 검색 중..." :
              `${toolName} 실행 중...`;
            setMessages((prev) =>
              prev.map((m) => m.id === msgId ? { ...m, toolStatus: label } : m)
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

      // 스트림 종료 시 toolStatus 정리
      setMessages((prev) =>
        prev.map((m) => m.id === msgId ? { ...m, toolStatus: null } : m)
      );
    } catch {
      setMessages((prev) =>
        prev.map((m) =>
          m.id === msgId
            ? { ...m, answer: "서버에 연결할 수 없습니다. Python 서버가 실행 중인지 확인하세요." }
            : m
        )
      );
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <div className="flex flex-col h-screen bg-white text-gray-900">
      <header className="shrink-0 flex items-center px-4 pt-6 pb-4">
        <div className="flex-1" />
        <div className="flex flex-col items-center">
          <h1 className="text-4xl font-bold tracking-tight mb-1">IMDB 영화 챗봇</h1>
          <p className="text-gray-500 text-sm">
            IMDB Top 250 기반 AI 영화 전문가에게 무엇이든 물어보세요
          </p>
        </div>
        <div className="flex-1 flex justify-end">
          <button
            onClick={handleNewConversation}
            disabled={isLoading}
            className="px-3 py-2 rounded-lg text-sm text-gray-500 hover:text-gray-800 hover:bg-gray-100 disabled:opacity-40 transition-colors"
          >
            새 대화
          </button>
        </div>
      </header>

      <main className="flex-1 overflow-y-auto px-4 py-4">
        {messages.length > 0 && (
          <div className="mx-auto w-full max-w-2xl flex flex-col gap-6">
            {messages.map((msg) => (
              <div key={msg.id} className="flex flex-col gap-2">
                {/* 사용자 질문 */}
                <div className="self-end max-w-[80%] px-4 py-3 rounded-2xl bg-blue-600 text-white text-sm">
                  {msg.question}
                </div>

                {/* AI 응답 */}
                <div className="self-start max-w-[80%] relative group">
                  <div className="px-4 py-3 rounded-2xl bg-gray-100 text-gray-900 text-sm leading-relaxed min-h-[44px]">
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
                          code: ({ children }) => <code className="bg-gray-200 rounded px-1 font-mono text-xs">{children}</code>,
                          blockquote: ({ children }) => <blockquote className="border-l-2 border-gray-400 pl-3 text-gray-600 italic">{children}</blockquote>,
                          hr: () => <hr className="my-2 border-gray-300" />,
                        }}
                      >
                        {msg.answer}
                      </ReactMarkdown>
                    ) : (
                      // 로딩 dots: 도구 상태도 없고 답변도 없을 때만
                      !msg.toolStatus && (
                        <span className="flex gap-1 items-center">
                          <span className="w-1.5 h-1.5 bg-gray-400 rounded-full animate-bounce [animation-delay:0ms]" />
                          <span className="w-1.5 h-1.5 bg-gray-400 rounded-full animate-bounce [animation-delay:150ms]" />
                          <span className="w-1.5 h-1.5 bg-gray-400 rounded-full animate-bounce [animation-delay:300ms]" />
                        </span>
                      )
                    )}
                  </div>

                  {/* 복사 버튼 (응답 완료 후, hover 시 표시) */}
                  {msg.answer && (
                    <button
                      onClick={() => handleCopy(msg.id, msg.answer)}
                      className="absolute -bottom-6 right-0 opacity-0 group-hover:opacity-100 transition-opacity flex items-center gap-1 text-xs text-gray-400 hover:text-gray-600 px-2 py-1 rounded"
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

      <footer className="shrink-0 px-4 py-4 bg-white border-t border-gray-100">
        <form onSubmit={handleSubmit} className="mx-auto w-full max-w-2xl flex gap-2">
          <input
            type="text"
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            placeholder="영화에 대해 질문해보세요..."
            className="flex-1 px-4 py-3 rounded-xl border border-gray-300 bg-white text-gray-900 placeholder-gray-400 focus:outline-none focus:ring-2 focus:ring-blue-500 transition-colors"
          />
          <button
            type="submit"
            disabled={!question.trim() || isLoading}
            className="px-5 py-3 rounded-xl bg-blue-600 hover:bg-blue-700 disabled:bg-gray-300 text-white font-medium transition-colors disabled:cursor-not-allowed min-w-[72px]"
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
