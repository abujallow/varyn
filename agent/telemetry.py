from __future__ import annotations

import platform
import threading
import time
from datetime import datetime, timezone

import psutil


class SystemMonitor:
    """Thread-safe local telemetry sampler backed by psutil."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        counters = psutil.net_io_counters()
        self._last_net_bytes = counters.bytes_sent + counters.bytes_recv
        self._last_sample_time = time.monotonic()
        psutil.cpu_percent(interval=None)

    def sample(self) -> dict:
        with self._lock:
            now = time.monotonic()
            counters = psutil.net_io_counters()
            total_net_bytes = counters.bytes_sent + counters.bytes_recv
            elapsed = max(now - self._last_sample_time, 0.001)
            byte_delta = max(total_net_bytes - self._last_net_bytes, 0)
            network_kbps = byte_delta / 1024 / elapsed
            self._last_net_bytes = total_net_bytes
            self._last_sample_time = now

            uptime_seconds = max(0, int(time.time() - psutil.boot_time()))
            temperature = read_temperature()

            return {
                "ok": True,
                "source": "psutil",
                "sampled_at": datetime.now(timezone.utc).isoformat(),
                "cpu_percent": round(psutil.cpu_percent(interval=None), 1),
                "memory_percent": round(psutil.virtual_memory().percent, 1),
                "network_kbps": round(network_kbps, 1),
                "process_count": len(psutil.pids()),
                "os": short_os_name(),
                "uptime_seconds": uptime_seconds,
                "uptime": format_uptime(uptime_seconds),
                "gpu_percent": None,
                "temperature_c": temperature,
                "availability": {
                    "cpu": True,
                    "memory": True,
                    "network": True,
                    "processes": True,
                    "gpu": False,
                    "temperature": temperature is not None,
                },
            }


def read_temperature() -> float | None:
    sensor_reader = getattr(psutil, "sensors_temperatures", None)
    if not sensor_reader:
        return None

    try:
        sensors = sensor_reader(fahrenheit=False) or {}
    except (AttributeError, NotImplementedError, OSError):
        return None

    for entries in sensors.values():
        for entry in entries:
            current = getattr(entry, "current", None)
            if isinstance(current, (int, float)):
                return round(float(current), 1)
    return None


def short_os_name() -> str:
    names = {
        "Windows": "WIN",
        "Linux": "LINUX",
        "Darwin": "MAC",
    }
    system = platform.system()
    return names.get(system, system.upper() or "N/A")


def format_uptime(total_seconds: int) -> str:
    days, remainder = divmod(total_seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, seconds = divmod(remainder, 60)
    if days:
        return f"{days}d {hours:02}:{minutes:02}:{seconds:02}"
    return f"{hours:02}:{minutes:02}:{seconds:02}"

