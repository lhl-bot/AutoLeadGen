import smtplib
import ssl
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.utils import make_msgid
from typing import Optional


def send_email(
    smtp_host: str,
    smtp_port: int,
    smtp_user: str,
    smtp_pass: str,
    use_ssl: bool,
    use_tls: bool,
    from_email: str,
    to_email: str,
    subject: str,
    body_html: str,
    body_text: Optional[str] = None,
    sender_name: Optional[str] = None,
    reply_to: Optional[str] = None,
    in_reply_to: Optional[str] = None,
    references: Optional[str] = None
) -> dict:
    """Send an email via SMTP. Returns status dict."""
    try:
        from email.utils import formataddr, formatdate
        
        msg = MIMEMultipart("alternative")
        
        if sender_name:
            msg["From"] = formataddr((sender_name, from_email))
        else:
            msg["From"] = from_email
            
        msg["To"] = to_email
        msg["Subject"] = subject
        msg["Date"] = formatdate(localtime=True)
        
        if reply_to:
            msg["Reply-To"] = reply_to
            msg["List-Unsubscribe"] = f"<mailto:{reply_to}?subject=unsubscribe>"
        
        if in_reply_to:
            msg["In-Reply-To"] = in_reply_to
        if references:
            msg["References"] = references

        msg_id = make_msgid(domain=from_email.split('@')[-1])
        msg["Message-ID"] = msg_id

        if body_text:
            msg.attach(MIMEText(body_text, "plain", "utf-8"))
        msg.attach(MIMEText(body_html, "html", "utf-8"))

        context = ssl.create_default_context()

        if use_ssl:
            server = smtplib.SMTP_SSL(smtp_host, smtp_port, context=context, timeout=15)
        else:
            server = smtplib.SMTP(smtp_host, smtp_port, timeout=15)
            if use_tls:
                server.starttls(context=context)

        server.login(smtp_user, smtp_pass)
        server.sendmail(from_email, to_email, msg.as_string())
        server.quit()

        return {"success": True, "message": f"Email sent to {to_email}", "message_id": msg_id}

    except Exception as e:
        return {"success": False, "message": str(e)}


def test_smtp_connection(smtp_host: str, smtp_port: int, smtp_user: str, smtp_pass: str, use_ssl: bool, use_tls: bool) -> dict:
    """Test SMTP connection without sending."""
    try:
        context = ssl.create_default_context()
        if use_ssl:
            server = smtplib.SMTP_SSL(smtp_host, smtp_port, context=context, timeout=10)
        else:
            server = smtplib.SMTP(smtp_host, smtp_port, timeout=10)
            if use_tls:
                server.starttls(context=context)
        server.login(smtp_user, smtp_pass)
        server.quit()
        return {"success": True, "message": "SMTP connection successful!"}
    except Exception as e:
        return {"success": False, "message": str(e)}
