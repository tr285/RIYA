"""Latency and process resource telemetry for RIYA's local AI runtime."""

from __future__ import annotations

from collections import deque
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import resource
import sys
import threading
import time
from typing import Any


def read_process_rss_mb() -> float:
    """Return current/peak resident memory footprint in megabytes using stdlib resource."""
    usage = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    # On macOS (Darwin), ru_maxrss is reported in bytes; on Linux it is kilobytes.
    if sys.platform == "darwin":
        return round(usage / (1024.0 * 1024.0), 2)
    return round(usage / 1024.0, 2)


def estimate_tokens(text: str | None) -> int:
    """Rough conservative token estimate (~3.5 chars per token for English/code)."""
    if not text:
        return 0
    return max(1, (len(text) + 3) // 4)


@dataclass(frozen=True)
class InferenceMetrics:
    """Single inference or model lifecycle telemetry record."""

    timestamp: str
    operation: str  # "generate" | "load_model" | "reload_skipped" | "unload_model"
    model_name: str
    backend: str
    context_size: int
    prompt_chars: int
    context_chars: int
    output_chars: int
    approx_prompt_tokens: int
    approx_output_tokens: int
    latency_ms: float
    tokens_per_second: float
    rss_mb_before: float
    rss_mb_after: float
    rss_delta_mb: float
    success: bool
    error_type: str | None = None
    fallback_used: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class RuntimeMetricsTracker:
    """Thread-safe bounded metrics collector for LocalLlamaRuntime."""

    def __init__(self, max_history: int = 100):
        self._lock = threading.Lock()
        self._records: deque[InferenceMetrics] = deque(maxlen=max(10, int(max_history)))
        self.load_count: int = 0
        self.reload_skipped_count: int = 0
        self.unload_count: int = 0
        self.last_load_time_ms: float = 0.0
        self.cpu_fallback_count: int = 0

    def record_load(
        self,
        *,
        model_name: str,
        backend: str,
        context_size: int,
        latency_ms: float,
        rss_before: float,
        rss_after: float,
        success: bool,
        error_type: str | None = None,
        fallback_used: bool = False,
    ) -> InferenceMetrics:
        rec = InferenceMetrics(
            timestamp=datetime.now(timezone.utc).isoformat(),
            operation="load_model",
            model_name=model_name,
            backend=backend,
            context_size=context_size,
            prompt_chars=0,
            context_chars=0,
            output_chars=0,
            approx_prompt_tokens=0,
            approx_output_tokens=0,
            latency_ms=round(latency_ms, 2),
            tokens_per_second=0.0,
            rss_mb_before=rss_before,
            rss_mb_after=rss_after,
            rss_delta_mb=round(rss_after - rss_before, 2),
            success=success,
            error_type=error_type,
            fallback_used=fallback_used,
        )
        with self._lock:
            if success:
                self.load_count += 1
                self.last_load_time_ms = rec.latency_ms
                if fallback_used:
                    self.cpu_fallback_count += 1
            self._records.append(rec)
        return rec

    def record_reload_skipped(
        self,
        *,
        model_name: str,
        backend: str,
        context_size: int,
    ) -> InferenceMetrics:
        rss_now = read_process_rss_mb()
        rec = InferenceMetrics(
            timestamp=datetime.now(timezone.utc).isoformat(),
            operation="reload_skipped",
            model_name=model_name,
            backend=backend,
            context_size=context_size,
            prompt_chars=0,
            context_chars=0,
            output_chars=0,
            approx_prompt_tokens=0,
            approx_output_tokens=0,
            latency_ms=0.0,
            tokens_per_second=0.0,
            rss_mb_before=rss_now,
            rss_mb_after=rss_now,
            rss_delta_mb=0.0,
            success=True,
        )
        with self._lock:
            self.reload_skipped_count += 1
            self._records.append(rec)
        return rec

    def record_unload(self, *, model_name: str, backend: str, context_size: int) -> None:
        rss_now = read_process_rss_mb()
        rec = InferenceMetrics(
            timestamp=datetime.now(timezone.utc).isoformat(),
            operation="unload_model",
            model_name=model_name,
            backend=backend,
            context_size=context_size,
            prompt_chars=0,
            context_chars=0,
            output_chars=0,
            approx_prompt_tokens=0,
            approx_output_tokens=0,
            latency_ms=0.0,
            tokens_per_second=0.0,
            rss_mb_before=rss_now,
            rss_mb_after=rss_now,
            rss_delta_mb=0.0,
            success=True,
        )
        with self._lock:
            self.unload_count += 1
            self._records.append(rec)

    def record_generate(
        self,
        *,
        model_name: str,
        backend: str,
        context_size: int,
        prompt_chars: int,
        context_chars: int,
        output_text: str,
        approx_prompt_tokens: int,
        latency_ms: float,
        rss_before: float,
        rss_after: float,
        success: bool,
        error_type: str | None = None,
    ) -> InferenceMetrics:
        out_tokens = estimate_tokens(output_text) if success else 0
        secs = max(latency_ms / 1000.0, 1e-6)
        tps = round(out_tokens / secs, 2) if (success and out_tokens > 0) else 0.0
        rec = InferenceMetrics(
            timestamp=datetime.now(timezone.utc).isoformat(),
            operation="generate",
            model_name=model_name,
            backend=backend,
            context_size=context_size,
            prompt_chars=prompt_chars,
            context_chars=context_chars,
            output_chars=len(output_text or ""),
            approx_prompt_tokens=approx_prompt_tokens,
            approx_output_tokens=out_tokens,
            latency_ms=round(latency_ms, 2),
            tokens_per_second=tps,
            rss_mb_before=rss_before,
            rss_mb_after=rss_after,
            rss_delta_mb=round(rss_after - rss_before, 2),
            success=success,
            error_type=error_type,
        )
        with self._lock:
            self._records.append(rec)
        return rec

    @property
    def last_metrics(self) -> InferenceMetrics | None:
        with self._lock:
            return self._records[-1] if self._records else None

    def history(self) -> list[InferenceMetrics]:
        with self._lock:
            return list(self._records)

    def summary(self) -> dict[str, Any]:
        with self._lock:
            gen_records = [r for r in self._records if r.operation == "generate"]
            ok_gen = [r for r in gen_records if r.success]
            avg_latency = (
                round(sum(r.latency_ms for r in ok_gen) / len(ok_gen), 2)
                if ok_gen
                else 0.0
            )
            avg_tps = (
                round(sum(r.tokens_per_second for r in ok_gen) / len(ok_gen), 2)
                if ok_gen
                else 0.0
            )
            return {
                "load_count": self.load_count,
                "reload_skipped_count": self.reload_skipped_count,
                "unload_count": self.unload_count,
                "last_load_time_ms": self.last_load_time_ms,
                "cpu_fallback_count": self.cpu_fallback_count,
                "generation_calls": len(gen_records),
                "successful_generations": len(ok_gen),
                "failed_generations": len(gen_records) - len(ok_gen),
                "avg_latency_ms": avg_latency,
                "avg_tokens_per_second": avg_tps,
                "process_rss_mb": read_process_rss_mb(),
            }


class Stopwatch:
    """Simple monotonic timer for measuring runtime latencies."""

    def __init__(self) -> None:
        self._start = time.perf_counter()

    def elapsed_ms(self) -> float:
        return (time.perf_counter() - self._start) * 1000.0
