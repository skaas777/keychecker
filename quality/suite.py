"""
Test suite orchestrator — coordinates the execution of all probes.

Three test tiers:
- Tier 1 (Smoke): authenticity (3 probes) + latency (5 samples) + anomalies. ~15s, ~$0.01
- Tier 2 (Standard): full authenticity + capability + latency (10 samples) + anomalies. ~60s, ~$0.10
- Tier 3 (Deep): all probes with higher sample counts. ~5min, ~$0.50
"""

import asyncio
import time
import json
from typing import Callable, Optional

from quality.probes.authenticity import AuthenticityProbe
from quality.probes.capability import CapabilityProbe
from quality.probes.latency import LatencyProbe
from quality.probes.anomalies import AnomalyProbe
from quality.scoring import Scoring


TIER_CONFIG = {
    1: {
        "authenticity": {"enabled": True, "n_probes": 3},
        "capability": {"enabled": False},
        "latency": {"enabled": True, "n_samples": 5},
        "anomalies": {"enabled": True},
        "label": "Smoke Test",
    },
    2: {
        "authenticity": {"enabled": True, "n_probes": 6},
        "capability": {"enabled": True},
        "latency": {"enabled": True, "n_samples": 10},
        "anomalies": {"enabled": True},
        "label": "Standard Test",
    },
    3: {
        "authenticity": {"enabled": True, "n_probes": 6},
        "capability": {"enabled": True},
        "latency": {"enabled": True, "n_samples": 30},
        "anomalies": {"enabled": True},
        "label": "Deep Audit",
    },
}


class TestSuite:
    """Coordinates the execution of all quality test probes."""

    def __init__(self, tier: int = 2):
        self.tier = tier
        self.config = TIER_CONFIG.get(tier, TIER_CONFIG[2])
        self.authenticity = AuthenticityProbe()
        self.capability = CapabilityProbe()
        self.latency = LatencyProbe()
        self.anomalies = AnomalyProbe()
        self.scoring = Scoring()

    async def run(
        self,
        caller,
        model: str,
        advertised_model: str = None,
        progress_callback: Callable[[str], None] = None,
    ) -> dict:
        """
        Run the full test suite.

        Args:
            caller: LLMCaller instance
            model: model ID for API calls
            advertised_model: the model the endpoint claims to serve
            progress_callback: optional callback for progress updates

        Returns:
            {
                "tier": int,
                "tier_label": str,
                "overall": {...},
                "probes": {...},
                "duration_seconds": float,
            }
        """
        start = time.monotonic()
        results = {
            "tier": self.tier,
            "tier_label": self.config["label"],
            "probes": {},
        }

        def _progress(msg):
            if progress_callback:
                progress_callback(msg)

        # --- Authenticity ---
        if self.config["authenticity"]["enabled"]:
            n = self.config["authenticity"]["n_probes"]
            _progress(f"Running authenticity probes ({n} probes)...")
            results["probes"]["authenticity"] = await self.authenticity.run(
                caller, model, advertised_model or model, max_probes=n
            )

        # --- Capability ---
        if self.config["capability"]["enabled"]:
            _progress("Running capability benchmarks (GSM8K + IFEval + code)...")
            results["probes"]["capability"] = await self.capability.run(caller, model)

        # --- Latency ---
        if self.config["latency"]["enabled"]:
            n_samples = self.config["latency"]["n_samples"]
            _progress(f"Running latency probes ({n_samples} concurrent samples)...")
            results["probes"]["latency"] = await self.latency.run(caller, model, n_samples)

        # --- Anomalies ---
        if self.config["anomalies"]["enabled"]:
            _progress("Running anomaly detection probes...")
            results["probes"]["anomalies"] = await self.anomalies.run(caller, model)

        # --- Scoring ---
        _progress("Aggregating results and computing score...")
        results["overall"] = self.scoring.evaluate(results["probes"])

        duration = time.monotonic() - start
        results["duration_seconds"] = round(duration, 2)

        _progress(f"Test complete: {results['overall']['summary']}")

        return results
