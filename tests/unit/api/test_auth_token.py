from apps.api.auth import sign_token, verify_token


def test_sign_verify_roundtrip(monkeypatch):
    monkeypatch.setenv("APP_SECRET", "test-secret-xyz")
    tok = sign_token(1_000_000)
    assert verify_token(tok, 1_000_000 + 10) is True


def test_expired_token_rejected(monkeypatch):
    monkeypatch.setenv("APP_SECRET", "test-secret-xyz")
    tok = sign_token(1_000_000)
    assert verify_token(tok, 1_000_000 + 31 * 86400) is False


def test_tampered_token_rejected(monkeypatch):
    monkeypatch.setenv("APP_SECRET", "test-secret-xyz")
    tok = sign_token(1_000_000)
    bad = tok[:-2] + ("aa" if not tok.endswith("aa") else "bb")
    assert verify_token(bad, 1_000_000 + 10) is False


def test_wrong_secret_rejected(monkeypatch):
    monkeypatch.setenv("APP_SECRET", "secret-A")
    tok = sign_token(1_000_000)
    monkeypatch.setenv("APP_SECRET", "secret-B")
    assert verify_token(tok, 1_000_000 + 10) is False


def test_garbage_token_rejected(monkeypatch):
    monkeypatch.setenv("APP_SECRET", "s")
    assert verify_token("not-a-token", 1) is False
    assert verify_token("", 1) is False
