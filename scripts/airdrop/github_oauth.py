#!/usr/bin/env python3
"""GitHub OAuth identity verification for the RIP-305 airdrop.

Verifies contributor identity through GitHub OAuth and collects
the metadata needed for eligibility tier calculation.
"""
from __future__ import annotations

import json
import logging
import re
import urllib.request
import urllib.error
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from scripts.airdrop.config import AirdropConfig, get_config

logger = logging.getLogger("airdrop.oauth")

_API = "https://api.github.com"


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class GitHubIdentity:
    """Verified GitHub identity with contribution metadata."""

    id: int
    login: str
    account_age_days: int
    avatar_url: str
    starred_repos: Tuple[str, ...] = ()
    merged_pr_count: int = 0
    oauth_token: str = ""


# ---------------------------------------------------------------------------
# HTTP helpers (reuses pattern from auto_triage_claims.py)
# ---------------------------------------------------------------------------

def _gh_request(
    path: str,
    token: str,
    method: str = "GET",
) -> Tuple[Any, Dict[str, str]]:
    """Make an authenticated GitHub API request.  Returns (data, headers)."""
    url = f"{_API}{path}" if path.startswith("/") else path
    req = urllib.request.Request(url, method=method)
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    if token:
        req.add_header("Authorization", f"Bearer {token}")

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            headers = {k.lower(): v for k, v in resp.getheaders()}
            data = json.loads(resp.read().decode())
            return data, headers
    except urllib.error.HTTPError as exc:
        logger.error("GitHub API %s %s → %s", method, url, exc.code)
        raise


def _gh_paginated(path: str, token: str, max_pages: int = 10) -> List[Any]:
    """Fetch all pages from a paginated GitHub endpoint."""
    results: List[Any] = []
    url = f"{_API}{path}" if path.startswith("/") else path
    for _ in range(max_pages):
        data, headers = _gh_request(url, token)
        if isinstance(data, list):
            results.extend(data)
        else:
            results.append(data)
        link = headers.get("link", "")
        match = re.search(r'<([^>]+)>;\s*rel="next"', link)
        if not match:
            break
        url = match.group(1)
    return results


# ---------------------------------------------------------------------------
# Identity verification
# ---------------------------------------------------------------------------

def _count_starred_org_repos(
    token: str,
    org: str,
) -> Tuple[List[str], int]:
    """Return (list of starred repo names under *org*, count)."""
    starred = _gh_paginated("/user/starred", token, max_pages=20)
    org_lower = org.lower()
    matched = [
        repo["name"]
        for repo in starred
        if isinstance(repo, dict)
        and repo.get("owner", {}).get("login", "").lower() == org_lower
    ]
    return matched, len(matched)


def _count_merged_prs(
    login: str,
    token: str,
    org: str,
) -> int:
    """Count merged PRs by *login* in repos owned by *org*."""
    query = f"is:pr is:merged author:{login} org:{org}"
    data, _ = _gh_request(
        f"/search/issues?q={urllib.request.quote(query)}&per_page=1",
        token,
    )
    return data.get("total_count", 0)


def verify_github_identity(
    oauth_token: str,
    config: Optional[AirdropConfig] = None,
) -> GitHubIdentity:
    """Verify a GitHub user via OAuth token and collect contribution metadata.

    Parameters
    ----------
    oauth_token:
        A valid GitHub personal-access or OAuth token with ``read:user`` scope.
    config:
        Optional :class:`AirdropConfig`; uses defaults if *None*.

    Returns
    -------
    GitHubIdentity
        Verified identity with starred repos and merged-PR count.
    """
    if config is None:
        config = get_config()

    # -- basic profile -------------------------------------------------------
    user_data, _ = _gh_request("/user", oauth_token)
    user_id: int = user_data["id"]
    login: str = user_data["login"]
    avatar: str = user_data.get("avatar_url", "")
    created_at = user_data.get("created_at", "")

    account_age_days = 0
    if created_at:
        created = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        account_age_days = (datetime.now(timezone.utc) - created).days

    # -- contribution data ---------------------------------------------------
    starred_names, _star_count = _count_starred_org_repos(
        oauth_token, config.github_org
    )
    merged_prs = _count_merged_prs(login, oauth_token, config.github_org)

    logger.info(
        "Verified %s (id=%d, age=%dd, stars=%d, merged_prs=%d)",
        login, user_id, account_age_days, len(starred_names), merged_prs,
    )

    return GitHubIdentity(
        id=user_id,
        login=login,
        account_age_days=account_age_days,
        avatar_url=avatar,
        starred_repos=tuple(starred_names),
        merged_pr_count=merged_prs,
        oauth_token=oauth_token,
    )
