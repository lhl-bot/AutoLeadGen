import re
from dataclasses import dataclass
from typing import Optional


@dataclass
class PreparedEmailContent:
    subject: str
    body: str


def prepare_email_content(
    draft: str,
    *,
    company_name: Optional[str] = None,
    first_name: Optional[str] = None,
    sender_name: str = "there",
) -> PreparedEmailContent:
    """Extract a subject and clean generated email body placeholders/sign-offs."""
    lines = (draft or "").split("\n")
    subject = ""
    body = draft or ""

    for i, line in enumerate(lines):
        line_stripped = line.strip()
        subject_match = re.match(
            r"^(?:\*{0,2})(?:subject|SUBJECT|\u4e3b\u9898)\s*[\uff1a:]\s*(.+)",
            line_stripped,
            re.IGNORECASE,
        )
        if subject_match:
            subject = subject_match.group(1).strip().strip("*").strip()
            raw_body = "\n".join(lines[i + 1:]).strip()
            body = re.sub(
                r"^(?:\*{0,2})(?:body|BODY)\s*[\uff1a:]\s*\n?",
                "",
                raw_body,
                flags=re.IGNORECASE,
            ).strip()
            break

    if not subject:
        subject = f"Quick question for {company_name or 'you'}"

    first_n = first_name or "there"
    comp_n = company_name or "your company"

    body = re.sub(r"\[First Name\]|\[first name\]", first_n, body, flags=re.IGNORECASE)
    body = re.sub(r"\[Company\]|\[Company Name\]|\[Target Company\]", comp_n, body, flags=re.IGNORECASE)
    body = re.sub(r"\[Your Name\]|\[Name\]|\[Sender Name\]", sender_name, body, flags=re.IGNORECASE)
    body = re.sub(r"\[Our Company\]|\[Your Company\]", "", body, flags=re.IGNORECASE)

    # Drop any leftover generated placeholders such as [Title], [Phone], or [LinkedIn].
    body = re.sub(r"\[.*?\]", "", body)

    sign_off_words = (
        r"(?:Best regards|Kind regards|Warm regards|Regards|Cheers|Thanks|Thank you|"
        r"Best|Sincerely|Yours truly|Looking forward)"
    )
    body = re.sub(
        r"\n\s*" + sign_off_words + r",?\s*(?:\n.{0,60}){0,3}\s*$",
        "",
        body,
        flags=re.IGNORECASE,
    )

    body = "\n".join(
        line for line in body.split("\n")
        if line.strip() not in ("|", "", "  |  ", "|  ")
    )

    return PreparedEmailContent(subject=subject, body=body)


def build_email_html(
    body_text: str,
    sender_name: str,
    custom_signature: Optional[str] = None,
    unsubscribe_url: Optional[str] = None,
) -> str:
    """Wrap plain text body in a clean, professional HTML email template."""
    body_paragraphs = ""
    for para in body_text.strip().split("\n\n"):
        cleaned = para.strip().replace("\n", "<br>")
        if cleaned:
            body_paragraphs += f"<p style='margin:0 0 12px 0;line-height:1.6;color:#333333;'>{cleaned}</p>\n"

    if not body_paragraphs:
        body_paragraphs = f"<p style='margin:0 0 12px 0;line-height:1.6;color:#333333;'>{body_text.replace(chr(10), '<br>')}</p>"

    if custom_signature and custom_signature.strip():
        sig_lines = custom_signature.strip().split("\n")
        sig_html = "<br>".join(line.strip() for line in sig_lines if line.strip())
        signature_block = f"<p style='margin:0;color:#555;line-height:1.5;'>{sig_html}</p>"
    else:
        signature_block = f"<p style='margin:0;'>Best regards,<br><strong style='color:#555;'>{sender_name}</strong></p>"

    unsubscribe_copy = "If you no longer wish to receive these emails, please reply with \"unsubscribe\"."
    if unsubscribe_url:
        unsubscribe_copy = f'If you no longer wish to receive these emails, <a href="{unsubscribe_url}" style="color:#94a3b8;">unsubscribe here</a>.'

    return f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1.0"></head>
<body style="margin:0;padding:0;background-color:#f4f4f7;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;">
<table width="100%" cellpadding="0" cellspacing="0" style="background-color:#f4f4f7;padding:24px 0;">
<tr><td align="center">
<table width="600" cellpadding="0" cellspacing="0" style="background-color:#ffffff;border-radius:8px;overflow:hidden;box-shadow:0 2px 8px rgba(0,0,0,0.06);">
<tr><td style="padding:32px 40px;">
{body_paragraphs}
</td></tr>
<tr><td style="padding:16px 40px 24px;border-top:1px solid #eee;font-size:13px;color:#999;">
{signature_block}
<p style="margin:8px 0 0;font-size:11px;color:#bbb;">{unsubscribe_copy}</p>
</td></tr>
</table>
</td></tr>
</table>
</body>
</html>"""
