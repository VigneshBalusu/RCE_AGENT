import { useRef, useState, useEffect } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import "./App.css";

const API_URL      = import.meta.env.VITE_API_URL      || "http://localhost:5678/webhook/chat";
const CALL_API_URL = import.meta.env.VITE_CALL_API_URL  || "http://localhost:8000/voice-call";

const QUICK_QUESTIONS = [
  "What are the BTech fees?",
  "Who is the Principal?",
  "What are the placement stats?",
  "How to apply for admission?",
];

// ── Strip codec suffix: "audio/webm;codecs=opus" → "audio/webm" ──
const stripCodec = (m = "") => m.split(";")[0].trim();

// ── Pick best MIME type MediaRecorder + Sarvam both accept ──
const getBestMime = () => {
  const candidates = [
    { mime: "audio/mp4",  ext: "mp4"  },
    { mime: "audio/webm", ext: "webm" },
    { mime: "audio/ogg",  ext: "ogg"  },
  ];
  for (const { mime, ext } of candidates) {
    if (MediaRecorder.isTypeSupported(mime))                      return { mime, ext };
    if (MediaRecorder.isTypeSupported(`${mime};codecs=opus`))     return { mime, ext };
    if (MediaRecorder.isTypeSupported(`${mime};codecs=aac`))      return { mime, ext };
  }
  return { mime: "", ext: "webm" };
};

// ── Call states ──
const CALL = {
  IDLE:       "idle",
  CONNECTING: "connecting",
  LISTENING:  "listening",
  PROCESSING: "processing",
  SPEAKING:   "speaking",
};

export default function App() {
  const [messages,   setMessages]   = useState([]);
  const [input,      setInput]      = useState("");
  const [busy,       setBusy]       = useState(false);
  const [isTyping,   setIsTyping]   = useState(false);
  const [theme,      setTheme]      = useState("dark");
  const [isRecording,setIsRecording]= useState(false);
  const [callState,  setCallState]  = useState(CALL.IDLE);

  const typingRef    = useRef({ text: "", index: 0, timer: null });
  const recorderRef  = useRef(null);
  const chunksRef    = useRef([]);
  const recordStart  = useRef(null);
  const chatEndRef   = useRef(null);

  // call refs
  const callActive      = useRef(false);
  const callRecorder    = useRef(null);
  const callChunks      = useRef([]);
  const audioCtxRef     = useRef(null);
  const analyserRef     = useRef(null);
  const silenceLoop     = useRef(null);
  const callMime        = useRef({ mime: "", ext: "webm" });
  const callStream      = useRef(null);
  const sessionIdRef    = useRef(null);

  // NEW: refs for immediate stop
  const currentAudioRef = useRef(null);   // currently playing Audio element
  const callAbortRef    = useRef(null);   // AbortController for in-flight fetch

  useEffect(() => {
    document.documentElement.setAttribute("data-theme", theme);
  }, [theme]);

  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, busy, isTyping]);

  useEffect(() => () => {
    if (typingRef.current.timer) clearInterval(typingRef.current.timer);
  }, []);

  /* ─────────────── TYPEWRITER ─────────────── */
  const typewrite = (fullText) => {
    if (typingRef.current.timer) clearInterval(typingRef.current.timer);
    setMessages(m => [...m, { role: "assistant", data: "" }]);
    setIsTyping(true);
    typingRef.current = { text: fullText, index: 0, timer: null };

    typingRef.current.timer = setInterval(() => {
      typingRef.current.index += 3;
      const slice = typingRef.current.text.slice(0, typingRef.current.index);
      setMessages(m => {
        const u = [...m];
        u[u.length - 1] = { role: "assistant", data: slice };
        return u;
      });
      if (typingRef.current.index >= typingRef.current.text.length) {
        clearInterval(typingRef.current.timer);
        typingRef.current.timer = null;
        setIsTyping(false);
        setMessages(m => {
          const u = [...m];
          u[u.length - 1] = { role: "assistant", data: typingRef.current.text };
          return u;
        });
      }
    }, 15);
  };

  /* ─────────────── TEXT SEND ─────────────── */
  /* ─────────────── TEXT SEND ─────────────── */
  const sendText = async (text) => {
    const msg = (text || input).trim();
    if (!msg || busy || isTyping) return;
    setMessages(m => [...m, { role: "user", data: msg }]);
    setInput("");
    setBusy(true);

    // NEW: Ensure session ID exists for this user session
    if (!sessionIdRef.current) {
      sessionIdRef.current = crypto.randomUUID?.() || `session_${Date.now()}`;
    }

    try {
      const res = await fetch(API_URL, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ 
          query: msg, 
          voice_mode: false,
          session_id: sessionIdRef.current // <-- ADDED HERE
        }),
      });
      const { answer } = await extractResponse(res);
      setBusy(false);
      answer ? typewrite(answer) : pushError("No response received.");
    } catch {
      setBusy(false);
      pushError("Network error. Please try again.");
    }
  };

  /* ─────────────── VOICE (mic button) ─────────────── */
  const toggleRecording = async () => {
    if (isRecording) stopVoice();
    else startVoice();
  };

  const startVoice = async () => {
    if (busy || isTyping) return;
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const { mime, ext } = getBestMime();
      recorderRef.current = new MediaRecorder(stream, mime ? { mimeType: mime } : {});
      recorderRef.current._mime = mime;
      recorderRef.current._ext  = ext;
      chunksRef.current    = [];
      recordStart.current  = Date.now();
      recorderRef.current.ondataavailable = e => e.data?.size > 0 && chunksRef.current.push(e.data);
      recorderRef.current.onstop          = submitVoice;
      recorderRef.current.start(250);
      setIsRecording(true);
    } catch { alert("Microphone access denied."); }
  };

  const stopVoice = () => {
    if (recorderRef.current?.state !== "inactive") {
      recorderRef.current.stop();
      recorderRef.current.stream?.getTracks().forEach(t => t.stop());
    }
    setIsRecording(false);
  };

  const submitVoice = async () => {
    if (Date.now() - recordStart.current < 1000) return pushError("Recording too short.");
    const base     = stripCodec(recorderRef.current?.mimeType || recorderRef.current?._mime || "");
    const safeType = base.includes("mp4") ? "audio/mp4" : base.includes("ogg") ? "audio/ogg" : "audio/webm";
    const ext      = safeType.includes("mp4") ? "mp4" : safeType.includes("ogg") ? "ogg" : "webm";
    const blob     = new Blob(chunksRef.current, { type: safeType });
    if (blob.size < 500) return pushError("No audio detected.");

    setMessages(m => [...m, { role: "user", data: "🎤 Voice message", _isVoice: true }]);
    setBusy(true);

    // NEW: Ensure session ID exists
    if (!sessionIdRef.current) {
      sessionIdRef.current = crypto.randomUUID?.() || `session_${Date.now()}`;
    }

    const fd = new FormData();
    fd.append("file", new File([blob], `voice.${ext}`, { type: safeType }));
    fd.append("session_id", sessionIdRef.current); // <-- ADDED HERE

    try {
      const res = await fetch(API_URL, { method: "POST", body: fd });
      const { answer, transcript } = await extractResponse(res);
      if (transcript) {
        setMessages(m => {
          const u = [...m];
          for (let i = u.length - 1; i >= 0; i--) {
            if (u[i]._isVoice) { u[i] = { role: "user", data: transcript }; break; }
          }
          return u;
        });
      }
      setBusy(false);
      answer ? typewrite(answer) : pushError("Couldn't understand. Please try again.");
    } catch (e) {
      setBusy(false);
      pushError(`Voice failed: ${e.message}`);
    }
  };

  /* ─────────────── CALL FEATURE ─────────────── */
  const startCall = async () => {
    try {
      setCallState(CALL.CONNECTING);
// Keep existing session if it exists, otherwise create one

  if (!sessionIdRef.current) {
    sessionIdRef.current = crypto.randomUUID?.() || `session_${Date.now()}`;
}

      let ready = false;
      let retries = 0;

      while (!ready && retries < 10) {
        try {
          const res = await fetch(CALL_API_URL + "/status");
          const data = await res.json();
          ready = data.ready;
        } catch {
          ready = false;
        }
        if (!ready) {
          retries++;
          await new Promise(r => setTimeout(r, 1000));
        }
      }

      if (!ready) {
        alert("Voice service is not available. Please try again later.");
        setCallState(CALL.IDLE);
        return;
      }

      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      callStream.current  = stream;
      callActive.current  = true;

      audioCtxRef.current = new AudioContext();
      analyserRef.current = audioCtxRef.current.createAnalyser();
      analyserRef.current.fftSize = 512;
      audioCtxRef.current.createMediaStreamSource(stream).connect(analyserRef.current);

      setCallState(CALL.LISTENING);
      beginCallListening();
    } catch (err) {
      console.error("Mic error:", err);
      alert("Microphone access denied: " + err.message);
      setCallState(CALL.IDLE);
    }
  };

  const beginCallListening = () => {
    if (!callActive.current) return;

    const { mime, ext } = getBestMime();
    callMime.current = { mime, ext };

    callRecorder.current = new MediaRecorder(
      callStream.current,
      mime ? { mimeType: mime } : {}
    );

    callChunks.current = [];

    callRecorder.current.ondataavailable = (e) => {
      if (e.data?.size > 0) callChunks.current.push(e.data);
    };

    callRecorder.current.onstop = submitCallAudio;
    callRecorder.current.start(250);

    const buf = new Uint8Array(analyserRef.current.fftSize);
    let silentMs = 0;

    silenceLoop.current = setInterval(() => {
      if (!callActive.current) {
        clearInterval(silenceLoop.current);
        return;
      }

      analyserRef.current.getByteTimeDomainData(buf);
      const rms = Math.sqrt(
        buf.reduce((s, v) => s + (v - 128) ** 2, 0) / buf.length
      );

      if (rms < 4) {
        silentMs += 100;
        if (silentMs >= 2000) {
          clearInterval(silenceLoop.current);
          if (callRecorder.current?.state !== "inactive") {
            callRecorder.current.stop();
          }
        }
      } else {
        silentMs = 0;
      }
    }, 100);
  };

  const playAudioBlob = (blob) =>
    new Promise((resolve) => {
      // If call already ended, don't play
      if (!callActive.current) return resolve();

      const url   = URL.createObjectURL(blob);
      const audio = new Audio(url);

      // Store reference so endCall can stop it
      currentAudioRef.current = audio;

      audio.onended = () => {
        currentAudioRef.current = null;
        URL.revokeObjectURL(url);
        resolve();
      };

      audio.onerror = () => {
        currentAudioRef.current = null;
        URL.revokeObjectURL(url);
        resolve();
      };

      audio.play().catch(() => {
        currentAudioRef.current = null;
        resolve();
      });
    });

  const submitCallAudio = async () => {
    if (!callActive.current) return;

    const { mime } = callMime.current;
    const base     = stripCodec(callRecorder.current?.mimeType || mime || "");
    const safeType = base.includes("mp4") ? "audio/mp4" : base.includes("ogg") ? "audio/ogg" : "audio/webm";
    const safeExt  = safeType.includes("mp4") ? "mp4" : safeType.includes("ogg") ? "ogg" : "webm";
    const blob     = new Blob(callChunks.current, { type: safeType });

    if (blob.size < 500) {
      if (callActive.current) {
        setCallState(CALL.LISTENING);
        beginCallListening();
      }
      return;
    }

    setCallState(CALL.PROCESSING);

    const fd = new FormData();
    fd.append("file", new File([blob], `call.${safeExt}`, { type: safeType }));
    fd.append("session_id", sessionIdRef.current);

    // Create AbortController so endCall can cancel this request
    const controller   = new AbortController();
    callAbortRef.current = controller;

    try {
      const res = await fetch(CALL_API_URL, {
        method: "POST",
        body:   fd,
        signal: controller.signal,
      });

      // Check if call ended while we were waiting
      if (!callActive.current) return;

      if (!res.ok) throw new Error(`HTTP ${res.status}`);

      const audioBlob = await res.blob();

      // Check again after getting response
      if (!callActive.current) return;

      setCallState(CALL.SPEAKING);
      await playAudioBlob(audioBlob);

      if (callActive.current) {
        setCallState(CALL.LISTENING);
        beginCallListening();
      }
    } catch (e) {
      // If aborted by endCall, exit silently
      if (e.name === "AbortError") {
        console.log("[CALL] Request aborted by user");
        return;
      }

      console.error("[CALL] Error:", e);

      if (callActive.current) {
        setCallState(CALL.LISTENING);
        beginCallListening();
      }
    } finally {
      callAbortRef.current = null;
    }
  };

  const endCall = () => {
    // 1. Flag off immediately — all loops and checks will see this
    callActive.current = false;

    // 2. Stop audio playback immediately
    if (currentAudioRef.current) {
      currentAudioRef.current.pause();
      currentAudioRef.current.currentTime = 0;
      currentAudioRef.current.src = "";
      currentAudioRef.current = null;
    }

    // 3. Abort any in-flight API request
    if (callAbortRef.current) {
      callAbortRef.current.abort();
      callAbortRef.current = null;
    }

    // 4. Stop silence detection loop
    clearInterval(silenceLoop.current);

    // 5. Stop recorder WITHOUT triggering submitCallAudio
    if (callRecorder.current?.state !== "inactive") {
      callRecorder.current.onstop = null;   // ← prevent submit
      callRecorder.current.stop();
    }

    // 6. Stop microphone tracks
    callStream.current?.getTracks().forEach(t => t.stop());

    // 7. Close audio context
    if (audioCtxRef.current) {
      audioCtxRef.current.close();
      audioCtxRef.current = null;
    }
    callStream.current = null;

    // 8. Tell backend to cancel + clean up session
    if (sessionIdRef.current) {
      fetch(CALL_API_URL + "/end", {
        method:  "POST",
        headers: { "Content-Type": "application/json" },
        body:    JSON.stringify({ session_id: sessionIdRef.current }),
      }).catch(() => {});
      sessionIdRef.current = null;
    }

    // 9. Reset UI state
    setCallState(CALL.IDLE);
  };

  /* ─────────────── HELPERS ─────────────── */
  const extractResponse = async (res) => {
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const raw = await res.text();
    if (!raw.trim()) throw new Error("Empty response");
    try {
      let d = JSON.parse(raw);
      if (Array.isArray(d)) d = d[0];
      const answer = d.answer || d.translated_text || d.text ||
        (typeof d.output === "string" ? d.output : null) ||
        d.output?.[0]?.content?.[0]?.text || "";
      return { answer, transcript: d.transcript || "" };
    } catch { return { answer: raw, transcript: "" }; }
  };

  const pushError = (msg) => {
    setMessages(m => [...m, { role: "assistant", data: `⚠️ ${msg}` }]);
  };

  const isCallActive = callState !== CALL.IDLE;

  /* ─────────────── RENDER ─────────────── */
  return (
    <div className="page">
      {/* HEADER */}
      <header className="chat-header">
        <div className="header-left">
          <div className="logo-dot" />
          <span className="header-title">RCEE Assistant</span>
        </div>
        <div className="header-right">
          {isCallActive ? (
            <span className={`call-pill ${callState}`}>
              <span className="call-dot" />
              {callState === CALL.CONNECTING && "Connecting..."}
              {callState === CALL.LISTENING  && "Listening..."}
              {callState === CALL.PROCESSING && "Thinking..."}
              {callState === CALL.SPEAKING   && "Speaking..."}
            </span>
          ) : (
            <span className={`status-pill ${busy ? "busy" : ""}`}>
              <span className="status-dot" />
              {busy ? "Thinking..." : "Ready"}
            </span>
          )}
          <button
            className="theme-toggle"
            onClick={() => setTheme(t => t === "dark" ? "light" : "dark")}
            title="Toggle theme"
          >
            {theme === "dark" ? "☀️" : "🌙"}
          </button>
        </div>
      </header>

      {/* CALL OVERLAY */}
      {isCallActive && (
        <div className="call-overlay">
          {callState === CALL.CONNECTING ? (
            <div className="call-connecting">
              <div className="connecting-spinner" />
              <p className="call-status-text">Connecting to RCEE Assistant...</p>
            </div>
          ) : (
            <>
              <div className="call-avatar-wrap">
                <div className={`call-ring ${callState}`} />
                <div className={`call-ring2 ${callState}`} />
                <div className="call-avatar-inner">RCE</div>
              </div>
              <p className="call-status-text">
                {callState === CALL.LISTENING  && "Speak now…"}
                {callState === CALL.PROCESSING && "Processing your query…"}
                {callState === CALL.SPEAKING   && "RCEE Assistant is speaking…"}
              </p>
            </>
          )}
          <button className="end-call-btn" onClick={endCall}>
            <svg width="22" height="22" viewBox="0 0 24 24" fill="currentColor">
              <path d="M6.6 10.8c1.4 2.8 3.8 5.1 6.6 6.6l2.2-2.2c.3-.3.7-.4 1-.2 1.1.4 2.3.6 3.6.6.6 0 1 .4 1 1V20c0 .6-.4 1-1 1-9.4 0-17-7.6-17-17 0-.6.4-1 1-1h3.5c.6 0 1 .4 1 1 0 1.3.2 2.5.6 3.6.1.3 0 .7-.2 1L6.6 10.8z"/>
            </svg>
            End Call
          </button>
        </div>
      )}

      {/* CHAT AREA */}
      <div className={`chat ${isCallActive ? "call-active" : ""}`}>
        {messages.length === 0 && !isCallActive && (
          <div className="empty-state">
            <div className="empty-avatar">
              <div className="avatar-ring" />
              <svg width="40" height="40" viewBox="0 0 40 40" fill="none" xmlns="http://www.w3.org/2000/svg" style={{ zIndex: 1 }}>
                <text x="50%" y="50%" dominantBaseline="central" textAnchor="middle"
                  fontFamily="-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif"
                  fontWeight="700" fontSize="13" fill="url(#lg)" letterSpacing="-0.5">RCE</text>
                <defs>
                  <linearGradient id="lg" x1="0" y1="0" x2="40" y2="40" gradientUnits="userSpaceOnUse">
                    <stop stopColor="#3b82f6"/><stop offset="1" stopColor="#8b5cf6"/>
                  </linearGradient>
                </defs>
              </svg>
            </div>
            <h1 className="empty-title">Hello! I'm RCEE Assistant</h1>
            <p className="empty-subtitle">Ask me anything about Ramachandra College of Engineering</p>
            <div className="quick-chips">
              {QUICK_QUESTIONS.map(q => (
                <button key={q} className="chip" onClick={() => sendText(q)}>{q}</button>
              ))}
            </div>
          </div>
        )}

        {messages.map((m, i) => (
          <div key={i} className={`msg-row ${m.role}`}>
            {m.role === "assistant" && (
              <div className="msg-avatar">
                <svg width="16" height="16" viewBox="0 0 40 40" fill="none">
                  <text x="50%" y="50%" dominantBaseline="central" textAnchor="middle"
                    fontFamily="-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif"
                    fontWeight="700" fontSize="14" fill="url(#lg2)" letterSpacing="-0.5">RCE</text>
                  <defs>
                    <linearGradient id="lg2" x1="0" y1="0" x2="40" y2="40" gradientUnits="userSpaceOnUse">
                      <stop stopColor="#60a5fa"/><stop offset="1" stopColor="#a78bfa"/>
                    </linearGradient>
                  </defs>
                </svg>
              </div>
            )}
            <div className={`msg ${m.role}${isTyping && m.role === "assistant" && i === messages.length - 1 ? " typing-active" : ""}`}>
              {m._isVoice ? (
                <div className="voice-processing">
                  <span className="vbar"/><span className="vbar"/><span className="vbar"/>
                  <span className="vbar"/><span className="vbar"/>
                </div>
              ) : (
                <ReactMarkdown remarkPlugins={[remarkGfm]}>{m.data}</ReactMarkdown>
              )}
            </div>
          </div>
        ))}

        {busy && (
          <div className="msg-row assistant">
            <div className="msg-avatar">🎓</div>
            <div className="msg assistant typing-bubble">
              <span className="dot"/><span className="dot"/><span className="dot"/>
            </div>
          </div>
        )}
        <div ref={chatEndRef} />
      </div>

      {/* INPUT BAR — hidden during call */}
      {!isCallActive && (
        <div className="input-bar-wrap">
          <div className="input-bar">
            <input
              value={input}
              onChange={e => setInput(e.target.value)}
              placeholder="Ask anything about RCEE…"
              onKeyDown={e => e.key === "Enter" && sendText()}
            />
            <button
              className={`mic-btn ${isRecording ? "recording" : ""}`}
              onClick={toggleRecording}
              disabled={busy || isTyping}
              title={isRecording ? "Stop recording" : "Start voice"}
            >
              {isRecording ? "⏹️" : "🎙️"}
            </button>
            <button
              className="call-btn"
              onClick={startCall}
              disabled={busy || isTyping}
              title="Start voice call"
            >
              📞
            </button>
            <button
              className="send-btn"
              onClick={() => sendText()}
              disabled={busy || isTyping}
            >
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                <line x1="22" y1="2" x2="11" y2="13"/>
                <polygon points="22 2 15 22 11 13 2 9 22 2"/>
              </svg>
            </button>
          </div>
          <p className="disclaimer">RCEE Assistant may make mistakes. Verify important info with the college directly.</p>
        </div>
      )}
    </div>
  );
}