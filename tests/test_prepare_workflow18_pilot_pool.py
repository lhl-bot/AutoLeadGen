import copy

import pytest

from prepare_workflow18_pilot_pool import DEFAULT_MANIFEST, load_manifest, validate_manifest


def test_workflow18_pilot_manifest_has_30_unique_verified_companies():
    entries = load_manifest(DEFAULT_MANIFEST)

    counts = validate_manifest(entries)

    assert len(entries) == 30
    assert counts == {
        "ready_for_email_lookup": 14,
        "missing_contact": 7,
        "manual_role_review": 9,
    }


def test_workflow18_pilot_manifest_rejects_same_source_twice():
    entries = load_manifest(DEFAULT_MANIFEST)
    broken = copy.deepcopy(entries)
    broken[0]["verification_source"] = "https://www.pillowtalk.com.au/about"

    with pytest.raises(ValueError, match="independent"):
        validate_manifest(broken)


def test_workflow18_pilot_manifest_requires_public_contact_evidence():
    entries = load_manifest(DEFAULT_MANIFEST)
    broken = copy.deepcopy(entries)
    broken[0]["selected_contact"]["linkedin_url"] = ""

    with pytest.raises(ValueError, match="contact evidence"):
        validate_manifest(broken)
