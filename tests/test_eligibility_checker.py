import unittest

from scripts.airdrop.config import AirdropConfig
from scripts.airdrop.eligibility_checker import (
    EligibilityTier,
    check_eligibility,
)


class TestEligibilityChecker(unittest.TestCase):
    """Tests for the 6-tier eligibility engine."""

    def setUp(self):
        self.config = AirdropConfig()

    def test_stargazer_tier_with_10_stars(self):
        result = check_eligibility(
            github_username="alice",
            starred_repos=tuple(f"repo-{i}" for i in range(10)),
            merged_pr_count=0,
            config=self.config,
            check_miner=False,
        )
        self.assertIsNotNone(result.tier)
        self.assertEqual(result.tier, EligibilityTier.STARGAZER)
        self.assertEqual(result.base_claim_wrtc, 25)

    def test_contributor_tier_with_1_merged_pr(self):
        result = check_eligibility(
            github_username="bob",
            starred_repos=(),
            merged_pr_count=1,
            config=self.config,
            check_miner=False,
        )
        self.assertEqual(result.tier, EligibilityTier.CONTRIBUTOR)
        self.assertEqual(result.base_claim_wrtc, 50)

    def test_builder_tier_with_3_merged_prs(self):
        result = check_eligibility(
            github_username="carol",
            starred_repos=(),
            merged_pr_count=3,
            config=self.config,
            check_miner=False,
        )
        self.assertEqual(result.tier, EligibilityTier.BUILDER)
        self.assertEqual(result.base_claim_wrtc, 100)

    def test_core_tier_with_5_merged_prs(self):
        result = check_eligibility(
            github_username="dave",
            starred_repos=(),
            merged_pr_count=5,
            config=self.config,
            check_miner=False,
        )
        self.assertEqual(result.tier, EligibilityTier.CORE)
        self.assertEqual(result.base_claim_wrtc, 200)

    def test_core_tier_with_star_king_badge(self):
        result = check_eligibility(
            github_username="eve",
            starred_repos=(),
            merged_pr_count=0,
            config=self.config,
            check_miner=False,
            has_star_king_badge=True,
        )
        self.assertEqual(result.tier, EligibilityTier.CORE)
        self.assertEqual(result.base_claim_wrtc, 200)

    def test_security_tier_with_finding(self):
        result = check_eligibility(
            github_username="frank",
            starred_repos=(),
            merged_pr_count=0,
            config=self.config,
            check_miner=False,
            has_security_finding=True,
        )
        self.assertEqual(result.tier, EligibilityTier.SECURITY)
        self.assertEqual(result.base_claim_wrtc, 150)

    def test_highest_tier_wins(self):
        """User qualifying for multiple tiers gets the best one."""
        result = check_eligibility(
            github_username="multi",
            starred_repos=tuple(f"repo-{i}" for i in range(15)),
            merged_pr_count=5,
            config=self.config,
            check_miner=False,
        )
        # CORE (200) has higher priority than STARGAZER (25)
        self.assertEqual(result.tier, EligibilityTier.CORE)
        self.assertEqual(result.base_claim_wrtc, 200)

    def test_ineligible_user_returns_none(self):
        result = check_eligibility(
            github_username="nobody",
            starred_repos=(),
            merged_pr_count=0,
            config=self.config,
            check_miner=False,
        )
        self.assertIsNone(result.tier)
        self.assertEqual(result.base_claim_wrtc, 0)
        self.assertEqual(result.final_claim_wrtc, 0)

    def test_requirements_met_dict_populated(self):
        result = check_eligibility(
            github_username="test",
            starred_repos=("repo1",),
            merged_pr_count=2,
            config=self.config,
            check_miner=False,
        )
        self.assertIn("stars_gte_10", result.requirements_met)
        self.assertIn("merged_prs_gte_1", result.requirements_met)
        self.assertIn("merged_prs_gte_3", result.requirements_met)
        self.assertIn("merged_prs_gte_5", result.requirements_met)
        self.assertFalse(result.requirements_met["stars_gte_10"])
        self.assertTrue(result.requirements_met["merged_prs_gte_1"])

    def test_tier_priority_ordering(self):
        """Verify priority ordering matches RIP-305 claim values."""
        self.assertGreater(
            EligibilityTier.CORE.priority,
            EligibilityTier.SECURITY.priority,
        )
        self.assertGreater(
            EligibilityTier.SECURITY.priority,
            EligibilityTier.BUILDER.priority,
        )
        self.assertGreater(
            EligibilityTier.BUILDER.priority,
            EligibilityTier.CONTRIBUTOR.priority,
        )
        self.assertGreater(
            EligibilityTier.CONTRIBUTOR.priority,
            EligibilityTier.STARGAZER.priority,
        )


if __name__ == "__main__":
    unittest.main()
