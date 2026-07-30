"""FastAPI dependencies — one Repository per process, policy/predictor cached."""

from __future__ import annotations

from functools import lru_cache

from trustsphere.config import get_settings
from trustsphere.persistence import Repository, get_repository
from trustsphere.scoring.policy import ScoringPolicy, load_policy


@lru_cache
def _repo_singleton() -> Repository:
    return get_repository()


def get_repo() -> Repository:
    return _repo_singleton()


def get_scoring_policy() -> ScoringPolicy:
    return load_policy(get_settings().scoring_policy_path)
