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

// ─── MIME HELPERS ────────────────────────────────────────────────────────────
/**
 * Strip codec suffix: "audio/webm;codecs=opus" → "audio/webm"
 * Sarvam AI rejects anything with a semicolon in the Content-Type.
 */
const stripCodec = (mimeType = "") => mimeType.split(";")[0].trim();

/**
 * Pick the best supported MIME type for MediaRecorder.
 * Returns { mimeType: string, ext: string }
 */
const getBestMimeType = () => {
  // Preference order: formats that Sarvam AI definitely accepts
  const candidates = [
    { mime: "audio/mp4",  ext: "mp4"  },
    { mime: "audio/webm", ext: "webm" },
    { mime: "audio/ogg",  ext: "ogg"  },
  ];
  for (const { mime, ext } of candidates) {
    // Check without codec suffix first
    if (MediaRecorder.isTypeSupported(mime)) return { mimeType: mime, ext };
    // Some browsers only support the codec-suffixed form; we still use the base type for the File
    if (MediaRecorder.isTypeSupported(`${mime};codecs=opus`)) return { mimeType: mime, ext };
    if (MediaRecorder.isTypeSupported(`${mime};codecs=aac`))  return { mimeType: mime, ext };
  }
  // Last resort — let browser choose, we'll strip later
  return { mimeType: "", ext: "webm" };
};
// ─────────────────────────────────────────────────────────────────────────────

export default function App() {
  const [messages, setMessages]   = useState([]);
  const [input, setInput]         = useState("");
  const [status, setStatus]       = useState("Ready");
  const [isRecording, setIsRecording] = useState(false);
  const [theme, setTheme]         = useState("dark");
  const [isLoading, setIsLoading] = useState(false);
  const [isTyping, setIsTyping]   = useState(false);
  const typingRef = useRef({ fullText: "", index: 0, timer: null });

  const recorderRef = useRef(null);
  const chunksRef   = useRef([]);
  const chatEndRef  = useRef(null);
  const recordStartRef = useRef(null); // track recording duration

  // Apply theme to root
  useEffect(() => {
    document.documentElement.setAttribute("data-theme", theme);
  }, [theme]);

  // Auto-scroll to bottom on new messages
  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, isLoading, isTyping]);

  // Cleanup typewriter timer on unmount
  useEffect(() => {
    return () => {
      if (typingRef.current.timer) clearInterval(typingRef.current.timer);
    };
  }, []);

  /* ───────────────── TYPEWRITER EFFECT ───────────────── */
  const startTypewriter = (fullText) => {
    if (typingRef.current.timer) clearInterval(typingRef.current.timer);

    setMessages((m) => [...m, { role: "assistant", type: "text", data: "" }]);
    setIsTyping(true);
    typingRef.current = { fullText, index: 0, timer: null };

    const CHARS_PER_TICK = 3;
    const TICK_MS = 15;

    typingRef.current.timer = setInterval(() => {
      typingRef.current.index += CHARS_PER_TICK;
      const currentText = typingRef.current.fullText.slice(0, typingRef.current.index);

      setMessages((m) => {
        const updated = [...m];
        updated[updated.length - 1] = { role: "assistant", type: "text", data: currentText };
        return updated;
      });

      if (typingRef.current.index >= typingRef.current.fullText.length) {
        clearInterval(typingRef.current.timer);
        typingRef.current.timer = null;
        setIsTyping(false);
        setMessages((m) => {
          const updated = [...m];
          updated[updated.length - 1] = {
            role: "assistant",
            type: "text",
            data: typingRef.current.fullText,
          };
          return updated;
        });
      }
    }, TICK_MS);
  };

  /* ───────────────── TEXT SEND ───────────────── */
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
    } catch {
      setMessages((m) => [
        ...m,
        { role: "assistant", type: "text", data: "⚠️ Network error. Please try again." },
      ]);
      setStatus("Ready");
      setIsLoading(false);
    }
  };

  /* ───────────────── VOICE RECORDING ───────────────── */
  const toggleRecording = async () => {
    if (isRecording) stopRecording();
    else startRecording();
  };

  const startRecording = async () => {
    if (status !== "Ready") return;

    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const { mimeType, ext } = getBestMimeType();

      // Store chosen ext on ref so onstop can use it
      recorderRef._ext = ext;
      recorderRef._baseMime = mimeType; // already stripped of codec

      const options = mimeType ? { mimeType: mimeType } : {};
      recorderRef.current = new MediaRecorder(stream, options);
      chunksRef.current = [];
      recordStartRef.current = Date.now();

      recorderRef.current.ondataavailable = (e) => {
        if (e.data && e.data.size > 0) chunksRef.current.push(e.data);
      };

      recorderRef.current.onstop = sendVoice;
      recorderRef.current.start(250);

      setIsRecording(true);
      setStatus("Recording...");
    } catch (err) {
      console.error("🎙️ [MIC] Error:", err);
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
    // ── Duration guard: reject clips shorter than 1 second ──
    const durationMs = Date.now() - (recordStartRef.current || 0);
    if (durationMs < 1000) {
      setStatus("Ready");
      setMessages((m) => [
        ...m,
        {
          role: "assistant",
          type: "text",
          data: "🎙️ Recording was too short. Please hold the button and speak clearly.",
        },
      ]);
      return;
    }

    // ── Determine the clean MIME type (NO codec suffix) ──────────────────────
    // Priority: what we told MediaRecorder to use (already clean)
    // Fallback: strip codec from whatever the recorder actually used
    let baseMime = recorderRef._baseMime || "";
    if (!baseMime && recorderRef.current?.mimeType) {
      baseMime = stripCodec(recorderRef.current.mimeType);
    }

    // Map to a Sarvam-safe type + extension
    let safeType, ext;
    if (baseMime.includes("mp4")) {
      safeType = "audio/mp4";  ext = "mp4";
    } else if (baseMime.includes("ogg")) {
      safeType = "audio/ogg";  ext = "ogg";
    } else {
      // Default: webm — Sarvam accepts plain "audio/webm" (without codec suffix)
      safeType = "audio/webm"; ext = "webm";
    }

    // ── Build the blob with the CLEAN type ───────────────────────────────────
    const blob = new Blob(chunksRef.current, { type: safeType });

    // Size guard: ~500 bytes minimum
    if (blob.size < 500) {
      setStatus("Ready");
      setMessages((m) => [
        ...m,
        {
          role: "assistant",
          type: "text",
          data: "🎙️ No audio detected. Please try again and speak clearly into the mic.",
        },
      ]);
      return;
    }

    // ── Show "processing" bubble ─────────────────────────────────────────────
    setMessages((m) => [
      ...m,
      { role: "user", type: "voice-processing", data: "__VOICE_PROCESSING__" },
    ]);
    setIsLoading(true);

    // ── Build FormData with explicit clean type ───────────────────────────────
    const safeFile = new File([blob], `voice.${ext}`, { type: safeType });
    const formData = new FormData();
    formData.append("file", safeFile);

    try {
      const res = await fetch(API_URL, { method: "POST", body: formData });

      if (!res.ok) {
        const errText = await res.text();
        throw new Error(`HTTP ${res.status}: ${errText}`);
      }

      const rawText = await res.text();
      if (!rawText.trim()) throw new Error("Empty response from server");

      let data;
      try {
        data = JSON.parse(rawText);
        if (Array.isArray(data)) data = data[0];
      } catch {
        // Plain text response — show directly
        _replaceVoiceProcessing("🎤 Voice message");
        setIsLoading(false);
        setStatus("Ready");
        startTypewriter(rawText);
        return;
      }

      const transcript = data.transcript || "🎤 Voice message";
      _replaceVoiceProcessing(transcript);

      const answer = data.answer || "";
      setIsLoading(false);
      setStatus("Ready");

      if (answer) {
        startTypewriter(answer);
      } else {
        setMessages((m) => [
          ...m,
          {
            role: "assistant",
            type: "text",
            data: "🎤 I couldn't understand that. Please try again, speaking slowly and clearly.",
          },
        ]);
      }
    } catch (err) {
      console.error("[VOICE] Error:", err);
      // Remove the processing bubble
      setMessages((m) => m.filter((msg) => msg.data !== "__VOICE_PROCESSING__"));
      setMessages((m) => [
        ...m,
        {
          role: "assistant",
          type: "text",
          data: `⚠️ Voice request failed: ${err.message}`,
        },
      ]);
      setIsLoading(false);
      setStatus("Ready");
    }
  };

  /** Replace the __VOICE_PROCESSING__ placeholder with the real transcript */
  const _replaceVoiceProcessing = (transcript) => {
    setMessages((m) => {
      const updated = [...m];
      for (let i = updated.length - 1; i >= 0; i--) {
        if (updated[i].data === "__VOICE_PROCESSING__") {
          updated[i] = { role: "user", type: "text", data: transcript };
          break;
        }
      }
      return updated;
    });
  };

  /* ───────────────── RESPONSE HANDLER (text) ───────────────── */
  const handleResponse = async (res) => {
    if (!res.ok) throw new Error(`HTTP ${res.status}`);

    try {
      const rawText = await res.text();
      if (!rawText.trim()) throw new Error("Empty response");

      let data;
      try {
        data = JSON.parse(rawText);
        if (Array.isArray(data)) data = data[0];
      } catch {
        setIsLoading(false);
        setStatus("Ready");
        startTypewriter(rawText);
        return;
      }

      let text = data.answer;

      if (!text || text.trim() === "") {
        if (data.DB?.output?.[0]?.translated_text)  text = data.DB.output[0].translated_text;
        else if (data.Translator?.translated_text)  text = data.Translator.translated_text;
        else if (data.translated_text)              text = data.translated_text;
      }
      if (!text || text.trim() === "") {
        text = Array.isArray(data.output)
          ? data.output[0]?.content?.[0]?.text
          : null;
      }
      if (!text || text.trim() === "") text = typeof data.output === "string" ? data.output : null;
      if (!text || text.trim() === "") text = data.text;
      text = text || "";

      setIsLoading(false);
      setStatus("Ready");

      if (text) {
        startTypewriter(text);
      } else {
        setMessages((m) => [
          ...m,
          {
            role: "assistant",
            type: "text",
            data: "🎤 I couldn't hear that clearly. Please hold the mic button, speak clearly, then release.",
          },
        ]);
      }
    } catch {
      setMessages((m) => [
        ...m,
        {
          role: "assistant",
          type: "text",
          data: "⚠️ **Error:** Received an invalid response. Please check the server.",
        },
      ]);
    }

    if (!isTyping) {
      setStatus("Ready");
      setIsLoading(false);
    }
  };

  /* ───────────────── RENDER ───────────────── */
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
          <button
            className="theme-toggle"
            onClick={() => setTheme((t) => (t === "dark" ? "light" : "dark"))}
            title="Toggle theme"
          >
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
              <svg
                width="40" height="40" viewBox="0 0 40 40"
                fill="none" xmlns="http://www.w3.org/2000/svg"
                style={{ zIndex: 1 }}
              >
                <text
                  x="50%" y="50%" dominantBaseline="central" textAnchor="middle"
                  fontFamily="-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif"
                  fontWeight="700" fontSize="13" fill="url(#lg)" letterSpacing="-0.5"
                >RCE</text>
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
                  <text
                    x="50%" y="50%" dominantBaseline="central" textAnchor="middle"
                    fontFamily="-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif"
                    fontWeight="700" fontSize="14" fill="url(#lg2)" letterSpacing="-0.5"
                  >RCE</text>
                  <defs>
                    <linearGradient id="lg2" x1="0" y1="0" x2="40" y2="40" gradientUnits="userSpaceOnUse">
                      <stop stopColor="#60a5fa" /><stop offset="1" stopColor="#a78bfa" />
                    </linearGradient>
                  </defs>
                </svg>
              </div>
            )}
            <div
              className={`msg ${m.role}${
                isTyping && m.role === "assistant" && i === messages.length - 1
                  ? " typing-active"
                  : ""
              }`}
            >
              {m.type === "voice-processing" ? (
                <div className="voice-wave">
                  <span className="wave-bar" />
                  <span className="wave-bar" />
                  <span className="wave-bar" />
                  <span className="wave-bar" />
                  <span className="wave-bar" />
                  <span className="wave-label">Processing voice...</span>
                </div>
              ) : m.type === "audio" ? (
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
              <span className="dot" />
              <span className="dot" />
              <span className="dot" />
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
            disabled={(status !== "Ready" && status !== "Recording...") || isTyping}
            title={isRecording ? "Stop recording" : "Start recording"}
          >
            {isRecording ? "⏹️" : "🎙️"}
          </button>
          <button
            className="send-btn"
            onClick={() => sendText()}
            disabled={status !== "Ready" || isTyping}
          >
            <svg
              width="18" height="18" viewBox="0 0 24 24" fill="none"
              stroke="currentColor" strokeWidth="2.5"
              strokeLinecap="round" strokeLinejoin="round"
            >
              <line x1="22" y1="2" x2="11" y2="13" />
              <polygon points="22 2 15 22 11 13 2 9 22 2" />
            </svg>
          </button>
        </div>
        <p className="disclaimer">
          RCEE Assistant may make mistakes. Verify important info with the college directly.
        </p>
      </div>
    </div>
  );
}