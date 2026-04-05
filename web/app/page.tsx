"use client";

import { useState, useRef, useEffect } from "react";

interface Message {
  id: string;
  question: string;
  answer: string;
}

export default function Home() {
  const [question, setQuestion] = useState("");
  const [messages, setMessages] = useState<Message[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const sessionId = useRef<string>(crypto.randomUUID());
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const prevCountRef = useRef(0);

  useEffect(() => {
    if (messages.length > prevCountRef.current) {
      messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
      prevCountRef.current = messages.length;
    }
  }, [messages.length]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!question.trim() || isLoading) return;

    const q = question.trim();
    const msgId = crypto.randomUUID();
    setQuestion("");
    setIsLoading(true);
    setMessages((prev) => [...prev, { id: msgId, question: q, answer: "" }]);

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
        setMessages((prev) =>
          prev.map((m) => m.id === msgId ? { ...m, answer: m.answer + chunk } : m)
        );
      }
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
      <header className="shrink-0 text-center px-4 pt-10 pb-4">
        <h1 className="text-4xl font-bold tracking-tight mb-2">IMDB 영화 챗봇</h1>
        <p className="text-gray-500 text-sm">
          IMDB Top 250 기반 AI 영화 전문가에게 무엇이든 물어보세요
        </p>
      </header>

      <main className="flex-1 overflow-y-auto px-4 py-4">
        {messages.length > 0 && (
          <div className="mx-auto w-full max-w-2xl flex flex-col gap-4">
            {messages.map((msg) => (
              <div key={msg.id} className="flex flex-col gap-2">
                <div className="self-end max-w-[80%] px-4 py-3 rounded-2xl bg-blue-600 text-white text-sm">
                  {msg.question}
                </div>
                <div className="self-start max-w-[80%] px-4 py-3 rounded-2xl bg-gray-100 text-gray-900 text-sm leading-relaxed min-h-[44px]">
                  {msg.answer || (
                    <span className="flex gap-1 items-center">
                      <span className="w-1.5 h-1.5 bg-gray-400 rounded-full animate-bounce [animation-delay:0ms]" />
                      <span className="w-1.5 h-1.5 bg-gray-400 rounded-full animate-bounce [animation-delay:150ms]" />
                      <span className="w-1.5 h-1.5 bg-gray-400 rounded-full animate-bounce [animation-delay:300ms]" />
                    </span>
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
