"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Maximize2, Minimize2, MoreHorizontal } from "lucide-react";
import {
  DEFAULT_PREFERRED_VOICES,
  sanitizeForSpeech,
  selectPreferredVoices,
  splitForSpeech,
} from "./speech";

const initialEvents = [
  { type: "system", label: "Varyn online" },
  { type: "provider", label: "Provider connecting" },
  { type: "system", label: "Local agent connecting" },
  { type: "risk", label: "Risk engine active" },
];

const STARFIELD_STARS = Array.from({ length: 400 }, (_, index) => {
  const pairIndex = Math.floor(index / 2);
  const isLeft = index % 2 === 0;
  const left = isLeft
    ? 4 + ((pairIndex * 23) % 43)
    : 54 + ((pairIndex * 19) % 43);
  const top = 5 + ((pairIndex * (isLeft ? 31 : 37)) % 90);

  return {
    id: `star-${index + 1}`,
    style: {
      animationDelay: `-${(index * 0.06).toFixed(2)}s`,
      left: `${left}%`,
      top: `${top}%`,
    },
  };
});

function shouldStopSpeech(input, stopPhrases) {
  const text = input.toLowerCase().replace(/[^\w\s]/g, " ").replace(/\s+/g, " ").trim();
  return stopPhrases.some((phrase) => text === phrase || text.startsWith(`${phrase} `));
}

function createSessionId() {
  const randomPart = Math.random().toString(36).slice(2, 9);
  return `varyn-${Date.now()}-${randomPart}`;
}

function cleanDisplayText(text) {
  if (!text) return "";
  return text
    .replace(/\*\*([^*]+)\*\*/g, "$1")
    .replace(/`([^`]+)`/g, "$1")
    .replace(/\[(.*?)\]\((.*?)\)/g, "$1");
}

function isMeaningfulAnalysis(analysis) {
  return Boolean(analysis?.title && Array.isArray(analysis?.modules) && analysis.modules.length > 0);
}

function formatTime(date = new Date()) {
  return date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

function formatMarketPrice(value) {
  const price = Number(value);
  if (!Number.isFinite(price)) return "Unavailable";
  return `$${price.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

function formatMarketChange(value) {
  const change = Number(value);
  if (!Number.isFinite(change)) return "--";
  return `${change >= 0 ? "+" : ""}${change.toFixed(2)}%`;
}

function formatMarketTimestamp(value) {
  if (!value) return null;
  const sampledAt = new Date(value);
  if (Number.isNaN(sampledAt.getTime())) return null;
  return sampledAt.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function formatFileSize(bytes) {
  if (!bytes) return "0 KB";
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function sourceStatusLabel(source) {
  if (!source) return "Awaiting";
  if (source.enabled === false) return "Disabled";
  const status = String(source.status || "unknown").toLowerCase();
  if (status === "active") return "Active";
  if (status === "degraded") return "Degraded";
  if (status === "unavailable") return "Unavailable";
  return "Awaiting";
}

function sourceHealthTitle(label, source) {
  if (!source) return `${label}: awaiting first source check`;
  const lastCheck = source.last_failed_pull || source.last_successful_pull;
  const details = [
    `${label}: ${sourceStatusLabel(source)}`,
    lastCheck ? `last checked ${new Date(lastCheck).toLocaleString()}` : null,
    source.last_error || null,
  ].filter(Boolean);
  return details.join(". ");
}

function voiceErrorMessage(error) {
  const messages = {
    "audio-capture": "Microphone capture failed. Check that a microphone is connected and available to the browser.",
    "not-allowed": "Microphone permission is blocked. Allow microphone access in the browser, then enable voice again.",
    "no-speech": "No speech detected. Try again when you are ready to speak.",
    network: "Speech recognition network service failed. Try typed input or restart voice.",
    aborted: "Voice capture was aborted.",
  };
  return messages[error] || `Voice recognition paused: ${error}.`;
}

function normalizeTranscript(text) {
  return String(text || "").replace(/\s+/g, " ").trim();
}

function isUsableTranscript(text) {
  const normalized = normalizeTranscript(text);
  return normalized.length >= 2 && /[a-z0-9]/i.test(normalized);
}

function isTextEntryTarget(target) {
  if (!(target instanceof HTMLElement)) return false;
  return Boolean(target.closest("input, textarea, select, button, [contenteditable='true']"));
}

function pushToTalkKeyLabel(code) {
  if (code === "Space") return "Space";
  return code.replace(/^Key/, "").replace(/^Digit/, "");
}

function downloadMemoArtifact(artifact) {
  if (!artifact?.content || artifact.encoding !== "base64") {
    throw new Error("This memo format is not available for browser download.");
  }
  const binary = window.atob(artifact.content);
  const bytes = new Uint8Array(binary.length);
  for (let index = 0; index < binary.length; index += 1) {
    bytes[index] = binary.charCodeAt(index);
  }
  const blob = new Blob([bytes], { type: artifact.mime_type || "application/octet-stream" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = artifact.filename || `varyn-risk-memo.${artifact.format || "bin"}`;
  document.body.appendChild(link);
  link.click();
  link.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export default function Home() {
  const [activeAnalysis, setActiveAnalysis] = useState(null);
  const [agentReply, setAgentReply] = useState("");
  const [activityLog, setActivityLog] = useState(initialEvents);
  const [command, setCommand] = useState("");
  const [currentTime, setCurrentTime] = useState("--:--:--");
  const [heardTranscript, setHeardTranscript] = useState("");
  const [isFullscreen, setIsFullscreen] = useState(false);
  const [listening, setListening] = useState(false);
  const [memoArtifacts, setMemoArtifacts] = useState([]);
  const [openMicAvailable, setOpenMicAvailable] = useState(true);
  const [openMicEnabled, setOpenMicEnabled] = useState(false);
  const [ownerAccessConfigured, setOwnerAccessConfigured] = useState(false);
  const [ownerAccessError, setOwnerAccessError] = useState("");
  const [ownerAccessKey, setOwnerAccessKey] = useState("");
  const [ownerAuthenticated, setOwnerAuthenticated] = useState(false);
  const [ownerPromptOpen, setOwnerPromptOpen] = useState(false);
  const [processing, setProcessing] = useState(false);
  const [pushToTalkHeld, setPushToTalkHeld] = useState(false);
  const [pushToTalkKey, setPushToTalkKey] = useState("Space");
  const [pendingConfirmation, setPendingConfirmation] = useState(null);
  const [sessionId, setSessionId] = useState(() => createSessionId());
  const [selectedFile, setSelectedFile] = useState(null);
  const [speaking, setSpeaking] = useState(false);
  const [status, setStatus] = useState("Online");
  const [system, setSystem] = useState({
    agent: "Standby",
    provider: "Connecting",
    model: "Configuring",
    backend: "Local Agent",
    memory: "Active",
    risk: "Active",
    market: "Active",
    voice: "Standby",
    heartbeat: "Starting",
    data: "Connecting",
  });
  const [voiceSupported, setVoiceSupported] = useState(true);
  const [voiceError, setVoiceError] = useState("");
  const [voiceMode, setVoiceMode] = useState("push-to-talk");
  const [heartbeatState, setHeartbeatState] = useState({
    enabled: true,
    running: false,
    proactivePaused: false,
    notices: [],
    heldNoticeCount: 0,
    nextDue: null,
    watchlist: [],
    marketSnapshot: {
      sampledAt: null,
      symbols: [],
      indexUpdatedAt: null,
    },
  });
  const [telemetry, setTelemetry] = useState({
    cpu: "N/A",
    mem: "N/A",
    net: "N/A",
    netLevel: 0,
    uptime: "--:--:--",
    proc: "N/A",
    os: "N/A",
    source: "connecting",
  });
  const [sourceHealth, setSourceHealth] = useState({
    overall: "unknown",
    sources: {},
    updatedAt: null,
  });
  const [voiceConfigReady, setVoiceConfigReady] = useState(false);

  const fileInputRef = useRef(null);
  const stageRef = useRef(null);
  const responseBoxRef = useRef(null);
  const recognitionRef = useRef(null);
  const listeningRef = useRef(false);
  const recognitionActiveRef = useRef(false);
  const recognitionStartPendingRef = useRef(false);
  const pauseForSpeechRef = useRef(false);
  const commandBufferRef = useRef("");
  const capturedTranscriptRef = useRef("");
  const interimTranscriptRef = useRef("");
  const submitOnEndRef = useRef(false);
  const intentionalAbortRef = useRef(false);
  const captureModeRef = useRef("push-to-talk");
  const pushToTalkHeldRef = useRef(false);
  const openMicEnabledRef = useRef(false);
  const openMicTurnPendingRef = useRef(false);
  const voiceModeRef = useRef("push-to-talk");
  const pushToTalkKeyRef = useRef("Space");
  const processCommandRef = useRef(null);
  const submitCapturedTranscriptRef = useRef(null);
  const silenceTimerRef = useRef(null);
  const recognitionRestartTimerRef = useRef(null);
  const lastCommandRef = useRef({ text: "", time: 0 });
  const speechRunRef = useRef(0);
  const preferredVoiceRef = useRef(null);
  const voiceCandidatesRef = useRef([]);
  const failedVoiceNamesRef = useRef(new Set());
  const voiceChoiceRef = useRef(DEFAULT_PREFERRED_VOICES[0]);
  const voicePreferredNamesRef = useRef(DEFAULT_PREFERRED_VOICES);
  const voiceRateRef = useRef(1.0);
  const voicePitchRef = useRef(0.99);
  const paragraphPauseRef = useRef(170);
  const voiceTestTextRef = useRef("Varyn voice online.");
  const voiceTestCompleteRef = useRef(false);
  const voiceTestRunRef = useRef(0);
  const speechPauseTimerRef = useRef(null);
  const telemetryErrorRef = useRef(false);
  const heartbeatErrorRef = useRef(false);
  const seenNoticeIdsRef = useRef(new Set());
  const stopPhrasesRef = useRef(["stop"]);

  const addLog = useCallback((entry) => {
    if (!entry?.label) return;
    setActivityLog((items) => {
      const now = Date.now();
      const last = items[0];
      const nextEntry = {
        time: formatTime(new Date(now)),
        ts: now,
        type: entry.type || "system",
        label: entry.label,
      };

      if (last?.label === nextEntry.label && last?.type === nextEntry.type && now - (last.ts || 0) < 3500) {
        return items;
      }

      return [nextEntry, ...items].slice(0, 14);
    });
  }, []);

  const requestOwnerAccess = useCallback(() => {
    setOwnerAccessError("");
    setOwnerPromptOpen(true);
  }, []);

  const requireOwner = useCallback((action) => {
    if (ownerAuthenticated) return true;
    setOwnerAccessError(`${action} requires owner access.`);
    setOwnerPromptOpen(true);
    return false;
  }, [ownerAuthenticated]);

  const submitOwnerAccess = useCallback(async (event) => {
    event.preventDefault();
    setOwnerAccessError("");
    try {
      const response = await fetch("/api/varyn/auth", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ accessKey: ownerAccessKey }),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok || !data.owner) throw new Error(data.error || "Owner access was not accepted.");
      setOwnerAuthenticated(true);
      setOwnerAccessKey("");
      setOwnerPromptOpen(false);
      addLog({ type: "system", label: "Owner access enabled" });
    } catch (error) {
      setOwnerAccessError(error.message || "Owner access was not accepted.");
    }
  }, [addLog, ownerAccessKey]);

  const logoutOwner = useCallback(async () => {
    await fetch("/api/varyn/auth", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action: "logout" }),
    }).catch(() => null);
    setOwnerAuthenticated(false);
    setPendingConfirmation(null);
    addLog({ type: "system", label: "Owner access disabled" });
  }, [addLog]);

  const state = useMemo(() => {
    if (speaking) return "speaking";
    if (processing) return "processing";
    if (listening) return "listening";
    if (activeAnalysis) return "analysis";
    return "idle";
  }, [activeAnalysis, listening, processing, speaking]);

  const dismissAnalysis = useCallback(() => {
    setActiveAnalysis(null);
    addLog({ type: "system", label: "Analysis panel dismissed" });
  }, [addLog]);

  const cancelSpeech = useCallback(
    (label = "Speech cancelled") => {
      speechRunRef.current += 1;
      if (typeof window !== "undefined") {
        window.speechSynthesis?.cancel();
        window.clearTimeout(silenceTimerRef.current);
        window.clearTimeout(recognitionRestartTimerRef.current);
        window.clearTimeout(speechPauseTimerRef.current);
      }
      pauseForSpeechRef.current = false;
      commandBufferRef.current = "";
      submitOnEndRef.current = false;
      openMicTurnPendingRef.current = false;
      setSpeaking(false);
      setStatus(label);
      setSystem((current) => ({
        ...current,
        voice: recognitionActiveRef.current ? "Listening" : openMicEnabledRef.current ? "Open mic ready" : "Standby",
      }));
      addLog({ type: "voice", label });
    },
    [addLog],
  );

  const restartRecognition = useCallback((captureMode = voiceModeRef.current) => {
    const recognition = recognitionRef.current;
    if (!recognition || recognitionActiveRef.current || recognitionStartPendingRef.current) return false;

    try {
      captureModeRef.current = captureMode;
      recognition.continuous = captureMode === "open-mic";
      recognitionStartPendingRef.current = true;
      recognition.start();
      return true;
    } catch {
      recognitionStartPendingRef.current = false;
      recognitionActiveRef.current = false;
      return false;
    }
  }, []);

  const speak = useCallback(
    (text) => {
      const speechParts = splitForSpeech(
        text,
        paragraphPauseRef.current,
      )
        .map((part) => ({
          ...part,
          text: part.sentences
            .map((sentence) => {
              const cleanSentence = sanitizeForSpeech(sentence);
              if (!cleanSentence) return "";
              const ending = sentence.match(/[.!?]+\s*$/)?.[0].trim().slice(-1) || ".";
              return `${cleanSentence}${ending}`;
            })
            .filter(Boolean)
            .join(" "),
        }))
        .filter((part) => part.text);
      if (typeof window === "undefined" || !window.speechSynthesis || speechParts.length === 0) return;

      const runId = speechRunRef.current + 1;
      speechRunRef.current = runId;
      voiceTestCompleteRef.current = true;
      voiceTestRunRef.current += 1;
      window.speechSynthesis.cancel();
      window.clearTimeout(speechPauseTimerRef.current);

      const recognition = recognitionRef.current;
      const shouldResume = openMicEnabledRef.current;
      if (recognition && recognitionActiveRef.current) {
        pauseForSpeechRef.current = true;
        recognition.stop();
        recognitionActiveRef.current = false;
      }

      const finishSpeech = () => {
        if (speechRunRef.current !== runId) return;
        setSpeaking(false);
        pauseForSpeechRef.current = false;
        openMicTurnPendingRef.current = false;
        setSystem((current) => ({ ...current, voice: shouldResume ? "Open mic ready" : "Standby" }));
        if (shouldResume && openMicEnabledRef.current) {
          window.clearTimeout(recognitionRestartTimerRef.current);
          recognitionRestartTimerRef.current = window.setTimeout(() => restartRecognition("open-mic"), 320);
        }
      };

      const availableVoices = voiceCandidatesRef.current.filter(
        (voice) => !failedVoiceNamesRef.current.has(voice.name),
      );
      const selectedIndex = availableVoices.findIndex(
        (voice) => voice === preferredVoiceRef.current || voice.name === preferredVoiceRef.current?.name,
      );
      if (selectedIndex > 0) {
        availableVoices.unshift(...availableVoices.splice(selectedIndex, 1));
      }
      const playbackChoices = [...availableVoices, null];

      const playPart = (partIndex, voiceIndex) => {
        if (speechRunRef.current !== runId) return;
        const part = speechParts[partIndex];
        const voice = playbackChoices[voiceIndex] || null;
        const utterance = new SpeechSynthesisUtterance(part.text);
        if (voice) utterance.voice = voice;
        utterance.rate = voiceRateRef.current;
        utterance.pitch = voicePitchRef.current;
        utterance.volume = 1;
        utterance.onstart = () => {
          if (speechRunRef.current !== runId) return;
          if (voice) preferredVoiceRef.current = voice;
          setSpeaking(true);
          setSystem((current) => ({ ...current, voice: "Speaking" }));
        };
        utterance.onend = () => {
          if (speechRunRef.current !== runId) return;
          if (partIndex === speechParts.length - 1) {
            finishSpeech();
            return;
          }
          speechPauseTimerRef.current = window.setTimeout(
            () => playPart(partIndex + 1, voiceIndex),
            part.pauseAfterMs,
          );
        };
        utterance.onerror = () => {
          if (speechRunRef.current !== runId) return;
          if (voice) failedVoiceNamesRef.current.add(voice.name);
          if (voiceIndex + 1 < playbackChoices.length) {
            preferredVoiceRef.current = playbackChoices[voiceIndex + 1] || null;
            playPart(partIndex, voiceIndex + 1);
            return;
          }
          preferredVoiceRef.current = null;
          finishSpeech();
        };
        window.speechSynthesis.speak(utterance);
      };

      playPart(0, 0);
    },
    [restartRecognition],
  );

  const processCommand = useCallback(
    async (input, source = "typed") => {
      const cleanInput = input.trim();
      if (!cleanInput) return;

      if (shouldStopSpeech(cleanInput, stopPhrasesRef.current)) {
        cancelSpeech("Speech cancelled");
        if (openMicEnabledRef.current) {
          window.clearTimeout(recognitionRestartTimerRef.current);
          recognitionRestartTimerRef.current = window.setTimeout(() => restartRecognition("open-mic"), 220);
        }
        return;
      }

      if (typeof window !== "undefined" && (speaking || window.speechSynthesis?.speaking)) {
        cancelSpeech("Interrupted by new turn");
      }
      if (openMicEnabledRef.current && recognitionActiveRef.current) {
        openMicTurnPendingRef.current = true;
        pauseForSpeechRef.current = true;
        recognitionRef.current?.stop();
      }

      const now = Date.now();
      if (lastCommandRef.current.text === cleanInput && now - lastCommandRef.current.time < 2500) {
        return;
      }
      lastCommandRef.current = { text: cleanInput, time: now };

      setCommand("");
      setMemoArtifacts([]);
      if (source === "voice") setHeardTranscript(cleanInput);
      setVoiceError("");
      setProcessing(true);
      setStatus("Thinking");
      setSystem((current) => ({ ...current, agent: "Thinking" }));
      setAgentReply("Consulting the local Varyn agent...");
      addLog({ type: "user", label: `Command received: ${cleanInput.slice(0, 48)}` });

      try {
        const response = await fetch("/api/varyn/chat/stream", {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
          },
          body: JSON.stringify({
            message: cleanInput,
            sessionId,
            source,
          }),
        });

        const contentType = response.headers.get("content-type") || "";
        if (contentType.includes("application/json")) {
          const data = await response.json().catch(() => ({}));
          if (data.mode === "offline") {
            setActiveAnalysis(null);
            setPendingConfirmation(null);
            setAgentReply(data.reply);
            setStatus("Local agent offline");
            setSystem((current) => ({
              ...current,
              agent: "Offline",
              provider: "None",
            }));
            addLog({ type: "system", label: "HUD online; local intelligence layer offline" });
            return;
          }
          if (data.error) {
            setActiveAnalysis(null);
            setPendingConfirmation(null);
            setAgentReply(data.error);
            setStatus(response.status === 429 ? "Demo limit reached" : "Request blocked");
            addLog({ type: "error", label: data.error });
            return;
          }
          throw new Error(data.error || "Varyn local agent failed to respond.");
        }

        if (!response.ok || !response.body) {
          const data = await response.json().catch(() => ({}));
          throw new Error(data.error || "Varyn local agent failed to respond.");
        }

        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let streamBuffer = "";
        let streamedReply = "";
        let receivedFirstToken = false;
        let data = null;

        const handleStreamBlock = (block) => {
          let eventName = "message";
          const dataLines = [];
          block.split("\n").forEach((line) => {
            if (line.startsWith("event:")) eventName = line.slice(6).trim();
            if (line.startsWith("data:")) dataLines.push(line.slice(5).trim());
          });
          if (!dataLines.length) return;
          const payload = JSON.parse(dataLines.join("\n"));

          if (eventName === "token") {
            if (!receivedFirstToken) {
              streamedReply = "";
              receivedFirstToken = true;
              setStatus("Responding");
            }
            streamedReply += payload.text || "";
            setAgentReply(streamedReply);
          } else if (eventName === "activity") {
            addLog({ type: payload.type, label: payload.label });
          } else if (eventName === "result") {
            data = payload;
          } else if (eventName === "error") {
            throw new Error(payload.error || "Varyn stream failed.");
          }
        };

        while (true) {
          const { value, done } = await reader.read();
          streamBuffer += decoder.decode(value || new Uint8Array(), { stream: !done });
          let boundary = streamBuffer.indexOf("\n\n");
          while (boundary >= 0) {
            const block = streamBuffer.slice(0, boundary).trim();
            streamBuffer = streamBuffer.slice(boundary + 2);
            if (block) handleStreamBlock(block);
            boundary = streamBuffer.indexOf("\n\n");
          }
          if (done) break;
        }

        if (streamBuffer.trim()) handleStreamBlock(streamBuffer.trim());
        if (!data) throw new Error("Varyn stream ended without a final response.");

        const hasAnalysis = data.mode === "analysis" && isMeaningfulAnalysis(data.analysis);
        setAgentReply(data.reply || streamedReply);
        setMemoArtifacts(Array.isArray(data.artifacts) ? data.artifacts : []);
        setPendingConfirmation(data.confirmation || null);
        setActiveAnalysis(hasAnalysis ? data.analysis : null);
        setStatus(data.status || data.provider || "Local agent");
        setSystem((current) => ({
          ...current,
          agent: "Online",
          provider: data.provider === "openrouter" ? "OpenRouter" : data.provider || current.provider,
          model: data.model || current.model,
          memory: data.memory ? "Active" : current.memory,
          risk: hasAnalysis ? "Analyzing" : "Active",
          market: data.market ? "Active" : current.market,
        }));

        (data.events || []).forEach((event) => addLog({ type: event.type, label: event.label }));
        if (hasAnalysis) {
          addLog({ type: "risk", label: "Structured risk panel generated" });
        }
        if (data.reply || data.spoken) speak(data.reply || data.spoken);
      } catch (error) {
        console.error("Varyn frontend error:", error);
        setActiveAnalysis(null);
        setAgentReply("");
        setStatus("Agent offline");
        setSystem((current) => ({ ...current, agent: "Offline" }));
        addLog({ type: "error", label: "Local agent unavailable" });
        speak("I cannot reach the local Varyn agent yet. Start the Python agent backend, then try again.");
      } finally {
        setProcessing(false);
      }
    },
    [addLog, cancelSpeech, restartRecognition, sessionId, speak, speaking],
  );

  useEffect(() => {
    processCommandRef.current = processCommand;
  }, [processCommand]);

  useEffect(() => {
    fetch("/api/varyn/auth", { cache: "no-store" })
      .then((response) => response.json())
      .then((data) => {
        setOwnerAuthenticated(Boolean(data.owner));
        setOwnerAccessConfigured(Boolean(data.configured));
      })
      .catch(() => {
        setOwnerAuthenticated(false);
        setOwnerAccessConfigured(false);
      });
  }, []);

  const submitCapturedTranscript = useCallback(() => {
    const transcript = normalizeTranscript(capturedTranscriptRef.current || interimTranscriptRef.current);
    capturedTranscriptRef.current = "";
    interimTranscriptRef.current = "";
    commandBufferRef.current = "";

    if (!isUsableTranscript(transcript)) {
      openMicTurnPendingRef.current = false;
      setHeardTranscript("");
      setVoiceError("No clear speech was captured. Hold push-to-talk, speak, then release. Nothing was sent.");
      setStatus("Voice standby");
      setSystem((current) => ({ ...current, voice: openMicEnabledRef.current ? "Open mic ready" : "Standby" }));
      addLog({ type: "error", label: "Empty or unclear transcript discarded" });
      if (openMicEnabledRef.current) {
        window.clearTimeout(recognitionRestartTimerRef.current);
        recognitionRestartTimerRef.current = window.setTimeout(() => restartRecognition("open-mic"), 260);
      }
      return;
    }

    setHeardTranscript(transcript);
    setVoiceError("");
    addLog({ type: "voice", label: `Transcript received: ${transcript.slice(0, 48)}` });
    addLog({ type: "voice", label: "Transcript submitted" });
    openMicTurnPendingRef.current = openMicEnabledRef.current;
    processCommandRef.current?.(transcript, "voice");
  }, [addLog, restartRecognition]);

  useEffect(() => {
    submitCapturedTranscriptRef.current = submitCapturedTranscript;
  }, [submitCapturedTranscript]);

  const ensureRecognition = useCallback(() => {
    if (typeof window === "undefined") return null;
    const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;

    if (!SpeechRecognition) {
      setVoiceSupported(false);
      setVoiceError("Voice recognition is not supported in this browser. Use Chrome or Edge, or type a command.");
      addLog({ type: "error", label: "Voice recognition unsupported" });
      return null;
    }

    if (recognitionRef.current) return recognitionRef.current;

    const recognition = new SpeechRecognition();
    recognition.interimResults = true;
    recognition.lang = "en-US";

    recognition.onstart = () => {
      intentionalAbortRef.current = false;
      recognitionStartPendingRef.current = false;
      recognitionActiveRef.current = true;
      listeningRef.current = true;
      setListening(true);
      setStatus("Listening");
      setSystem((current) => ({ ...current, voice: "Listening" }));
      if (captureModeRef.current === "push-to-talk" && !pushToTalkHeldRef.current) {
        submitOnEndRef.current = true;
        recognition.stop();
      }
    };

    recognition.onspeechstart = () => {
      if (window.speechSynthesis?.speaking) cancelSpeech("Interrupted by user speech");
      setStatus("Listening");
    };

    recognition.onresult = (event) => {
      let finalText = "";
      let interimText = "";
      for (let index = 0; index < event.results.length; index += 1) {
        const result = event.results[index];
        const text = result[0]?.transcript || "";
        if (result.isFinal) finalText += ` ${text}`;
        else interimText += ` ${text}`;
      }

      const normalizedFinal = normalizeTranscript(finalText);
      const normalizedInterim = normalizeTranscript(interimText);
      if (normalizedFinal) capturedTranscriptRef.current = normalizedFinal;
      interimTranscriptRef.current = normalizedInterim;
      const visibleTranscript = normalizeTranscript(`${normalizedFinal} ${normalizedInterim}`);
      if (visibleTranscript) setHeardTranscript(visibleTranscript);

      if (shouldStopSpeech(visibleTranscript, stopPhrasesRef.current)) {
        submitOnEndRef.current = false;
        intentionalAbortRef.current = true;
        recognition.abort();
        cancelSpeech("Speech cancelled");
        return;
      }

      if (captureModeRef.current === "open-mic" && normalizedFinal) {
        submitOnEndRef.current = true;
        recognition.stop();
      }
    };

    recognition.onerror = (event) => {
      recognitionStartPendingRef.current = false;
      recognitionActiveRef.current = false;
      listeningRef.current = false;
      setListening(false);

      if (intentionalAbortRef.current && event.error === "aborted") {
        intentionalAbortRef.current = false;
        return;
      }

      openMicEnabledRef.current = false;
      openMicTurnPendingRef.current = false;
      pushToTalkHeldRef.current = false;
      submitOnEndRef.current = false;
      pauseForSpeechRef.current = false;
      setOpenMicEnabled(false);
      setPushToTalkHeld(false);
      const message = voiceErrorMessage(event.error);
      setVoiceError(message);
      setStatus("Voice unavailable");
      setSystem((current) => ({ ...current, voice: "Unavailable" }));
      addLog({ type: "error", label: `Voice capture failed: ${event.error}` });
    };

    recognition.onend = () => {
      intentionalAbortRef.current = false;
      recognitionStartPendingRef.current = false;
      recognitionActiveRef.current = false;
      listeningRef.current = false;
      setListening(false);

      if (submitOnEndRef.current) {
        submitOnEndRef.current = false;
        submitCapturedTranscriptRef.current?.();
        return;
      }

      if (captureModeRef.current === "push-to-talk" && pushToTalkHeldRef.current) {
        window.clearTimeout(recognitionRestartTimerRef.current);
        recognitionRestartTimerRef.current = window.setTimeout(() => restartRecognition("push-to-talk"), 180);
        return;
      }

      if (openMicEnabledRef.current && !pauseForSpeechRef.current && !openMicTurnPendingRef.current) {
        setSystem((current) => ({ ...current, voice: "Open mic ready" }));
        window.clearTimeout(recognitionRestartTimerRef.current);
        recognitionRestartTimerRef.current = window.setTimeout(() => restartRecognition("open-mic"), 280);
      } else if (!pauseForSpeechRef.current) {
        setSystem((current) => ({ ...current, voice: "Standby" }));
      }
    };

    recognitionRef.current = recognition;
    return recognition;
  }, [addLog, cancelSpeech, restartRecognition]);

  const beginPushToTalk = useCallback(() => {
    if (pushToTalkHeldRef.current) return;
    const recognition = ensureRecognition();
    if (!recognition) return;

    if (window.speechSynthesis?.speaking || speaking) cancelSpeech("Interrupted by push-to-talk");
    window.clearTimeout(recognitionRestartTimerRef.current);
    window.clearTimeout(silenceTimerRef.current);
    pushToTalkHeldRef.current = true;
    captureModeRef.current = "push-to-talk";
    submitOnEndRef.current = false;
    capturedTranscriptRef.current = "";
    interimTranscriptRef.current = "";
    setPushToTalkHeld(true);
    setHeardTranscript("");
    setVoiceError("");
    addLog({ type: "voice", label: "Push-to-talk started" });

    if (!recognitionActiveRef.current) restartRecognition("push-to-talk");
  }, [addLog, cancelSpeech, ensureRecognition, restartRecognition, speaking]);

  const releasePushToTalk = useCallback(() => {
    if (!pushToTalkHeldRef.current) return;
    pushToTalkHeldRef.current = false;
    setPushToTalkHeld(false);
    submitOnEndRef.current = true;
    setStatus("Processing transcript");
    addLog({ type: "voice", label: "Push-to-talk released" });

    if (recognitionActiveRef.current) {
      recognitionRef.current?.stop();
    } else if (!recognitionStartPendingRef.current) {
      submitOnEndRef.current = false;
      submitCapturedTranscriptRef.current?.();
    }
  }, [addLog]);

  const startOpenMic = useCallback(() => {
    if (!openMicAvailable) {
      setVoiceError("Open mic is disabled in Varyn configuration. Push-to-talk remains available.");
      return;
    }
    const recognition = ensureRecognition();
    if (!recognition) return;

    if (window.speechSynthesis?.speaking || speaking) cancelSpeech("Interrupted by new voice turn");
    openMicEnabledRef.current = true;
    setOpenMicEnabled(true);
    voiceModeRef.current = "open-mic";
    setVoiceMode("open-mic");
    setVoiceError("");
    setSystem((current) => ({ ...current, voice: "Open mic ready" }));
    addLog({ type: "voice", label: "Open mic enabled" });
    restartRecognition("open-mic");
  }, [addLog, cancelSpeech, ensureRecognition, openMicAvailable, restartRecognition, speaking]);

  const stopVoice = useCallback(() => {
    openMicEnabledRef.current = false;
    setOpenMicEnabled(false);
    openMicTurnPendingRef.current = false;
    pushToTalkHeldRef.current = false;
    submitOnEndRef.current = false;
    pauseForSpeechRef.current = false;
    intentionalAbortRef.current = true;
    window.clearTimeout(silenceTimerRef.current);
    window.clearTimeout(recognitionRestartTimerRef.current);
    if (recognitionRef.current && (recognitionActiveRef.current || recognitionStartPendingRef.current)) {
      try {
        recognitionRef.current.abort();
      } catch {
        intentionalAbortRef.current = false;
      }
    } else {
      intentionalAbortRef.current = false;
    }
    recognitionActiveRef.current = false;
    recognitionStartPendingRef.current = false;
    listeningRef.current = false;
    setPushToTalkHeld(false);
    setListening(false);
    setStatus("Voice standby");
    setSystem((current) => ({ ...current, voice: "Standby" }));
    addLog({ type: "voice", label: "Voice standby" });
  }, [addLog]);

  const selectVoiceMode = useCallback((mode) => {
    stopVoice();
    voiceModeRef.current = mode;
    setVoiceMode(mode);
    setVoiceError("");
    setStatus(mode === "open-mic" ? "Open mic standby" : "Push-to-talk ready");
    addLog({ type: "voice", label: mode === "open-mic" ? "Open mic selected" : "Push-to-talk selected" });
  }, [addLog, stopVoice]);

  const openFilePicker = useCallback(() => {
    if (!requireOwner("File upload")) return;
    fileInputRef.current?.click();
  }, [requireOwner]);

  const uploadFile = useCallback(
    async (file) => {
      if (!file) return;
      if (!requireOwner("File upload")) return;

      setSelectedFile({
        name: file.name,
        size: formatFileSize(file.size),
        type: file.type || "local file",
        status: "Uploading",
        message: "Sending file to local Varyn agent...",
      });
      addLog({ type: "system", label: `File selected: ${file.name}` });

      try {
        const formData = new FormData();
        formData.append("file", file);
        formData.append("sessionId", sessionId);

        const response = await fetch("/api/varyn/upload", {
          method: "POST",
          body: formData,
        });
        const data = await response.json();

        if (!response.ok || !data.file) {
          throw new Error(data.error || "File upload failed.");
        }

        setSelectedFile({
          name: data.file.name,
          size: formatFileSize(data.file.size),
          type: data.file.extension || file.type || "local file",
          status: data.file.ready ? "Ready for questions" : "Loaded without extractable text",
          message: data.file.message,
          extractedChars: data.file.extracted_chars,
          ready: data.file.ready,
          securityStatus: data.file.security_status,
          instructionFlags: data.file.instruction_flags || [],
        });
        setSystem((current) => ({ ...current, memory: data.file.ready ? "File ready" : "File loaded" }));
        (data.events || []).forEach((eventItem) => addLog({ type: eventItem.type, label: eventItem.label }));
        addLog({
          type: data.file.ready ? "memory" : "error",
          label: data.file.ready ? "File text extracted; ready for questions" : data.file.message,
        });
        if (data.file.instruction_flags?.length) {
          addLog({ type: "error", label: "Untrusted instruction-like file content flagged" });
          setAgentReply(
            "Security notice: the uploaded file contains instruction-like text. Varyn will treat it only as data and will not obey it.",
          );
        }
      } catch (error) {
        console.error("Varyn file upload error:", error);
        setSelectedFile((current) => ({
          ...(current || { name: file.name, size: formatFileSize(file.size) }),
          status: "Upload failed",
          message: error.message || "The local agent could not process this file.",
          ready: false,
        }));
        addLog({ type: "error", label: "File upload failed" });
      } finally {
        if (fileInputRef.current) {
          fileInputRef.current.value = "";
        }
      }
    },
    [addLog, requireOwner, sessionId],
  );

  const handleFileSelected = useCallback(
    (event) => {
      uploadFile(event.target.files?.[0]);
    },
    [uploadFile],
  );

  const handleFileDrop = useCallback(
    (event) => {
      event.preventDefault();
      uploadFile(event.dataTransfer.files?.[0]);
    },
    [uploadFile],
  );

  const clearFileContext = useCallback(async () => {
    if (!requireOwner("Clearing file context")) return;
    addLog({ type: "memory", label: "Requesting file-context clearance" });
    try {
      const response = await fetch("/api/varyn/session", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action: "clear-file", sessionId }),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.error || "Clear file failed.");
      if (data.confirmation) {
        setPendingConfirmation(data.confirmation);
        setStatus("Awaiting confirmation");
        setAgentReply(`Confirmation required. ${data.confirmation.what_it_will_do}`);
        return;
      }
      (data.events || []).forEach((eventItem) => addLog({ type: eventItem.type, label: eventItem.label }));
      setSystem((current) => ({ ...current, memory: "Active" }));
    } catch (error) {
      console.error("Varyn clear file error:", error);
      addLog({ type: "error", label: "Could not clear file context" });
    }
  }, [addLog, requireOwner, sessionId]);

  const resetSession = useCallback(async () => {
    if (!requireOwner("Resetting the session")) return;
    cancelSpeech("Speech cancelled");
    addLog({ type: "system", label: "Requesting session reset" });

    try {
      const response = await fetch("/api/varyn/session", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action: "reset", sessionId }),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.error || "Session reset request failed.");
      if (data.confirmation) {
        setPendingConfirmation(data.confirmation);
        setStatus("Awaiting confirmation");
        setAgentReply(`Confirmation required. ${data.confirmation.what_it_will_do}`);
      }
    } catch {
      addLog({ type: "error", label: "Backend session reset unavailable" });
    }
  }, [addLog, cancelSpeech, requireOwner, sessionId]);

  const resolveConfirmation = useCallback(async (decision) => {
    if (!pendingConfirmation) return;
    const action = pendingConfirmation.action;
    setProcessing(true);
    try {
      const response = await fetch("/api/varyn/safety", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          action: "resolve",
          confirmationId: pendingConfirmation.id,
          decision,
          sessionId,
        }),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.error || "Confirmation could not be resolved.");
      setPendingConfirmation(null);
      setAgentReply(data.reply || (decision === "approve" ? "Approved action completed." : "Action denied."));
      if (action === "export_risk_memo") {
        setMemoArtifacts(decision === "approve" && Array.isArray(data.artifacts) ? data.artifacts : []);
      }
      setStatus(data.status || "Online");
      (data.events || []).forEach((event) => addLog({ type: event.type, label: event.label }));
      if (decision === "approve" && action === "clear_file_context") {
        setSelectedFile(null);
        setSystem((current) => ({ ...current, memory: "Active" }));
      }
      if (decision === "approve" && action === "reset_session") {
        setActiveAnalysis(null);
        setCommand("");
        setMemoArtifacts([]);
        setSelectedFile(null);
        setSessionId(createSessionId());
        setSystem((current) => ({ ...current, agent: "Standby", memory: "Active", risk: "Active", market: "Active" }));
      }
    } catch (error) {
      console.error("Varyn confirmation error:", error);
      addLog({ type: "error", label: error.message || "Confirmation failed" });
    } finally {
      setProcessing(false);
    }
  }, [addLog, pendingConfirmation, sessionId]);

  const toggleProactive = useCallback(async () => {
    if (!requireOwner("Monitoring controls")) return;
    const paused = !heartbeatState.proactivePaused;
    try {
      const response = await fetch("/api/varyn/safety", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action: "proactive", paused, sessionId }),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.error || "Kill switch failed.");
      setHeartbeatState((current) => ({ ...current, proactivePaused: Boolean(data.proactive_paused) }));
      setSystem((current) => ({ ...current, heartbeat: data.proactive_paused ? "Paused" : "Watching" }));
      setStatus(data.proactive_paused ? "Proactive systems paused" : "Proactive systems online");
      addLog({ type: "system", label: data.proactive_paused ? "All proactive behavior paused" : "Proactive behavior resumed" });
    } catch (error) {
      console.error("Varyn kill-switch error:", error);
      addLog({ type: "error", label: "Could not change proactive state" });
    }
  }, [addLog, heartbeatState.proactivePaused, requireOwner, sessionId]);

  const toggleFullscreen = useCallback(async () => {
    if (typeof document === "undefined") return;
    try {
      if (document.fullscreenElement) {
        await document.exitFullscreen();
      } else if (stageRef.current?.requestFullscreen) {
        await stageRef.current.requestFullscreen();
      } else {
        throw new Error("Fullscreen is unavailable in this browser.");
      }
    } catch (error) {
      addLog({ type: "error", label: error.message || "Fullscreen unavailable" });
    }
  }, [addLog]);

  const dismissHeartbeatNotice = useCallback(async (noticeId) => {
    if (!requireOwner("Dismissing monitoring notices")) return;
    try {
      const response = await fetch("/api/varyn/heartbeat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action: "dismiss", noticeId }),
      });
      if (!response.ok) throw new Error("Notice dismissal failed.");
      setHeartbeatState((current) => ({
        ...current,
        notices: current.notices.filter((notice) => notice.id !== noticeId),
      }));
      addLog({ type: "system", label: "Heartbeat notice dismissed" });
    } catch {
      addLog({ type: "error", label: "Could not dismiss heartbeat notice" });
    }
  }, [addLog, requireOwner]);

  useEffect(() => {
    let active = true;
    fetch("/api/varyn/config", { cache: "no-store" })
      .then((response) => response.json())
      .then((data) => {
        if (!active || data.error) return;
        stopPhrasesRef.current = data.voice?.stop_commands?.length ? data.voice.stop_commands : ["stop"];
        const configuredVoiceMode = data.voice?.input_mode === "open-mic" ? "open-mic" : "push-to-talk";
        const configuredPushKey = data.voice?.push_to_talk_key || "Space";
        const configuredRate = Number(data.voice?.rate);
        const configuredPitch = Number(data.voice?.pitch);
        const configuredParagraphPause = Number(data.voice?.paragraph_pause_ms);
        voiceModeRef.current = configuredVoiceMode;
        pushToTalkKeyRef.current = configuredPushKey;
        voiceChoiceRef.current = data.voice?.choice || DEFAULT_PREFERRED_VOICES[0];
        voicePreferredNamesRef.current = data.voice?.preferred_voices?.length
          ? data.voice.preferred_voices
          : DEFAULT_PREFERRED_VOICES;
        voiceRateRef.current = Number.isFinite(configuredRate) ? configuredRate : 1.0;
        voicePitchRef.current = Number.isFinite(configuredPitch) ? configuredPitch : 0.99;
        paragraphPauseRef.current = Number.isFinite(configuredParagraphPause) ? configuredParagraphPause : 170;
        voiceTestTextRef.current = data.voice?.test_utterance || "Varyn voice online.";
        setVoiceMode(configuredVoiceMode);
        setPushToTalkKey(configuredPushKey);
        setOpenMicAvailable(data.voice?.open_mic_enabled !== false);
        const backendPort = data.runtime?.backend_port;
        setHeartbeatState((current) => ({
          ...current,
          watchlist: data.watchlist?.length ? data.watchlist : current.watchlist,
        }));
        setSystem((current) => ({
          ...current,
          provider: data.provider?.name || current.provider,
          model: data.provider?.active_model || data.provider?.primary_model || current.model,
          backend: backendPort ? `Local Agent ${backendPort}` : current.backend,
          voice: configuredVoiceMode === "open-mic" ? "Open mic standby" : "PTT ready",
        }));
        addLog({ type: "system", label: "Runtime configuration loaded" });
      })
      .catch(() => addLog({ type: "error", label: "Runtime configuration unavailable" }))
      .finally(() => {
        if (active) setVoiceConfigReady(true);
      });
    return () => { active = false; };
  }, [addLog]);

  useEffect(() => {
    if (typeof window === "undefined") return undefined;
    const silenceTimer = silenceTimerRef;
    const recognitionRestartTimer = recognitionRestartTimerRef;
    setCurrentTime(formatTime());
    const clockTimer = window.setInterval(() => setCurrentTime(formatTime()), 1000);
    const onKeyDown = (event) => {
      if (event.key === "Escape") {
        setActiveAnalysis(null);
      }
    };
    const onFullscreenChange = () => setIsFullscreen(Boolean(document.fullscreenElement));
    window.addEventListener("keydown", onKeyDown);
    document.addEventListener("fullscreenchange", onFullscreenChange);
    return () => {
      window.removeEventListener("keydown", onKeyDown);
      document.removeEventListener("fullscreenchange", onFullscreenChange);
      window.clearInterval(clockTimer);
      window.clearTimeout(silenceTimer.current);
      window.clearTimeout(recognitionRestartTimer.current);
      window.clearTimeout(speechPauseTimerRef.current);
      window.speechSynthesis?.cancel();
      listeningRef.current = false;
      recognitionRef.current?.abort();
    };
  }, []);

  useEffect(() => {
    if (typeof window === "undefined" || !window.speechSynthesis || !voiceConfigReady) return undefined;
    const synthesis = window.speechSynthesis;
    let disposed = false;
    let testTimer = null;

    const testCandidates = (voices) => {
      if (disposed || voiceTestCompleteRef.current || voices.length === 0) return;
      const candidates = selectPreferredVoices(
        voices,
        voiceChoiceRef.current,
        voicePreferredNamesRef.current,
      );
      voiceCandidatesRef.current = candidates;
      preferredVoiceRef.current = candidates[0] || null;
      if (candidates.length === 0) {
        voiceTestCompleteRef.current = true;
        return;
      }

      const testRun = voiceTestRunRef.current + 1;
      voiceTestRunRef.current = testRun;
      const tryCandidate = (index) => {
        if (disposed || voiceTestCompleteRef.current || voiceTestRunRef.current !== testRun) return;
        const voice = candidates[index];
        if (!voice) {
          preferredVoiceRef.current = null;
          voiceTestCompleteRef.current = true;
          return;
        }

        let settled = false;
        const utterance = new SpeechSynthesisUtterance(voiceTestTextRef.current);
        utterance.voice = voice;
        utterance.rate = voiceRateRef.current;
        utterance.pitch = voicePitchRef.current;
        utterance.volume = 1;
        const tryNext = () => {
          if (settled || disposed || voiceTestRunRef.current !== testRun) return;
          settled = true;
          window.clearTimeout(testTimer);
          failedVoiceNamesRef.current.add(voice.name);
          tryCandidate(index + 1);
        };
        utterance.onstart = () => {
          if (disposed || voiceTestRunRef.current !== testRun) return;
          preferredVoiceRef.current = voice;
        };
        utterance.onend = () => {
          if (settled || disposed || voiceTestRunRef.current !== testRun) return;
          settled = true;
          window.clearTimeout(testTimer);
          preferredVoiceRef.current = voice;
          voiceTestCompleteRef.current = true;
        };
        utterance.onerror = tryNext;
        testTimer = window.setTimeout(() => {
          synthesis.cancel();
          tryNext();
        }, 3500);
        synthesis.speak(utterance);
      };
      tryCandidate(0);
    };

    const updatePreferredVoice = () => testCandidates(synthesis.getVoices());
    const onWindowLoad = () => updatePreferredVoice();
    synthesis.addEventListener?.("voiceschanged", updatePreferredVoice);
    if (document.readyState === "complete") {
      window.setTimeout(updatePreferredVoice, 0);
    } else {
      window.addEventListener("load", onWindowLoad, { once: true });
    }
    return () => {
      disposed = true;
      window.clearTimeout(testTimer);
      synthesis.removeEventListener?.("voiceschanged", updatePreferredVoice);
      window.removeEventListener("load", onWindowLoad);
    };
  }, [voiceConfigReady]);

  useEffect(() => {
    if (typeof window === "undefined") return undefined;

    const onPushToTalkDown = (event) => {
      if (event.code !== pushToTalkKeyRef.current || event.repeat || isTextEntryTarget(event.target)) return;
      event.preventDefault();
      beginPushToTalk();
    };
    const onPushToTalkUp = (event) => {
      if (event.code !== pushToTalkKeyRef.current || !pushToTalkHeldRef.current) return;
      event.preventDefault();
      releasePushToTalk();
    };
    const releaseOnBlur = () => {
      if (pushToTalkHeldRef.current) releasePushToTalk();
    };

    window.addEventListener("keydown", onPushToTalkDown);
    window.addEventListener("keyup", onPushToTalkUp);
    window.addEventListener("blur", releaseOnBlur);
    return () => {
      window.removeEventListener("keydown", onPushToTalkDown);
      window.removeEventListener("keyup", onPushToTalkUp);
      window.removeEventListener("blur", releaseOnBlur);
    };
  }, [beginPushToTalk, releasePushToTalk]);

  useEffect(() => {
    if (typeof window === "undefined") return undefined;
    let active = true;
    let requestInFlight = false;

    const pollTelemetry = async () => {
      if (requestInFlight) return;
      requestInFlight = true;
      try {
        const response = await fetch("/api/varyn/telemetry", { cache: "no-store" });
        const data = await response.json();
        if (!response.ok || data.source !== "psutil") {
          throw new Error(data.error || "Telemetry unavailable");
        }
        if (!active) return;

        const networkKbps = Number(data.network_kbps);
        setTelemetry({
          cpu: Number(data.cpu_percent),
          mem: Number(data.memory_percent),
          net: networkKbps,
          netLevel: Math.min(100, Math.log10(Math.max(0, networkKbps) + 1) * 28),
          uptime: data.uptime || "N/A",
          proc: data.process_count ?? "N/A",
          os: data.os || "N/A",
          source: "psutil",
        });
        if (telemetryErrorRef.current) {
          addLog({ type: "system", label: "Live telemetry restored" });
          telemetryErrorRef.current = false;
        }
      } catch {
        if (!active) return;
        setTelemetry((current) => ({
          ...current,
          cpu: "N/A",
          mem: "N/A",
          net: "N/A",
          netLevel: 0,
          proc: "N/A",
          source: "unavailable",
        }));
        if (!telemetryErrorRef.current) {
          addLog({ type: "error", label: "Live telemetry unavailable" });
          telemetryErrorRef.current = true;
        }
      } finally {
        requestInFlight = false;
      }
    };

    pollTelemetry();
    const telemetryTimer = window.setInterval(pollTelemetry, 1500);
    return () => {
      active = false;
      window.clearInterval(telemetryTimer);
    };
  }, [addLog]);

  useEffect(() => {
    if (typeof window === "undefined") return undefined;
    let active = true;
    let requestInFlight = false;

    const pollHeartbeat = async () => {
      if (requestInFlight) return;
      requestInFlight = true;
      try {
        const response = await fetch("/api/varyn/heartbeat", { cache: "no-store" });
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || "Heartbeat unavailable");
        if (!active) return;

        const notices = Array.isArray(data.notices) ? data.notices : [];
        setHeartbeatState({
          enabled: Boolean(data.enabled),
          running: Boolean(data.running),
          proactivePaused: Boolean(data.proactive_paused),
          notices,
          heldNoticeCount: Number(data.held_notice_count || 0),
          nextDue: data.next_due || null,
          watchlist: Array.isArray(data.watchlist) && data.watchlist.length ? data.watchlist : [],
          marketSnapshot: {
            sampledAt: data.market_snapshot?.sampled_at || null,
            symbols: Array.isArray(data.market_snapshot?.symbols) ? data.market_snapshot.symbols : [],
            indexUpdatedAt: data.market_snapshot?.index_window?.cache_updated_at || null,
          },
        });
        setSourceHealth({
          overall: data.source_health?.overall || "unknown",
          sources: data.source_health?.sources || {},
          updatedAt: data.source_health?.updated_at || null,
        });
        setSystem((current) => ({
          ...current,
          heartbeat: data.proactive_paused
            ? "Paused"
            : data.enabled
              ? (data.running ? "Scanning" : "Watching")
              : "Disabled",
          data: data.source_health?.overall === "healthy"
            ? "Validated"
            : data.source_health?.overall === "degraded"
              ? "Degraded"
              : "Awaiting",
        }));

        notices.forEach((notice) => {
          if (seenNoticeIdsRef.current.has(notice.id)) return;
          seenNoticeIdsRef.current.add(notice.id);
          addLog({
            type: notice.severity === "critical" ? "error" : "risk",
            label: `Heartbeat: ${notice.title}`,
          });
        });

        if (heartbeatErrorRef.current) {
          addLog({ type: "system", label: "Heartbeat connection restored" });
          heartbeatErrorRef.current = false;
        }
      } catch {
        if (!active) return;
        setSystem((current) => ({ ...current, heartbeat: "Unavailable" }));
        if (!heartbeatErrorRef.current) {
          addLog({ type: "error", label: "Heartbeat unavailable" });
          heartbeatErrorRef.current = true;
        }
      } finally {
        requestInFlight = false;
      }
    };

    pollHeartbeat();
    const heartbeatTimer = window.setInterval(pollHeartbeat, 5000);
    return () => {
      active = false;
      window.clearInterval(heartbeatTimer);
    };
  }, [addLog]);

  useEffect(() => {
    const responseBox = responseBoxRef.current;
    if (responseBox) responseBox.scrollTop = responseBox.scrollHeight;
  }, [agentReply]);

  const submitCommand = (event) => {
    event.preventDefault();
    processCommand(command);
  };

  const riskModules = activeAnalysis?.modules || [];
  const marketTickerItems = useMemo(() => {
    const latestBySymbol = new Map();
    const sourceItems = heartbeatState.marketSnapshot.symbols.length
      ? heartbeatState.marketSnapshot.symbols
      : heartbeatState.watchlist.map((symbol) => ({ symbol, available: false, stale: true, pinned: true }));
    sourceItems.forEach((item) => {
      const symbol = String(item?.symbol || "").toUpperCase();
      if (symbol && !latestBySymbol.has(symbol)) latestBySymbol.set(symbol, item);
    });
    return [...latestBySymbol.values()];
  }, [heartbeatState.marketSnapshot.symbols, heartbeatState.watchlist]);
  const marketTimestamp = formatMarketTimestamp(
    heartbeatState.marketSnapshot.sampledAt || heartbeatState.marketSnapshot.indexUpdatedAt,
  );

  return (
    <main className={`varyn-shell is-${state} ${isFullscreen ? "is-fullscreen" : ""}`}>
      <section ref={stageRef} className="live-stage" aria-label="Varyn live assistant interface">
        <div className="corner corner-tl" aria-hidden="true" />
        <div className="corner corner-tr" aria-hidden="true" />
        <div className="corner corner-bl" aria-hidden="true" />
        <div className="corner corner-br" aria-hidden="true" />

        <header className="top-command-bar">
          <span>VARYN</span>
          <strong>AI Risk Intelligence Command System</strong>
          <div className="top-status-actions">
            <time>{currentTime}</time>
            <button
              className="top-icon-button"
              onClick={toggleFullscreen}
              title={isFullscreen ? "Exit fullscreen" : "Enter fullscreen"}
              type="button"
              aria-label={isFullscreen ? "Exit fullscreen" : "Enter fullscreen"}
            >
              {isFullscreen ? <Minimize2 aria-hidden="true" size={16} /> : <Maximize2 aria-hidden="true" size={16} />}
            </button>
          </div>
        </header>

        <section className="market-ticker" aria-label="Live heartbeat market watch">
          <div className="market-ticker-status">
            <span>Online</span>
            <small>{heartbeatState.proactivePaused ? "Proactive paused" : heartbeatState.running ? "Scanning" : "Market watch"}</small>
          </div>
          <div className="market-ticker-window">
            <div className="market-ticker-track">
              {[0, 1].map((cycleIndex) => (
                <div className="market-ticker-cycle" aria-hidden={cycleIndex === 1} key={cycleIndex}>
                  {marketTickerItems.map((item) => {
                    const change = Number(item.change_percent);
                    const changeTone = Number.isFinite(change) ? (change > 0 ? "up" : change < 0 ? "down" : "flat") : "missing";
                    const quoteTime = formatMarketTimestamp(item.sampled_at);
                    return (
                      <article
                        className={`market-ticker-item ${item.stale ? "is-stale" : ""}`}
                        key={`${cycleIndex}-${item.symbol}`}
                        title={item.stale
                          ? `${item.symbol} last-known value${quoteTime ? ` as of ${quoteTime}` : ""}; latest refresh unavailable`
                          : `${item.symbol} latest cached heartbeat value`}
                      >
                        <strong>{item.symbol}</strong>
                        <span className="ticker-price">{item.available ? formatMarketPrice(item.price) : "Unavailable"}</span>
                        <span className={`ticker-change is-${changeTone}`}>
                          {item.available ? formatMarketChange(item.change_percent) : "--"}
                        </span>
                      </article>
                    );
                  })}
                </div>
              ))}
            </div>
          </div>
          <time className="market-ticker-time" dateTime={heartbeatState.marketSnapshot.sampledAt || undefined}>
            {marketTimestamp ? `As of ${marketTimestamp}` : "Awaiting market scan..."}
          </time>
        </section>

        <aside className="system-panel left-panel" aria-label="System monitor">
          <div className="panel-heading">Sys Monitor</div>
          <div className="telemetry-note">
            {telemetry.source === "psutil"
              ? "Live local telemetry"
              : telemetry.source === "unavailable"
                ? "Telemetry unavailable"
                : "Telemetry connecting"}
          </div>
          <div className="status-readout" aria-live="polite">
            <span>Runtime</span>
            <strong>{status}</strong>
          </div>
          {[
            ["CPU", typeof telemetry.cpu === "number" ? `${telemetry.cpu}%` : "N/A", typeof telemetry.cpu === "number" ? telemetry.cpu : 0, "cyan"],
            ["MEM", typeof telemetry.mem === "number" ? `${telemetry.mem}%` : "N/A", typeof telemetry.mem === "number" ? telemetry.mem : 0, typeof telemetry.mem === "number" && telemetry.mem > 62 ? "amber" : "cyan"],
            ["NET", typeof telemetry.net === "number" ? `${telemetry.net}KB/s` : "N/A", telemetry.netLevel, "green"],
          ].map(([label, value, level, tone]) => (
            <div className={`metric-row metric-${tone}`} key={label}>
              <div>
                <span>{label}</span>
                <strong>{value}</strong>
              </div>
              <i style={{ "--level": `${level}%` }} />
            </div>
          ))}
          <div className="mini-readout">
            <span>UP</span>
            <strong>{telemetry.uptime}</strong>
            <span>PROC</span>
            <strong>{telemetry.proc}</strong>
            <span>OS</span>
            <strong>{telemetry.os}</strong>
          </div>
          <div className="panel-heading data-health-heading">Data Health</div>
          <div className={`source-health source-${sourceHealth.overall}`}>
            {[
              ["yfinance", sourceHealth.sources.yfinance],
              ["SEC EDGAR", sourceHealth.sources.sec_edgar],
              ["FRED", sourceHealth.sources.fred],
              ["CFPB", sourceHealth.sources.cfpb],
            ].map(([label, source]) => (
              <div
                className={`source-health-row source-status-${source?.status || "unknown"}`}
                key={label}
                title={sourceHealthTitle(label, source)}
              >
                <span>{label}</span>
                <strong>{sourceStatusLabel(source)}</strong>
                <small>{source ? `${Math.round((1 - Number(source.error_rate || 0)) * 100)}%` : "--"}</small>
              </div>
            ))}
          </div>
          <div className="panel-heading status-heading">Agent Status</div>
          {[
            ["Agent", system.agent],
            ["Provider", system.provider],
            ["Model", system.model],
            ["Backend", system.backend],
            ["Memory", system.memory],
            ["Risk", system.risk],
            ["Market", system.market],
            ["Heartbeat", system.heartbeat],
            ["Voice", system.voice],
          ].map(([label, value]) => (
            <div className="status-row" key={label}>
              <span>{label}</span>
              <strong>{value}</strong>
            </div>
          ))}
          <div className="clearance-stack">
            <span>AI Core Active</span>
            <span>Local Agent 8788</span>
            <span>Protocol Varyn</span>
          </div>
        </aside>

        <div className="orbital-field" aria-hidden="true">
          {STARFIELD_STARS.map((star) => (
            <span className="star" key={star.id} style={star.style} />
          ))}
          <span className="sweep-line sweep-a" />
          <span className="sweep-line sweep-b" />
        </div>

        <div className="vital-core">
          <div className="orbital-core-frame">
            <button
              className={`orbital-core ${pushToTalkHeld ? "is-held" : ""}`}
              onPointerDown={beginPushToTalk}
              onPointerUp={releasePushToTalk}
              onPointerCancel={releasePushToTalk}
              onPointerLeave={releasePushToTalk}
              type="button"
              aria-label={`Hold for Varyn push-to-talk. Keyboard key: ${pushToTalkKeyLabel(pushToTalkKey)}`}
            >
              <span className="orbit orbit-one" />
              <span className="orbit orbit-two" />
              <span className="orbit orbit-three" />
              <span className="orbit orbit-four" />
              <span className="orbit orbit-five" />
              <span className="scan-line" />
              <span className="scan-line scan-line-secondary" />
              <span className="crosshair crosshair-x" />
              <span className="crosshair crosshair-y" />
              <span className="pulse-ring pulse-one" />
              <span className="pulse-ring pulse-two" />
              <span className="core-glass">
                <strong>VARYN</strong>
              </span>
            </button>
            <div className="core-state-readout" aria-live="polite">
              {state}
            </div>
          </div>
          <div className="waveform" aria-hidden="true">
            {Array.from({ length: 36 }).map((_, index) => (
              <span key={index} style={{ "--i": index }} />
            ))}
          </div>
        </div>

        <aside className="system-panel right-panel" aria-label="Activity and response">
          <div className="panel-heading">Activity Log</div>
          {!ownerAuthenticated && (
            <p className="usage-limit-note">Public usage is limited to 10 requests per hour.</p>
          )}
          <div className="activity-log">
            {heartbeatState.notices.map((notice) => (
              <details className={`heartbeat-readout notice-${notice.severity}`} key={notice.id}>
                <summary>
                  <span>{notice.symbol}</span>
                  <strong>{notice.title}</strong>
                </summary>
                <p>{notice.message}</p>
                <p>{notice.risk_read || "A configured heartbeat threshold was crossed."}</p>
                <div className="notice-meta">
                  <span>{notice.window || "market event"}</span>
                  <span>{notice.confidence || "Unrated"} confidence</span>
                  <span>{notice.source || "Heartbeat cache"}</span>
                </div>
                <div className="notice-actions">
                  <button
                    onClick={() => processCommand(notice.analysis_prompt || `Analyze ${notice.symbol} risk.`)}
                    type="button"
                  >
                    Run analysis
                  </button>
                  <button onClick={() => dismissHeartbeatNotice(notice.id)} type="button">
                    Dismiss
                  </button>
                </div>
              </details>
            ))}
            {activityLog.map((event, index) => (
              <div className={`log-line log-${event.type}`} key={`${event.label}-${index}`}>
                <span>{event.time || "--:--:--"}</span>
                <p>{event.label}</p>
              </div>
            ))}
          </div>
          <div className="transcript-box" aria-live="polite">
            <span>Heard</span>
            <p>{heardTranscript || "No voice transcript yet."}</p>
          </div>
          <div className="panel-heading response-heading">Varyn Response</div>
          <div ref={responseBoxRef} className={`response-box ${processing ? "is-pending" : ""}`}>
            {agentReply ? cleanDisplayText(agentReply) : "Awaiting command input."}
          </div>
          {memoArtifacts.length > 0 && (
            <div className="memo-downloads" aria-label="Risk memo downloads">
              <span>Download memo</span>
              <div>
                {memoArtifacts.map((artifact) => (
                  <button
                    key={`${artifact.format}-${artifact.filename}`}
                    onClick={() => {
                      try {
                        downloadMemoArtifact(artifact);
                        addLog({ type: "system", label: `${artifact.format.toUpperCase()} memo downloaded` });
                      } catch (error) {
                        setAgentReply(error.message || "The memo download could not be prepared.");
                        addLog({ type: "error", label: "Memo download failed" });
                      }
                    }}
                    type="button"
                  >
                    {artifact.format === "markdown" ? "MD" : artifact.format.toUpperCase()}
                  </button>
                ))}
              </div>
            </div>
          )}
          <button
            className={`file-zone ${selectedFile?.ready ? "file-ready" : ""}`}
            onClick={openFilePicker}
            onDragOver={(event) => event.preventDefault()}
            onDrop={handleFileDrop}
            type="button"
          >
            <span>{selectedFile ? selectedFile.name : "File Upload"}</span>
            <p>
              {selectedFile
                ? `${selectedFile.status}: ${selectedFile.message || selectedFile.size}`
                : "Click to load txt, md, csv, json, code, pdf, or image reference files."}
            </p>
            {selectedFile?.extractedChars > 0 && <small>{selectedFile.extractedChars} characters extracted</small>}
            {selectedFile?.securityStatus === "flagged" && <small className="file-security">Instruction-like content flagged as data</small>}
          </button>
          <input
            ref={fileInputRef}
            className="visually-hidden"
            type="file"
            accept=".txt,.md,.csv,.json,.js,.jsx,.ts,.tsx,.py,.html,.css,.pdf,image/*"
            onChange={handleFileSelected}
          />
        </aside>

        {pendingConfirmation && (
          <aside className="confirmation-panel" aria-live="assertive" aria-label="Action confirmation required">
            <div className="panel-label">Confirmation required</div>
            <h2>{pendingConfirmation.action.replaceAll("_", " ")}</h2>
            <p>{pendingConfirmation.what_it_will_do}</p>
            <small>Approval applies only to this exact action.</small>
            <div className="confirmation-actions">
              <button className="control-button danger" onClick={() => resolveConfirmation("deny")} type="button">
                Deny
              </button>
              <button className="control-button primary" onClick={() => resolveConfirmation("approve")} type="button">
                Approve once
              </button>
            </div>
          </aside>
        )}

        {ownerPromptOpen && (
          <aside className="owner-access-panel" aria-live="polite" aria-label="Owner access">
            <div className="panel-label">Protected controls</div>
            <h2>Owner access</h2>
            <p>Unlock uploads, memory, exports, confirmations, and monitoring controls.</p>
            {ownerAccessConfigured ? (
              <form onSubmit={submitOwnerAccess}>
                <label htmlFor="owner-access-key">Access key</label>
                <input
                  autoComplete="current-password"
                  id="owner-access-key"
                  onChange={(event) => setOwnerAccessKey(event.target.value)}
                  type="password"
                  value={ownerAccessKey}
                />
                {ownerAccessError && <small>{ownerAccessError}</small>}
                <div className="confirmation-actions">
                  <button className="control-button" onClick={() => setOwnerPromptOpen(false)} type="button">
                    Cancel
                  </button>
                  <button className="control-button primary" type="submit">Unlock</button>
                </div>
              </form>
            ) : (
              <>
                <p>Owner access is not configured on this deployment.</p>
                <button className="control-button" onClick={() => setOwnerPromptOpen(false)} type="button">Close</button>
              </>
            )}
          </aside>
        )}

        {activeAnalysis && (
          <aside className="analysis-panel" aria-live="polite">
            <div className="analysis-header">
              <div>
                <div className="panel-label">Generated analysis</div>
                <h1>{activeAnalysis.title}</h1>
              </div>
              <button className="icon-button" onClick={dismissAnalysis} type="button" aria-label="Dismiss analysis">
                X
              </button>
            </div>
            <p>{activeAnalysis.summary}</p>
            <div className="analysis-meta">
              {activeAnalysis.overall_score && <span>Overall {activeAnalysis.overall_score}</span>}
              {activeAnalysis.source && <span>{activeAnalysis.source}</span>}
              {activeAnalysis.location && <span>{activeAnalysis.location}</span>}
            </div>
            {activeAnalysis.data_points?.length > 0 && (
              <div className="market-data-grid">
                {activeAnalysis.data_points.map((point) => (
                  <article key={point.symbol}>
                    <strong>{point.symbol}</strong>
                    <span>{point.source}</span>
                    <p>Price: {point.price}</p>
                    <p>Move: {point.change_percent}%</p>
                    <p>Beta: {point.beta}</p>
                    <p>Debt/Equity: {point.debt_to_equity}</p>
                    <p>Current ratio: {point.current_ratio}</p>
                  </article>
                ))}
              </div>
            )}
            <div className="module-grid">
              {riskModules.map((module) => (
                <article className="risk-module" key={module.title}>
                  <span>{module.score}</span>
                  <strong>{module.title}</strong>
                  <p>{module.detail}</p>
                </article>
              ))}
            </div>
            {activeAnalysis.drivers?.length > 0 && (
              <div className="driver-list">
                <strong>Key drivers</strong>
                {activeAnalysis.drivers.map((driver) => (
                  <span key={driver}>{driver}</span>
                ))}
              </div>
            )}
            <div className="action-list">
              {(activeAnalysis.actions || []).map((action) => (
                <span key={action}>{action}</span>
              ))}
            </div>
            <button className="control-button clear-analysis" onClick={dismissAnalysis} type="button">
              Clear analysis
            </button>
          </aside>
        )}

        <form className="command-deck" onSubmit={submitCommand}>
          <label htmlFor="varyn-command">Command Input</label>
          <div className="command-row">
            <input
              autoComplete="off"
              id="varyn-command"
              onChange={(event) => setCommand(event.target.value)}
              placeholder="Ask Varyn anything, or request a structured risk assessment."
              value={command}
            />
            <button type="submit">Send</button>
          </div>
          <div className="deck-actions">
            <div className="voice-mode-toggle" role="group" aria-label="Voice input mode">
              <button
                className={voiceMode === "push-to-talk" ? "is-active" : ""}
                onClick={() => selectVoiceMode("push-to-talk")}
                type="button"
              >
                Push to talk
              </button>
              <button
                className={voiceMode === "open-mic" ? "is-active" : ""}
                disabled={!openMicAvailable}
                onClick={() => selectVoiceMode("open-mic")}
                type="button"
              >
                Open mic
              </button>
            </div>
            {voiceMode === "push-to-talk" ? (
              <button
                className={`control-button primary push-to-talk ${pushToTalkHeld ? "is-held" : ""}`}
                onPointerDown={beginPushToTalk}
                onPointerUp={releasePushToTalk}
                onPointerCancel={releasePushToTalk}
                onPointerLeave={releasePushToTalk}
                onKeyDown={(event) => {
                  if ((event.key === " " || event.key === "Enter") && !event.repeat) {
                    event.preventDefault();
                    beginPushToTalk();
                  }
                }}
                onKeyUp={(event) => {
                  if (event.key === " " || event.key === "Enter") {
                    event.preventDefault();
                    releasePushToTalk();
                  }
                }}
                title={`Hold ${pushToTalkKeyLabel(pushToTalkKey)} or hold this control to speak`}
                type="button"
              >
                {pushToTalkHeld || listening ? "Listening" : "Hold to talk"}
              </button>
            ) : (
              <button className="control-button primary" onClick={openMicEnabled ? stopVoice : startOpenMic} type="button">
                {openMicEnabled ? "Disable open mic" : "Enable open mic"}
              </button>
            )}
            <button className="control-button danger" onClick={() => cancelSpeech("Speech cancelled")} type="button">
              Stop speaking
            </button>
            <button
              className={`control-button kill-switch ${heartbeatState.proactivePaused ? "is-paused" : ""}`}
              onClick={toggleProactive}
              title="Pauses heartbeat and background monitoring while chat remains available"
              type="button"
            >
              {heartbeatState.proactivePaused ? "Resume Monitoring" : "Pause Monitoring"}
            </button>
            <details className="secondary-actions">
              <summary className="control-button" title="Session and analysis actions">
                <MoreHorizontal aria-hidden="true" size={16} />
                More
              </summary>
              <div className="secondary-actions-menu">
                {ownerAuthenticated ? (
                  <button className="control-button owner-control" onClick={logoutOwner} type="button">
                    Lock owner access
                  </button>
                ) : (
                  <button className="control-button owner-control" onClick={requestOwnerAccess} type="button">
                    Owner access
                  </button>
                )}
                <button className="control-button" disabled={!selectedFile} onClick={clearFileContext} type="button">
                  Clear file
                </button>
                <button className="control-button" onClick={resetSession} type="button">
                  Reset session
                </button>
                <button className="control-button" disabled={!activeAnalysis} onClick={dismissAnalysis} type="button">
                  Clear analysis
                </button>
              </div>
            </details>
          </div>
          {!voiceSupported && <p>{voiceError}</p>}
        </form>

        {voiceError && <div className="voice-error">{voiceError}</div>}
      </section>
    </main>
  );
}
