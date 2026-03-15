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
  const [isTyping, setIsTyping] = useState(false);
  const typingRef = useRef({ fullText: "", index: 0, timer: null });

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
  }, [messages, isLoading, isTyping]);

  // Cleanup typewriter timer on unmount
  useEffect(() => {
    return () => {
      if (typingRef.current.timer) clearInterval(typingRef.current.timer);
    };
  }, []);

  /* ---------------- TYPEWRITER EFFECT ---------------- */
  const startTypewriter = (fullText) => {
    // Clear any existing timer
    if (typingRef.current.timer) clearInterval(typingRef.current.timer);

    // Add empty assistant message
    setMessages((m) => [...m, { role: "assistant", type: "text", data: "" }]);
    setIsTyping(true);
    typingRef.current = { fullText, index: 0, timer: null };

    const CHARS_PER_TICK = 3; // characters per interval
    const TICK_MS = 15; // milliseconds per tick

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
        // Ensure final text is complete
        setMessages((m) => {
          const updated = [...m];
          updated[updated.length - 1] = { role: "assistant", type: "text", data: typingRef.current.fullText };
          return updated;
        });
      }
    }, TICK_MS);
  };

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

  /* ---------------- VOICE (mic → n8n, original behavior) ---------------- */
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
      // console.log("🎙️ [MIC] Recording started — MIME:", recorderRef.current.mimeType);
      recorderRef.current.ondataavailable = (e) => {
        // console.log("🎙️ [MIC] Chunk received — size:", e.data.size, "bytes");
        chunksRef.current.push(e.data);
      };
      recorderRef.current.onstop = sendVoice;
      recorderRef.current.start();
      setIsRecording(true);
      setStatus("Recording...");
    } catch (err) {
      console.error("🎙️ [MIC] Error:", err);
      alert("Could not access microphone. Please check permissions.");
    }
  };

  const stopRecording = () => {
    if (recorderRef.current && recorderRef.current.state !== "inactive") {
      // console.log("🎙️ [MIC] Stopping recording...");
      recorderRef.current.stop();
      recorderRef.current.stream?.getTracks().forEach((t) => t.stop());
      setIsRecording(false);
      setStatus("Processing...");
    }
  };

  const sendVoice = async () => {
    // console.log("🎙️ [MIC] sendVoice called — chunks:", chunksRef.current.length);
    
    // Check the mimeType from MediaRecorder
    const mimeType = recorderRef.current?.mimeType || "audio/webm";
    // console.log("🎙️ [MIC] Using mimeType:", mimeType);
    
    const audioBlob = new Blob(chunksRef.current, { type: mimeType });
    // console.log("🎙️ [MIC] Blob created — size:", audioBlob.size, "bytes, type:", audioBlob.type);

    if (audioBlob.size < 1000) {
      // console.warn("⚠️ [MIC] Audio blob too small! Recording may have failed.");
    }

    // Show a voice processing animation while we wait
    setMessages((m) => [...m, { role: "user", type: "voice-processing", data: "__VOICE_PROCESSING__" }]);
    setIsLoading(true);

    const formData = new FormData();
    // Use the actual file extension based on mimeType
    const fileExt = mimeType.includes("webm") ? "webm" : "wav";
    formData.append("file", audioBlob, `voice.${fileExt}`);

    // Send to the new transcription endpoint in main.py
    const TRANSCRIBE_URL = "http://localhost:8000/transcribe";
    // console.log("🎙️ [MIC] Sending to:", TRANSCRIBE_URL);

    try {
      const res = await fetch(TRANSCRIBE_URL, { method: "POST", body: formData });
      // console.log("🎙️ [MIC] Response status:", res.status, res.statusText);
      
      if (!res.ok) {
        throw new Error(`HTTP ${res.status}`);
      }
      
      const data = await res.json();
      // console.log("🎙️ [MIC] Response data:", data);
      
      // Handle the transcription response
      if (data.transcript) {
        // Update the voice-processing bubble with the transcript
        setMessages((m) => {
          const updated = [...m];
          for (let i = updated.length - 1; i >= 0; i--) {
            if (updated[i].role === "user" && updated[i].data === "__VOICE_PROCESSING__") {
              updated[i] = { role: "user", type: "text", data: data.transcript };
              break;
            }
          }
          return updated;
        });
        
        // Send to n8n API (transcript is already shown in messages above)
        try {
          const res = await fetch(API_URL, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ query: data.transcript, voice_mode: true }),
          });
          await handleResponse(res);
        } catch (error) {
          setMessages((m) => [...m, { role: "assistant", type: "text", data: "⚠️ Network error. Please try again." }]);
          setStatus("Ready");
          setIsLoading(false);
        }
      } else {
        // No transcript received
        setMessages((m) => [...m, {
          role: "assistant", type: "text",
          data: "🎤 I couldn't hear that clearly. Please hold the mic button, speak clearly, then release."
        }]);
        setStatus("Ready");
        setIsLoading(false);
      }
    } catch (err) {
      console.error("🎙️ [MIC] Fetch error:", err);
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
      // console.log("📡 Raw n8n response:", rawText); // debug — remove later
      if (!rawText.trim()) throw new Error("Empty response");

      let data;
      try {
        data = JSON.parse(rawText);
        if (Array.isArray(data)) data = data[0];
      } catch {
        // n8n sent plain text (not JSON) — use directly
        setIsLoading(false);
        setStatus("Ready");
        startTypewriter(rawText);
        return;
      }
      // console.log("📡 [RESPONSE] Full data object:", data); // Debug: show full response
      
      let text = data.answer;
      
      // If answer is empty, check for nested translator output
      if (!text || text.trim() === "") {
        // Check if translator model output is in the response
        if (data.DB?.output?.[0]?.translated_text) {
          text = data.DB.output[0].translated_text;
          // console.log("🌐 [RESPONSE] Using DB.output[0].translated_text:", text);
        } else if (data.Translator?.translated_text) {
          text = data.Translator.translated_text;
          // console.log("🌐 [RESPONSE] Using Translator.translated_text:", text);
        } else if (data.translated_text) {
          text = data.translated_text;
          // console.log("🌐 [RESPONSE] Using translated_text:", text);
        }
      }
      
      // Fallback to other common response fields
      if (!text || text.trim() === "") {
        text = Array.isArray(data.output) ? data.output[0]?.content?.[0]?.text : null;
        // console.log("🔄 [RESPONSE] Using output[0].content[0].text:", text);
      }
      
      if (!text || text.trim() === "") {
        text = (typeof data.output === "string" ? data.output : null);
        // console.log("🔄 [RESPONSE] Using output as string:", text);
      }
      
      if (!text || text.trim() === "") {
        text = data.text;
        // console.log("🔄 [RESPONSE] Using text field:", text);
      }
      
      text = text || "";

      setIsLoading(false);
      setStatus("Ready");

      if (text) {
        startTypewriter(text);
      } else {
        // n8n returned empty answer — voice wasn't captured properly
        setMessages((m) => [...m, {
          role: "assistant", type: "text",
          data: "🎤 I couldn't hear that clearly. Please hold the mic button, speak clearly, then release."
        }]);
      }
    } catch (err) {
      setMessages((m) => [...m, {
        role: "assistant", type: "text",
        data: "⚠️ **Error:** Received an invalid response. Please check the server."
      }]);
    }

    if (!isTyping) {
      setStatus("Ready");
      setIsLoading(false);
    }
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
            <div className={`msg ${m.role}${isTyping && m.role === "assistant" && i === messages.length - 1 ? " typing-active" : ""}`}>
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
          {/* MIC BUTTON — original: record → send to n8n */}
          <button
            className={`mic-btn ${isRecording ? "recording" : ""}`}
            onClick={toggleRecording}
            disabled={(status !== "Ready" && status !== "Recording...") || isTyping}
            title={isRecording ? "Stop recording" : "Start recording"}
          >
            {isRecording ? "⏹️" : "🎙️"}
          </button>
          <button className="send-btn" onClick={() => sendText()} disabled={status !== "Ready" || isTyping}>
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