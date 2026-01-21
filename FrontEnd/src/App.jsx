import { useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import "./App.css";

// Fallback to localhost if env variable is not set
const API_URL = import.meta.env.VITE_API_URL || "http://localhost:5678/webhook/chat";

export default function App() {
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState("");
  const [status, setStatus] = useState("Ready");
  const [isRecording, setIsRecording] = useState(false);

  const recorderRef = useRef(null);
  const chunksRef = useRef([]);

  /* ---------------- TEXT ---------------- */
  const sendText = async () => {
    if (!input.trim() || status !== "Ready") return;

    // Add User Message immediately
    setMessages((m) => [...m, { role: "user", type: "text", data: input }]);
    const currentInput = input;
    setInput("");
    setStatus("Thinking...");

    try {
      const res = await fetch(API_URL, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ query: currentInput, voice_mode: false }),
      });

      await handleResponse(res);
    } catch (error) {
      console.error("Network error:", error);
      alert("Network error: " + error.message);
      setStatus("Ready");
    }
  };

  /* ---------------- VOICE ---------------- */
  const toggleRecording = async () => {
    if (isRecording) {
      stopRecording();
    } else {
      startRecording();
    }
  };

  const startRecording = async () => {
    if (status !== "Ready") return;

    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      recorderRef.current = new MediaRecorder(stream);
      chunksRef.current = [];

      recorderRef.current.ondataavailable = (e) =>
        chunksRef.current.push(e.data);

      recorderRef.current.onstop = sendVoice;
      recorderRef.current.start();
      setIsRecording(true);
      setStatus("Recording...");
    } catch (error) {
      console.error("Microphone error:", error);
      alert("Could not access microphone. Please check permissions.");
    }
  };

  const stopRecording = () => {
    if (recorderRef.current && recorderRef.current.state !== "inactive") {
      recorderRef.current.stop();
      recorderRef.current.stream?.getTracks().forEach(track => track.stop());
      setIsRecording(false);
      setStatus("Processing...");
    }
  };

  const sendVoice = async () => {
    const audioBlob = new Blob(chunksRef.current, { type: "audio/wav" });
    
    // Add Placeholder for User Voice
    setMessages((m) => [...m, { role: "user", type: "audio", data: "🎤 Voice message" }]);
    
    const formData = new FormData();
    formData.append("file", audioBlob, "voice.wav");
    formData.append("voice_mode", "true");

    try {
      const res = await fetch(API_URL, {
        method: "POST",
        body: formData,
      });

      await handleResponse(res);
    } catch (error) {
      console.error("Voice request failed:", error);
      alert("Voice request failed: " + error.message);
      setStatus("Ready");
    }
  };

  /* ---------------- RESPONSE HANDLER (FIXED) ---------------- */
  const handleResponse = async (res) => {
    if (!res.ok) {
      throw new Error(`HTTP error! status: ${res.status}`);
    }

    const contentType = res.headers.get("content-type") || "";
    console.log("Content-Type received:", contentType); // Debug Log

    // CASE A: Audio Response (Voice Mode)
    if (contentType.includes("audio") || contentType.includes("application/octet-stream")) {
      setStatus("Speaking...");
      try {
        const blob = await res.blob();
        const audioUrl = URL.createObjectURL(blob);
        const audio = new Audio(audioUrl);
        
        audio.onended = () => {
          setStatus("Ready");
          URL.revokeObjectURL(audioUrl);
        };
        
        audio.onerror = (e) => {
          console.error("Audio playback error:", e);
          setStatus("Ready");
        };
        
        await audio.play();
        
        setMessages((m) => [...m, { role: "assistant", type: "audio", data: "🔊 Audio response" }]);
      } catch (err) {
        console.error("Audio handling failed:", err);
        setStatus("Ready");
      }
    } 
    // CASE B: Text/JSON Response (Chat Mode)
    else {
      try {
        // 1. READ RAW TEXT FIRST (Prevents crash on empty body)
        const rawText = await res.text();
        console.log("Raw Response from n8n:", rawText); // Debug Log

        if (!rawText.trim()) {
           throw new Error("Empty response received from n8n");
        }

        // 2. PARSE JSON SAFELY
        let data = JSON.parse(rawText);
        
        if (Array.isArray(data)) {
          data = data[0];
        }
        
        const text = data.output || data.text || "Done";
        
        setMessages((m) => [
          ...m,
          { role: "assistant", type: "text", data: text },
        ]);
      } catch (error) {
        console.error("Response processing error:", error);
        
        // Fallback: Show error in chat so you know what happened
        setMessages((m) => [
          ...m,
          { 
            role: "assistant", 
            type: "text", 
            data: "⚠️ **Error:** The server returned an empty or invalid response. Check the browser console (F12) for details." 
          },
        ]);
      }
      setStatus("Ready");
    }
  };

  return (
    <div className="page">
      <div className="chat-shell">
        <header className="chat-header">
          <span>RCE Agent</span>
          <span className={`status ${status !== "Ready" ? "busy" : ""}`}>
            ● {status}
          </span>
        </header>

        <div className="chat">
          {messages.length === 0 && (
            <div className="empty">
              <h2>Welcome to RCE</h2>
              <p>Ask anything about the college or use the mic.</p>
            </div>
          )}

          {messages.map((m, i) => (
            <div key={i} className={`msg ${m.role}`}>
              {m.type === "audio" ? (
                <span style={{ fontStyle: "italic", opacity: 0.8 }}>{m.data}</span>
              ) : (
                <ReactMarkdown remarkPlugins={[remarkGfm]}>
                  {m.data}
                </ReactMarkdown>
              )}
            </div>
          ))}
        </div>

        <div className="input-bar">
          <button
            onClick={toggleRecording}
            disabled={status !== "Ready" && status !== "Recording..."}
            title={isRecording ? "Stop recording" : "Start recording"}
            className={isRecording ? "recording" : ""}
          >
            {isRecording ? "⏹️" : "🎤"}
          </button>

          <input
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder="Type a message…"
            disabled={status !== "Ready"}
            onKeyDown={(e) => e.key === "Enter" && sendText()}
          />

          <button onClick={sendText} disabled={status !== "Ready"}>
            ➤
          </button>
        </div>
      </div>
    </div>
  );
}