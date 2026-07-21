from migrate_v16 import expected_followup_count, planned_changes


def test_expected_followup_count_uses_successful_outbound_history():
    assert expected_followup_count(0) == 0
    assert expected_followup_count(1) == 0
    assert expected_followup_count(3) == 2


def test_planned_changes_restores_contacted_lead_without_touching_logs():
    plan = planned_changes({
        "id": 10,
        "status": "needs_email",
        "followup_count": 1,
        "domain": "retailer.example",
        "email": "buyer@retailer.example",
        "has_replied": False,
        "brief_id": None,
        "outbound_count": 1,
    }, ["bedding"])

    assert plan["lead"]["status"] == "sent"
    assert plan["lead"]["followup_count"] == 0
    assert "email_logs" not in plan

