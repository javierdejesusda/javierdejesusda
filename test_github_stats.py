"""Tests for token-scope failure detection in github_stats."""

import asyncio
import os
import tempfile
import unittest

from generate_images import generate_overview
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

    async def test_zero_language_bytes_does_not_raise_zero_division(self):
        payload = {
            "data": {
                "viewer": {
                    "login": "javierdejesusda",
                    "name": "Javier De Jesus",
                    "repositories": {
                        "pageInfo": {"hasNextPage": False},
                        "nodes": [
                            {
                                "nameWithOwner": "javierdejesusda/empty-repo",
                                "stargazers": {"totalCount": 0},
                                "forkCount": 0,
                                "languages": {"edges": []},
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
        stats = build_stats(payload)
        await stats.get_stats()
        self.assertEqual(stats._languages, {})

    async def test_excluded_all_languages_sets_zero_prop_without_error(self):
        stats = Stats(
            "javierdejesusda",
            "fake-token",
            None,
            exclude_langs={"Python"},
        )
        stats.queries = FakeQueries(HEALTHY)
        await stats.get_stats()
        self.assertEqual(stats._languages, {})

    async def test_null_data_in_graphql_raises_runtime_error_not_attribute_error(self):
        stats = build_stats({"data": None, "errors": [{"message": "Bad credentials"}]})
        with self.assertRaises(RuntimeError):
            await stats.get_stats()

    async def test_null_stargazers_handled_gracefully(self):
        payload = {
            "data": {
                "viewer": {
                    "login": "javierdejesusda",
                    "name": "Javier De Jesus",
                    "repositories": {
                        "pageInfo": {"hasNextPage": False},
                        "nodes": [
                            {
                                "nameWithOwner": "javierdejesusda/no-stars",
                                "stargazers": None,
                                "forkCount": 0,
                                "languages": {"edges": []},
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
        stats = build_stats(payload)
        await stats.get_stats()
        self.assertEqual(stats._stargazers, 0)

    async def test_lines_changed_and_views_handle_non_list_or_null_rest_responses(self):
        stats = build_stats(HEALTHY)
        # FakeQueries.query_rest returns {} (simulating 202 exhaustion or error dict)
        self.assertEqual(await stats.lines_changed, (0, 0))
        self.assertEqual(await stats.views, 0)

        # Simulate {"views": None}
        async def null_views(path, params=None):
            return {"views": None}

        stats._views = None
        stats.queries.query_rest = null_views
        self.assertEqual(await stats.views, 0)

    async def test_generate_overview_handles_backslashes_in_name(self):
        payload = {
            "data": {
                "viewer": {
                    "login": "javierdejesusda",
                    "name": r"Javier \1 De Jesus",
                    "repositories": HEALTHY["data"]["viewer"]["repositories"],
                    "repositoriesContributedTo": {
                        "pageInfo": {"hasNextPage": False},
                        "nodes": [],
                    },
                }
            }
        }
        stats = build_stats(payload)
        with tempfile.TemporaryDirectory() as tmpdir:
            orig_cwd = os.getcwd()
            os.chdir(tmpdir)
            try:
                os.makedirs("templates", exist_ok=True)
                with open("templates/overview.svg", "w", encoding="utf-8") as f:
                    f.write("<svg>{{ name }} {{ stars }} {{ forks }} {{ contributions }} {{ lines_changed }} {{ views }} {{ repos }}</svg>")
                stats._total_contributions = 5
                stats._lines_changed = (10, 2)
                stats._views = 3
                await generate_overview(stats)
                with open("generated/overview.svg", "r", encoding="utf-8") as f:
                    content = f.read()
                self.assertIn(r"Javier \1 De Jesus", content)
            finally:
                os.chdir(orig_cwd)


if __name__ == "__main__":
    unittest.main()
