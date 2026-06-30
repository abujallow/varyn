# Varyn Local Agent

Varyn's local agent is a Python backend inspired by MARK XXXIX-OR's separation of UI, model routing, memory, and tools. It is original to Varyn and focused on finance, analytics, and risk intelligence.

## Responsibilities

- Receive commands from the Next.js HUD.
- Route prompts to OpenRouter when configured.
- Keep OpenAI optional and disabled by default.
- Store local session memory in `agent/data/memory.json`.
- Run market lookup and structured risk-analysis tools.
- Return JSON that the HUD can speak and render.

## Setup

```powershell
cd C:\varyn\agent
copy .env.example .env
```

Add either `GEMINI_API_KEY` or `OPENROUTER_API_KEY` to `agent\.env`.
The agent also supports `C:\varyn\agent.env` for local provider keys.

OpenRouter defaults to:

```text
OPENROUTER_MODEL=openai/gpt-oss-20b:free
```

This is an OpenRouter model ID. It does not use the OpenAI npm package, OpenAI API key, or OpenAI billing quota.

Then start the backend:

```powershell
powershell -ExecutionPolicy Bypass -File .\start-varyn-agent.ps1
```

The agent runs at:

```text
http://127.0.0.1:8788
```

Health check:

```powershell
Invoke-RestMethod http://127.0.0.1:8788/health
```

## Offline Mode

If no Gemini/OpenRouter key is configured, the agent still runs in local offline mode. It can answer setup/status questions and return structured risk frames, but it will not provide true autonomous conversation until a provider key is added.
