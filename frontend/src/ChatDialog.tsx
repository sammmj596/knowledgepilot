import { FormEvent, useCallback, useEffect, useRef, useState } from "react";
import type { ChatMessage } from "./api";
import { sendChat } from "./api";

export function ChatDialog() {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const listRef = useRef<HTMLDivElement>(null);

  const scrollToBottom = useCallback(() => {
    const el = listRef.current;
    if (el) {
      el.scrollTop = el.scrollHeight;
    }
  }, []);

  useEffect(() => {
    scrollToBottom();
  }, [messages, scrollToBottom]);

  async function submitCurrentMessage() {
    const text = input.trim();
    if (!text || loading) return;
    setError(null);
    const userMsg: ChatMessage = { role: "user", content: text };
    const next = [...messages, userMsg];
    setMessages(next);
    setInput("");
    setLoading(true);
    try {
      const reply = await sendChat(next);
      setMessages((m) => [...m, reply]);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unknown error");
      setMessages((prev) => prev.slice(0, -1));
    } finally {
      setLoading(false);
    }
  }

  function handleFormSubmit(e: FormEvent) {
    e.preventDefault();
    void submitCurrentMessage();
  }

  return (
    <section className="dialog" aria-label="Chat">
      <div className="dialog-inner">
        <div className="messages" ref={listRef}>
          {messages.length === 0 && (
            <p className="empty">
              Ask a question about your documents. Upload a document on the Documents tab first.
            </p>
          )}
          {messages.map((m, i) => (
            <div
              key={i}
              className={`bubble bubble-${m.role}`}
              role="article"
              aria-label={m.role === "user" ? "User" : "Assistant"}
            >
              <div className="bubble-meta">{m.role === "user" ? "You" : "Assistant"}</div>
              <div className="bubble-body">{m.content}</div>
            </div>
          ))}
          {loading && (
            <div className="bubble bubble-assistant thinking" aria-live="polite">
              <div className="bubble-meta">Assistant</div>
              <div className="bubble-body">
                <span className="dots">
                  <span />
                  <span />
                  <span />
                </span>
              </div>
            </div>
          )}
        </div>
        {error && <div className="banner-error">{error}</div>}
        <form className="composer" onSubmit={handleFormSubmit}>
          <textarea
            className="input"
            rows={2}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder="Ask a question…"
            disabled={loading}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                void submitCurrentMessage();
              }
            }}
          />
          <button
            type="button"
            className="send"
            disabled={loading || !input.trim()}
            onClick={() => void submitCurrentMessage()}
          >
            Send
          </button>
        </form>
      </div>
    </section>
  );
}
