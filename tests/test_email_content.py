from services.email_content import build_email_html, prepare_email_content


def test_prepare_email_content_extracts_subject_and_body():
    prepared = prepare_email_content(
        "Subject: Partnership idea\nBody:\nHi [First Name],\nI noticed [Company].",
        company_name="Acme",
        first_name="Maya",
        sender_name="Huilong",
    )

    assert prepared.subject == "Partnership idea"
    assert prepared.body == "Hi Maya,\nI noticed Acme."


def test_prepare_email_content_falls_back_to_company_subject():
    prepared = prepare_email_content(
        "Hi there,\nCould this help?",
        company_name="Acme",
    )

    assert prepared.subject == "Quick question for Acme"
    assert prepared.body == "Hi there,\nCould this help?"


def test_prepare_email_content_removes_leftover_placeholders_and_generated_signature():
    prepared = prepare_email_content(
        "Subject: Quick note\n\nHi [First Name],\n[Title]\nCould we help [Target Company]?\n\nBest regards,\nBot Name\nAI Company",
        company_name="Acme",
        first_name="Maya",
        sender_name="Huilong",
    )

    assert prepared.body == "Hi Maya,\nCould we help Acme?"


def test_build_email_html_uses_custom_signature_and_unsubscribe_link():
    html = build_email_html(
        "Hi Maya,\nCan we help?\n\nSecond paragraph.",
        "Huilong",
        custom_signature="Huilong\nSales",
        unsubscribe_url="https://example.com/unsubscribe",
    )

    assert "Hi Maya,<br>Can we help?" in html
    assert "Second paragraph." in html
    assert "Huilong<br>Sales" in html
    assert 'href="https://example.com/unsubscribe"' in html


def test_build_email_html_falls_back_to_sender_signature():
    html = build_email_html("Hello", "Huilong")

    assert "Best regards,<br><strong" in html
    assert "Huilong" in html
    assert "reply with &quot;unsubscribe&quot;" not in html
    assert 'reply with "unsubscribe"' in html
