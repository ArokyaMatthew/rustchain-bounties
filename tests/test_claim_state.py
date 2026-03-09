import json
import os
import tempfile
import unittest

from scripts.airdrop.claim_state import (
    ClaimRecord,
    ClaimStatus,
    ClaimStore,
    SQLiteClaimStore,
)
from scripts.airdrop.config import AirdropConfig


class TestClaimStore(unittest.TestCase):
    """Tests for the JSON-file-backed ClaimStore."""

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        )
        self.tmp.write('{"claims": []}')
        self.tmp.close()
        self.store = ClaimStore(self.tmp.name)

    def tearDown(self):
        os.unlink(self.tmp.name)

    def _create_test_claim(self, **kwargs):
        defaults = dict(
            github_id=12345,
            github_username="alice",
            rtc_wallet="alice-miner",
            target_chain="solana",
            target_address="SoLaNaAdDrEsS",
            tier="CONTRIBUTOR",
            base_claim_wrtc=50,
            multiplier=1.5,
            final_claim_wrtc=75.0,
        )
        defaults.update(kwargs)
        return self.store.create_claim(**defaults)

    def test_create_claim(self):
        rec = self._create_test_claim()
        self.assertEqual(rec.github_username, "alice")
        self.assertEqual(rec.status, ClaimStatus.PENDING.value)
        self.assertIsNotNone(rec.claim_id)

    def test_get_claim_by_id(self):
        rec = self._create_test_claim()
        found = self.store.get_claim(rec.claim_id)
        self.assertIsNotNone(found)
        self.assertEqual(found.github_username, "alice")

    def test_get_by_github(self):
        self._create_test_claim()
        found = self.store.get_by_github(12345)
        self.assertIsNotNone(found)

    def test_get_by_wallet(self):
        self._create_test_claim()
        found = self.store.get_by_wallet("SoLaNaAdDrEsS")
        self.assertIsNotNone(found)

    def test_claim_lifecycle_happy_path(self):
        rec = self._create_test_claim()
        cid = rec.claim_id

        self.store.transition(cid, ClaimStatus.ELIGIBLE)
        self.assertEqual(self.store.get_claim(cid).status, "ELIGIBLE")

        self.store.transition(cid, ClaimStatus.ANTI_SYBIL_PASSED)
        self.assertEqual(self.store.get_claim(cid).status, "ANTI_SYBIL_PASSED")

        self.store.transition(cid, ClaimStatus.RTC_LOCKED, lock_id="lock-123")
        rec = self.store.get_claim(cid)
        self.assertEqual(rec.status, "RTC_LOCKED")
        self.assertEqual(rec.lock_id, "lock-123")

        self.store.transition(cid, ClaimStatus.WRTC_MINTED, mint_tx_hash="tx-abc")
        rec = self.store.get_claim(cid)
        self.assertEqual(rec.status, "WRTC_MINTED")
        self.assertEqual(rec.mint_tx_hash, "tx-abc")

        self.store.transition(cid, ClaimStatus.COMPLETED)
        self.assertEqual(self.store.get_claim(cid).status, "COMPLETED")

    def test_invalid_transition_raises(self):
        rec = self._create_test_claim()
        with self.assertRaises(ValueError):
            self.store.transition(rec.claim_id, ClaimStatus.COMPLETED)

    def test_rejection_from_pending(self):
        rec = self._create_test_claim()
        self.store.transition(rec.claim_id, ClaimStatus.REJECTED)
        self.assertEqual(self.store.get_claim(rec.claim_id).status, "REJECTED")

    def test_terminal_states_block_further_transitions(self):
        rec = self._create_test_claim()
        self.store.transition(rec.claim_id, ClaimStatus.REJECTED)
        with self.assertRaises(ValueError):
            self.store.transition(rec.claim_id, ClaimStatus.ELIGIBLE)

    def test_persistence_roundtrip(self):
        rec = self._create_test_claim()
        # Reload from disk
        store2 = ClaimStore(self.tmp.name)
        found = store2.get_claim(rec.claim_id)
        self.assertIsNotNone(found)
        self.assertEqual(found.github_username, "alice")

    def test_get_stats(self):
        rec = self._create_test_claim()
        cid = rec.claim_id
        self.store.transition(cid, ClaimStatus.ELIGIBLE)
        self.store.transition(cid, ClaimStatus.ANTI_SYBIL_PASSED)
        self.store.transition(cid, ClaimStatus.RTC_LOCKED)
        self.store.transition(cid, ClaimStatus.WRTC_MINTED)
        self.store.transition(cid, ClaimStatus.COMPLETED)

        stats = self.store.get_stats()
        self.assertEqual(stats["total_claims"], 1)
        self.assertGreater(stats["total_distributed"], 0)
        self.assertEqual(stats["claims_by_chain"]["solana"], 1)

    def test_leaderboard(self):
        rec = self._create_test_claim()
        cid = rec.claim_id
        self.store.transition(cid, ClaimStatus.ELIGIBLE)
        self.store.transition(cid, ClaimStatus.ANTI_SYBIL_PASSED)
        self.store.transition(cid, ClaimStatus.RTC_LOCKED)
        self.store.transition(cid, ClaimStatus.WRTC_MINTED)
        self.store.transition(cid, ClaimStatus.COMPLETED)

        board = self.store.get_leaderboard(5)
        self.assertEqual(len(board), 1)
        self.assertEqual(board[0]["github_username"], "alice")
        self.assertEqual(board[0]["wrtc_claimed"], 75.0)

    def test_get_by_github_excludes_rejected(self):
        rec = self._create_test_claim()
        self.store.transition(rec.claim_id, ClaimStatus.REJECTED)
        # Rejected claim should not block a new attempt
        found = self.store.get_by_github(12345)
        self.assertIsNone(found)


class TestSQLiteClaimStore(unittest.TestCase):
    """Smoke tests for the SQLite backend."""

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(
            suffix=".db", delete=False
        )
        self.tmp.close()
        self.store = SQLiteClaimStore(self.tmp.name)

    def tearDown(self):
        os.unlink(self.tmp.name)

    def test_create_and_retrieve(self):
        rec = self.store.create_claim(
            github_id=99,
            github_username="sqlite_user",
            rtc_wallet="wallet",
            target_chain="base",
            target_address="0x" + "ab" * 20,
            tier="BUILDER",
            base_claim_wrtc=100,
            multiplier=2.0,
            final_claim_wrtc=200.0,
        )
        found = self.store.get_claim(rec.claim_id)
        self.assertIsNotNone(found)
        self.assertEqual(found.github_username, "sqlite_user")

    def test_transition_records_history(self):
        import sqlite3

        rec = self.store.create_claim(
            github_id=99,
            github_username="sqlite_user",
            rtc_wallet="wallet",
            target_chain="base",
            target_address="0x" + "ab" * 20,
            tier="BUILDER",
            base_claim_wrtc=100,
            multiplier=2.0,
            final_claim_wrtc=200.0,
        )
        self.store.transition(rec.claim_id, ClaimStatus.ELIGIBLE)

        conn = sqlite3.connect(self.tmp.name)
        rows = conn.execute(
            "SELECT from_status, to_status FROM state_transitions WHERE claim_id = ?",
            (rec.claim_id,),
        ).fetchall()
        conn.close()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][0], "PENDING")
        self.assertEqual(rows[0][1], "ELIGIBLE")


class TestClaimRecord(unittest.TestCase):
    def test_to_dict_and_from_dict_roundtrip(self):
        rec = ClaimRecord(
            claim_id="test-id",
            github_id=123,
            github_username="test",
            rtc_wallet="wallet",
            target_chain="solana",
            target_address="addr",
            tier="STARGAZER",
            base_claim_wrtc=25,
            multiplier=1.0,
            final_claim_wrtc=25.0,
            status="PENDING",
            created_at="2026-03-09T00:00:00Z",
            updated_at="2026-03-09T00:00:00Z",
        )
        d = rec.to_dict()
        rec2 = ClaimRecord.from_dict(d)
        self.assertEqual(rec.claim_id, rec2.claim_id)
        self.assertEqual(rec.tier, rec2.tier)


if __name__ == "__main__":
    unittest.main()
