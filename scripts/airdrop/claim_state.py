#!/usr/bin/env python3
"""Claim state machine and persistence for the RIP-305 wRTC airdrop.

Manages the lifecycle of a claim from initiation to completion with
idempotent state transitions and JSON-file or SQLite persistence.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

from scripts.airdrop.config import AirdropConfig, get_config

logger = logging.getLogger("airdrop.state")


# ---------------------------------------------------------------------------
# Claim status enum
# ---------------------------------------------------------------------------

class ClaimStatus(str, Enum):
    PENDING = "PENDING"
    ELIGIBLE = "ELIGIBLE"
    ANTI_SYBIL_PASSED = "ANTI_SYBIL_PASSED"
    RTC_LOCKED = "RTC_LOCKED"
    WRTC_MINTED = "WRTC_MINTED"
    COMPLETED = "COMPLETED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"


# Valid state transitions
_TRANSITIONS: Dict[ClaimStatus, tuple] = {
    ClaimStatus.PENDING: (ClaimStatus.ELIGIBLE, ClaimStatus.REJECTED),
    ClaimStatus.ELIGIBLE: (ClaimStatus.ANTI_SYBIL_PASSED, ClaimStatus.REJECTED),
    ClaimStatus.ANTI_SYBIL_PASSED: (ClaimStatus.RTC_LOCKED, ClaimStatus.REJECTED, ClaimStatus.EXPIRED),
    ClaimStatus.RTC_LOCKED: (ClaimStatus.WRTC_MINTED, ClaimStatus.EXPIRED),
    ClaimStatus.WRTC_MINTED: (ClaimStatus.COMPLETED,),
    ClaimStatus.COMPLETED: (),
    ClaimStatus.REJECTED: (),
    ClaimStatus.EXPIRED: (),
}


# ---------------------------------------------------------------------------
# Claim record
# ---------------------------------------------------------------------------

@dataclass
class ClaimRecord:
    """Full state of a single airdrop claim."""

    claim_id: str
    github_id: int
    github_username: str
    rtc_wallet: str
    target_chain: str        # "solana" | "base"
    target_address: str
    tier: str                # EligibilityTier value
    base_claim_wrtc: int
    multiplier: float
    final_claim_wrtc: float
    status: str              # ClaimStatus value
    anti_sybil_summary: Dict[str, Any] = field(default_factory=dict)
    lock_id: Optional[str] = None
    mint_tx_hash: Optional[str] = None
    created_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> ClaimRecord:
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


# ---------------------------------------------------------------------------
# Abstract store interface
# ---------------------------------------------------------------------------

class ClaimStore:
    """JSON-file-backed claim store.

    Thread safety: not guaranteed — suitable for single-process use.
    For production, switch to :class:`SQLiteClaimStore` via ``config.use_sqlite``.
    """

    def __init__(self, path: str = "claims.json") -> None:
        self._path = Path(path)
        self._claims: Dict[str, ClaimRecord] = {}
        self._load()

    def _load(self) -> None:
        if self._path.exists():
            raw = json.loads(self._path.read_text())
            for item in raw.get("claims", []):
                rec = ClaimRecord.from_dict(item)
                self._claims[rec.claim_id] = rec
            logger.info("Loaded %d claims from %s", len(self._claims), self._path)

    def _save(self) -> None:
        payload = {"claims": [rec.to_dict() for rec in self._claims.values()]}
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(payload, indent=2, default=str) + "\n")

    def create_claim(
        self,
        github_id: int,
        github_username: str,
        rtc_wallet: str,
        target_chain: str,
        target_address: str,
        tier: str,
        base_claim_wrtc: int,
        multiplier: float,
        final_claim_wrtc: float,
    ) -> ClaimRecord:
        now = datetime.now(timezone.utc).isoformat()
        record = ClaimRecord(
            claim_id=str(uuid.uuid4()),
            github_id=github_id,
            github_username=github_username,
            rtc_wallet=rtc_wallet,
            target_chain=target_chain,
            target_address=target_address,
            tier=tier,
            base_claim_wrtc=base_claim_wrtc,
            multiplier=multiplier,
            final_claim_wrtc=final_claim_wrtc,
            status=ClaimStatus.PENDING.value,
            created_at=now,
            updated_at=now,
        )
        self._claims[record.claim_id] = record
        self._save()
        logger.info("Created claim %s for %s", record.claim_id, github_username)
        return record

    def get_claim(self, claim_id: str) -> Optional[ClaimRecord]:
        return self._claims.get(claim_id)

    def get_by_github(self, github_id: int) -> Optional[ClaimRecord]:
        for rec in self._claims.values():
            if rec.github_id == github_id and rec.status != ClaimStatus.REJECTED.value:
                return rec
        return None

    def get_by_wallet(self, address: str) -> Optional[ClaimRecord]:
        for rec in self._claims.values():
            if rec.target_address == address and rec.status != ClaimStatus.REJECTED.value:
                return rec
        return None

    def transition(self, claim_id: str, new_status: ClaimStatus, **kwargs: Any) -> ClaimRecord:
        """Advance *claim_id* to *new_status*.  Raises on invalid transition."""
        rec = self._claims.get(claim_id)
        if rec is None:
            raise KeyError(f"claim {claim_id} not found")

        current = ClaimStatus(rec.status)
        allowed = _TRANSITIONS.get(current, ())
        if new_status not in allowed:
            raise ValueError(
                f"invalid transition {current.value} → {new_status.value} "
                f"(allowed: {[s.value for s in allowed]})"
            )

        rec.status = new_status.value
        rec.updated_at = datetime.now(timezone.utc).isoformat()
        for key, value in kwargs.items():
            if hasattr(rec, key):
                setattr(rec, key, value)
        self._save()
        logger.info("Claim %s: %s → %s", claim_id, current.value, new_status.value)
        return rec

    def get_stats(self, config: Optional[AirdropConfig] = None) -> Dict[str, Any]:
        """Return aggregate airdrop statistics."""
        if config is None:
            config = get_config()
        total_alloc = config.solana_allocation + config.base_allocation
        distributed = 0.0
        by_chain: Dict[str, int] = {"solana": 0, "base": 0}
        for rec in self._claims.values():
            if rec.status == ClaimStatus.COMPLETED.value:
                distributed += rec.final_claim_wrtc
                by_chain[rec.target_chain] = by_chain.get(rec.target_chain, 0) + 1
        return {
            "total_allocation": total_alloc,
            "total_distributed": distributed,
            "remaining": total_alloc - distributed,
            "claims_by_chain": by_chain,
            "total_claims": len(self._claims),
        }

    def get_leaderboard(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Return top claimants by final_claim_wrtc (completed only)."""
        completed = [
            rec for rec in self._claims.values()
            if rec.status == ClaimStatus.COMPLETED.value
        ]
        completed.sort(key=lambda r: -r.final_claim_wrtc)
        return [
            {
                "github_username": r.github_username,
                "tier": r.tier,
                "chain": r.target_chain,
                "wrtc_claimed": r.final_claim_wrtc,
            }
            for r in completed[:limit]
        ]

    def all_claims(self) -> List[ClaimRecord]:
        return list(self._claims.values())


# ---------------------------------------------------------------------------
# SQLite backend
# ---------------------------------------------------------------------------

class SQLiteClaimStore(ClaimStore):
    """SQLite-backed claim store for production use."""

    def __init__(self, db_path: str = "claims.db") -> None:
        self._db_path = db_path
        self._claims: Dict[str, ClaimRecord] = {}
        self._init_db()
        self._load_from_db()

    def _init_db(self) -> None:
        conn = sqlite3.connect(self._db_path)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS claims (
                claim_id TEXT PRIMARY KEY,
                github_id INTEGER NOT NULL,
                github_username TEXT NOT NULL,
                rtc_wallet TEXT NOT NULL,
                target_chain TEXT NOT NULL,
                target_address TEXT NOT NULL,
                tier TEXT NOT NULL,
                base_claim_wrtc INTEGER NOT NULL,
                multiplier REAL NOT NULL,
                final_claim_wrtc REAL NOT NULL,
                status TEXT NOT NULL,
                anti_sybil_summary TEXT DEFAULT '{}',
                lock_id TEXT,
                mint_tx_hash TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS state_transitions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                claim_id TEXT NOT NULL,
                from_status TEXT NOT NULL,
                to_status TEXT NOT NULL,
                transitioned_at TEXT NOT NULL,
                FOREIGN KEY (claim_id) REFERENCES claims(claim_id)
            )
        """)
        conn.commit()
        conn.close()

    def _load_from_db(self) -> None:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM claims").fetchall()
        for row in rows:
            data = dict(row)
            data["anti_sybil_summary"] = json.loads(data.get("anti_sybil_summary", "{}"))
            rec = ClaimRecord.from_dict(data)
            self._claims[rec.claim_id] = rec
        conn.close()

    def _save(self) -> None:
        conn = sqlite3.connect(self._db_path)
        for rec in self._claims.values():
            conn.execute(
                """INSERT OR REPLACE INTO claims VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )""",
                (
                    rec.claim_id, rec.github_id, rec.github_username,
                    rec.rtc_wallet, rec.target_chain, rec.target_address,
                    rec.tier, rec.base_claim_wrtc, rec.multiplier,
                    rec.final_claim_wrtc, rec.status,
                    json.dumps(rec.anti_sybil_summary, default=str),
                    rec.lock_id, rec.mint_tx_hash,
                    rec.created_at, rec.updated_at,
                ),
            )
        conn.commit()
        conn.close()

    def transition(self, claim_id: str, new_status: ClaimStatus, **kwargs: Any) -> ClaimRecord:
        rec = self._claims.get(claim_id)
        if rec is None:
            raise KeyError(f"claim {claim_id} not found")
        old_status = rec.status
        result = super().transition(claim_id, new_status, **kwargs)

        # Record transition history
        conn = sqlite3.connect(self._db_path)
        conn.execute(
            "INSERT INTO state_transitions (claim_id, from_status, to_status, transitioned_at) VALUES (?, ?, ?, ?)",
            (claim_id, old_status, new_status.value, datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
        conn.close()
        return result

    # Path property not used in SQLite mode
    @property
    def _path(self):
        return Path(self._db_path)

    @_path.setter
    def _path(self, value):
        pass


def get_store(config: Optional[AirdropConfig] = None) -> ClaimStore:
    """Factory: return the appropriate store based on config."""
    if config is None:
        config = get_config()
    if config.use_sqlite:
        return SQLiteClaimStore(config.sqlite_path)
    return ClaimStore(config.claims_db_path)
