#!/usr/bin/env python3
"""Migrate JSON claim store to SQLite.

Reads an existing JSON-based claim store and creates a normalized
SQLite database.  Idempotent: can be re-run safely.

Usage:
    python scripts/airdrop/migrate_json_to_sqlite.py \\
        --input claims.json --output claims.db
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from pathlib import Path


def _schema(conn: sqlite3.Connection) -> None:
    """Create the target schema."""
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
        CREATE TABLE IF NOT EXISTS anti_sybil_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            claim_id TEXT NOT NULL,
            check_name TEXT NOT NULL,
            passed INTEGER NOT NULL,
            detail TEXT,
            FOREIGN KEY (claim_id) REFERENCES claims(claim_id)
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


def _migrate(input_path: Path, output_path: Path) -> dict:
    """Run the migration and return a summary."""
    raw = json.loads(input_path.read_text())
    claims = raw.get("claims", [])

    conn = sqlite3.connect(str(output_path))
    _schema(conn)

    migrated = 0
    skipped = 0

    for claim in claims:
        claim_id = claim.get("claim_id", "")
        # Idempotent: skip if already exists
        existing = conn.execute(
            "SELECT 1 FROM claims WHERE claim_id = ?", (claim_id,)
        ).fetchone()
        if existing:
            skipped += 1
            continue

        anti_sybil = claim.get("anti_sybil_summary", {})
        conn.execute(
            """INSERT INTO claims VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )""",
            (
                claim_id,
                claim.get("github_id", 0),
                claim.get("github_username", ""),
                claim.get("rtc_wallet", ""),
                claim.get("target_chain", ""),
                claim.get("target_address", ""),
                claim.get("tier", ""),
                claim.get("base_claim_wrtc", 0),
                claim.get("multiplier", 1.0),
                claim.get("final_claim_wrtc", 0.0),
                claim.get("status", ""),
                json.dumps(anti_sybil, default=str),
                claim.get("lock_id"),
                claim.get("mint_tx_hash"),
                claim.get("created_at", ""),
                claim.get("updated_at", ""),
            ),
        )

        # Normalize anti-Sybil results into separate table
        if isinstance(anti_sybil, dict):
            for check_name, passed in anti_sybil.items():
                detail = ""
                if isinstance(passed, dict):
                    detail = passed.get("detail", "")
                    passed = passed.get("passed", False)
                conn.execute(
                    "INSERT INTO anti_sybil_results (claim_id, check_name, passed, detail) VALUES (?, ?, ?, ?)",
                    (claim_id, check_name, int(bool(passed)), detail),
                )
        migrated += 1

    conn.commit()

    # Integrity check
    db_count = conn.execute("SELECT COUNT(*) FROM claims").fetchone()[0]
    conn.close()

    # Checksum of source data
    source_hash = hashlib.sha256(json.dumps(claims, sort_keys=True, default=str).encode()).hexdigest()[:12]

    return {
        "source_file": str(input_path),
        "target_db": str(output_path),
        "source_claims": len(claims),
        "migrated": migrated,
        "skipped": skipped,
        "db_total": db_count,
        "source_hash": source_hash,
        "integrity_ok": db_count == (migrated + skipped) and db_count >= len(claims) - skipped,
    }


def main(argv: list | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Migrate JSON claim store to SQLite"
    )
    parser.add_argument("--input", required=True, help="Path to JSON claims file")
    parser.add_argument("--output", required=True, help="Path to SQLite database")
    args = parser.parse_args(argv)

    input_path = Path(args.input)
    output_path = Path(args.output)

    if not input_path.exists():
        print(f"Input file not found: {input_path}", file=sys.stderr)
        return 1

    result = _migrate(input_path, output_path)
    print(json.dumps(result, indent=2))

    if not result["integrity_ok"]:
        print("WARNING: integrity check failed!", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
