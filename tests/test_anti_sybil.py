import unittest

from scripts.airdrop.anti_sybil import (
    check_github_account_age,
    check_github_unique,
    check_no_cross_chain_double_claim,
    check_rtc_wallet_binding,
    check_wallet_age,
    check_wallet_balance,
    check_wallet_unique,
    compute_wallet_multiplier,
    run_anti_sybil,
)
from scripts.airdrop.config import AirdropConfig


class FakeClaimsDB:
    """Minimal fake claims DB for testing."""

    def __init__(self):
        self._by_github = {}
        self._by_wallet = {}

    def add(self, github_id, wallet):
        self._by_github[github_id] = True
        self._by_wallet[wallet] = True

    def get_by_github(self, github_id):
        return self._by_github.get(github_id)

    def get_by_wallet(self, address):
        return self._by_wallet.get(address)


class TestWalletBalance(unittest.TestCase):
    def test_sol_above_minimum_passes(self):
        result = check_wallet_balance("solana", 0.2)
        self.assertTrue(result.passed)

    def test_sol_below_minimum_fails(self):
        result = check_wallet_balance("solana", 0.05)
        self.assertFalse(result.passed)

    def test_eth_above_minimum_passes(self):
        result = check_wallet_balance("base", 0.02)
        self.assertTrue(result.passed)

    def test_eth_below_minimum_fails(self):
        result = check_wallet_balance("base", 0.005)
        self.assertFalse(result.passed)

    def test_unsupported_chain_fails(self):
        result = check_wallet_balance("ethereum", 100.0)
        self.assertFalse(result.passed)

    def test_custom_threshold(self):
        cfg = AirdropConfig(min_sol_balance=0.5)
        result = check_wallet_balance("solana", 0.3, config=cfg)
        self.assertFalse(result.passed)


class TestWalletAge(unittest.TestCase):
    def test_above_threshold_passes(self):
        result = check_wallet_age(10)
        self.assertTrue(result.passed)

    def test_below_threshold_fails(self):
        result = check_wallet_age(3)
        self.assertFalse(result.passed)

    def test_exact_threshold_passes(self):
        result = check_wallet_age(7)
        self.assertTrue(result.passed)


class TestGitHubAccountAge(unittest.TestCase):
    def test_above_threshold_passes(self):
        result = check_github_account_age(60)
        self.assertTrue(result.passed)

    def test_below_threshold_fails(self):
        result = check_github_account_age(15)
        self.assertFalse(result.passed)


class TestGitHubUnique(unittest.TestCase):
    def test_no_prior_claim_passes(self):
        db = FakeClaimsDB()
        result = check_github_unique(12345, db)
        self.assertTrue(result.passed)

    def test_existing_claim_fails(self):
        db = FakeClaimsDB()
        db.add(12345, "wallet1")
        result = check_github_unique(12345, db)
        self.assertFalse(result.passed)


class TestWalletUnique(unittest.TestCase):
    def test_no_prior_claim_passes(self):
        db = FakeClaimsDB()
        result = check_wallet_unique("0x1234567890abcdef1234567890abcdef12345678", db)
        self.assertTrue(result.passed)

    def test_existing_claim_fails(self):
        db = FakeClaimsDB()
        db.add(999, "0xABC")
        result = check_wallet_unique("0xABC", db)
        self.assertFalse(result.passed)


class TestCrossChainDoubleClaim(unittest.TestCase):
    def test_no_prior_claim_passes(self):
        db = FakeClaimsDB()
        result = check_no_cross_chain_double_claim(12345, db)
        self.assertTrue(result.passed)

    def test_existing_cross_chain_claim_fails(self):
        db = FakeClaimsDB()
        db.add(12345, "wallet")
        result = check_no_cross_chain_double_claim(12345, db)
        self.assertFalse(result.passed)


class TestRTCWalletBinding(unittest.TestCase):
    def test_with_wallet_passes(self):
        result = check_rtc_wallet_binding("alice-miner", "alice")
        self.assertTrue(result.passed)

    def test_empty_wallet_fails(self):
        result = check_rtc_wallet_binding("", "alice")
        self.assertFalse(result.passed)

    def test_whitespace_wallet_fails(self):
        result = check_rtc_wallet_binding("  ", "alice")
        self.assertFalse(result.passed)


class TestWalletMultiplier(unittest.TestCase):
    def test_solana_1x(self):
        self.assertEqual(compute_wallet_multiplier("solana", 0.5), 1.0)

    def test_solana_1_5x(self):
        self.assertEqual(compute_wallet_multiplier("solana", 5.0), 1.5)

    def test_solana_2x(self):
        self.assertEqual(compute_wallet_multiplier("solana", 15.0), 2.0)

    def test_base_1x(self):
        self.assertEqual(compute_wallet_multiplier("base", 0.05), 1.0)

    def test_base_1_5x(self):
        self.assertEqual(compute_wallet_multiplier("base", 0.5), 1.5)

    def test_base_2x(self):
        self.assertEqual(compute_wallet_multiplier("base", 2.0), 2.0)

    def test_unknown_chain_returns_1x(self):
        self.assertEqual(compute_wallet_multiplier("polygon", 100.0), 1.0)


class TestRunAntiSybil(unittest.TestCase):
    def test_all_checks_pass(self):
        result = run_anti_sybil(
            github_id=12345,
            github_username="alice",
            account_age_days=365,
            chain="solana",
            wallet_address="valid_address",
            wallet_balance=1.0,
            wallet_age_days=30,
            rtc_wallet="alice-miner",
            claims_db=None,
        )
        self.assertTrue(result.passed)
        self.assertEqual(len(result.blocking_reasons), 0)

    def test_multiple_failures_reported(self):
        result = run_anti_sybil(
            github_id=12345,
            github_username="bot",
            account_age_days=5,
            chain="solana",
            wallet_address="addr",
            wallet_balance=0.01,
            wallet_age_days=2,
            rtc_wallet="",
        )
        self.assertFalse(result.passed)
        self.assertIn("wallet_balance", result.blocking_reasons)
        self.assertIn("wallet_age", result.blocking_reasons)
        self.assertIn("github_account_age", result.blocking_reasons)
        self.assertIn("rtc_wallet_binding", result.blocking_reasons)


if __name__ == "__main__":
    unittest.main()
