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

  // NEW: refs for immediate stop
  const currentAudioRef = useRef(null);   // currently playing Audio element
  const callAbortRef    = useRef(null);   // AbortController for in-flight fetch

  useEffect(() => {
    document.documentElement.setAttribute("data-theme", "dark");
  }, []);

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

  /* ─────────────── BUILD CHAT HISTORY ─────────────── */
  const buildChatHistory = (extraMsg = null) => {
    const all = extraMsg ? [...messages, extraMsg] : [...messages];
    const recent = all
      .filter(m => !m._isVoice && m.data)
      .slice(-10);                         // last 10 messages ≈ 5 turns

    if (recent.length === 0) return "";

    return recent
      .map(m => `${m.role === "user" ? "Student" : "Assistant"}: ${m.data}`)
      .join("\n");
  };

  /* ─────────────── FORMAT MARKDOWN ─────────────── */
  const formatMarkdown = (text) => {
    if (!text) return text;
    // Add double newline before numbered items (1. 2. 3.) when inline
    let formatted = text.replace(/([^\n])\s*(\d+\.\s)/g, '$1\n\n$2');
    // Add double newline before bullet points (- or *) when inline
    formatted = formatted.replace(/([^\n])\s*([\-\*]\s)/g, '$1\n\n$2');
    return formatted.trim();
  };

  /* ─────────────── TEXT SEND ─────────────── */
  const sendText = async (text) => {
    const msg = (text || input).trim();
    if (!msg || busy || isTyping) return;
    setMessages(m => [...m, { role: "user", data: msg }]);
    setInput("");
    setBusy(true);

    const chatHistory = buildChatHistory({ role: "user", data: msg });

    try {
      const res = await fetch(API_URL, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ query: msg, chat_history: chatHistory }),
      });
      const { answer } = await extractResponse(res);
      setBusy(false);
      answer ? typewrite(formatMarkdown(answer)) : pushError("No response received.");
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
    const base     = stripCodec(recorderRef.current?.mimeType || recorderRef.current?._mime || "");
    const safeType = base.includes("mp4") ? "audio/mp4" : base.includes("ogg") ? "audio/ogg" : "audio/webm";
    const ext      = safeType.includes("mp4") ? "mp4" : safeType.includes("ogg") ? "ogg" : "webm";
    const blob     = new Blob(chunksRef.current, { type: safeType });
    if (blob.size < 500) return pushError("No audio detected.");

    setMessages(m => [...m, { role: "user", data: "🎤 Voice message", _isVoice: true }]);
    setBusy(true);

    const chatHistory = buildChatHistory();

    const fd = new FormData();
    fd.append("file", new File([blob], `voice.${ext}`, { type: safeType }));
    fd.append("chat_history", JSON.stringify(chatHistory));

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
      answer ? typewrite(formatMarkdown(answer)) : pushError("Couldn't understand. Please try again.");
    } catch (e) {
      setBusy(false);
      pushError(`Voice failed: ${e.message}`);
    }
  };

  /* ─────────────── CALL FEATURE ─────────────── */
  const startCall = async () => {
    try {
      setCallState(CALL.CONNECTING);

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

    const buf = new Uint8Array(analyserRef.current.fftSize);
    const VOICE_THRESHOLD = 6;    // RMS level to detect speech
    const SILENCE_TIMEOUT = 2000; // 2s silence after speaking → send

    let voiceDetected = false;
    let silentMs = 0;

    // Phase 1: Just listen — don't record yet
    setCallState(CALL.LISTENING);

    silenceLoop.current = setInterval(() => {
      if (!callActive.current) {
        clearInterval(silenceLoop.current);
        return;
      }

      analyserRef.current.getByteTimeDomainData(buf);
      const rms = Math.sqrt(
        buf.reduce((s, v) => s + (v - 128) ** 2, 0) / buf.length
      );

      if (!voiceDetected) {
        // ── Phase 1: Waiting for voice activity ──
        if (rms >= VOICE_THRESHOLD) {
          voiceDetected = true;
          silentMs = 0;

          // NOW start recording
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
        }
      } else {
        // ── Phase 2: Recording — stop after 2s silence ──
        if (rms < 4) {
          silentMs += 100;
          if (silentMs >= SILENCE_TIMEOUT) {
            clearInterval(silenceLoop.current);
            if (callRecorder.current?.state !== "inactive") {
              callRecorder.current.stop();
            }
          }
        } else {
          silentMs = 0;
        }
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

    // 8. Tell backend to cancel + clean up
    fetch(CALL_API_URL + "/end", {
      method:  "POST",
      headers: { "Content-Type": "application/json" },
      body:    JSON.stringify({ session_id: "call" }),
    }).catch(() => {});

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
  const hasMessages = messages.length > 0;

  /* ─────────────── RENDER ─────────────── */
  return (
    <div className="page">
      {/* Grid overlay for depth */}
      <div className="grid-overlay" />

      {/* HEADER */}
      <header className="chat-header">
        <div className="header-left">
          <img src="/image.png" alt="RCEE" className="header-logo" />
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
                <div className="call-avatar-inner"><img src="/image.png" alt="RCEE" className="call-logo-img" /></div>
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
        {/* ── WELCOME SCREEN (no messages yet) ── */}
        {!hasMessages && !isCallActive && (
          <div className="empty-state">
            <div className="empty-avatar">
              <div className="avatar-ring" />
              <img src="/image.png" alt="RCEE" className="empty-logo-img" />
            </div>

            <h1 className="empty-title">What's on your mind today?</h1>
            <p className="empty-subtitle">Ask me anything about Ramachandra College of Engineering</p>

            {/* Centered input on welcome screen */}
            <div className="empty-input-container">
              <div className="empty-input-bar">
                <input
                  value={input}
                  onChange={e => setInput(e.target.value)}
                  placeholder="Message RCEE Assistant..."
                  onKeyDown={e => e.key === "Enter" && sendText()}
                  disabled={isRecording}
                />
                <button
                  className={`mic-btn ${isRecording ? "recording" : ""}`}
                  onClick={toggleRecording}
                  disabled={busy || isTyping || input.trim().length > 0}
                  title={isRecording ? "Stop recording" : "Start voice"}
                >
                  {isRecording ? "⏹️" : "🎙️"}
                </button>
                <button
                  className="call-btn"
                  onClick={startCall}
                  disabled={busy || isTyping || isRecording || input.trim().length > 0}
                  title="Start voice call"
                >
                  📞
                </button>
                <button
                  className="send-btn"
                  onClick={() => sendText()}
                  disabled={busy || isTyping || isRecording}
                >
                  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M12 19V5M5 12l7-7 7 7" />
                  </svg>
                </button>
              </div>
            </div>

            {/* Quick suggestion chips */}
            <div className="quick-chips">
              {QUICK_QUESTIONS.map(q => (
                <button key={q} className="chip" onClick={() => sendText(q)}>{q}</button>
              ))}
            </div>
          </div>
        )}

        {/* ── MESSAGES ── */}
        {messages.map((m, i) => (
          <div key={i} className={`msg-row ${m.role}`}>
            {m.role === "assistant" && (
              <div className="msg-avatar">
                <img src="/image.png" alt="RCEE" className="msg-logo-img" />
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
            <div className="msg-avatar">
              <img src="/image.png" alt="RCEE" className="msg-logo-img" />
            </div>
            <div className="msg assistant typing-bubble">
              <span className="dot"/><span className="dot"/><span className="dot"/>
            </div>
          </div>
        )}
        <div ref={chatEndRef} />
      </div>

      {/* INPUT BAR — shown only when there are messages and not during call */}
      {hasMessages && !isCallActive && (
        <div className="input-bar-wrap">
          <div className="input-bar">
            <input
              value={input}
              onChange={e => setInput(e.target.value)}
              placeholder="Ask anything about RCEE…"
              onKeyDown={e => e.key === "Enter" && sendText()}
              disabled={isRecording}
            />
            <button
              className={`mic-btn ${isRecording ? "recording" : ""}`}
              onClick={toggleRecording}
              disabled={busy || isTyping || input.trim().length > 0}
              title={isRecording ? "Stop recording" : "Start voice"}
            >
              {isRecording ? "⏹️" : "🎙️"}
            </button>
            <button
              className="call-btn"
              onClick={startCall}
              disabled={busy || isTyping || isRecording || input.trim().length > 0}
              title="Start voice call"
            >
              📞
            </button>
            <button
              className="send-btn"
              onClick={() => sendText()}
              disabled={busy || isTyping || isRecording}
            >
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                <path d="M12 19V5M5 12l7-7 7 7" />
              </svg>
            </button>
          </div>
          <p className="disclaimer">RCEE Assistant may make mistakes. Verify important info with the college directly.</p>
        </div>
      )}
    </div>
  );
}