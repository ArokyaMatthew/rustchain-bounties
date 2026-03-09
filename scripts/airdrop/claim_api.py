#!/usr/bin/env python3
"""REST-style claim API for the RIP-305 wRTC airdrop.

Implements the 4 endpoints from RIP-305 §7:
    GET  /airdrop/eligibility
    POST /airdrop/claim
    GET  /airdrop/status
    GET  /airdrop/leaderboard

Bridge calls are abstracted behind ``BridgeClient`` so Track C can be
integrated without modifying this module.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Protocol

from scripts.airdrop.anti_sybil import compute_wallet_multiplier, run_anti_sybil
from scripts.airdrop.claim_state import ClaimStatus, ClaimStore, get_store
from scripts.airdrop.config import AirdropConfig, get_config
from scripts.airdrop.eligibility_checker import check_eligibility
from scripts.airdrop.github_oauth import GitHubIdentity, verify_github_identity
from scripts.airdrop.observability import AirdropMetrics, get_metrics, setup_logging
from scripts.airdrop.wallet_connect import fetch_wallet_info, validate_base_address, validate_solana_address

logger = logging.getLogger("airdrop.api")


# ---------------------------------------------------------------------------
# Bridge client protocol + stub (Track C dependency)
# ---------------------------------------------------------------------------

class BridgeClient(Protocol):
    """Abstract bridge interface — see docs/API_CONTRACTS.md for spec."""

    def lock_rtc(
        self,
        rtc_wallet: str,
        amount: float,
        target_chain: str,
        target_address: str,
    ) -> Dict[str, Any]:
        """Lock RTC on RustChain and return ``{lock_id, status}``."""
        ...


@dataclass
class StubBridgeClient:
    """Stub bridge for development — always succeeds.

    Replace with real implementation when Track C lands.
    """

    def lock_rtc(
        self,
        rtc_wallet: str,
        amount: float,
        target_chain: str,
        target_address: str,
    ) -> Dict[str, Any]:
        logger.warning(
            "STUB: lock_rtc(wallet=%s, amount=%.2f, chain=%s) — no real lock",
            rtc_wallet, amount, target_chain,
        )
        return {
            "lock_id": f"stub-lock-{rtc_wallet}-{target_chain}",
            "status": "locked",
            "amount": amount,
            "target_chain": target_chain,
            "target_address": target_address,
        }


# ---------------------------------------------------------------------------
# API functions
# ---------------------------------------------------------------------------

def check_eligibility_api(
    github_username: str,
    starred_repos: tuple = (),
    merged_pr_count: int = 0,
    config: Optional[AirdropConfig] = None,
    **kwargs: Any,
) -> Dict[str, Any]:
    """``GET /airdrop/eligibility?github={username}`` logic.

    Returns tier, base_claim, and requirements_met.
    """
    if config is None:
        config = get_config()

    result = check_eligibility(
        github_username=github_username,
        starred_repos=starred_repos,
        merged_pr_count=merged_pr_count,
        config=config,
        **kwargs,
    )

    return {
        "github_username": result.github_username,
        "tier": result.tier.value if result.tier else None,
        "base_claim_wrtc": result.base_claim_wrtc,
        "requirements_met": result.requirements_met,
        "eligible": result.tier is not None,
    }


def process_claim(
    github_token: str,
    rtc_wallet: str,
    target_chain: str,
    target_address: str,
    config: Optional[AirdropConfig] = None,
    store: Optional[ClaimStore] = None,
    bridge: Optional[BridgeClient] = None,
    metrics: Optional[AirdropMetrics] = None,
    *,
    wallet_stub: bool = False,
) -> Dict[str, Any]:
    """``POST /airdrop/claim`` orchestration.

    Full pipeline:
        1. Verify GitHub identity via OAuth
        2. Check eligibility tier
        3. Run anti-Sybil checks
        4. Create claim record
        5. Lock RTC via bridge
        6. Emit metrics
        7. Return claim receipt

    Parameters
    ----------
    github_token:
        GitHub OAuth or PAT token.
    rtc_wallet:
        RustChain wallet name.
    target_chain:
        ``"solana"`` or ``"base"``.
    target_address:
        Wallet address on the target chain.
    config:
        Optional config.
    store:
        Optional claim store; created via factory if *None*.
    bridge:
        Optional bridge client; uses stub if *None*.
    metrics:
        Optional metrics instance.
    wallet_stub:
        If *True*, skip real wallet RPC calls.
    """
    if config is None:
        config = get_config()
    if store is None:
        store = get_store(config)
    if bridge is None:
        bridge = StubBridgeClient()
    if metrics is None:
        metrics = get_metrics(enable=config.enable_metrics, port=config.metrics_port)

    metrics.record_claim_started(target_chain)

    # 1. Verify GitHub identity
    try:
        identity: GitHubIdentity = verify_github_identity(github_token, config)
    except Exception as exc:
        metrics.record_claim_rejected(target_chain, "oauth_failed")
        return {"error": f"GitHub verification failed: {exc}", "status": "rejected"}

    # 2. Check eligibility
    eligibility = check_eligibility(
        github_username=identity.login,
        starred_repos=identity.starred_repos,
        merged_pr_count=identity.merged_pr_count,
        config=config,
    )
    if eligibility.tier is None:
        metrics.record_claim_rejected(target_chain, "ineligible")
        return {
            "error": "Not eligible for any tier",
            "github_username": identity.login,
            "requirements_met": eligibility.requirements_met,
            "status": "rejected",
        }
    metrics.record_eligibility_check(eligibility.tier.value, True)

    # 3. Validate wallet address
    if target_chain == "solana" and not validate_solana_address(target_address):
        metrics.record_claim_rejected(target_chain, "invalid_wallet")
        return {"error": "Invalid Solana address", "status": "rejected"}
    if target_chain == "base" and not validate_base_address(target_address):
        metrics.record_claim_rejected(target_chain, "invalid_wallet")
        return {"error": "Invalid Base address", "status": "rejected"}

    # 4. Fetch wallet info for anti-Sybil
    wallet_info = fetch_wallet_info(target_chain, target_address, config, stub=wallet_stub)

    # 5. Anti-Sybil checks
    sybil_result = run_anti_sybil(
        github_id=identity.id,
        github_username=identity.login,
        account_age_days=identity.account_age_days,
        chain=target_chain,
        wallet_address=target_address,
        wallet_balance=wallet_info.balance,
        wallet_age_days=wallet_info.age_days,
        rtc_wallet=rtc_wallet,
        claims_db=store,
        config=config,
    )
    if not sybil_result.passed:
        for reason in sybil_result.blocking_reasons:
            metrics.record_anti_sybil_failure(reason)
        metrics.record_claim_rejected(target_chain, "anti_sybil")
        return {
            "error": "Anti-Sybil checks failed",
            "blocking_reasons": list(sybil_result.blocking_reasons),
            "checks": {k: {"passed": v.passed, "detail": v.detail} for k, v in sybil_result.checks.items()},
            "status": "rejected",
        }

    # 6. Compute multiplier
    multiplier = compute_wallet_multiplier(target_chain, wallet_info.balance, config)
    final_wrtc = eligibility.base_claim_wrtc * multiplier

    # 7. Create claim and advance state
    record = store.create_claim(
        github_id=identity.id,
        github_username=identity.login,
        rtc_wallet=rtc_wallet,
        target_chain=target_chain,
        target_address=target_address,
        tier=eligibility.tier.value,
        base_claim_wrtc=eligibility.base_claim_wrtc,
        multiplier=multiplier,
        final_claim_wrtc=final_wrtc,
    )
    store.transition(record.claim_id, ClaimStatus.ELIGIBLE)
    store.transition(record.claim_id, ClaimStatus.ANTI_SYBIL_PASSED,
                     anti_sybil_summary={k: v.passed for k, v in sybil_result.checks.items()})

    # 8. Lock RTC via bridge (Track C)
    try:
        lock_result = bridge.lock_rtc(rtc_wallet, final_wrtc, target_chain, target_address)
        store.transition(record.claim_id, ClaimStatus.RTC_LOCKED, lock_id=lock_result.get("lock_id"))
    except Exception as exc:
        logger.error("Bridge lock failed for claim %s: %s", record.claim_id, exc)
        store.transition(record.claim_id, ClaimStatus.EXPIRED)
        metrics.record_claim_rejected(target_chain, "bridge_failed")
        return {"error": f"Bridge lock failed: {exc}", "claim_id": record.claim_id, "status": "expired"}

    # 9. For stub, auto-complete; real impl waits for mint confirmation
    if isinstance(bridge, StubBridgeClient):
        store.transition(record.claim_id, ClaimStatus.WRTC_MINTED, mint_tx_hash="stub-tx")
        store.transition(record.claim_id, ClaimStatus.COMPLETED)

    metrics.record_claim_completed(target_chain, eligibility.tier.value, final_wrtc)

    logger.info(
        "Claim %s completed: %s → %s, tier=%s, wRTC=%.2f",
        record.claim_id, identity.login, target_chain,
        eligibility.tier.value, final_wrtc,
    )

    return {
        "status": "completed" if isinstance(bridge, StubBridgeClient) else "locked",
        "claim_id": record.claim_id,
        "github_username": identity.login,
        "tier": eligibility.tier.value,
        "base_claim_wrtc": eligibility.base_claim_wrtc,
        "multiplier": multiplier,
        "final_claim_wrtc": final_wrtc,
        "target_chain": target_chain,
        "target_address": target_address,
        "lock_id": lock_result.get("lock_id"),
    }


def get_airdrop_status(
    config: Optional[AirdropConfig] = None,
    store: Optional[ClaimStore] = None,
) -> Dict[str, Any]:
    """``GET /airdrop/status`` logic."""
    if config is None:
        config = get_config()
    if store is None:
        store = get_store(config)
    return store.get_stats(config)


def get_leaderboard(
    limit: int = 10,
    config: Optional[AirdropConfig] = None,
    store: Optional[ClaimStore] = None,
) -> List[Dict[str, Any]]:
    """``GET /airdrop/leaderboard`` logic."""
    if config is None:
        config = get_config()
    if store is None:
        store = get_store(config)
    return store.get_leaderboard(limit)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(
        description="RIP-305 wRTC Airdrop Claim API",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python scripts/airdrop/claim_api.py --action status
  python scripts/airdrop/claim_api.py --action eligibility --github-username alice
  python scripts/airdrop/claim_api.py --action leaderboard --limit 5
        """,
    )
    parser.add_argument(
        "--action",
        choices=["status", "eligibility", "leaderboard"],
        required=True,
        help="API action to execute",
    )
    parser.add_argument("--github-username", help="GitHub username for eligibility check")
    parser.add_argument("--limit", type=int, default=10, help="Leaderboard limit")
    parser.add_argument("--log-level", default="INFO", help="Log level")
    args = parser.parse_args(argv)

    setup_logging(level=args.log_level)
    config = get_config()

    if args.action == "status":
        result = get_airdrop_status(config)
    elif args.action == "eligibility":
        if not args.github_username:
            print("--github-username required for eligibility check", file=sys.stderr)
            return 1
        result = check_eligibility_api(args.github_username, config=config, check_miner=False)
    elif args.action == "leaderboard":
        result = get_leaderboard(args.limit, config)
    else:
        return 1

    print(json.dumps(result, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
