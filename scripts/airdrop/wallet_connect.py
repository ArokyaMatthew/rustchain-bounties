#!/usr/bin/env python3
"""Wallet address validation and balance/age verification for Solana and Base.

Provides address format validation, RPC-based balance fetching, and
documents Phantom (Solana) / MetaMask (Base) wallet-connect expectations.

Wallet-Connect Expectations
----------------------------
**Solana (Phantom)**:
    - User connects via Phantom browser extension or mobile deep-link.
    - Frontend receives the base58-encoded public key.
    - Backend validates address format via :func:`validate_solana_address`.

**Base (MetaMask)**:
    - User connects via MetaMask with Base L2 network selected
      (chainId ``0x2105`` for mainnet, ``0x14a34`` for Sepolia).
    - Frontend receives the EIP-55 checksummed hex address.
    - Backend validates address format via :func:`validate_base_address`.
"""
from __future__ import annotations

import json
import logging
import re
import urllib.request
import urllib.error
from dataclasses import dataclass
from typing import Optional

from scripts.airdrop.config import AirdropConfig, get_config

logger = logging.getLogger("airdrop.wallet")

# Base58 alphabet (Bitcoin variant)
_B58_ALPHABET = b"123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
_B58_SET = set(_B58_ALPHABET.decode())

# EVM hex address pattern
_EVM_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class WalletInfo:
    """Wallet metadata used for anti-Sybil and multiplier checks."""

    chain: str          # "solana" | "base"
    address: str
    balance: float      # SOL or ETH (not lamports/wei)
    age_days: int       # days since first transaction (0 = unknown)
    is_valid: bool


# ---------------------------------------------------------------------------
# Address validation
# ---------------------------------------------------------------------------

def validate_solana_address(address: str) -> bool:
    """Return *True* if *address* is a plausible Solana public key.

    Checks:
        - Non-empty string
        - All characters in Base58 alphabet
        - Decoded length == 32 bytes
    """
    if not address or not isinstance(address, str):
        return False
    if not all(ch in _B58_SET for ch in address):
        return False
    # Decode base58 to check byte length
    try:
        value = 0
        for ch in address:
            value = value * 58 + _B58_ALPHABET.index(ch.encode())
        byte_length = (value.bit_length() + 7) // 8
        # Solana pubkeys are exactly 32 bytes
        return byte_length == 32
    except Exception:
        return False


def validate_base_address(address: str) -> bool:
    """Return *True* if *address* is a valid EVM (Base L2) address.

    Checks:
        - Matches ``0x`` + 40 hex characters
    """
    if not address or not isinstance(address, str):
        return False
    return bool(_EVM_RE.match(address))


# ---------------------------------------------------------------------------
# Balance fetching (RPC)
# ---------------------------------------------------------------------------

def _solana_get_balance(address: str, rpc_url: str) -> float:
    """Fetch SOL balance via Solana JSON-RPC ``getBalance``."""
    payload = json.dumps({
        "jsonrpc": "2.0",
        "id": 1,
        "method": "getBalance",
        "params": [address],
    }).encode()
    req = urllib.request.Request(
        rpc_url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read().decode())
    lamports = data.get("result", {}).get("value", 0)
    return lamports / 1e9  # lamports → SOL


def _base_get_balance(address: str, rpc_url: str) -> float:
    """Fetch ETH balance via Base L2 JSON-RPC ``eth_getBalance``."""
    payload = json.dumps({
        "jsonrpc": "2.0",
        "id": 1,
        "method": "eth_getBalance",
        "params": [address, "latest"],
    }).encode()
    req = urllib.request.Request(
        rpc_url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read().decode())
    wei_hex = data.get("result", "0x0")
    return int(wei_hex, 16) / 1e18  # wei → ETH


def fetch_wallet_info(
    chain: str,
    address: str,
    config: Optional[AirdropConfig] = None,
    *,
    stub: bool = False,
) -> WalletInfo:
    """Fetch wallet balance and age for anti-Sybil validation.

    Parameters
    ----------
    chain:
        ``"solana"`` or ``"base"``.
    address:
        Wallet address string.
    config:
        Optional config; uses defaults if *None*.
    stub:
        If *True*, return a synthetic result without calling RPC.
        Useful for testing.

    Returns
    -------
    WalletInfo
    """
    if config is None:
        config = get_config()

    if chain == "solana":
        is_valid = validate_solana_address(address)
    elif chain == "base":
        is_valid = validate_base_address(address)
    else:
        return WalletInfo(chain=chain, address=address, balance=0.0, age_days=0, is_valid=False)

    if not is_valid:
        return WalletInfo(chain=chain, address=address, balance=0.0, age_days=0, is_valid=False)

    if stub:
        return WalletInfo(chain=chain, address=address, balance=1.0, age_days=30, is_valid=True)

    balance = 0.0
    try:
        if chain == "solana":
            balance = _solana_get_balance(address, config.solana_rpc_url)
        else:
            balance = _base_get_balance(address, config.base_rpc_url)
    except Exception as exc:
        logger.warning("Failed to fetch balance for %s on %s: %s", address, chain, exc)

    # Wallet age requires indexer/explorer API — default to 0 (unknown)
    # until a dedicated age-checking service is integrated.
    age_days = 0

    logger.info("Wallet %s on %s: balance=%.6f, age=%dd", address, chain, balance, age_days)
    return WalletInfo(
        chain=chain,
        address=address,
        balance=balance,
        age_days=age_days,
        is_valid=True,
    )
