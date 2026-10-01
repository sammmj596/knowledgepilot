import { useState } from "react";
import { ChatDialog } from "./ChatDialog";
import { DocumentUpload } from "./DocumentUpload";

type Page = "chat" | "upload";

export function App() {
  const [page, setPage] = useState<Page>("chat");

  return (
    <div className="shell">
      <header className="header">
        <h1 className="title">KnowledgePilot</h1>
        <p className="subtitle">Ask questions about your documents</p>
        <nav className="nav" aria-label="Main navigation">
          <button
            type="button"
            className={`nav-btn${page === "chat" ? " nav-btn-active" : ""}`}
            onClick={() => setPage("chat")}
          >
            Chat
          </button>
          <button
            type="button"
            className={`nav-btn${page === "upload" ? " nav-btn-active" : ""}`}
            onClick={() => setPage("upload")}
          >
            Documents
          </button>
        </nav>
      </header>
      <div className={page === "chat" ? "page-panel" : "page-panel page-hidden"}>
        <ChatDialog />
      </div>
      <div className={page === "upload" ? "page-panel" : "page-panel page-hidden"}>
        <DocumentUpload />
      </div>
    </div>
  );
}
