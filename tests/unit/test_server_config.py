"""Tests for server.config: data_dir/static_dir env overrides, store factory,
SSM env-var resolution.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from server import config


def test_data_dir_env_override(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("MORNING_DIGEST_DATA_DIR", str(tmp_path))
    got = config.data_dir()
    # Resolved + expanded; should land on the same inode.
    assert got.resolve() == tmp_path.resolve()


def test_data_dir_default_is_cwd_data(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MORNING_DIGEST_DATA_DIR", raising=False)
    assert config.data_dir() == Path.cwd() / "data"


def test_static_dir_env_override(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("MORNING_DIGEST_STATIC_DIR", str(tmp_path))
    assert config.static_dir().resolve() == tmp_path.resolve()


def test_static_dir_default_bundled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MORNING_DIGEST_STATIC_DIR", raising=False)
    # The default lives next to server/__init__.py.
    assert config.static_dir().name == "static"


def test_s3_bucket_raises_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MORNING_DIGEST_S3_BUCKET", raising=False)
    with pytest.raises(RuntimeError, match="MORNING_DIGEST_S3_BUCKET"):
        config.s3_bucket()


def test_s3_bucket_returns_env_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MORNING_DIGEST_S3_BUCKET", "my-bucket")
    assert config.s3_bucket() == "my-bucket"


def test_auth_token_default_is_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MORNING_DIGEST_AUTH_TOKEN", raising=False)
    assert config.auth_token() is None


def test_auth_token_returns_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MORNING_DIGEST_AUTH_TOKEN", "tok")
    assert config.auth_token() == "tok"


def test_make_store_filesystem_when_no_bucket(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MORNING_DIGEST_S3_BUCKET", raising=False)
    from digest.store import Store

    assert isinstance(config.make_store(), Store)


def test_make_store_s3_when_bucket_env_set(monkeypatch: pytest.MonkeyPatch) -> None:
    """S3Store calls boto3.client at __init__; stub it so the test stays offline."""
    monkeypatch.setenv("MORNING_DIGEST_S3_BUCKET", "the-bucket")
    import boto3

    class _FakeClient:
        pass

    monkeypatch.setattr(boto3, "client", lambda *_a, **_k: _FakeClient())
    from digest.s3_store import S3Store

    store = config.make_store()
    assert isinstance(store, S3Store)
    assert store.bucket == "the-bucket"


def test_resolve_ssm_env_vars_replaces_path_with_secure_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "/morning-digest/claude-oauth-token")
    monkeypatch.setenv("MORNING_DIGEST_AUTH_TOKEN", "literal-not-ssm")
    # _read_ssm is functools.cache'd — clear so test data sticks.
    config._read_ssm.cache_clear()

    calls: list[str] = []

    def _fake_read(name: str) -> str:
        calls.append(name)
        return "resolved-" + name.rsplit("/", 1)[-1]

    monkeypatch.setattr(config, "_read_ssm", _fake_read)
    config.resolve_ssm_env_vars()

    import os

    assert os.environ["CLAUDE_CODE_OAUTH_TOKEN"] == "resolved-claude-oauth-token"
    # Literal value (does not start with '/') should not be rewritten.
    assert os.environ["MORNING_DIGEST_AUTH_TOKEN"] == "literal-not-ssm"
    assert calls == ["/morning-digest/claude-oauth-token"]


def test_resolve_ssm_env_vars_handles_missing_vars(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.delenv("MORNING_DIGEST_AUTH_TOKEN", raising=False)

    def _boom(_name: str) -> str:
        raise AssertionError("_read_ssm should not be called when env var is unset")

    monkeypatch.setattr(config, "_read_ssm", _boom)
    config.resolve_ssm_env_vars()  # must not raise


def test_resolve_ssm_env_vars_resolves_optional_nyt_cookies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.delenv("MORNING_DIGEST_AUTH_TOKEN", raising=False)
    monkeypatch.setenv("NYT_COOKIES", "/morning-digest/nyt-cookies")
    config._read_ssm.cache_clear()

    monkeypatch.setattr(config, "_read_ssm", lambda _name: "NYT-S=abc; foo=bar")
    config.resolve_ssm_env_vars()

    import os

    assert os.environ["NYT_COOKIES"] == "NYT-S=abc; foo=bar"


def test_resolve_ssm_env_vars_tolerates_missing_nyt_cookies_param(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """NYT_COOKIES is optional — a ParameterNotFound must not crash cold start."""
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.delenv("MORNING_DIGEST_AUTH_TOKEN", raising=False)
    monkeypatch.setenv("NYT_COOKIES", "/morning-digest/nyt-cookies")
    config._read_ssm.cache_clear()

    def _boom(_name: str) -> str:
        raise RuntimeError("ParameterNotFound")

    monkeypatch.setattr(config, "_read_ssm", _boom)
    config.resolve_ssm_env_vars()  # must not raise

    import os

    assert os.environ["NYT_COOKIES"] == ""


def test_read_ssm_invokes_boto3(monkeypatch: pytest.MonkeyPatch) -> None:
    """_read_ssm is functools.cache'd; clear it so we can prove the boto3 call."""
    config._read_ssm.cache_clear()
    captured: dict[str, Any] = {}

    class _FakeSsm:
        def get_parameter(self, *, Name: str, WithDecryption: bool) -> dict[str, Any]:
            captured["name"] = Name
            captured["decrypt"] = WithDecryption
            return {"Parameter": {"Value": "the-secret"}}

    import boto3

    monkeypatch.setattr(boto3, "client", lambda service: _FakeSsm())
    out = config._read_ssm("/path/to/secret")
    assert out == "the-secret"
    assert captured == {"name": "/path/to/secret", "decrypt": True}
    # Cached on subsequent calls — no second boto3 invocation needed.
    config._read_ssm.cache_clear()
