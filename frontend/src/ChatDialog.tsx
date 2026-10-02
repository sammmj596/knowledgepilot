import { FormEvent, useCallback, useEffect, useRef, useState } from "react";
import type { ChatMessage } from "./api";
import { sendChat, streamChat } from "./api";

export function ChatDialog() {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState("");
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
    setStatus("Thinking\u2026");

    let gotEvent = false;
    let bubbleAdded = false;
    const ensureBubble = () => {
      if (bubbleAdded) return;
      bubbleAdded = true;
      setMessages((m) => [...m, { role: "assistant", content: "" }]);
    };
    const setAnswer = (update: (current: string) => string) =>
      setMessages((m) => {
        const copy = [...m];
        const i = copy.length - 1;
        if (i >= 0 && copy[i].role === "assistant") {
          copy[i] = { ...copy[i], content: update(copy[i].content) };
        }
        return copy;
      });

    try {
      await streamChat(next, (ev) => {
        gotEvent = true;
        switch (ev.type) {
          case "status":
            setStatus(ev.text);
            break;
          case "token":
            ensureBubble();
            setAnswer((c) => c + ev.text);
            break;
          case "replace":
            ensureBubble();
            setAnswer(() => "");
            break;
          case "done":
            if (ev.text) {
              ensureBubble();
              const finalText = ev.text;
              setAnswer(() => finalText);
            }
            break;
          case "error":
            throw new Error(ev.text);
        }
      });
    } catch (err) {
      if (!gotEvent) {
        // Streaming never started (older server, network issue): use plain chat.
        try {
          const reply = await sendChat(next);
          setMessages((m) => [...m, reply]);
        } catch (err2) {
          setError(err2 instanceof Error ? err2.message : "Unknown error");
          setMessages((prev) => prev.slice(0, -1));
        }
      } else {
        setError(err instanceof Error ? err.message : "Unknown error");
        setMessages((prev) => {
          const last = prev[prev.length - 1];
          // Nothing useful was shown: drop the empty bubble and the unanswered question.
          if (last?.role === "assistant" && !last.content) return prev.slice(0, -2);
          if (last?.role === "user") return prev.slice(0, -1);
          return prev; // keep the partial answer that was already shown
        });
      }
    } finally {
      setLoading(false);
      setStatus("");
    }
  }

  function handleFormSubmit(e: FormEvent) {
    e.preventDefault();
    void submitCurrentMessage();
  }

  const lastMessage = messages[messages.length - 1];
  const showingAnswer = lastMessage?.role === "assistant" && lastMessage.content !== "";

  return (
    <section className="dialog" aria-label="Chat">
      <div className="dialog-inner">
        <div className="messages" ref={listRef}>
          {messages.length === 0 && (
            <p className="empty">
              Ask a question about your documents. Upload a document on the Documents tab first.
            </p>
          )}
          {messages.map((m, i) =>
            m.role === "assistant" && !m.content ? null : (
            <div
              key={i}
              className={`bubble bubble-${m.role}`}
              role="article"
              aria-label={m.role === "user" ? "User" : "Assistant"}
            >
              <div className="bubble-meta">{m.role === "user" ? "You" : "Assistant"}</div>
              <div className="bubble-body">{m.content}</div>
            </div>
            )
          )}
          {loading && !showingAnswer && (
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
          {loading && status && <div className="stream-status">{status}</div>}
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
