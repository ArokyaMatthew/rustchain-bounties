#!/usr/bin/env python3
"""Environment-driven configuration for the RIP-305 airdrop system.

All thresholds, API endpoints, and contract addresses are loaded from
environment variables with the ``AIRDROP_`` prefix.  This makes the system
deployable to different environments (testnet, mainnet) without code changes.

Usage::

    from scripts.airdrop.config import get_config
    cfg = get_config()
    print(cfg.rustchain_node_url)

Override any setting via environment::

    AIRDROP_RUSTCHAIN_NODE_URL=https://testnet.rustchain.org python ...
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Optional


# ---------------------------------------------------------------------------
# Eligibility tier defaults (RIP-305 §3)
# ---------------------------------------------------------------------------

DEFAULT_TIER_CLAIMS: Dict[str, int] = {
    "STARGAZER": 25,
    "CONTRIBUTOR": 50,
    "BUILDER": 100,
    "SECURITY": 150,
    "CORE": 200,
    "MINER": 100,
}

# Tier qualification thresholds
DEFAULT_STARGAZER_MIN_STARS: int = 10
DEFAULT_CONTRIBUTOR_MIN_PRS: int = 1
DEFAULT_BUILDER_MIN_PRS: int = 3
DEFAULT_CORE_MIN_PRS: int = 5

# Wallet-value multiplier boundaries (RIP-305 §3)
DEFAULT_MULTIPLIER_TIERS = {
    "solana": [(0.1, 1.0), (1.0, 1.5), (10.0, 2.0)],
    "base": [(0.01, 1.0), (0.1, 1.5), (1.0, 2.0)],
}


@dataclass(frozen=True)
class AirdropConfig:
    """Centralised configuration — every tuneable knob lives here."""

    # -- Network endpoints ---------------------------------------------------
    rustchain_node_url: str = "https://50.28.86.131"
    solana_rpc_url: str = "https://api.mainnet-beta.solana.com"
    base_rpc_url: str = "https://mainnet.base.org"

    # -- Contract addresses --------------------------------------------------
    wrtc_solana_mint: str = "12TAdKXxcGf6oCv4rqDz2NkgxjyHq6HQKoxKZYGf5i4X"
    wrtc_base_contract: str = ""  # set when Track B deploys

    # -- Allocation ----------------------------------------------------------
    solana_allocation: int = 30_000
    base_allocation: int = 20_000

    # -- Anti-Sybil thresholds (RIP-305 §4) ----------------------------------
    min_sol_balance: float = 0.1
    min_eth_balance: float = 0.01
    min_wallet_age_days: int = 7
    min_github_age_days: int = 30

    # -- Eligibility tiers ---------------------------------------------------
    tier_claims: Dict[str, int] = field(default_factory=lambda: dict(DEFAULT_TIER_CLAIMS))
    stargazer_min_stars: int = DEFAULT_STARGAZER_MIN_STARS
    contributor_min_prs: int = DEFAULT_CONTRIBUTOR_MIN_PRS
    builder_min_prs: int = DEFAULT_BUILDER_MIN_PRS
    core_min_prs: int = DEFAULT_CORE_MIN_PRS

    # -- Wallet multiplier tiers ---------------------------------------------
    multiplier_tiers: Dict[str, list] = field(
        default_factory=lambda: {k: list(v) for k, v in DEFAULT_MULTIPLIER_TIERS.items()}
    )

    # -- GitHub OAuth --------------------------------------------------------
    github_client_id: str = ""
    github_client_secret: str = ""
    github_redirect_uri: str = ""
    github_org: str = "Scottcjn"

    # -- Storage -------------------------------------------------------------
    claims_db_path: str = "claims.json"
    use_sqlite: bool = False
    sqlite_path: str = "claims.db"

    # -- Observability -------------------------------------------------------
    log_level: str = "INFO"
    metrics_port: int = 9121  # next to existing exporter on 9120
    enable_metrics: bool = True


def _env(name: str, default: str) -> str:
    """Read ``AIRDROP_<NAME>`` from the environment."""
    return os.environ.get(f"AIRDROP_{name.upper()}", default)


def _env_int(name: str, default: int) -> int:
    raw = _env(name, "")
    if raw == "":
        return default
    return int(raw)


def _env_float(name: str, default: float) -> float:
    raw = _env(name, "")
    if raw == "":
        return default
    return float(raw)


def _env_bool(name: str, default: bool) -> bool:
    raw = _env(name, "").lower()
    if raw in ("1", "true", "yes"):
        return True
    if raw in ("0", "false", "no"):
        return False
    return default


def get_config() -> AirdropConfig:
    """Build an :class:`AirdropConfig` from environment variables.

    Every field can be overridden with ``AIRDROP_<FIELD_NAME>`` (upper-case).
    """
    return AirdropConfig(
        rustchain_node_url=_env("rustchain_node_url", AirdropConfig.rustchain_node_url),
        solana_rpc_url=_env("solana_rpc_url", AirdropConfig.solana_rpc_url),
        base_rpc_url=_env("base_rpc_url", AirdropConfig.base_rpc_url),
        wrtc_solana_mint=_env("wrtc_solana_mint", AirdropConfig.wrtc_solana_mint),
        wrtc_base_contract=_env("wrtc_base_contract", AirdropConfig.wrtc_base_contract),
        solana_allocation=_env_int("solana_allocation", AirdropConfig.solana_allocation),
        base_allocation=_env_int("base_allocation", AirdropConfig.base_allocation),
        min_sol_balance=_env_float("min_sol_balance", AirdropConfig.min_sol_balance),
        min_eth_balance=_env_float("min_eth_balance", AirdropConfig.min_eth_balance),
        min_wallet_age_days=_env_int("min_wallet_age_days", AirdropConfig.min_wallet_age_days),
        min_github_age_days=_env_int("min_github_age_days", AirdropConfig.min_github_age_days),
        stargazer_min_stars=_env_int("stargazer_min_stars", AirdropConfig.stargazer_min_stars),
        contributor_min_prs=_env_int("contributor_min_prs", AirdropConfig.contributor_min_prs),
        builder_min_prs=_env_int("builder_min_prs", AirdropConfig.builder_min_prs),
        core_min_prs=_env_int("core_min_prs", AirdropConfig.core_min_prs),
        github_client_id=_env("github_client_id", AirdropConfig.github_client_id),
        github_client_secret=_env("github_client_secret", AirdropConfig.github_client_secret),
        github_redirect_uri=_env("github_redirect_uri", AirdropConfig.github_redirect_uri),
        github_org=_env("github_org", AirdropConfig.github_org),
        claims_db_path=_env("claims_db_path", AirdropConfig.claims_db_path),
        use_sqlite=_env_bool("use_sqlite", AirdropConfig.use_sqlite),
        sqlite_path=_env("sqlite_path", AirdropConfig.sqlite_path),
        log_level=_env("log_level", AirdropConfig.log_level),
        metrics_port=_env_int("metrics_port", AirdropConfig.metrics_port),
        enable_metrics=_env_bool("enable_metrics", AirdropConfig.enable_metrics),
    )
