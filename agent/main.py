from __future__ import annotations

import json
import os
from contextlib import asynccontextmanager
from concurrent.futures import ThreadPoolExecutor

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from agent_core import run_agent_turn, run_agent_turn_stream
from audit import get_audit_logger
from cfpb import cfpb_status, get_complaint_signal
from heartbeat import HeartbeatService
from fred import fred_status, get_macro_context, get_macro_snapshot
from market_data_store import source_health_status
from memory import LongTermMemoryStore, MemoryStore
from providers import provider_status
from safety import get_safety_rails
from security import enforce_request_security, request_role
from sec_edgar import get_official_fundamentals, resolve_cik, sec_status
from telemetry import SystemMonitor
from tools.files import UploadValidationError, process_upload
from tools.registry import ToolRuntime, build_tool_registry
from varyn_settings import public_settings, setting


audit = get_audit_logger()
safety = get_safety_rails()
heartbeat = HeartbeatService(safety=safety, audit=audit)
memory_writer = ThreadPoolExecutor(max_workers=1, thread_name_prefix="varyn-memory")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    heartbeat.start()
    yield
    heartbeat.stop()
    memory_writer.shutdown(wait=False, cancel_futures=False)


app = FastAPI(title="Varyn Local Agent", version="0.3.0", lifespan=lifespan)
app.middleware("http")(enforce_request_security)

runtime_public = public_settings()["runtime"]
frontend_port = int(runtime_public["frontend_port"])
frontend_origins = list(
    dict.fromkeys(
        origin
        for origin in [
            f"http://localhost:{frontend_port}",
            f"http://127.0.0.1:{frontend_port}",
            "http://localhost:3200",
            "http://localhost:3000",
            "https://varyn-ai.vercel.app",
            os.environ.get("FRONTEND_URL", ""),
        ]
        if origin
    )
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=frontend_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

memory = MemoryStore()
long_term_memory = LongTermMemoryStore()
system_monitor = SystemMonitor()


def current_source_health() -> dict:
    return source_health_status(
        {
            "sec_edgar": sec_status(),
            "fred": fred_status(),
        }
    )


class ChatRequest(BaseModel):
    message: str
    session_id: str = "local-preview"
    source: str = "typed"


class SessionRequest(BaseModel):
    session_id: str = "local-preview"


class ConfirmationDecision(BaseModel):
    session_id: str = "local-preview"
    decision: str


class ProactiveRequest(BaseModel):
    session_id: str = "local-preview"
    paused: bool


def normalized_command(message: str) -> str:
    return " ".join(message.lower().replace(".", " ").replace(",", " ").split())


def is_stop_command(message: str) -> bool:
    clean = normalized_command(message)
    commands = {
        normalized_command(command)
        for command in setting("voice.stop_commands", ["stop"])
    }
    return clean in commands or any(clean.startswith(f"{cmd} ") for cmd in commands)


@app.get("/ping")
def ping():
    return {"status": "awake"}


@app.get("/health")
def health():
    return {"ok": True, "service": "varyn-agent", "status": "online"}


@app.get("/health/details")
def health_details():
    return {
        "ok": True,
        "service": "varyn-agent",
        "providers": provider_status(),
        "tools": {
            "agent_core": "registered tool loop",
            "registered_tools": [
                item["name"] for item in build_tool_registry().descriptions()
            ],
            "memory": True,
            "durable_memory": long_term_memory.summary(),
            "risk_engine": True,
            "market_data": True,
            "file_context": True,
            "telemetry": "psutil",
            "heartbeat": True,
            "market_data_validation": "yfinance + Stooq",
            "official_fundamentals": "SEC EDGAR companyfacts",
            "macro_context": "Federal Reserve Bank of St. Louis FRED",
            "regulatory_signals": "CFPB Consumer Complaint Database",
            "safety_rails": True,
            "persistent_audit": True,
            "proactive_kill_switch": True,
            "exportable_risk_memo": "Markdown + HTML + PDF browser downloads with confirmation gate",
        },
        "source_health": current_source_health(),
        "sec_edgar": sec_status(),
        "fred": fred_status(),
        "cfpb": cfpb_status(),
        "safety": safety.status(),
        "audit": audit.summary(),
        "persistence": {
            "note": "Hosted demo state is ephemeral across redeploys except durable remembered facts.",
            "durable_memory_backend": long_term_memory.summary().get("backend"),
            "session_memory": "ephemeral, pruned after inactivity",
            "uploads": "ephemeral, not retained across restarts",
            "audit_log": "ephemeral, size/entry-capped",
            "market_and_regulatory_caches": "ephemeral, regenerated on demand from public sources",
        },
    }


@app.get("/config/public")
def config_public():
    payload = public_settings()
    status = provider_status()
    payload["provider"]["active_model"] = status.get("active_model")
    payload["provider"]["model_chain"] = status.get("openrouter_model_chain") or payload[
        "provider"
    ].get("model_chain", [])
    return payload


@app.get("/audit")
def audit_status(limit: int = 30):
    return {"summary": audit.summary(), "recent": audit.recent(limit)}


@app.get("/safety")
def safety_status(session_id: str = "local-preview"):
    return {**safety.status(), "pending": safety.pending_for_session(session_id)}


@app.post("/safety/proactive")
def proactive_control(request: ProactiveRequest):
    status = safety.set_proactive_paused(request.paused, request.session_id)
    return {
        "ok": True,
        **status,
        "heartbeat": heartbeat.status(),
    }


@app.post("/confirmations/{confirmation_id}")
def resolve_confirmation(confirmation_id: str, request: ConfirmationDecision):
    try:
        confirmation = safety.resolve_confirmation(
            confirmation_id,
            request.session_id,
            request.decision,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if confirmation["status"] == "denied":
        return {
            "ok": True,
            "status": "Denied",
            "mode": "system_command",
            "reply": "Action denied. Nothing was changed.",
            "confirmation": None,
            "events": [{"type": "system", "label": "Consequential action denied"}],
        }

    return execute_approved_confirmation(confirmation)


@app.get("/telemetry")
def telemetry():
    return system_monitor.sample()


@app.get("/source-health")
def source_health():
    return current_source_health()


@app.get("/sec/status")
def sec_edgar_status():
    return sec_status()


@app.get("/sec/resolve/{symbol}")
def sec_edgar_resolve(symbol: str):
    identity = resolve_cik(symbol)
    if not identity:
        raise HTTPException(status_code=404, detail=f"No SEC CIK mapping found for {symbol.upper()}.")
    return {"ok": True, **identity}


@app.get("/sec/fundamentals/{symbol}")
def sec_edgar_fundamentals(symbol: str, refresh: bool = False):
    result = get_official_fundamentals(symbol, force=refresh)
    if not result.get("found") and not result.get("fields"):
        return {"ok": False, **result}
    return {"ok": True, **result}


@app.get("/fred/status")
def fred_source_status():
    return fred_status()


@app.get("/cfpb/status")
def cfpb_source_status():
    return cfpb_status()


@app.get("/cfpb/{symbol}")
def cfpb_company_signal(symbol: str, refresh: bool = False):
    result = get_complaint_signal(symbol, force=refresh)
    return {"ok": bool(result.get("found")), **result}


@app.get("/fred/snapshot")
def fred_macro_snapshot():
    return get_macro_snapshot()


@app.get("/fred/context")
def fred_macro_context(query: str = ""):
    return get_macro_context(query)


@app.get("/heartbeat")
def heartbeat_status():
    return heartbeat.status()


@app.post("/heartbeat/run")
def heartbeat_run():
    return heartbeat.trigger()


@app.post("/heartbeat/notices/{notice_id}/dismiss")
def heartbeat_dismiss(notice_id: str):
    try:
        result = heartbeat.dismiss(notice_id)
        audit.log(
            "heartbeat_notice_dismissed",
            reason="User dismissed a surfaced heartbeat notice.",
            details={"notice_id": notice_id},
        )
        return result
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/upload")
def upload_file(file: UploadFile = File(...), session_id: str = Form("local-preview")):
    if not file.filename:
        return {"error": "No file selected."}

    try:
        context = process_upload(file, session_id)
    except UploadValidationError as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    memory.set_file_context(session_id, context)
    audit.log(
        "file_uploaded",
        session_id=session_id,
        reason="User explicitly uploaded a file into the approved session scope.",
        details={
            "name": context["name"],
            "extension": context["extension"],
            "size": context["size"],
            "ready": context["ready"],
            "instruction_flag_count": len(context.get("instruction_flags") or []),
        },
    )

    return {
        "ok": True,
        "file": {
            "name": context["name"],
            "size": context["size"],
            "extension": context["extension"],
            "ready": context["ready"],
            "status": context["status"],
            "extraction_status": context["extraction_status"],
            "message": context["message"],
            "extracted_chars": context["extracted_chars"],
            "text_preview": context["text_preview"],
            "security_status": context.get("security_status"),
            "instruction_flags": context.get("instruction_flags") or [],
        },
        "events": [
            {"type": "system", "label": f"File loaded: {context['name']}"},
            {"type": "memory", "label": "File context stored" if context["ready"] else "File loaded without extractable text"},
        ],
    }


@app.get("/files/{session_id}")
def active_file(session_id: str):
    context = memory.get_file_context(session_id)
    if not context:
        return {"active_file": None}

    return {
        "active_file": {
            "name": context.get("name"),
            "size": context.get("size"),
            "extension": context.get("extension"),
            "ready": context.get("ready"),
            "extraction_status": context.get("extraction_status"),
            "message": context.get("message"),
            "extracted_chars": context.get("extracted_chars"),
            "loaded_at": context.get("loaded_at"),
        }
    }


@app.delete("/files/{session_id}")
def clear_file(session_id: str):
    return {
        "ok": False,
        "confirmation_required": True,
        "confirmation": safety.request_confirmation(
            session_id=session_id,
            action="clear_file_context",
            arguments={"session_id": session_id},
            action_kind="operation",
        ),
    }


@app.post("/session/reset")
def reset_session(request: SessionRequest):
    return {
        "ok": False,
        "confirmation_required": True,
        "confirmation": safety.request_confirmation(
            session_id=request.session_id,
            action="reset_session",
            arguments={"session_id": request.session_id},
            action_kind="operation",
        ),
    }


@app.post("/chat")
def chat(payload: ChatRequest, request: Request):
    message = payload.message.strip()
    if not message:
        return {"error": "No message provided."}

    events = [{"type": "system", "label": "Command received"}]
    audit.log(
        "chat_received",
        session_id=payload.session_id,
        reason="User submitted a conversational command.",
        details={"source": payload.source, "character_count": len(message)},
    )

    if is_stop_command(message):
        return {
            "reply": "Speech cancelled.",
            "spoken": "",
            "status": "Speech cancelled",
            "provider": "local",
            "mode": "system_command",
            "analysis": None,
            "market": None,
            "memory": memory.session_summary(payload.session_id),
            "file": memory.session_summary(payload.session_id).get("active_file"),
            "events": [{"type": "voice", "label": "Stop command handled locally"}],
        }

    recent_context = memory.recent_context(payload.session_id)
    file_context = memory.get_file_context(payload.session_id)

    result = run_agent_turn(
        message=message,
        recent_context=recent_context,
        file_context=file_context,
        long_term_memory=long_term_memory,
        source=payload.source,
        session_id=payload.session_id,
        access_role=request_role(request),
        safety=safety,
        audit=audit,
    )
    events.extend(condense_events(result.events))

    memory_writer.submit(memory.add_turn, payload.session_id, "user", message)
    memory_writer.submit(memory.add_turn, payload.session_id, "assistant", result.reply)
    memory_summary = memory.session_summary(payload.session_id)
    memory_summary["durable_fact_count"] = len(long_term_memory.list_facts())

    return {
        "reply": result.reply,
        "spoken": result.spoken,
        "status": result.status,
        "provider": result.provider,
        "model": result.model,
        "mode": result.mode,
        "analysis": result.analysis,
        "market": result.market,
        "market_contexts": result.market_contexts,
        "memory": memory_summary,
        "file": memory_summary.get("active_file"),
        "events": events,
        "confirmation": result.confirmation,
    }


@app.post("/chat/stream")
def chat_stream(payload: ChatRequest, request: Request):
    message = payload.message.strip()
    if not message:
        raise HTTPException(status_code=400, detail="No message provided.")
    return StreamingResponse(
        stream_chat_events(payload, message, request_role(request)),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


def stream_chat_events(request: ChatRequest, message: str, access_role: str):
    yield sse("status", {"status": "Thinking", "label": "Command received"})
    audit.log(
        "chat_received",
        session_id=request.session_id,
        reason="User submitted a conversational command.",
        details={"source": request.source, "character_count": len(message)},
    )

    if is_stop_command(message):
        result = {
            "reply": "Speech cancelled.",
            "spoken": "",
            "status": "Speech cancelled",
            "provider": "local",
            "model": None,
            "mode": "system_command",
            "analysis": None,
            "market": None,
            "market_contexts": [],
            "memory": memory.session_summary(request.session_id),
            "file": memory.session_summary(request.session_id).get("active_file"),
            "events": [{"type": "voice", "label": "Speech cancelled"}],
        }
        yield sse("result", result)
        return

    recent_context = memory.recent_context(request.session_id)
    file_context = memory.get_file_context(request.session_id)
    user_write_submitted = False
    final_result = None

    try:
        for event in run_agent_turn_stream(
            message=message,
            recent_context=recent_context,
            file_context=file_context,
            long_term_memory=long_term_memory,
            source=request.source,
            session_id=request.session_id,
            access_role=access_role,
            safety=safety,
            audit=audit,
        ):
            if event["type"] == "token":
                text = event.get("text", "")
                if text:
                    yield sse("token", {"text": text})
                    if not user_write_submitted:
                        memory_writer.submit(memory.add_turn, request.session_id, "user", message)
                        user_write_submitted = True
            elif event["type"] == "activity":
                activity = event.get("event") or {}
                if activity.get("type") in {"market", "risk", "memory", "error"}:
                    yield sse("activity", activity)
            elif event["type"] == "result":
                final_result = event["result"]
    finally:
        if not user_write_submitted:
            memory_writer.submit(memory.add_turn, request.session_id, "user", message)

    if not final_result:
        yield sse("error", {"error": "Varyn stream ended without a final result."})
        return

    memory_writer.submit(memory.add_turn, request.session_id, "assistant", final_result.reply)
    memory_summary = memory.session_summary(request.session_id)
    memory_summary["durable_fact_count"] = len(long_term_memory.list_facts())
    events = condense_events(final_result.events)
    payload = {
        "reply": final_result.reply,
        "spoken": final_result.spoken,
        "status": final_result.status,
        "provider": final_result.provider,
        "model": final_result.model,
        "mode": final_result.mode,
        "analysis": final_result.analysis,
        "market": final_result.market,
        "market_contexts": final_result.market_contexts,
        "memory": memory_summary,
        "file": memory_summary.get("active_file"),
        "events": events,
        "confirmation": final_result.confirmation,
    }
    yield sse("result", payload)


def execute_approved_confirmation(confirmation: dict) -> dict:
    action = confirmation["action"]
    session_id = confirmation["session_id"]
    arguments = confirmation.get("arguments") or {}
    output = {}
    if confirmation.get("action_kind") == "tool":
        runtime = ToolRuntime(
            session_id=session_id,
            file_context=memory.get_file_context(session_id),
            long_term_memory=long_term_memory,
            safety=safety,
            audit=audit,
            access_role="owner",
        )
        execution = build_tool_registry().run(
            action,
            arguments,
            runtime,
            confirmation_granted=True,
        )
        if not execution.ok:
            raise HTTPException(status_code=400, detail=execution.error or "Approved action failed.")
        output = execution.output or {}
        reply = approved_tool_reply(action, output)
    elif action == "clear_file_context":
        memory.clear_file_context(session_id)
        reply = "Active file context cleared."
    elif action == "reset_session":
        memory.reset_session(session_id)
        reply = "Session conversation and active-file context reset."
    else:
        raise HTTPException(status_code=400, detail="Approved action is not implemented safely.")

    audit.log(
        "confirmed_action_executed",
        session_id=session_id,
        reason="The exact pending action received explicit approval.",
        details={"confirmation_id": confirmation["id"], "action": action},
    )
    return {
        "ok": True,
        "status": "Completed",
        "mode": "system_command",
        "reply": reply,
        "confirmation": None,
        "memory": memory.session_summary(session_id),
        "events": [{"type": "system", "label": f"Approved action completed: {action}"}],
        "artifacts": output.get("artifacts") or [],
        "delivery_status": output.get("delivery_status"),
        "delivery_errors": output.get("delivery_errors") or [],
    }


def approved_tool_reply(action: str, output: dict) -> str:
    fact = output.get("fact") or {}
    if action == "remember_fact":
        return f"Remembered: {fact.get('statement', 'the approved fact')}"
    if action == "update_fact":
        return f"Updated durable fact {fact.get('id', '')}.".strip()
    if action == "forget_fact":
        return f"Forgot durable fact {fact.get('id', '')}.".strip()
    if action == "export_risk_memo":
        company = output.get("company") or output.get("symbol")
        formats = [item.get("format", "").upper() for item in output.get("artifacts") or []]
        if formats:
            availability = f"Browser downloads ready: {', '.join(formats)}."
        else:
            availability = "The memo was generated, but browser download content could not be prepared."
        errors = " ".join(output.get("delivery_errors") or [])
        return (
            f"Risk memo generated for {company}. {availability} "
            f"{errors} Analyst narrative status: {output.get('narrative_status') or 'unavailable'}."
        ).strip()
    return f"Approved action completed: {action}."


def condense_events(events: list[dict]) -> list[dict]:
    condensed: list[dict] = []
    seen: set[tuple[str, str]] = set()
    provider_event = None
    for event in events:
        event_type = event.get("type", "system")
        label = event.get("label", "")
        if not label:
            continue
        if event_type == "provider":
            provider_event = {"type": "provider", "label": label}
            continue
        if event_type not in {"market", "risk", "memory", "error"}:
            continue
        key = (event_type, label)
        if key not in seen:
            seen.add(key)
            condensed.append({"type": event_type, "label": label})
    if provider_event:
        condensed.append(provider_event)
    return condensed


def sse(event: str, payload: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=True)}\n\n"


if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("PORT", 8788))
    uvicorn.run("main:app", host="0.0.0.0", port=port)
