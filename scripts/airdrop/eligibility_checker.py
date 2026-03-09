#!/usr/bin/env python3
"""Eligibility checker for the RIP-305 wRTC airdrop.

Implements the 6-tier contribution scoring from RIP-305 §3.
Tier resolution: highest qualifying tier wins.

Tiers
-----
| Tier       | Requirement                        | Base Claim |
|------------|------------------------------------|------------|
| Stargazer  | 10+ Scottcjn repos starred         | 25 wRTC    |
| Contributor| 1+ merged PR                       | 50 wRTC    |
| Builder    | 3+ merged PRs                      | 100 wRTC   |
| Security   | Verified vulnerability found       | 150 wRTC   |
| Core       | 5+ merged PRs or Star King badge   | 200 wRTC   |
| Miner      | Active attestation history         | 100 wRTC   |
"""
from __future__ import annotations

import json
import logging
import urllib.request
import urllib.error
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, Optional, Tuple

from scripts.airdrop.config import AirdropConfig, get_config

logger = logging.getLogger("airdrop.eligibility")


# ---------------------------------------------------------------------------
# Tier definition
# ---------------------------------------------------------------------------

class EligibilityTier(str, Enum):
    STARGAZER = "STARGAZER"
    CONTRIBUTOR = "CONTRIBUTOR"
    BUILDER = "BUILDER"
    SECURITY = "SECURITY"
    CORE = "CORE"
    MINER = "MINER"

    @property
    def priority(self) -> int:
        """Higher is better — used to resolve 'highest qualifying tier'."""
        _order = {
            "STARGAZER": 0,
            "CONTRIBUTOR": 1,
            "MINER": 2,
            "BUILDER": 3,
            "SECURITY": 4,
            "CORE": 5,
        }
        return _order.get(self.value, -1)


# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class EligibilityResult:
    """Outcome of an eligibility check."""

    tier: Optional[EligibilityTier]
    base_claim_wrtc: int
    multiplier: float
    final_claim_wrtc: float
    requirements_met: Dict[str, bool]
    github_username: str
    target_chain: str = ""


# ---------------------------------------------------------------------------
# GitHub contribution checks
# ---------------------------------------------------------------------------

def _check_stars(
    starred_repos: Tuple[str, ...],
    min_stars: int,
) -> bool:
    return len(starred_repos) >= min_stars


def _check_merged_prs(merged_count: int, threshold: int) -> bool:
    return merged_count >= threshold


# ---------------------------------------------------------------------------
# Miner check (RustChain node API)
# ---------------------------------------------------------------------------

def _check_miner(
    github_username: str,
    node_url: str,
) -> bool:
    """Return *True* if *github_username* has an active miner on-chain.

    Queries ``GET /api/miners`` and looks for a miner whose ID
    contains the GitHub username (convention: ``<machine>-<arch>-<user>``).
    """
    try:
        url = f"{node_url}/api/miners"
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=15) as resp:
            miners = json.loads(resp.read().decode())
        if isinstance(miners, dict):
            miners = miners.get("miners", [])
        username_lower = github_username.lower()
        for miner in miners:
            miner_id = (miner.get("miner") or miner.get("miner_id") or "").lower()
            if username_lower in miner_id:
                return True
    except Exception as exc:
        logger.warning("Miner check failed: %s", exc)
    return False


# ---------------------------------------------------------------------------
# Main eligibility function
# ---------------------------------------------------------------------------

def check_eligibility(
    github_username: str,
    starred_repos: Tuple[str, ...],
    merged_pr_count: int,
    config: Optional[AirdropConfig] = None,
    *,
    has_security_finding: bool = False,
    has_star_king_badge: bool = False,
    check_miner: bool = True,
) -> EligibilityResult:
    """Determine the highest eligible tier for a GitHub user.

    Parameters
    ----------
    github_username:
        GitHub login.
    starred_repos:
        Names of Scottcjn repos the user has starred.
    merged_pr_count:
        Number of merged PRs in Scottcjn org repos.
    config:
        Airdrop configuration; defaults used if *None*.
    has_security_finding:
        Whether user has a verified vulnerability finding.
    has_star_king_badge:
        Whether user holds the Star King badge.
    check_miner:
        Whether to query RustChain node for miner status.

    Returns
    -------
    EligibilityResult
    """
    if config is None:
        config = get_config()

    requirements: Dict[str, bool] = {}

    # -- evaluate all tiers --------------------------------------------------
    requirements["stars_gte_10"] = _check_stars(
        starred_repos, config.stargazer_min_stars
    )
    requirements["merged_prs_gte_1"] = _check_merged_prs(
        merged_pr_count, config.contributor_min_prs
    )
    requirements["merged_prs_gte_3"] = _check_merged_prs(
        merged_pr_count, config.builder_min_prs
    )
    requirements["merged_prs_gte_5"] = _check_merged_prs(
        merged_pr_count, config.core_min_prs
    )
    requirements["star_king_badge"] = has_star_king_badge
    requirements["security_finding"] = has_security_finding

    is_miner = False
    if check_miner:
        try:
            is_miner = _check_miner(github_username, config.rustchain_node_url)
        except Exception:
            pass
    requirements["active_miner"] = is_miner

    # -- resolve highest tier ------------------------------------------------
    qualifying: list[EligibilityTier] = []

    if requirements["stars_gte_10"]:
        qualifying.append(EligibilityTier.STARGAZER)
    if requirements["merged_prs_gte_1"]:
        qualifying.append(EligibilityTier.CONTRIBUTOR)
    if requirements["merged_prs_gte_3"]:
        qualifying.append(EligibilityTier.BUILDER)
    if requirements["security_finding"]:
        qualifying.append(EligibilityTier.SECURITY)
    if requirements["merged_prs_gte_5"] or requirements["star_king_badge"]:
        qualifying.append(EligibilityTier.CORE)
    if requirements["active_miner"]:
        qualifying.append(EligibilityTier.MINER)

    if not qualifying:
        logger.info("User %s is not eligible for any tier", github_username)
        return EligibilityResult(
            tier=None,
            base_claim_wrtc=0,
            multiplier=1.0,
            final_claim_wrtc=0,
            requirements_met=requirements,
            github_username=github_username,
        )

    best = max(qualifying, key=lambda t: t.priority)
    base_claim = config.tier_claims.get(best.value, 0)

    logger.info(
        "User %s qualifies for tier %s (%d wRTC base)",
        github_username, best.value, base_claim,
    )

    return EligibilityResult(
        tier=best,
        base_claim_wrtc=base_claim,
        multiplier=1.0,  # multiplier applied later from wallet check
        final_claim_wrtc=float(base_claim),
        requirements_met=requirements,
        github_username=github_username,
    )
