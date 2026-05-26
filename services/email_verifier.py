"""
Email verification pipeline — dual-layer validation before sending.
Layer 1: Snov.io API verification (if available)
Layer 2: DNS MX record check (always available)
"""
import logging
import os
from typing import Dict, Any

logger = logging.getLogger("outbound_engine")


def _domain_has_mx(domain: str) -> bool:
    """Check if domain has valid MX records (can receive email)."""
    if not domain:
        return False
    try:
        import dns.resolver
        answers = dns.resolver.resolve(domain, 'MX')
        return len(answers) > 0
    except Exception:
        return False


def verify_email_sync(email: str) -> Dict[str, Any]:
    """
    Synchronous dual-layer email verification.
    Returns: { email, status, has_mx, source }
    
    Status values:
    - "valid": Confirmed deliverable
    - "invalid": Known invalid / bounced
    - "catch-all": Domain accepts all emails (risky)
    - "unknown": Could not determine
    """
    if not email or "@" not in email:
        return {"email": email, "status": "invalid", "has_mx": False, "source": "format_check"}

    domain = email.split("@")[-1].lower().strip()
    result = {
        "email": email,
        "status": "unknown",
        "has_mx": False,
        "source": "none",
    }

    # Layer 1: DNS MX check (fast, always available)
    result["has_mx"] = _domain_has_mx(domain)
    if not result["has_mx"]:
        result["status"] = "invalid"
        result["source"] = "mx_check"
        return result

    # Layer 2: Snov.io verification (if credentials are configured)
    snovio_id = os.environ.get("SNOVIO_CLIENT_ID", "")
    snovio_secret = os.environ.get("SNOVIO_CLIENT_SECRET", "")
    if snovio_id and snovio_secret:
        try:
            from services.snovio_client import SnovioClient
            client = SnovioClient(snovio_id, snovio_secret)
            snov_status = client.verify_email(email)
            if snov_status:
                status_map = {
                    "valid": "valid",
                    "verified": "valid",
                    "invalid": "invalid",
                    "unverifiable": "unknown",
                    "catch-all": "catch-all",
                    "disposable": "invalid",
                }
                result["status"] = status_map.get(snov_status.lower(), "unknown")
                result["source"] = "snovio"
                return result
        except Exception as e:
            logger.warning(f"Snov.io verification failed for {email}: {e}")

    # If Snov.io unavailable but MX exists, mark as unknown (sendable but unverified)
    if result["has_mx"]:
        result["status"] = "unknown"
        result["source"] = "mx_only"

    return result


async def verify_email(email: str) -> Dict[str, Any]:
    """Async wrapper around the sync verification."""
    import asyncio
    return await asyncio.to_thread(verify_email_sync, email)


def update_lead_verification(lead_id: int, verification_result: Dict[str, Any]) -> None:
    """Write verification result back to the Lead record."""
    from database import SessionLocal
    from models import Lead
    
    db = SessionLocal()
    try:
        lead = db.query(Lead).filter(Lead.id == lead_id).first()
        if lead:
            lead.email_validation_status = verification_result.get("status", "unknown")
            lead.email_verified = verification_result.get("status") == "valid"
            db.commit()
            logger.info(
                f"Email verification for {lead.email}: "
                f"status={verification_result['status']}, source={verification_result.get('source')}"
            )
    except Exception as e:
        logger.error(f"Failed to update lead {lead_id} verification: {e}")
    finally:
        db.close()
