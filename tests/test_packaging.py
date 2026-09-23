# Guards what `agentengine build` uploads. The repo root is the agent workspace,
# so .agentengineignore is the only thing keeping the docs out of the archive.

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
IGNORE_FILE = REPO_ROOT / ".agentengineignore"

# Paths the archive must never carry. The docs are not the agent's to ship, and
# the seed corpus is just weight.
WITHHELD = ["docs", "PR_DESCRIPTION.md", "seed"]


def _rules() -> list[str]:
    return [
        line.strip()
        for line in IGNORE_FILE.read_text().splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


def test_the_archive_starts_from_deny_everything():
    """`/*` and not `*`: a negation cannot re-include a file under an excluded dir."""
    assert _rules()[0] == "/*"


@pytest.mark.parametrize("path", WITHHELD)
def test_nothing_negates_a_withheld_path(path):
    negations = {rule.lstrip("!").strip("/") for rule in _rules() if rule.startswith("!")}
    assert path not in negations


def test_the_agent_and_its_imports_are_negated_back_in():
    negations = {rule.lstrip("!").strip("/") for rule in _rules() if rule.startswith("!")}
    assert {"agent.yaml", "mongodb_agent_engine", "pipeline", "pyproject.toml"} <= negations


def test_every_wheel_package_survives_the_archive():
    """A package listed for the wheel but excluded from the archive fails the build."""
    import tomllib

    pyproject = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())
    packages = pyproject["tool"]["hatch"]["build"]["targets"]["wheel"]["packages"]
    negations = {rule.lstrip("!").strip("/") for rule in _rules() if rule.startswith("!")}
    assert set(packages) <= negations


def test_the_tool_sandbox_is_granted_the_database_name():
    """The archive never carries .env, so the hosted Tool Pod sees only the secrets
    agent.yaml grants it. In dev the repo is bind-mounted and pydantic-settings reads
    MONGODB_DB straight from .env, which hid this: without the grant, the hosted tools
    fall back to the default database and every search returns nothing."""
    import yaml

    config = yaml.safe_load((REPO_ROOT / "agent.yaml").read_text())
    assert "MONGODB_DB" in config["sandboxes"]["tool"]["secrets"]
