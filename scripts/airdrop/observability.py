#!/usr/bin/env python3
"""Logging and Prometheus metrics hooks for the RIP-305 airdrop system.

Aligned with the existing ``prometheus_exporter.py`` infrastructure.
Prometheus counters are optional and degrade gracefully when the
``prometheus_client`` package is not installed.
"""
from __future__ import annotations

import logging
import json
import sys
from datetime import datetime, timezone
from typing import Optional

# Structured JSON formatter for production deployments
# ---------------------------------------------------------------------------


class JSONFormatter(logging.Formatter):
    """Emit log records as single-line JSON for machine parsing."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info and record.exc_info[0] is not None:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def setup_logging(
    level: str = "INFO",
    json_output: bool = False,
    name: str = "airdrop",
) -> logging.Logger:
    """Configure the airdrop logger.

    Parameters
    ----------
    level:
        Standard Python log level name.
    json_output:
        If *True*, use :class:`JSONFormatter` for structured output.
    name:
        Logger name.
    """
    logger = logging.getLogger(name)
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))

    if not logger.handlers:
        handler = logging.StreamHandler(sys.stderr)
        if json_output:
            handler.setFormatter(JSONFormatter())
        else:
            handler.setFormatter(
                logging.Formatter(
                    "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
                    datefmt="%Y-%m-%dT%H:%M:%S",
                )
            )
        logger.addHandler(handler)

    return logger


# ---------------------------------------------------------------------------
# Prometheus metrics (optional dependency)
# ---------------------------------------------------------------------------

try:
    from prometheus_client import Counter, Gauge, start_http_server

    _HAS_PROMETHEUS = True
except ImportError:  # pragma: no cover
    _HAS_PROMETHEUS = False


class AirdropMetrics:
    """Prometheus counters and gauges for monitoring the airdrop pipeline.

    If ``prometheus_client`` is not installed, all methods become no-ops.
    """

    def __init__(self, enable: bool = True, port: int = 9121) -> None:
        self._enabled = enable and _HAS_PROMETHEUS
        self._port = port
        self._started = False

        if not self._enabled:
            return

        self.claims_started = Counter(
            "airdrop_claims_started_total",
            "Total claim attempts initiated",
            ["chain"],
        )
        self.claims_completed = Counter(
            "airdrop_claims_completed_total",
            "Total claims successfully completed",
            ["chain", "tier"],
        )
        self.claims_rejected = Counter(
            "airdrop_claims_rejected_total",
            "Total claims rejected",
            ["chain", "reason"],
        )
        self.eligibility_checks = Counter(
            "airdrop_eligibility_checks_total",
            "Total eligibility checks performed",
            ["tier", "result"],
        )
        self.anti_sybil_failures = Counter(
            "airdrop_anti_sybil_failures_total",
            "Anti-Sybil check failures",
            ["check"],
        )
        self.wrtc_distributed = Counter(
            "airdrop_wrtc_distributed_total",
            "Total wRTC distributed",
            ["chain"],
        )
        self.claims_active = Gauge(
            "airdrop_claims_active",
            "Currently in-progress claims",
        )

    # -- convenience wrappers ------------------------------------------------

    def start_server(self) -> None:
        """Start the ``/metrics`` HTTP server if not already running."""
        if self._enabled and not self._started:
            start_http_server(self._port)
            self._started = True

    def record_claim_started(self, chain: str) -> None:
        if self._enabled:
            self.claims_started.labels(chain=chain).inc()
            self.claims_active.inc()

    def record_claim_completed(self, chain: str, tier: str, wrtc_amount: float) -> None:
        if self._enabled:
            self.claims_completed.labels(chain=chain, tier=tier).inc()
            self.wrtc_distributed.labels(chain=chain).inc(wrtc_amount)
            self.claims_active.dec()

    def record_claim_rejected(self, chain: str, reason: str) -> None:
        if self._enabled:
            self.claims_rejected.labels(chain=chain, reason=reason).inc()
            self.claims_active.dec()

    def record_eligibility_check(self, tier: str, passed: bool) -> None:
        if self._enabled:
            self.eligibility_checks.labels(
                tier=tier, result="pass" if passed else "fail"
            ).inc()

    def record_anti_sybil_failure(self, check: str) -> None:
        if self._enabled:
            self.anti_sybil_failures.labels(check=check).inc()


# Module-level singleton — import and use directly
_metrics: Optional[AirdropMetrics] = None


def get_metrics(enable: bool = True, port: int = 9121) -> AirdropMetrics:
    """Return the module-level :class:`AirdropMetrics` singleton."""
    global _metrics
    if _metrics is None:
        _metrics = AirdropMetrics(enable=enable, port=port)
    return _metrics
