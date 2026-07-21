import pytest

from scripts.backfill_product_v2 import assert_isolated_database


def test_backfill_cli_rejects_remote_or_unmarked_database(monkeypatch):
    # GIVEN: A local command without the explicit isolated-copy acknowledgement.
    monkeypatch.setenv("AUTOLEADGEN_ENV", "local")
    monkeypatch.delenv("PRODUCT_V2_ISOLATED_DATABASE", raising=False)

    # WHEN/THEN: Even a loopback-looking target is rejected before any connection.
    with pytest.raises(SystemExit, match="PRODUCT_V2_ISOLATED_DATABASE"):
        assert_isolated_database("mysql+pymysql://user:pass@127.0.0.1/haiwaike", apply=False)

    # GIVEN: The marker exists but the target is remote.
    monkeypatch.setenv("PRODUCT_V2_ISOLATED_DATABASE", "true")

    # WHEN/THEN: The CLI still fails closed before importing the application DB.
    with pytest.raises(SystemExit, match="loopback"):
        assert_isolated_database("mysql+pymysql://user:pass@db.example/autoleadgen_v2", apply=False)


def test_backfill_apply_requires_second_acknowledgement(monkeypatch):
    # GIVEN: A valid isolated SQLite target without apply authorization.
    monkeypatch.setenv("AUTOLEADGEN_ENV", "test")
    monkeypatch.setenv("PRODUCT_V2_ISOLATED_DATABASE", "true")
    monkeypatch.delenv("PRODUCT_V2_BACKFILL_APPLY", raising=False)

    # WHEN/THEN: Dry-run is accepted but mutation requires an additional switch.
    assert_isolated_database("sqlite+pysqlite:///:memory:", apply=False)
    with pytest.raises(SystemExit, match="PRODUCT_V2_BACKFILL_APPLY"):
        assert_isolated_database("sqlite+pysqlite:///:memory:", apply=True)

    monkeypatch.setenv("PRODUCT_V2_BACKFILL_APPLY", "true")
    assert_isolated_database("sqlite+pysqlite:///:memory:", apply=True)
