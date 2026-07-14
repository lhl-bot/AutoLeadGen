from services.outbound_engine import _email_lookup_target_tokens, _lead_worth_email_lookup

WF15_TOKENS = _email_lookup_target_tokens("padel club, padel academy, padel court operator", "padel")


def test_tokens_keep_distinctive_drop_stopwords():
    assert "padel" in WF15_TOKENS
    assert "club" in WF15_TOKENS
    assert "academy" in WF15_TOKENS
    # generic stopwords are dropped
    assert "operator" not in WF15_TOKENS
    assert "the" not in WF15_TOKENS


def test_on_target_company_is_worth_lookup():
    assert _lead_worth_email_lookup("UCL Padel Club", "CEO", WF15_TOKENS) is True
    assert _lead_worth_email_lookup("TAKTIKA PADEL CENTER", "Owner", WF15_TOKENS) is True


def test_clearly_off_target_company_is_skipped():
    assert _lead_worth_email_lookup("Hewlett Packard Enterprise", "CEO", WF15_TOKENS) is False
    assert _lead_worth_email_lookup("Beintoo", "Manager", WF15_TOKENS) is False


def test_blank_company_is_allowed_lenient():
    # No info to judge on -> don't silently drop; allow (lead parks as needs_email if no email).
    assert _lead_worth_email_lookup("", "", WF15_TOKENS) is True
    assert _lead_worth_email_lookup(None, None, WF15_TOKENS) is True


def test_no_tokens_means_no_gating():
    assert _lead_worth_email_lookup("Hewlett Packard Enterprise", "CEO", []) is True
