#!/usr/bin/env python3
"""Anti-Sybil validation for the RIP-305 wRTC airdrop.

Implements the 7 checks from RIP-305 §4 plus the wallet-value
multiplier from §3.  Designed to be testable in isolation — every
check is a pure function that returns a (passed, detail) pair.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Protocol, Tuple

from scripts.airdrop.config import AirdropConfig, get_config

logger = logging.getLogger("airdrop.sybil")


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CheckResult:
    """Outcome of a single anti-Sybil check."""

    passed: bool
    detail: str


@dataclass(frozen=True)
class AntiSybilResult:
    """Aggregate outcome of all anti-Sybil checks."""

    passed: bool
    checks: Dict[str, CheckResult]
    blocking_reasons: Tuple[str, ...]


# ---------------------------------------------------------------------------
# Claims database protocol (duck-typed)
# ---------------------------------------------------------------------------

class ClaimsDB(Protocol):
    """Minimal interface for checking claim uniqueness."""

    def get_by_github(self, github_id: int) -> Any: ...
    def get_by_wallet(self, address: str) -> Any: ...


# ---------------------------------------------------------------------------
# Individual checks (all pure functions)
# ---------------------------------------------------------------------------

def check_wallet_balance(
    chain: str,
    balance: float,
    config: Optional[AirdropConfig] = None,
) -> CheckResult:
    """RIP-305 §4 — minimum wallet balance."""
    if config is None:
        config = get_config()
    if chain == "solana":
        minimum = config.min_sol_balance
        unit = "SOL"
    elif chain == "base":
        minimum = config.min_eth_balance
        unit = "ETH"
    else:
        return CheckResult(False, f"unsupported chain: {chain}")

    if balance >= minimum:
        return CheckResult(True, f"{balance:.4f} {unit} >= {minimum} {unit}")
    return CheckResult(False, f"{balance:.4f} {unit} < {minimum} {unit} minimum")


def check_wallet_age(
    wallet_age_days: int,
    config: Optional[AirdropConfig] = None,
) -> CheckResult:
    """RIP-305 §4 — wallet must be at least 7 days old."""
    if config is None:
        config = get_config()
    threshold = config.min_wallet_age_days
    if wallet_age_days >= threshold:
        return CheckResult(True, f"wallet age {wallet_age_days}d >= {threshold}d")
    return CheckResult(False, f"wallet age {wallet_age_days}d < {threshold}d minimum")


def check_github_account_age(
    account_age_days: int,
    config: Optional[AirdropConfig] = None,
) -> CheckResult:
    """RIP-305 §4 — GitHub account must be at least 30 days old."""
    if config is None:
        config = get_config()
    threshold = config.min_github_age_days
    if account_age_days >= threshold:
        return CheckResult(True, f"account age {account_age_days}d >= {threshold}d")
    return CheckResult(False, f"account age {account_age_days}d < {threshold}d minimum")


def check_github_unique(
    github_id: int,
    claims_db: Optional[ClaimsDB] = None,
) -> CheckResult:
    """RIP-305 §4 — one claim per GitHub account."""
    if claims_db is None:
        return CheckResult(True, "no claims DB provided — skipped")
    existing = claims_db.get_by_github(github_id)
    if existing is not None:
        return CheckResult(False, f"GitHub ID {github_id} already claimed")
    return CheckResult(True, f"GitHub ID {github_id} has no prior claim")


def check_wallet_unique(
    address: str,
    claims_db: Optional[ClaimsDB] = None,
) -> CheckResult:
    """RIP-305 §4 — one claim per wallet address."""
    if claims_db is None:
        return CheckResult(True, "no claims DB provided — skipped")
    existing = claims_db.get_by_wallet(address)
    if existing is not None:
        return CheckResult(False, f"wallet {address[:12]}… already claimed")
    return CheckResult(True, f"wallet {address[:12]}… has no prior claim")


def check_no_cross_chain_double_claim(
    github_id: int,
    claims_db: Optional[ClaimsDB] = None,
) -> CheckResult:
    """RIP-305 §4 — prevent double-dipping across chains."""
    if claims_db is None:
        return CheckResult(True, "no claims DB provided — skipped")
    existing = claims_db.get_by_github(github_id)
    if existing is not None:
        return CheckResult(False, f"GitHub ID {github_id} already has a cross-chain claim")
    return CheckResult(True, "no cross-chain double-claim detected")


def check_rtc_wallet_binding(
    rtc_wallet: str,
    github_username: str,
) -> CheckResult:
    """RIP-305 §4 — links on-chain identity to GitHub.

    Currently a soft check: verifies the RTC wallet name is non-empty.
    Future: query the RustChain ledger to confirm RTC wallet ownership.
    """
    if not rtc_wallet or not rtc_wallet.strip():
        return CheckResult(False, "no RTC wallet provided")
    return CheckResult(True, f"RTC wallet '{rtc_wallet}' bound to {github_username}")


# ---------------------------------------------------------------------------
# Wallet-value multiplier (RIP-305 §3)
# ---------------------------------------------------------------------------

def compute_wallet_multiplier(
    chain: str,
    balance: float,
    config: Optional[AirdropConfig] = None,
) -> float:
    """Return the wallet-value multiplier (1.0x / 1.5x / 2.0x).

    Tiers from RIP-305 §3:
        Solana: 0.1–1 SOL → 1.0x, 1–10 SOL → 1.5x, 10+ SOL → 2.0x
        Base:   0.01–0.1 ETH → 1.0x, 0.1–1 ETH → 1.5x, 1+ ETH → 2.0x
    """
    if config is None:
        config = get_config()

    tiers = config.multiplier_tiers.get(chain)
    if not tiers:
        return 1.0

    multiplier = 1.0
    for threshold, mult in sorted(tiers, key=lambda t: t[0]):
        if balance >= threshold:
            multiplier = mult
    return multiplier


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

def run_anti_sybil(
    github_id: int,
    github_username: str,
    account_age_days: int,
    chain: str,
    wallet_address: str,
    wallet_balance: float,
    wallet_age_days: int,
    rtc_wallet: str,
    claims_db: Optional[ClaimsDB] = None,
    config: Optional[AirdropConfig] = None,
) -> AntiSybilResult:
    """Run all 7 anti-Sybil checks and return an aggregate result.

    Parameters
    ----------
    github_id:
        Unique GitHub user ID.
    github_username:
        GitHub login.
    account_age_days:
        Age of the GitHub account in days.
    chain:
        ``"solana"`` or ``"base"``.
    wallet_address:
        Target wallet address on the chosen chain.
    wallet_balance:
        Wallet balance in SOL or ETH.
    wallet_age_days:
        Age of the wallet in days.
    rtc_wallet:
        RustChain wallet name for on-chain binding.
    claims_db:
        Optional claims store for uniqueness checks.
    config:
        Optional configuration; defaults used if *None*.
    """
    if config is None:
        config = get_config()

    checks: Dict[str, CheckResult] = {}
    checks["wallet_balance"] = check_wallet_balance(chain, wallet_balance, config)
    checks["wallet_age"] = check_wallet_age(wallet_age_days, config)
    checks["github_account_age"] = check_github_account_age(account_age_days, config)
    checks["github_unique"] = check_github_unique(github_id, claims_db)
    checks["wallet_unique"] = check_wallet_unique(wallet_address, claims_db)
    checks["no_cross_chain_double_claim"] = check_no_cross_chain_double_claim(
        github_id, claims_db
    )
    checks["rtc_wallet_binding"] = check_rtc_wallet_binding(rtc_wallet, github_username)

    blocking = tuple(
        name for name, result in checks.items() if not result.passed
    )
    passed = len(blocking) == 0

    if not passed:
        logger.warning(
            "Anti-Sybil FAILED for %s: %s",
            github_username, ", ".join(blocking),
        )
    else:
        logger.info("Anti-Sybil PASSED for %s", github_username)

    return AntiSybilResult(passed=passed, checks=checks, blocking_reasons=blocking)
