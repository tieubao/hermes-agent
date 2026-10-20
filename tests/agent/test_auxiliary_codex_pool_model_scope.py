"""The Codex auxiliary client picks its pooled credential for the model it is built for.

A ChatGPT-account model entitlement 400 benches only (credential, model) (#71970). A pool pick
that names no model treats ANY active model cooldown as blocking, so one benched model used to
take the credential away from every auxiliary model on the same account.
"""
import json
import types

import pytest

from agent import auxiliary_client as aux

BENCHED = "gpt-5.3-codex"
OTHER = "gpt-5.3-codex-mini"
TOKENS = ("tok-account-a", "tok-account-b")


@pytest.fixture
def make_pool(tmp_path, monkeypatch):
    def _make(tokens):
        root = tmp_path / "hermes-root"
        root.mkdir(exist_ok=True)
        (tmp_path / "fakehome").mkdir(exist_ok=True)
        monkeypatch.setenv("HOME", str(tmp_path / "fakehome"))
        monkeypatch.setenv("HERMES_HOME", str(root))
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        import hermes_constants
        hermes_constants._default_hermes_root_memo = None  # type: ignore[attr-defined]
        (root / "auth.json").write_text(json.dumps({"credential_pool": {"openai-codex": [
            {"id": f"cred-{i}", "label": f"acct-{i}", "auth_type": "oauth", "priority": i, "source": "manual",
             "access_token": tok, "refresh_token": f"rt-{i}", "expires_at_ms": 4_000_000_000_000}
            for i, tok in enumerate(tokens)
        ]}}))
        from agent.credential_pool import load_pool
        pool = load_pool("openai-codex")
        pool.mark_exhausted_and_rotate(
            status_code=400, api_key_hint=tokens[0], credential_id="cred-0",
            failure_reason="model_entitlement", model=BENCHED,
        )
        return pool

    return _make


@pytest.fixture
def built_with(monkeypatch):
    """Records the api_key the aux client is built with instead of constructing a real client."""
    seen = []
    monkeypatch.setattr(aux, "_create_openai_client", lambda *, api_key, **_kw: seen.append(api_key) or types.SimpleNamespace(api_key=api_key, base_url=_kw.get("base_url", "")))
    return seen


def test_a_benched_model_does_not_take_the_credential_from_other_aux_models(make_pool, built_with):
    make_pool(TOKENS[:1])
    client, model = aux._build_codex_client(OTHER)
    assert client is not None and model == OTHER
    assert built_with == [TOKENS[0]]


def test_a_vendor_prefixed_model_is_matched_by_its_bare_id(make_pool, built_with):
    make_pool(TOKENS[:1])
    assert aux._build_codex_client(f"openai/{OTHER}")[0] is not None
    assert aux._build_codex_client(f"openai/{BENCHED}") == (None, None)  # the bench still applies
    assert built_with == [TOKENS[0]]


def test_the_benched_model_still_rotates_to_the_next_credential(make_pool, built_with):
    make_pool(TOKENS)
    assert aux._build_codex_client(BENCHED)[0] is not None
    assert built_with == [TOKENS[1]]
