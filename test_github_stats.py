"""Tests for token-scope failure detection in github_stats."""

import asyncio
import unittest

from github_stats import Stats


class FakeQueries:
    """Stands in for Queries, returning a canned GraphQL payload."""

    def __init__(self, payload):
        self.payload = payload

    async def query(self, generated_query: str):
        return self.payload

    async def query_rest(self, path: str, params=None):
        return {}


def build_stats(payload):
    """Builds a Stats instance with its queries replaced by a stub.

    Must be called from inside a running event loop: on Python 3.8 the
    Semaphore built by Queries binds to the current loop at construction
    time and raises when no loop is running.

    Args:
        payload: Canned GraphQL response returned by every query.

    Returns:
        A Stats instance wired to FakeQueries.
    """
    asyncio.get_event_loop()
    stats = Stats("javierdejesusda", "fake-token", None)
    stats.queries = FakeQueries(payload)
    return stats


# A token that authenticates but has no repository access: viewer resolves,
# but the repositories connection comes back empty with no GraphQL error.
NO_REPO_ACCESS = {
    "data": {
        "viewer": {
            "login": "javierdejesusda",
            "name": "Javier De Jesus",
            "repositories": {"pageInfo": {"hasNextPage": False}, "nodes": []},
            "repositoriesContributedTo": {
                "pageInfo": {"hasNextPage": False},
                "nodes": [],
            },
        }
    }
}

HEALTHY = {
    "data": {
        "viewer": {
            "login": "javierdejesusda",
            "name": "Javier De Jesus",
            "repositories": {
                "pageInfo": {"hasNextPage": False},
                "nodes": [
                    {
                        "nameWithOwner": "javierdejesusda/portfolio-cv",
                        "stargazers": {"totalCount": 3},
                        "forkCount": 1,
                        "languages": {
                            "edges": [
                                {"size": 100, "node": {"name": "Python", "color": "#3572A5"}}
                            ]
                        },
                    }
                ],
            },
            "repositoriesContributedTo": {
                "pageInfo": {"hasNextPage": False},
                "nodes": [],
            },
        }
    }
}


class TokenScopeDetectionTest(unittest.IsolatedAsyncioTestCase):
    async def test_builds_inside_a_running_loop(self):
        """Guards the Python 3.8 loop-binding trap that broke this suite."""
        self.assertIsNotNone(asyncio.get_running_loop())
        self.assertIsNotNone(build_stats(HEALTHY))

    async def test_raises_when_token_cannot_see_any_repository(self):
        stats = build_stats(NO_REPO_ACCESS)
        with self.assertRaises(RuntimeError) as ctx:
            await stats.get_stats()
        self.assertIn("ACCESS_TOKEN", str(ctx.exception))

    async def test_succeeds_when_repositories_are_visible(self):
        stats = build_stats(HEALTHY)
        await stats.get_stats()
        self.assertEqual(stats._stargazers, 3)
        self.assertEqual(stats._forks, 1)
        self.assertEqual(stats._repos, {"javierdejesusda/portfolio-cv"})


if __name__ == "__main__":
    unittest.main()
