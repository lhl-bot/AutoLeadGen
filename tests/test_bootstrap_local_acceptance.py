import importlib

import pytest


def test_acceptance_bootstrap_refuses_non_isolated_environment(monkeypatch):
    monkeypatch.setenv("AUTOLEADGEN_ENV", "production")
    monkeypatch.setenv("PRODUCT_V2_ISOLATED_DATABASE", "true")
    module = importlib.import_module("scripts.bootstrap_local_acceptance")

    with pytest.raises(SystemExit, match="local or test"):
        module.main()


def test_acceptance_bootstrap_requires_password_file(monkeypatch):
    monkeypatch.setenv("AUTOLEADGEN_ENV", "test")
    monkeypatch.setenv("PRODUCT_V2_ISOLATED_DATABASE", "true")
    monkeypatch.delenv("LOCAL_ACCEPTANCE_PASSWORD", raising=False)
    monkeypatch.delenv("LOCAL_ACCEPTANCE_PASSWORD_FILE", raising=False)
    module = importlib.import_module("scripts.bootstrap_local_acceptance")

    with pytest.raises(SystemExit, match="PASSWORD_FILE"):
        module.main()
