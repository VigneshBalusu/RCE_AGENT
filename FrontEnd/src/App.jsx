import { useRef, useState, useEffect } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import "./App.css";

const API_URL = import.meta.env.VITE_API_URL || "http://localhost:5678/webhook/chat";

const QUICK_QUESTIONS = [
  "What are the BTech fees?",
  "Who is the Principal?",
  "What are the placement stats?",
  "How to apply for admission?",
];

export default function App() {
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState("");
  const [status, setStatus] = useState("Ready");
  const [isRecording, setIsRecording] = useState(false);
  const [theme, setTheme] = useState("dark");
  const [isLoading, setIsLoading] = useState(false);

  const recorderRef = useRef(null);
  const chunksRef = useRef([]);
  const chatEndRef = useRef(null);

  // Apply theme to root
  useEffect(() => {
    document.documentElement.setAttribute("data-theme", theme);
  }, [theme]);

  // Auto-scroll to bottom on new messages
  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, isLoading]);

  /* ---------------- TEXT ---------------- */
  const sendText = async (text) => {
    const msg = text || input;
    if (!msg.trim() || status !== "Ready") return;

    setMessages((m) => [...m, { role: "user", type: "text", data: msg }]);
    setInput("");
    setStatus("Thinking...");
    setIsLoading(true);

    try {
      const res = await fetch(API_URL, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ query: msg, voice_mode: false }),
      });
      await handleResponse(res);
    } catch (error) {
      setMessages((m) => [...m, { role: "assistant", type: "text", data: "⚠️ Network error. Please try again." }]);
      setStatus("Ready");
      setIsLoading(false);
    }
  };

  /* ---------------- VOICE ---------------- */
  const toggleRecording = async () => {
    if (isRecording) stopRecording();
    else startRecording();
  };

  const startRecording = async () => {
    if (status !== "Ready") return;
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      recorderRef.current = new MediaRecorder(stream);
      chunksRef.current = [];
      recorderRef.current.ondataavailable = (e) => chunksRef.current.push(e.data);
      recorderRef.current.onstop = sendVoice;
      recorderRef.current.start();
      setIsRecording(true);
      setStatus("Recording...");
    } catch {
      alert("Could not access microphone. Please check permissions.");
    }
  };

  const stopRecording = () => {
    if (recorderRef.current && recorderRef.current.state !== "inactive") {
      recorderRef.current.stop();
      recorderRef.current.stream?.getTracks().forEach((t) => t.stop());
      setIsRecording(false);
      setStatus("Processing...");
    }
  };

  const sendVoice = async () => {
    const audioBlob = new Blob(chunksRef.current, { type: "audio/wav" });
    // Show a voice message bubble in chat while we wait
    setMessages((m) => [...m, { role: "user", type: "text", data: "🎤 Voice message sent" }]);
    setIsLoading(true);

    const formData = new FormData();
    formData.append("file", audioBlob, "voice.wav");
    formData.append("voice_mode", "true");

    try {
      const res = await fetch(API_URL, { method: "POST", body: formData });
      await handleResponse(res);
    } catch {
      setMessages((m) => [...m, { role: "assistant", type: "text", data: "⚠️ Voice request failed. Please try again." }]);
      setStatus("Ready");
      setIsLoading(false);
    }
  };

  /* ---------------- RESPONSE HANDLER (text-only) ---------------- */
  const handleResponse = async (res) => {
    if (!res.ok) throw new Error(`HTTP ${res.status}`);

    // Always read as text — voice input now returns a text answer, not audio
    try {
      const rawText = await res.text();
      console.log("📡 Raw n8n response:", rawText); // debug — remove later
      if (!rawText.trim()) throw new Error("Empty response");

      let data;
      try {
        data = JSON.parse(rawText);
        if (Array.isArray(data)) data = data[0];
      } catch {
        // n8n sent plain text (not JSON) — use directly
        setMessages((m) => [...m, { role: "assistant", type: "text", data: rawText }]);
        setStatus("Ready");
        setIsLoading(false);
        return;
      }
      const text =
        data.answer ||
        (Array.isArray(data.output) ? data.output[0]?.content?.[0]?.text : null) ||
        data.translated_text ||
        (typeof data.output === "string" ? data.output : null) ||
        data.text ||
        "Sorry, I couldn't get a response.";

      // If n8n returned a transcript, replace the "🎤 Voice message sent" bubble
      if (data.transcript) {
        setMessages((m) => {
          const updated = [...m];
          for (let i = updated.length - 1; i >= 0; i--) {
            if (updated[i].role === "user" && updated[i].data === "🎤 Voice message sent") {
              updated[i] = { ...updated[i], data: `🎤 "${data.transcript}"` };
              break;
            }
          }
          return updated;
        });
      }

      setMessages((m) => [...m, { role: "assistant", type: "text", data: text }]);
    } catch (err) {
      setMessages((m) => [...m, {
        role: "assistant", type: "text",
        data: "⚠️ **Error:** Received an invalid response. Please check the server."
      }]);
    }

    setStatus("Ready");
    setIsLoading(false);
  };


  return (
    <div className="page">
      {/* HEADER */}
      <header className="chat-header">
        <div className="header-left">
          <div className="logo-dot" />
          <span className="header-title">RCEE Assistant</span>
        </div>
        <div className="header-right">
          <span className={`status-pill ${status !== "Ready" ? "busy" : ""}`}>
            <span className="status-dot" />
            {status}
          </span>
          <button className="theme-toggle" onClick={() => setTheme(t => t === "dark" ? "light" : "dark")} title="Toggle theme">
            {theme === "dark" ? "☀️" : "🌙"}
          </button>
        </div>
      </header>

      {/* CHAT AREA */}
      <div className="chat">
        {messages.length === 0 && (
          <div className="empty-state">
            <div className="empty-avatar">
              <div className="avatar-ring" />
              <svg width="40" height="40" viewBox="0 0 40 40" fill="none" xmlns="http://www.w3.org/2000/svg" style={{ zIndex: 1 }}>
                <text x="50%" y="50%" dominantBaseline="central" textAnchor="middle"
                  fontFamily="-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif"
                  fontWeight="700" fontSize="13" fill="url(#lg)" letterSpacing="-0.5">RCE</text>
                <defs>
                  <linearGradient id="lg" x1="0" y1="0" x2="40" y2="40" gradientUnits="userSpaceOnUse">
                    <stop stopColor="#3b82f6" /><stop offset="1" stopColor="#8b5cf6" />
                  </linearGradient>
                </defs>
              </svg>
            </div>
            <h1 className="empty-title">Hello! I'm RCEE Assistant</h1>
            <p className="empty-subtitle">Ask me anything about Ramachandra College of Engineering</p>
            <div className="quick-chips">
              {QUICK_QUESTIONS.map((q) => (
                <button key={q} className="chip" onClick={() => sendText(q)}>
                  {q}
                </button>
              ))}
            </div>
          </div>
        )}

        {messages.map((m, i) => (
          <div key={i} className={`msg-row ${m.role}`}>
            {m.role === "assistant" && (
              <div className="msg-avatar">
                <svg width="16" height="16" viewBox="0 0 40 40" fill="none" xmlns="http://www.w3.org/2000/svg">
                  <text x="50%" y="50%" dominantBaseline="central" textAnchor="middle"
                    fontFamily="-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif"
                    fontWeight="700" fontSize="14" fill="url(#lg2)" letterSpacing="-0.5">RCE</text>
                  <defs>
                    <linearGradient id="lg2" x1="0" y1="0" x2="40" y2="40" gradientUnits="userSpaceOnUse">
                      <stop stopColor="#60a5fa" /><stop offset="1" stopColor="#a78bfa" />
                    </linearGradient>
                  </defs>
                </svg>
              </div>
            )}
            <div className={`msg ${m.role}`}>
              {m.type === "audio" ? (
                <span className="audio-label">{m.data}</span>
              ) : (
                <ReactMarkdown remarkPlugins={[remarkGfm]}>{m.data}</ReactMarkdown>
              )}
            </div>
          </div>
        ))}

        {/* TYPING LOADER */}
        {isLoading && (
          <div className="msg-row assistant">
            <div className="msg-avatar">🎓</div>
            <div className="msg assistant typing-bubble">
              <span className="dot" /><span className="dot" /><span className="dot" />
            </div>
          </div>
        )}

        <div ref={chatEndRef} />
      </div>

      {/* INPUT BAR */}
      <div className="input-bar-wrap">
        <div className="input-bar">
          <input
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder="Ask anything about RCEE…"
            disabled={status !== "Ready"}
            onKeyDown={(e) => e.key === "Enter" && sendText()}
          />
          <button
            className={`mic-btn ${isRecording ? "recording" : ""}`}
            onClick={toggleRecording}
            disabled={status !== "Ready" && status !== "Recording..."}
            title={isRecording ? "Stop recording" : "Start recording"}
          >
            {isRecording ? "⏹️" : "🎙️"}
          </button>
          <button className="send-btn" onClick={() => sendText()} disabled={status !== "Ready"}>
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
              <line x1="22" y1="2" x2="11" y2="13" /><polygon points="22 2 15 22 11 13 2 9 22 2" />
            </svg>
          </button>
        </div>
        <p className="disclaimer">RCEE Assistant may make mistakes. Verify important info with the college directly.</p>
      </div>
    </div>
  );
}