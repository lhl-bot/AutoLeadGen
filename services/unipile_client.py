import os
import httpx
import logging
from typing import Dict, Any, Optional

logger = logging.getLogger("unipile_client")
logger.setLevel(logging.INFO)

class UnipileClient:
    """
    Client for interacting with the Unipile API for Omnichannel messaging 
    (LinkedIn, WhatsApp, etc.) and account management.
    """
    
    def __init__(self):
        self.api_key = os.environ.get("UNIPILE_API_KEY", "")
        self.dsn = os.environ.get("UNIPILE_DSN", "https://api3.unipile.com:13300")
        self.last_error_status = None
        self.last_error_body = ""
        self.headers = {
            "X-API-KEY": self.api_key,
            "Content-Type": "application/json",
            "Accept": "application/json"
        }

    async def generate_hosted_auth_link(self, type: str, name: str, success_redirect_url: str) -> Optional[str]:
        """
        Generates a temporary link for the Hosted Authentication Wizard.
        type: "LINKEDIN" or "WHATSAPP"
        """
        from datetime import datetime, timedelta, timezone
        expires_on = (datetime.now(timezone.utc) + timedelta(days=29)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        
        url = f"{self.dsn}/api/v1/hosted/accounts/link"
        payload = {
            "type": "create",
            "providers": [type],
            "api_url": self.dsn,
            "name": name,
            "success_redirect_url": success_redirect_url,
            "expiresOn": expires_on
        }
        
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(url, headers=self.headers, json=payload, timeout=10.0)
                if resp.status_code == 201:
                    data = resp.json()
                    return data.get("url")
                else:
                    logger.error(f"Unipile Auth Link Error: {resp.status_code} - {resp.text}")
                    return None
        except Exception as e:
            logger.error(f"Failed to call Unipile generate_hosted_auth_link: {e}")
            return None

    async def get_account_status(self, account_id: str) -> Optional[str]:
        """Returns the status of the account's messaging source (e.g. OK, CREDENTIALS).

        Unipile v1 nests the status inside `sources[]` rather than at the top level —
        we pick the MESSAGING source's status, or the first available source.
        """
        url = f"{self.dsn}/api/v1/accounts/{account_id}"
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(url, headers=self.headers, timeout=10.0)
                if resp.status_code == 200:
                    data = resp.json()
                    sources = data.get("sources") or []
                    if sources:
                        messaging = next(
                            (s for s in sources if "MESSAGING" in str(s.get("id", "")).upper()),
                            sources[0],
                        )
                        return messaging.get("status")
                    return data.get("status")
                return None
        except Exception as e:
            logger.error(f"Failed to get Unipile account status: {e}")
            return None

    async def get_all_accounts(self) -> list:
        """Fetch all connected accounts from Unipile."""
        url = f"{self.dsn}/api/v1/accounts"
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(url, headers=self.headers, timeout=10.0)
                if resp.status_code == 200:
                    data = resp.json()
                    # It returns an object {"items": [...]} or just a list? Usually {"items": ...} or direct list
                    return data.get("items", []) if isinstance(data, dict) else (data if isinstance(data, list) else [])
                logger.error(f"Failed to get all Unipile accounts: {resp.status_code} - {resp.text}")
                return []
        except Exception as e:
            logger.error(f"Exception getting all Unipile accounts: {e}")
            return []

    async def send_linkedin_invitation(self, account_id: str, provider_id: str, message: str) -> bool:
        """Sends a LinkedIn connection request with an optional note."""
        self.last_error_status = None
        self.last_error_body = ""
        url = f"{self.dsn}/api/v1/users/invite"
        payload = {
            "account_id": account_id,
            "provider_id": provider_id,
            "message": message
        }
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(url, headers=self.headers, json=payload, timeout=15.0)
                if resp.status_code in [200, 201]:
                    return True
                self.last_error_status = resp.status_code
                self.last_error_body = resp.text
                logger.error(f"Unipile LinkedIn Invitation Error: {resp.status_code} - {resp.text}")
                return False
        except Exception as e:
            self.last_error_body = str(e)
            logger.error(f"Failed to send LinkedIn invitation via Unipile: {e}")
            return False

    async def send_whatsapp_message(self, account_id: str, phone_number: str, text: str) -> bool:
        """Sends a WhatsApp message."""
        url = f"{self.dsn}/api/v1/chats"
        # In Unipile, sending to a new contact creates a chat
        payload = {
            "account_id": account_id,
            "attendees": [{"provider_id": phone_number}],
            "text": text
        }
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(url, headers=self.headers, json=payload, timeout=15.0)
                if resp.status_code in [200, 201]:
                    return True
                logger.error(f"Unipile WhatsApp Message Error: {resp.status_code} - {resp.text}")
                return False
        except Exception as e:
            logger.error(f"Failed to send WhatsApp message via Unipile: {e}")
            return False

    async def get_linkedin_provider_id(self, account_id: str, linkedin_url: str) -> Optional[str]:
        """
        Retrieve the Unipile provider_id for a LinkedIn user from their profile URL.
        This is needed to send invitations or messages.
        """
        # Extract the LinkedIn vanity name from the URL
        # e.g. https://linkedin.com/in/johndoe -> johndoe
        import re
        match = re.search(r'linkedin\.com/in/([^/?#]+)', linkedin_url)
        if not match:
            logger.warning(f"Could not extract LinkedIn vanity name from: {linkedin_url}")
            return None
        
        vanity_name = match.group(1).strip('/')
        
        url = f"{self.dsn}/api/v1/users/{vanity_name}"
        params = {"account_id": account_id}
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(url, headers=self.headers, params=params, timeout=15.0)
                if resp.status_code == 200:
                    data = resp.json()
                    provider_id = data.get("provider_id") or data.get("id")
                    return provider_id
                logger.error(f"Unipile get_linkedin_provider_id error: {resp.status_code} - {resp.text}")
                return None
        except Exception as e:
            logger.error(f"Failed to get LinkedIn provider_id via Unipile: {e}")
            return None

    async def send_linkedin_message(self, account_id: str, provider_id: str, text: str) -> bool:
        """Sends a LinkedIn direct message to an existing connection."""
        url = f"{self.dsn}/api/v1/chats"
        payload = {
            "account_id": account_id,
            "attendees": [{"provider_id": provider_id}],
            "text": text
        }
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(url, headers=self.headers, json=payload, timeout=15.0)
                if resp.status_code in [200, 201]:
                    return True
                logger.error(f"Unipile LinkedIn Message Error: {resp.status_code} - {resp.text}")
                return False
        except Exception as e:
            logger.error(f"Failed to send LinkedIn message via Unipile: {e}")
            return False
