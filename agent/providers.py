from __future__ import annotations

import json
import re
import threading
import time
import urllib.error
import urllib.request
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from dataclasses import dataclass, field

from config import (
    DATA_DIR,
    GEMINI_API_KEY,
    GEMINI_MODEL,
    OPENROUTER_API_KEY,
    OPENROUTER_FALLBACK_MODEL,
    OPENROUTER_MODEL,
    OPENROUTER_MODEL_CHAIN,
    PROVIDER_BACKOFF_SECONDS,
    PROVIDER_CATALOG_CACHE_SECONDS,
    PROVIDER_CATALOG_TIMEOUT_SECONDS,
    PROVIDER_MAX_ATTEMPTS,
    PROVIDER_MAX_TOTAL_SECONDS,
    PROVIDER_RETRIES,
    PROVIDER_TIMEOUT_SECONDS,
    safe_config_snapshot,
)
from audit import get_audit_logger


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict
    native: bool = False


@dataclass
class ProviderResult:
    reply: str
    provider: str
    model: str | None
    status: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    assistant_message: dict | None = None
    error: str | None = None


class ProviderRequestError(RuntimeError):
    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


MODEL_CATALOG_URL = "https://openrouter.ai/api/v1/models"
MODEL_CATALOG_PATH = DATA_DIR / "provider" / "openrouter-free-models.json"
TRANSIENT_STATUS_CODES = {408, 409, 429, 500, 502, 503, 504}
_STATE_LOCK = threading.RLock()
_LAST_SERVED_MODEL: str | None = None
_LAST_REQUESTED_MODEL: str | None = None
_LAST_FAILOVER_COUNT = 0


def provider_status() -> dict:
    catalog = read_model_catalog(allow_stale=True)
    with _STATE_LOCK:
        active_model = _LAST_SERVED_MODEL
        requested_model = _LAST_REQUESTED_MODEL
        failover_count = _LAST_FAILOVER_COUNT
    return {
        "gemini_configured": bool(GEMINI_API_KEY),
        "gemini_model": GEMINI_MODEL if GEMINI_API_KEY else None,
        "openrouter_configured": bool(OPENROUTER_API_KEY),
        "openrouter_model": OPENROUTER_MODEL,
        "openrouter_fallback_model": OPENROUTER_FALLBACK_MODEL,
        "openrouter_model_chain": list(OPENROUTER_MODEL_CHAIN),
        "active_model": active_model,
        "last_requested_model": requested_model,
        "last_failover_count": failover_count,
        "catalog": {
            "cached": bool(catalog),
            "fetched_at": (catalog or {}).get("fetched_at"),
            "free_model_count": len((catalog or {}).get("free_models") or []),
            "tool_model_count": len((catalog or {}).get("tool_models") or []),
        },
        "debug": safe_config_snapshot(),
    }


def complete(messages: list[dict], tools: list[dict] | None = None) -> ProviderResult:
    errors: list[str] = []
    request_started = time.monotonic()
    if OPENROUTER_API_KEY:
        models, skipped = validated_model_chain(require_tools=bool(tools))
        audit_skipped_models(skipped)
        errors.extend(f"{model}: {reason}" for model, reason in skipped)
        total_attempts = 0
        previous_model = skipped[-1][0] if skipped and models else None
        previous_reason = skipped[-1][1] if skipped and models else None
        for model_index, model in enumerate(models):
            if previous_model:
                audit_provider_failover(previous_model, model, previous_reason or "request_failed")
            for attempt in range(PROVIDER_RETRIES + 1):
                if not attempt_allowed(request_started, total_attempts):
                    errors.append("Provider retry budget exhausted.")
                    break
                total_attempts += 1
                started = time.monotonic()
                try:
                    data = call_openrouter(
                        messages,
                        model,
                        tools,
                        timeout=request_timeout(request_started, len(models) - model_index),
                    )
                    result = parse_openrouter_response(data, model)
                    failover_count = max(0, model_index) + len(skipped)
                    audit_model_request(
                        "openrouter",
                        model,
                        result.model,
                        started,
                        "success",
                        attempt,
                        model_index,
                        total_attempts,
                        failover_count,
                    )
                    set_last_served(model, result.model, failover_count)
                    return result
                except ProviderRequestError as exc:
                    audit_model_request(
                        "openrouter",
                        model,
                        None,
                        started,
                        "failed",
                        attempt,
                        model_index,
                        total_attempts,
                        max(0, model_index) + len(skipped),
                        type(exc).__name__,
                    )
                    errors.append(f"{model}: {exc}")
                    transient = exc.status_code in TRANSIENT_STATUS_CODES
                    if (
                        not transient
                        or attempt >= PROVIDER_RETRIES
                        or not retry_allowed(
                            request_started,
                            total_attempts,
                            model_index,
                            len(models),
                        )
                    ):
                        break
                    bounded_backoff(request_started, attempt)
            previous_model = model
            previous_reason = compact_failure_reason(errors[-1] if errors else "request failed")

    return complete_gemini_or_local(messages, errors)


def complete_gemini_or_local(messages: list[dict], errors: list[str]) -> ProviderResult:
    if GEMINI_API_KEY:
        started = time.monotonic()
        try:
            reply = call_gemini(messages)
            audit_model_request("gemini", GEMINI_MODEL, GEMINI_MODEL, started, "success", 0, 0, 1, 0)
            return ProviderResult(
                reply=reply,
                provider="gemini",
                model=GEMINI_MODEL,
                status="Gemini local agent",
            )
        except ProviderRequestError as exc:
            audit_model_request(
                "gemini", GEMINI_MODEL, None, started, "failed", 0, 0, 1, 0, type(exc).__name__
            )
            errors.append(f"{GEMINI_MODEL}: {exc}")

    set_last_served(None, None, len(OPENROUTER_MODEL_CHAIN))
    return ProviderResult(
        reply=(
            "The reasoning provider is unavailable. Local tools remain available, "
            "but this response could not be expanded by the model."
        ),
        provider="local",
        model=None,
        status="Local offline mode",
        error="; ".join(errors) or "No model provider is configured.",
    )


def stream_complete(messages: list[dict], tools: list[dict] | None = None):
    errors: list[str] = []
    request_started = time.monotonic()
    if OPENROUTER_API_KEY:
        models, skipped = validated_model_chain(require_tools=bool(tools))
        audit_skipped_models(skipped)
        errors.extend(f"{model}: {reason}" for model, reason in skipped)
        total_attempts = 0
        previous_model = skipped[-1][0] if skipped and models else None
        previous_reason = skipped[-1][1] if skipped and models else None
        for model_index, model in enumerate(models):
            if previous_model:
                audit_provider_failover(previous_model, model, previous_reason or "request_failed")
            for attempt in range(PROVIDER_RETRIES + 1):
                if not attempt_allowed(request_started, total_attempts):
                    errors.append("Provider retry budget exhausted.")
                    break
                total_attempts += 1
                emitted_content = False
                completed_result: ProviderResult | None = None
                started = time.monotonic()
                try:
                    for event in call_openrouter_stream(
                        messages,
                        model,
                        tools,
                        timeout=request_timeout(request_started, len(models) - model_index),
                    ):
                        if event["type"] == "token":
                            emitted_content = True
                        elif event["type"] == "complete":
                            completed_result = event["result"]
                        yield event
                    served_model = completed_result.model if completed_result else model
                    failover_count = max(0, model_index) + len(skipped)
                    audit_model_request(
                        "openrouter",
                        model,
                        served_model,
                        started,
                        "success",
                        attempt,
                        model_index,
                        total_attempts,
                        failover_count,
                    )
                    set_last_served(model, served_model, failover_count)
                    return
                except ProviderRequestError as exc:
                    audit_model_request(
                        "openrouter",
                        model,
                        None,
                        started,
                        "failed",
                        attempt,
                        model_index,
                        total_attempts,
                        max(0, model_index) + len(skipped),
                        type(exc).__name__,
                    )
                    errors.append(f"{model}: {exc}")
                    if emitted_content:
                        yield {
                            "type": "complete",
                            "result": ProviderResult(
                                reply="",
                                provider="openrouter",
                                model=model,
                                status="OpenRouter stream interrupted",
                                error=str(exc),
                            ),
                        }
                        return
                    transient = exc.status_code in TRANSIENT_STATUS_CODES
                    if (
                        not transient
                        or attempt >= PROVIDER_RETRIES
                        or not retry_allowed(
                            request_started,
                            total_attempts,
                            model_index,
                            len(models),
                        )
                    ):
                        break
                    bounded_backoff(request_started, attempt)
            previous_model = model
            previous_reason = compact_failure_reason(errors[-1] if errors else "request failed")

    fallback = complete_gemini_or_local(messages, errors)
    if errors and not fallback.error:
        fallback.error = "; ".join(errors)
    if fallback.reply:
        yield {"type": "token", "text": fallback.reply}
    yield {"type": "complete", "result": fallback}


def audit_model_request(
    provider: str,
    requested_model: str,
    served_model: str | None,
    started: float,
    status: str,
    attempt: int,
    chain_index: int,
    total_attempt: int,
    failover_count: int,
    error_type: str | None = None,
) -> None:
    get_audit_logger().log(
        "model_request",
        reason="Reasoning-provider request completed.",
        details={
            "provider": provider,
            "model": served_model or requested_model,
            "requested_model": requested_model,
            "served_model": served_model,
            "status": status,
            "attempt": attempt + 1,
            "chain_index": chain_index,
            "total_attempt": total_attempt,
            "failover_count": failover_count,
            "latency_ms": round((time.monotonic() - started) * 1000, 2),
            "error_type": error_type,
        },
    )


def validated_model_chain(require_tools: bool) -> tuple[list[str], list[tuple[str, str]]]:
    configured = list(dict.fromkeys(OPENROUTER_MODEL_CHAIN))
    catalog = load_model_catalog()
    valid_ids = set((catalog or {}).get("tool_models" if require_tools else "free_models") or [])
    validated: list[str] = []
    skipped: list[tuple[str, str]] = []
    for model in configured:
        if not is_explicitly_free_model(model):
            skipped.append((model, "not_explicitly_free"))
        elif valid_ids and model not in valid_ids:
            skipped.append((model, "not_in_current_free_catalog"))
        else:
            validated.append(model)
    return validated, skipped


def is_explicitly_free_model(model: str) -> bool:
    return model == "openrouter/free" or model.endswith(":free")


def load_model_catalog() -> dict | None:
    cached = read_model_catalog()
    if cached:
        return cached
    try:
        request = urllib.request.Request(
            MODEL_CATALOG_URL,
            headers={"Accept": "application/json", "User-Agent": "Varyn Local Agent/0.4"},
        )
        with urllib.request.urlopen(request, timeout=PROVIDER_CATALOG_TIMEOUT_SECONDS) as response:
            payload = json.loads(response.read().decode("utf-8"))
        free_models = []
        tool_models = []
        for item in payload.get("data") or []:
            model = str(item.get("id") or "")
            pricing = item.get("pricing") or {}
            if not model or not pricing_is_free(pricing):
                continue
            free_models.append(model)
            parameters = set(item.get("supported_parameters") or [])
            if {"tools", "tool_choice"}.issubset(parameters):
                tool_models.append(model)
        catalog = {
            "version": 1,
            "source": MODEL_CATALOG_URL,
            "fetched_at": time.time(),
            "free_models": sorted(set(free_models)),
            "tool_models": sorted(set(tool_models)),
        }
        write_json_atomic(MODEL_CATALOG_PATH, catalog)
        return catalog
    except (OSError, ValueError, urllib.error.URLError, urllib.error.HTTPError, TimeoutError):
        return read_model_catalog(allow_stale=True)


def read_model_catalog(*, allow_stale: bool = False) -> dict | None:
    try:
        payload = json.loads(MODEL_CATALOG_PATH.read_text(encoding="utf-8"))
        fetched_at = float(payload.get("fetched_at") or 0)
        if not allow_stale and time.time() - fetched_at > PROVIDER_CATALOG_CACHE_SECONDS:
            return None
        return payload if isinstance(payload, dict) else None
    except (OSError, ValueError, json.JSONDecodeError):
        return None


def pricing_is_free(pricing: dict) -> bool:
    try:
        return Decimal(str(pricing.get("prompt"))) == 0 and Decimal(str(pricing.get("completion"))) == 0
    except (InvalidOperation, TypeError):
        return False


def write_json_atomic(path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def attempt_allowed(request_started: float, total_attempts: int) -> bool:
    return (
        total_attempts < max(1, PROVIDER_MAX_ATTEMPTS)
        and time.monotonic() - request_started < max(1.0, PROVIDER_MAX_TOTAL_SECONDS)
    )


def retry_allowed(
    request_started: float,
    total_attempts: int,
    model_index: int,
    model_count: int,
) -> bool:
    remaining_models = max(0, model_count - model_index - 1)
    reserved_attempts_fit = total_attempts + remaining_models < max(1, PROVIDER_MAX_ATTEMPTS)
    return reserved_attempts_fit and attempt_allowed(request_started, total_attempts)


def request_timeout(request_started: float, remaining_models: int) -> float:
    remaining = max(0.25, PROVIDER_MAX_TOTAL_SECONDS - (time.monotonic() - request_started))
    fair_share = remaining / max(1, remaining_models)
    return max(0.25, min(float(PROVIDER_TIMEOUT_SECONDS), fair_share))


def bounded_backoff(request_started: float, attempt: int) -> None:
    remaining = PROVIDER_MAX_TOTAL_SECONDS - (time.monotonic() - request_started)
    delay = min(max(0.0, PROVIDER_BACKOFF_SECONDS) * (attempt + 1), max(0.0, remaining))
    if delay > 0:
        time.sleep(delay)


def compact_failure_reason(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()[:180]


def audit_skipped_models(skipped: list[tuple[str, str]]) -> None:
    for model, reason in skipped:
        get_audit_logger().log(
            "provider_model_skipped",
            reason="Configured provider model was not eligible for a free request.",
            details={"model": model, "skip_reason": reason},
        )


def audit_provider_failover(from_model: str, to_model: str, reason: str) -> None:
    get_audit_logger().log(
        "provider_failover",
        reason="The provider seam advanced to the next configured free model.",
        details={"from_model": from_model, "to_model": to_model, "failure": reason},
    )


def set_last_served(requested_model: str | None, served_model: str | None, failover_count: int) -> None:
    global _LAST_SERVED_MODEL, _LAST_REQUESTED_MODEL, _LAST_FAILOVER_COUNT
    with _STATE_LOCK:
        _LAST_REQUESTED_MODEL = requested_model
        _LAST_SERVED_MODEL = served_model
        _LAST_FAILOVER_COUNT = failover_count


def call_openrouter_stream(
    messages: list[dict], model: str, tools: list[dict] | None, *, timeout: float
):
    payload: dict = {
        "model": model,
        "messages": messages,
        "temperature": 0.45,
        "max_tokens": 600,
        "stream": True,
        "reasoning": {"effort": "minimal", "exclude": True},
        "parallel_tool_calls": False,
    }
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"

    request = urllib.request.Request(
        "https://openrouter.ai/api/v1/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {OPENROUTER_API_KEY}",
            "HTTP-Referer": "http://localhost:3200",
            "X-Title": "Varyn Local Agent",
        },
        method="POST",
    )

    content_parts: list[str] = []
    tool_parts: dict[int, dict] = {}
    actual_model = model

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            for raw_line in response:
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line.startswith("data:"):
                    continue
                data_text = line[5:].strip()
                if data_text == "[DONE]":
                    break
                try:
                    data = json.loads(data_text)
                except json.JSONDecodeError:
                    continue

                actual_model = data.get("model") or actual_model
                choices = data.get("choices") or []
                delta = choices[0].get("delta", {}) if choices else {}
                raw_content = delta.get("content")
                if isinstance(raw_content, str):
                    content = raw_content
                elif isinstance(raw_content, list):
                    content = "".join(
                        item.get("text", "")
                        for item in raw_content
                        if isinstance(item, dict)
                    )
                else:
                    content = ""
                if content:
                    content_parts.append(content)
                    yield {"type": "token", "text": content}

                for raw_call in delta.get("tool_calls") or []:
                    index = int(raw_call.get("index", 0))
                    part = tool_parts.setdefault(
                        index,
                        {"id": "", "name": "", "arguments": ""},
                    )
                    if raw_call.get("id"):
                        part["id"] = raw_call["id"]
                    function = raw_call.get("function") or {}
                    if function.get("name"):
                        part["name"] += function["name"]
                    if function.get("arguments"):
                        part["arguments"] += function["arguments"]
    except urllib.error.HTTPError as exc:
        raise ProviderRequestError(
            f"HTTP {exc.code} from provider.", status_code=exc.code
        ) from exc
    except urllib.error.URLError as exc:
        raise ProviderRequestError("Provider connection failed.") from exc
    except TimeoutError as exc:
        raise ProviderRequestError("Provider request timed out.", status_code=408) from exc

    raw_calls = [
        {
            "id": part["id"] or f"stream-{index}",
            "type": "function",
            "function": {
                "name": part["name"],
                "arguments": part["arguments"] or "{}",
            },
        }
        for index, part in sorted(tool_parts.items())
        if part["name"]
    ]
    content = "".join(content_parts).strip()
    native_calls = parse_native_tool_calls(raw_calls)
    structured_calls = [] if native_calls else parse_structured_actions(content)
    tool_calls = native_calls or structured_calls

    if not content and not tool_calls:
        raise ProviderRequestError("Provider stream returned neither text nor a tool request.")

    yield {
        "type": "complete",
        "result": ProviderResult(
            reply=content,
            provider="openrouter",
            model=actual_model,
            status="OpenRouter local agent",
            tool_calls=tool_calls,
            assistant_message={
                "role": "assistant",
                "content": content or None,
                **({"tool_calls": raw_calls} if native_calls else {}),
            },
        ),
    }


def call_openrouter(
    messages: list[dict], model: str, tools: list[dict] | None, *, timeout: float
) -> dict:
    payload: dict = {
        "model": model,
        "messages": messages,
        "temperature": 0.45,
        "max_tokens": 600,
        "reasoning": {"effort": "minimal", "exclude": True},
        "parallel_tool_calls": False,
    }
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"

    return post_json(
        "https://openrouter.ai/api/v1/chat/completions",
        payload,
        headers={
            "Authorization": f"Bearer {OPENROUTER_API_KEY}",
            "HTTP-Referer": "http://localhost:3200",
            "X-Title": "Varyn Local Agent",
        },
        timeout=timeout,
    )


def parse_openrouter_response(data: dict, requested_model: str) -> ProviderResult:
    choices = data.get("choices") or []
    message = choices[0].get("message", {}) if choices else {}
    content = normalize_content(message.get("content"))
    native_calls = parse_native_tool_calls(message.get("tool_calls") or [])
    structured_calls = [] if native_calls else parse_structured_actions(content)
    tool_calls = native_calls or structured_calls
    actual_model = data.get("model") or requested_model

    if not content and not tool_calls:
        raise ProviderRequestError("Provider returned neither text nor a tool request.")

    return ProviderResult(
        reply=content,
        provider="openrouter",
        model=actual_model,
        status="OpenRouter local agent",
        tool_calls=tool_calls,
        assistant_message={
            "role": "assistant",
            "content": message.get("content"),
            **({"tool_calls": message.get("tool_calls")} if native_calls else {}),
        },
    )


def parse_native_tool_calls(raw_calls: list[dict]) -> list[ToolCall]:
    calls: list[ToolCall] = []
    for index, raw_call in enumerate(raw_calls):
        function = raw_call.get("function") or {}
        name = function.get("name")
        if not name:
            continue
        raw_arguments = function.get("arguments") or "{}"
        try:
            arguments = json.loads(raw_arguments) if isinstance(raw_arguments, str) else raw_arguments
        except json.JSONDecodeError:
            arguments = {}
        calls.append(
            ToolCall(
                id=raw_call.get("id") or f"native-{index}",
                name=name,
                arguments=arguments if isinstance(arguments, dict) else {},
                native=True,
            )
        )
    return calls


def parse_structured_actions(content: str) -> list[ToolCall]:
    if not content:
        return []

    tagged_calls = parse_tagged_tool_calls(content)
    if tagged_calls:
        return tagged_calls

    candidates = re.findall(r"```(?:json)?\s*([\s\S]*?)```", content, flags=re.IGNORECASE)
    stripped = content.strip()
    if stripped.startswith("{") and stripped.endswith("}"):
        candidates.append(stripped)

    for candidate in candidates:
        try:
            payload = json.loads(candidate.strip())
        except json.JSONDecodeError:
            continue

        actions = payload.get("actions") if isinstance(payload, dict) else None
        if not isinstance(actions, list):
            actions = [payload]

        calls: list[ToolCall] = []
        for index, action in enumerate(actions):
            if not isinstance(action, dict):
                continue
            action_type = action.get("action") or "tool"
            name = action.get("tool") or action.get("name")
            arguments = action.get("arguments") or action.get("args") or {}
            if action_type == "tool" and name and isinstance(arguments, dict):
                calls.append(
                    ToolCall(
                        id=f"structured-{index}",
                        name=name,
                        arguments=arguments,
                        native=False,
                    )
                )
        if calls:
            return calls

    return []


def parse_tagged_tool_calls(content: str) -> list[ToolCall]:
    calls: list[ToolCall] = []
    blocks = re.findall(
        r"<tool_call>\s*([A-Za-z0-9_]+)\s*([\s\S]*?)</tool_call>",
        content,
        flags=re.IGNORECASE,
    )
    for index, (name, body) in enumerate(blocks):
        arguments = {
            key.strip(): value.strip()
            for key, value in re.findall(
                r"<arg_key>\s*([\s\S]*?)\s*</arg_key>\s*"
                r"<arg_value>\s*([\s\S]*?)\s*</arg_value>",
                body,
                flags=re.IGNORECASE,
            )
            if key.strip()
        }
        calls.append(
            ToolCall(
                id=f"tagged-{index}",
                name=name.strip(),
                arguments=arguments,
                native=False,
            )
        )
    return calls


def call_gemini(messages: list[dict]) -> str:
    prompt = "\n\n".join(
        f"{message.get('role', 'user').upper()}: {normalize_content(message.get('content'))}"
        for message in messages
        if message.get("content")
    )
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}"
        f":generateContent?key={GEMINI_API_KEY}"
    )
    payload = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.45, "maxOutputTokens": 600},
    }
    data = post_json(url, payload)
    candidates = data.get("candidates") or []
    parts = candidates[0].get("content", {}).get("parts", []) if candidates else []
    text = "".join(part.get("text", "") for part in parts).strip()
    if not text:
        raise ProviderRequestError("Gemini returned no text.")
    return text


def normalize_content(content) -> str:
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        return "".join(
            item.get("text", "") for item in content if isinstance(item, dict)
        ).strip()
    return ""


SMALL_NUMBERS = (
    "zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine",
    "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen",
    "eighteen", "nineteen",
)
TENS_WORDS = ("", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety")
NUMBER_SCALES = ("", "thousand", "million", "billion", "trillion")


def _under_thousand_to_words(value: int) -> str:
    words = []
    number = value
    if number >= 100:
        words.extend((SMALL_NUMBERS[number // 100], "hundred"))
        number %= 100
    if number >= 20:
        words.append(TENS_WORDS[number // 10])
        number %= 10
    if number:
        words.append(SMALL_NUMBERS[number])
    return " ".join(words)


def _integer_to_words(value: int) -> str:
    number = abs(value)
    if number == 0:
        return SMALL_NUMBERS[0]
    words = []
    scale_index = 0
    while number and scale_index < len(NUMBER_SCALES):
        chunk = number % 1000
        if chunk:
            words.insert(
                0,
                " ".join(
                    item for item in (_under_thousand_to_words(chunk), NUMBER_SCALES[scale_index]) if item
                ),
            )
        number //= 1000
        scale_index += 1
    prefix = "minus " if value < 0 else ""
    return f"{prefix}{' '.join(words)}"


def _number_to_words(raw_value: str, maximum_decimals: int = 2) -> str:
    try:
        value = Decimal(str(raw_value).replace(",", ""))
    except InvalidOperation:
        return str(raw_value)
    quantum = Decimal(1).scaleb(-maximum_decimals)
    rounded = value.quantize(quantum, rounding=ROUND_HALF_UP) if maximum_decimals else value.quantize(Decimal(1), rounding=ROUND_HALF_UP)
    rendered = format(abs(rounded), "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    integer_part, _, decimal_part = rendered.partition(".")
    words = [_integer_to_words(int(integer_part or "0"))]
    if decimal_part:
        words.extend(("point", *(SMALL_NUMBERS[int(digit)] for digit in decimal_part)))
    prefix = "minus " if rounded < 0 else ""
    return f"{prefix}{' '.join(words)}"


def sanitize_for_speech(text: str) -> str:
    cleaned = re.sub(r"```[\s\S]*?```", " ", text)
    cleaned = re.sub(r"`([^`]+)`", r"\1", cleaned)
    cleaned = re.sub(r"\*\*([^*]+)\*\*", r"\1", cleaned)
    cleaned = re.sub(r"\*([^*]+)\*", r"\1", cleaned)
    cleaned = re.sub(r"__([^_]+)__", r"\1", cleaned)
    cleaned = re.sub(r"^#{1,6}\s*", "", cleaned, flags=re.MULTILINE)
    cleaned = re.sub(r"^\s*[-*+]\s+", "", cleaned, flags=re.MULTILINE)
    cleaned = re.sub(r"^\s*\d+\.\s+", "", cleaned, flags=re.MULTILINE)
    cleaned = cleaned.replace("|", " ").replace(">", " ")
    cleaned = re.sub(r"\[(.*?)\]\((.*?)\)", r"\1", cleaned)
    cleaned = re.sub(
        r"(?:last\s+(?:update|updated|refresh|pull)|cache\s+update|timestamp)\s*:\s*"
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?",
        "updated recently",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(
        r"\b\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?\b",
        "updated recently",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(
        r"\b[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\b",
        " ",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(
        r"(\d+)\s*[Yy]\s*[-\u2013\u2014]\s*(\d+)\s*[Yy]\b",
        lambda match: f"{_number_to_words(match.group(1), 0)}-year minus {_number_to_words(match.group(2), 0)}-year",
        cleaned,
    )
    scales = {"K": "thousand", "M": "million", "B": "billion", "T": "trillion"}
    cleaned = re.sub(
        r"\$\s*(-?\d[\d,]*(?:\.\d+)?)\s*(thousand|million|billion|trillion)\b(?:\s*USD)?",
        lambda match: (
            f"{_number_to_words(match.group(1).split('.')[0], 0)} "
            f"{match.group(2).lower()} dollars"
        ),
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(
        r"\$\s*(-?\d[\d,]*(?:\.\d+)?)\s*([KMBT])\b",
        lambda match: (
            f"{_number_to_words(match.group(1).split('.')[0], 0)} "
            f"{scales[match.group(2).upper()]} dollars"
        ),
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(
        r"\$\s*(-?\d[\d,]*(?:\.\d+)?)",
        lambda match: f"{_number_to_words(match.group(1))} dollars",
        cleaned,
    )
    cleaned = re.sub(
        r"(-?\d[\d,]*(?:\.\d+)?)\s*%",
        lambda match: f"{_number_to_words(match.group(1))} percent",
        cleaned,
    )
    cleaned = re.sub(
        r"\b(-?\d[\d,]*\.\d+)\b",
        lambda match: _number_to_words(match.group(1), 3),
        cleaned,
    )
    cleaned = re.sub(
        r"\b(\d+)\s*[Yy]\b",
        lambda match: f"{_number_to_words(match.group(1), 0)}-year",
        cleaned,
    )
    cleaned = re.sub(r"[\\/()\[\]{}]+", " ", cleaned)
    cleaned = re.sub(r"[|>`~*_#]+", " ", cleaned)
    cleaned = re.sub(r"[.;:!?]+", " ", cleaned)
    return re.sub(r"\s+", " ", cleaned).strip()


def post_json(
    url: str,
    payload: dict,
    headers: dict | None = None,
    *,
    timeout: float | None = None,
) -> dict:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", **(headers or {})},
        method="POST",
    )

    try:
        with urllib.request.urlopen(
            request,
            timeout=timeout if timeout is not None else PROVIDER_TIMEOUT_SECONDS,
        ) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise ProviderRequestError(
            f"HTTP {exc.code} from provider.", status_code=exc.code
        ) from exc
    except urllib.error.URLError as exc:
        raise ProviderRequestError("Provider connection failed.") from exc
    except TimeoutError as exc:
        raise ProviderRequestError("Provider request timed out.", status_code=408) from exc
