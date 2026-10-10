"""
Latency performance probe.

Measures streaming latency metrics via multiple concurrent streaming calls:
- TTFT (Time To First Token): request send → first token received
- TPS (Tokens Per Second): output tokens / generation duration
- ITL (Inter-Token Latency): average time between consecutive tokens
- Total latency: request send → last token received
- Error rate

Reports p50/p95/p99 percentiles plus mean.
"""

import asyncio
import math
from typing import Optional


class LatencyProbe:
    """Streaming latency measurement probe."""

    # Standard probe prompt — forces ~200 token output
    LATENCY_PROMPT = "Write a 200-word essay about the ocean. Be descriptive and varied in your vocabulary."

    async def run(self, caller, model: str, n_samples: int = 10) -> dict:
        """
        Run n_samples streaming latency tests concurrently.

        Args:
            caller: LLMCaller instance
            model: model ID
            n_samples: number of concurrent samples (default 10)

        Returns:
            {
                "ttft_ms": {"p50": float, "p95": float, "p99": float, "mean": float, "samples": [float]},
                "tps": {"p50": float, "p95": float, "mean": float, "samples": [float]},
                "itl_ms": {"p50": float, "p95": float, "mean": float, "samples": [float]},
                "total_latency_ms": {"p50": float, "p95": float, "mean": float},
                "error_rate": float,
                "n_samples": int,
                "n_success": int,
            }
        """
        # Run all samples concurrently
        tasks = [self._single_latency_test(caller, model) for _ in range(n_samples)]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        ttft_samples = []
        tps_samples = []
        itl_samples = []
        total_latency_samples = []
        n_errors = 0
        n_success = 0

        for result in results:
            if isinstance(result, Exception):
                n_errors += 1
                continue
            if result.get("error"):
                n_errors += 1
                continue

            n_success += 1
            ttft_samples.append(result["ttft_ms"])
            total_latency_samples.append(result["total_latency_ms"])

            # TPS = tokens / generation_time
            gen_time_s = (result["total_latency_ms"] - result["ttft_ms"]) / 1000
            if gen_time_s > 0 and result["token_count"] > 1:
                tps = result["token_count"] / gen_time_s
                tps_samples.append(tps)

            # ITL from chunks
            chunks = result.get("chunks", [])
            if len(chunks) > 1:
                intervals = []
                for i in range(1, len(chunks)):
                    interval = chunks[i]["timestamp_ms"] - chunks[i-1]["timestamp_ms"]
                    intervals.append(interval)
                if intervals:
                    itl_samples.extend(intervals)

        return {
            "ttft_ms": self._percentiles(ttft_samples),
            "tps": self._percentiles(tps_samples),
            "itl_ms": self._percentiles(itl_samples),
            "total_latency_ms": self._percentiles(total_latency_samples),
            "error_rate": n_errors / n_samples if n_samples > 0 else 0,
            "n_samples": n_samples,
            "n_success": n_success,
        }

    async def _single_latency_test(self, caller, model: str) -> dict:
        """Run a single streaming latency test."""
        return await caller.stream_chat(
            model=model,
            messages=[{"role": "user", "content": self.LATENCY_PROMPT}],
            max_tokens=256,
            temperature=0.0,
        )

    @staticmethod
    def _percentiles(samples: list[float]) -> dict:
        """Compute p50, p95, p99, mean from a list of samples."""
        if not samples:
            return {"p50": 0, "p95": 0, "p99": 0, "mean": 0, "min": 0, "max": 0, "n": 0}

        sorted_samples = sorted(samples)
        n = len(sorted_samples)

        def percentile(p: float) -> float:
            if n == 1:
                return sorted_samples[0]
            idx = (n - 1) * p
            lower = int(math.floor(idx))
            upper = int(math.ceil(idx))
            if lower == upper:
                return sorted_samples[lower]
            return sorted_samples[lower] + (sorted_samples[upper] - sorted_samples[lower]) * (idx - lower)

        return {
            "p50": round(percentile(0.50), 2),
            "p95": round(percentile(0.95), 2),
            "p99": round(percentile(0.99), 2),
            "mean": round(sum(samples) / n, 2),
            "min": round(sorted_samples[0], 2),
            "max": round(sorted_samples[-1], 2),
            "n": n,
        }
