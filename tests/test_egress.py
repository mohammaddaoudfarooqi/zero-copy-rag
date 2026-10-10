# agent.yaml's egress list is deny-all plus named hosts. These tests fail when
# the code starts calling a host the allow-list does not name, which in production is a
# connection timeout at the far end of a deploy rather than an obvious error.

from __future__ import annotations

import contextlib
from pathlib import Path
from urllib.parse import urlparse

import pytest
import yaml
from botocore.awsrequest import AWSResponse

from mongodb_agent_engine import llm
from pipeline import clients
from pipeline.config import Settings

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def allowed() -> set[str]:
    config = yaml.safe_load((REPO_ROOT / "agent.yaml").read_text())
    return {rule["fqdn"] for rule in config["network"]["egress"]}


def test_the_default_model_provider_is_reachable(allowed):
    assert llm.PROVIDER_HOSTS[llm.DEFAULT_PROVIDER] in allowed


def test_only_the_default_model_provider_is_reachable(allowed):
    """Other providers stay closed until someone switches to them on purpose."""
    others = {h for p, h in llm.PROVIDER_HOSTS.items() if p != llm.DEFAULT_PROVIDER}
    assert allowed.isdisjoint(others)


def test_the_voyage_endpoint_is_reachable(allowed):
    host = urlparse(Settings().voyage_base_url).hostname
    assert host == "ai.mongodb.com"
    assert host in allowed


def test_voyages_own_api_is_not_reachable(allowed):
    """Embeddings go through MongoDB's endpoint, so the direct host stays out."""
    assert "api.voyageai.com" not in allowed


def test_every_allowed_host_is_named_without_a_scheme_or_port(allowed):
    """`fqdn` is a hostname; a URL here validates but never matches."""
    for host in allowed:
        assert "://" not in host and ":" not in host and "/" not in host


# The bucket agent.yaml's allow-list is written around. At run time read_span
# takes the bucket from the pointer in Mongo; this constant only exists so the
# test can build a representative request.
ALLOW_LISTED_BUCKET = "temporal-agentic"


@pytest.fixture
def s3_request_hosts(monkeypatch):
    """Hosts boto3 actually builds, with the network stubbed out at before-send."""
    monkeypatch.setattr(clients.settings, "aws_region", "us-east-1")
    monkeypatch.setattr(clients.settings, "aws_access_key_id", "AKIAEXAMPLE")
    monkeypatch.setattr(clients.settings, "aws_secret_access_key", "example-secret")
    clients._s3_client_for.cache_clear()

    hosts: list[str] = []

    def _capture(request, **kwargs):
        hosts.append(urlparse(request.url).hostname)
        return AWSResponse(request.url, 200, {}, b"")

    client = clients.s3_client()
    client.meta.events.register("before-send.s3.*", _capture)
    yield client, hosts
    clients._s3_client_for.cache_clear()


def test_the_s3_host_boto3_builds_is_one_the_allow_list_names(s3_request_hosts, allowed):
    """The regression this file exists for, caught at the request rather than the config.

    botocore still resolves us-east-1 to the global ``s3.amazonaws.com`` unless
    it is told otherwise, so a bucket there is addressed as
    ``<bucket>.s3.amazonaws.com``. Every other region is regional already, which
    is why this is invisible until a deploy: the Tool Pod's egress proxy answers
    CONNECT to the unnamed host with 403 Forbidden and every read_span fails.
    Comparing agent.yaml against pipeline.config would not catch it, because
    neither one is where the hostname is decided.
    """
    client, hosts = s3_request_hosts
    with contextlib.suppress(Exception):  # only the host matters, not the parse
        client.get_object(Bucket=ALLOW_LISTED_BUCKET, Key="doc.md", Range="bytes=0-31")

    assert hosts, "boto3 built no request"
    assert hosts[0] in allowed


def test_the_global_s3_endpoint_stays_out_of_the_allow_list(allowed):
    """It fronts buckets in every region, so naming it would widen the boundary."""
    assert allowed.isdisjoint({"s3.amazonaws.com", f"{ALLOW_LISTED_BUCKET}.s3.amazonaws.com"})
