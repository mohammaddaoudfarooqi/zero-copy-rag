# How the worker, the trigger and the Lambda connect to Temporal: plain for a local or
# self-hosted server, an API key or mTLS for Temporal Cloud. All three go through
# pipeline.trigger.get_client, so a setting cannot work for one and not the others.

from __future__ import annotations

import pytest
from temporalio.service import TLSConfig

from pipeline import lambda_handler, trigger, worker
from pipeline.config import Settings


def _settings(**overrides) -> Settings:
    base = {"temporal_api_key": "", "temporal_tls_cert": "", "temporal_tls_key": ""}
    return Settings(**{**base, **overrides})


def test_a_local_server_connects_without_credentials():
    opts = trigger.connect_options(_settings(temporal_namespace="default"))
    assert opts == {"namespace": "default"}


def test_an_api_key_is_passed_and_tls_left_to_the_sdk():
    """temporalio turns TLS on by itself when an API key is set."""
    opts = trigger.connect_options(_settings(temporal_api_key="secret-key"))
    assert opts["api_key"] == "secret-key"
    assert "tls" not in opts


def test_mtls_reads_the_cert_and_key_files(tmp_path):
    cert, key = tmp_path / "client.pem", tmp_path / "client.key"
    cert.write_bytes(b"CERT")
    key.write_bytes(b"KEY")
    opts = trigger.connect_options(_settings(temporal_tls_cert=str(cert), temporal_tls_key=str(key)))
    assert isinstance(opts["tls"], TLSConfig)
    assert (opts["tls"].client_cert, opts["tls"].client_private_key) == (b"CERT", b"KEY")
    assert "api_key" not in opts


@pytest.mark.parametrize("half", ["temporal_tls_cert", "temporal_tls_key"])
def test_half_an_mtls_pair_is_refused(tmp_path, half):
    path = tmp_path / "one.pem"
    path.write_bytes(b"X")
    with pytest.raises(ValueError, match="TEMPORAL_TLS_CERT and TEMPORAL_TLS_KEY"):
        trigger.connect_options(_settings(**{half: str(path)}))


def test_an_api_key_and_mtls_together_are_refused(tmp_path):
    cert, key = tmp_path / "c.pem", tmp_path / "k.pem"
    cert.write_bytes(b"C")
    key.write_bytes(b"K")
    with pytest.raises(ValueError, match="either"):
        trigger.connect_options(
            _settings(temporal_api_key="k", temporal_tls_cert=str(cert), temporal_tls_key=str(key))
        )


@pytest.fixture
def connects(monkeypatch):
    """Capture Client.connect calls made through any entry point."""
    calls: list[tuple[str, dict]] = []

    async def _connect(target, **kwargs):
        calls.append((target, kwargs))
        return object()

    monkeypatch.setattr(trigger.Client, "connect", _connect)
    monkeypatch.setattr(trigger, "settings", _settings(temporal_api_key="from-settings"))
    return calls


async def test_get_client_applies_the_options(connects):
    await trigger.get_client()
    target, kwargs = connects[0]
    assert target == trigger.settings.temporal_address
    assert kwargs["api_key"] == "from-settings"


async def test_the_lambda_connects_through_get_client(connects, monkeypatch):
    async def _no_events(client, event):
        return []

    monkeypatch.setattr(lambda_handler, "handle_s3_event", _no_events)
    await lambda_handler._run({"Records": []})
    assert connects and connects[0][1]["api_key"] == "from-settings"


def test_the_worker_connects_through_get_client():
    import inspect

    src = inspect.getsource(worker.main)
    assert "get_client()" in src and "Client.connect" not in src
