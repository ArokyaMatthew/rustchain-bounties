import os
import unittest

from scripts.airdrop.config import AirdropConfig, get_config


class TestAirdropConfig(unittest.TestCase):
    """Tests for environment-driven configuration."""

    def test_defaults_are_sane(self):
        cfg = AirdropConfig()
        self.assertEqual(cfg.solana_allocation, 30_000)
        self.assertEqual(cfg.base_allocation, 20_000)
        self.assertEqual(cfg.min_sol_balance, 0.1)
        self.assertEqual(cfg.min_eth_balance, 0.01)
        self.assertEqual(cfg.min_wallet_age_days, 7)
        self.assertEqual(cfg.min_github_age_days, 30)
        self.assertEqual(cfg.wrtc_solana_mint, "12TAdKXxcGf6oCv4rqDz2NkgxjyHq6HQKoxKZYGf5i4X")
        self.assertEqual(cfg.log_level, "INFO")
        self.assertEqual(cfg.metrics_port, 9121)

    def test_tier_claims_match_rip305(self):
        cfg = AirdropConfig()
        self.assertEqual(cfg.tier_claims["STARGAZER"], 25)
        self.assertEqual(cfg.tier_claims["CONTRIBUTOR"], 50)
        self.assertEqual(cfg.tier_claims["BUILDER"], 100)
        self.assertEqual(cfg.tier_claims["SECURITY"], 150)
        self.assertEqual(cfg.tier_claims["CORE"], 200)
        self.assertEqual(cfg.tier_claims["MINER"], 100)

    def test_env_override_string(self):
        os.environ["AIRDROP_RUSTCHAIN_NODE_URL"] = "https://testnet.example.com"
        try:
            cfg = get_config()
            self.assertEqual(cfg.rustchain_node_url, "https://testnet.example.com")
        finally:
            del os.environ["AIRDROP_RUSTCHAIN_NODE_URL"]

    def test_env_override_int(self):
        os.environ["AIRDROP_MIN_WALLET_AGE_DAYS"] = "14"
        try:
            cfg = get_config()
            self.assertEqual(cfg.min_wallet_age_days, 14)
        finally:
            del os.environ["AIRDROP_MIN_WALLET_AGE_DAYS"]

    def test_env_override_float(self):
        os.environ["AIRDROP_MIN_SOL_BALANCE"] = "0.5"
        try:
            cfg = get_config()
            self.assertEqual(cfg.min_sol_balance, 0.5)
        finally:
            del os.environ["AIRDROP_MIN_SOL_BALANCE"]

    def test_env_override_bool_true(self):
        os.environ["AIRDROP_USE_SQLITE"] = "true"
        try:
            cfg = get_config()
            self.assertTrue(cfg.use_sqlite)
        finally:
            del os.environ["AIRDROP_USE_SQLITE"]

    def test_env_override_bool_false(self):
        os.environ["AIRDROP_USE_SQLITE"] = "false"
        try:
            cfg = get_config()
            self.assertFalse(cfg.use_sqlite)
        finally:
            del os.environ["AIRDROP_USE_SQLITE"]

    def test_multiplier_tiers_defaults(self):
        cfg = AirdropConfig()
        self.assertIn("solana", cfg.multiplier_tiers)
        self.assertIn("base", cfg.multiplier_tiers)
        # Solana: 0.1→1.0, 1.0→1.5, 10.0→2.0
        solana = cfg.multiplier_tiers["solana"]
        self.assertEqual(len(solana), 3)


if __name__ == "__main__":
    unittest.main()
