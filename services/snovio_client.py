import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import time
import os
import logging
from typing import Any, Dict, List, Optional

from services.snovio_usage import record_snovio_usage

logger = logging.getLogger(__name__)

FALSE_ENV_VALUES = {"0", "false", "no", "off"}


def _bool_env(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() not in FALSE_ENV_VALUES


def _int_env(name: str, default: int, min_value: int = 1, max_value: int = 1000) -> int:
    try:
        value = int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default
    return max(min_value, min(max_value, value))


def _float_env(name: str, default: float, min_value: float = 0.1, max_value: float = 60.0) -> float:
    try:
        value = float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default
    return max(min_value, min(max_value, value))


def _email_status(item: Dict[str, Any]) -> str:
    return str(
        item.get("smtp_status")
        or item.get("status")
        or item.get("result", {}).get("smtp_status", "")
    ).lower()


def _billable_finder_credits(emails: List[Dict[str, Any]]) -> int:
    # Snov.io finder APIs generally charge when a valid or unknown email is found.
    return 1 if any(_email_status(e) in {"valid", "unknown"} for e in emails) else 0


class SnovioClient:
    def __init__(self, client_id: str, client_secret: str):
        self.client_id = client_id
        self.client_secret = client_secret
        self.access_token = None
        self.token_expiry = 0
        self.auth_failed_until = 0.0
        self._reported_missing_credentials = False
        self.session = requests.Session()
        retry = Retry(
            connect=5,
            read=5,
            backoff_factor=0.5,
            status_forcelist=[429, 500, 502, 503, 504],
            respect_retry_after_header=True,
        )
        adapter = HTTPAdapter(max_retries=retry)
        self.session.mount('http://', adapter)
        self.session.mount('https://', adapter)
        self.last_request_time = 0
        
    def _wait_for_rate_limit(self):
        """Ensure we don't exceed 60 requests per minute (1 req / sec)."""
        elapsed = time.time() - self.last_request_time
        if elapsed < 1.1:
            time.sleep(1.1 - elapsed)
        self.last_request_time = time.time()
        
    def _authenticate(self):
        """Get or refresh access token."""
        now = time.time()
        if now < self.auth_failed_until:
            return False

        if self.access_token and now < self.token_expiry - 60:
            return True

        if not self.client_id or not self.client_secret:
            if not self._reported_missing_credentials:
                logger.warning("Snov.io credentials are missing. Email enrichment will be skipped.")
                self._reported_missing_credentials = True
            self.auth_failed_until = time.time() + 300
            return False
            
        url = "https://api.snov.io/v1/oauth/access_token"
        payload = {
            "grant_type": "client_credentials",
            "client_id": self.client_id,
            "client_secret": self.client_secret
        }
        self._wait_for_rate_limit()
        try:
            resp = self.session.post(url, data=payload, headers={"Accept": "application/json"}, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            self.access_token = data.get("access_token")
            if not self.access_token:
                raise ValueError("Snov.io did not return an access token")
            self.token_expiry = time.time() + int(data.get("expires_in", 3600))
            self.auth_failed_until = 0.0
            return True
        except Exception as e:
            if not _bool_env("SNOVIO_ENABLE_MOCK_FALLBACK", False):
                logger.warning(f"Snov.io auth failed: {e}. Email enrichment will be skipped until credentials work.")
            else:
                logger.warning(f"Snov.io auth failed: {e}. Falling back to mock data mode.")
            self.auth_failed_until = time.time() + 60
            return False
        
    def _get_headers(self):
        if not self._authenticate():
            return None
        return {"Authorization": f"Bearer {self.access_token}", "Accept": "application/json"}

    def _request_json(self, method: str, url: str, *, headers: Optional[Dict[str, str]] = None, timeout: int = 45, **kwargs) -> Optional[Dict[str, Any]]:
        request_headers = headers or self._get_headers()
        if not request_headers:
            return None

        for attempt in range(2):
            self._wait_for_rate_limit()
            try:
                resp = self.session.request(method, url, headers=request_headers, timeout=timeout, **kwargs)
            except Exception as e:
                logger.error(f"Snov.io {method} exception for {url}: {e}")
                return None

            if resp.status_code == 401 and attempt == 0:
                self.access_token = None
                request_headers = self._get_headers()
                if not request_headers:
                    return None
                continue

            if resp.status_code not in (200, 201, 202):
                logger.error(f"Snov.io {method} error: {resp.status_code} {resp.text[:500]}")
                return None

            try:
                return resp.json()
            except ValueError as e:
                logger.error(f"Snov.io {method} returned non-JSON response: {e}")
                return None

        return None

    def _result_link(self, payload: Dict[str, Any], fallback_url: Optional[str] = None) -> Optional[str]:
        links = payload.get("links")
        if isinstance(links, dict) and links.get("result"):
            return links["result"]
        return fallback_url

    def _poll_result(self, result_url: str, headers: Dict[str, str]) -> Dict[str, Any]:
        attempts = _int_env("SNOVIO_POLL_ATTEMPTS", 8, 1, 30)
        interval = _float_env("SNOVIO_POLL_INTERVAL_SECONDS", 2.0, 0.5, 15.0)
        last_payload: Dict[str, Any] = {}

        for _ in range(attempts):
            time.sleep(interval)
            payload = self._request_json("GET", result_url, headers=headers)
            if not payload:
                continue
            last_payload = payload
            status = payload.get("status")
            if status == "completed":
                return payload
            if status not in {"in_progress", "pending", None}:
                return payload

        return last_payload

    def _mock_fallback_enabled(self) -> bool:
        return _bool_env("SNOVIO_ENABLE_MOCK_FALLBACK", False)

    def get_balance(self) -> Optional[Dict[str, Any]]:
        """Return the Snov.io account balance payload, if credentials work."""
        if not self._authenticate():
            return None

        return self._request_json(
            "GET",
            "https://api.snov.io/v1/get-balance",
            headers={"Accept": "application/json"},
            params={"access_token": self.access_token},
            timeout=8,
        )

    def get_domain_emails_count(self, domain: str) -> Optional[int]:
        """Return Snov.io's available email count for a domain.

        This endpoint is free in Snov.io and lets us avoid expensive prospect
        searches for domains where Snov has no email data at all.
        """
        if not domain or not self._authenticate():
            return None

        payload = {
            "access_token": self.access_token,
            "domain": domain,
        }
        data = self._request_json(
            "POST",
            "https://api.snov.io/v1/get-domain-emails-count",
            headers={"Accept": "application/json"},
            data=payload,
            timeout=20,
        )
        if not data or not data.get("success"):
            record_snovio_usage(
                endpoint="get-domain-emails-count",
                domain=domain,
                status="failed",
                estimated_credits=0,
            )
            return None

        try:
            count = int(data.get("result", 0) or 0)
        except (TypeError, ValueError):
            record_snovio_usage(
                endpoint="get-domain-emails-count",
                domain=domain,
                status="invalid_result",
                estimated_credits=0,
            )
            return None

        record_snovio_usage(
            endpoint="get-domain-emails-count",
            domain=domain,
            status="success",
            result_count=count,
            estimated_credits=0,
        )
        return count
        
    def search_prospects_by_domain(self, domain: str, positions: List[str], max_pages: int = None, allow_broad_fallback: bool = None) -> List[Dict]:
        """Start domain search for prospects and fetch results."""
        headers = self._get_headers()
        max_pages = _int_env("SNOVIO_DOMAIN_SEARCH_PAGES", 3, 1, 10) if max_pages is None else max(1, min(int(max_pages), 10))
        if allow_broad_fallback is None:
            allow_broad_fallback = _bool_env("SNOVIO_ALLOW_BROAD_ROLE_FALLBACK", False)
        
        # Optional mock fallback for demos only. In normal runs, failed Snov.io auth
        # should not create random contacts that look real.
        if not headers:
            if not self._mock_fallback_enabled():
                return []
            import random
            time.sleep(0.5) # Simulate slight network delay
            if random.random() > 0.5: # 50% chance to find a mock lead
                pos = random.choice(positions) if positions else "CEO"
                first = random.choice(["Alex", "Sam", "Jordan", "Taylor", "Morgan", "Casey"])
                last = random.choice(["Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia"])
                return [{
                    "first_name": first,
                    "last_name": last,
                    "position": pos,
                    "company_name": domain.split('.')[0].capitalize(),
                    "source_page": f"https://linkedin.com/in/{first.lower()}{last.lower()}",
                    "search_emails_start": f"mock_email_url_{first}_{last}@{domain}"
                }]
            return []

        strict_results = self._search_domain_pages(domain, positions, headers, max_pages)
        if strict_results or not positions or not allow_broad_fallback:
            return strict_results

        logger.info(f"Snov.io found no strict role matches for {domain}. Retrying without position filter.")
        return self._search_domain_pages(domain, [], headers, max_pages)

    def _dedupe_prospects(self, prospects: List[Dict]) -> List[Dict]:
        seen = set()
        deduped = []
        for prospect in prospects:
            key = (
                prospect.get("id")
                or prospect.get("search_emails_start")
                or f"{prospect.get('first_name', '')}|{prospect.get('last_name', '')}|{prospect.get('position', '')}"
            )
            if not key or key in seen:
                continue
            seen.add(key)
            deduped.append(prospect)
        return deduped

    def _search_domain_pages(self, domain: str, positions: List[str], headers: Dict, max_pages: int) -> List[Dict]:
        prospects: List[Dict] = []
        start_url = "https://api.snov.io/v2/domain-search/prospects/start"
        normalized_positions = [pos.strip() for pos in (positions or []) if pos and pos.strip()][:10]

        for page in range(1, max_pages + 1):
            form_data = [("domain", domain), ("page", str(page))]
            for position in normalized_positions:
                form_data.append(("positions[]", position))

            start_data = self._request_json("POST", start_url, headers=headers, data=form_data)
            if not start_data:
                break

            # Check if data is already in initial response
            initial_data = start_data.get("data", [])
            if isinstance(initial_data, list) and initial_data:
                record_snovio_usage(
                    endpoint="domain-search/prospects",
                    domain=domain,
                    status="completed",
                    result_count=len(initial_data),
                    estimated_credits=0,
                    metadata={"page": page, "positions": normalized_positions},
                )
                prospects.extend(initial_data)
                continue

            task_hash = start_data.get("meta", {}).get("task_hash")
            if not task_hash:
                break

            fallback_url = f"https://api.snov.io/v2/domain-search/prospects/result/{task_hash}"
            result_url = self._result_link(start_data, fallback_url)
            if not result_url:
                break

            res_data = self._poll_result(result_url, headers)
            page_results = res_data.get("data", []) if res_data.get("status") == "completed" else []

            if page_results:
                record_snovio_usage(
                    endpoint="domain-search/prospects",
                    domain=domain,
                    status="completed",
                    result_count=len(page_results),
                    estimated_credits=0,
                    metadata={"page": page, "positions": normalized_positions},
                )
                prospects.extend(page_results)
            else:
                record_snovio_usage(
                    endpoint="domain-search/prospects",
                    domain=domain,
                    status=res_data.get("status", "empty"),
                    result_count=0,
                    estimated_credits=0,
                    metadata={"page": page, "positions": normalized_positions},
                )
                break

        return self._dedupe_prospects(prospects)

    def get_prospect_email(self, search_email_start_url: str, domain: Optional[str] = None) -> Optional[str]:
        """Search for a specific prospect's email using the provided start URL."""
        if not search_email_start_url:
            return None

        headers = self._get_headers()
        
        # Optional mock fallback for demos only.
        if not headers or search_email_start_url.startswith("mock_email_url_"):
            if not self._mock_fallback_enabled():
                return None
            time.sleep(0.5)
            if search_email_start_url.startswith("mock_email_url_"):
                return search_email_start_url.replace("mock_email_url_", "").lower()
            return None

        try:
            # Start email search
            logger.info(f"    [SNOVIO] POST {search_email_start_url}")
            start_data = self._request_json("POST", search_email_start_url, headers=headers)
            if not start_data:
                record_snovio_usage(
                    endpoint="domain-search/prospect-email",
                    domain=domain,
                    status="failed",
                    estimated_credits=0,
                )
                return None
            
            # Case 1: The response already contains email data directly
            if "data" in start_data and isinstance(start_data["data"], dict):
                emails = start_data["data"].get("emails", [])
                if emails:
                    estimated_credits = _billable_finder_credits(emails)
                    selected_email = None
                    for e in emails:
                        email_addr = e.get("email")
                        smtp_status = e.get("smtp_status", "")
                        logger.info(f"    [SNOVIO] Found email directly: {email_addr} (status: {smtp_status})")
                        if smtp_status == "valid":
                            selected_email = email_addr
                            break
                    record_snovio_usage(
                        endpoint="domain-search/prospect-email",
                        domain=domain,
                        email=selected_email,
                        status="completed",
                        result_count=len(emails),
                        estimated_credits=estimated_credits,
                    )
                    if selected_email:
                        return selected_email
                    # Only return verified emails to reduce bounce rate
                    return None
            
            # Case 2: We get a task_hash and need to poll
            task_hash = start_data.get("meta", {}).get("task_hash")
            if not task_hash:
                # Try alternative locations for task_hash
                task_hash = start_data.get("data", {}).get("task_hash") if isinstance(start_data.get("data"), dict) else None
                
            if not task_hash:
                logger.warning(f"    [SNOVIO] No task_hash found in response")
                record_snovio_usage(
                    endpoint="domain-search/prospect-email",
                    domain=domain,
                    status="missing_task_hash",
                    estimated_credits=0,
                )
                return None
                
            fallback_url = f"https://api.snov.io/v2/domain-search/prospects/search-emails/result/{task_hash}"
            result_url = self._result_link(start_data, fallback_url)
            logger.info(f"    [SNOVIO] Polling result at: {result_url}")
            
            res_data = self._poll_result(result_url, headers)
            status = res_data.get("status", "unknown")
            logger.info(f"    [SNOVIO] Email poll status={status}")
            if status == "completed":
                data_obj = res_data.get("data", {})
                if isinstance(data_obj, dict):
                    emails = data_obj.get("emails", [])
                elif isinstance(data_obj, list):
                    emails = data_obj
                else:
                    emails = []
                    
                estimated_credits = _billable_finder_credits(emails)
                selected_email = None
                for e in emails:
                    email_addr = e.get("email")
                    smtp_status = e.get("smtp_status", "")
                    logger.info(f"    [SNOVIO] Found email: {email_addr} (status: {smtp_status})")
                    if smtp_status == "valid":
                        selected_email = email_addr
                        break
                record_snovio_usage(
                    endpoint="domain-search/prospect-email",
                    domain=domain,
                    email=selected_email,
                    status=status,
                    result_count=len(emails),
                    estimated_credits=estimated_credits,
                )
                if selected_email:
                    return selected_email
                # Only return verified emails to reduce bounce rate
                return None

            record_snovio_usage(
                endpoint="domain-search/prospect-email",
                domain=domain,
                status=status,
                result_count=0,
                estimated_credits=0,
            )
                    
        except Exception as e:
            logger.error(f"    [SNOVIO] Exception in get_prospect_email: {e}")
            record_snovio_usage(
                endpoint="domain-search/prospect-email",
                domain=domain,
                status="exception",
                estimated_credits=0,
                metadata={"error": str(e)[:300]},
            )
            return None
                
        return None

    def verify_email(self, email: str) -> str:
        """Verify an email and return its smtp_status."""
        start_url = "https://api.snov.io/v2/email-verification/start"
        headers = self._get_headers()
        if not headers:
            return "unknown"
        form_data = [("emails[]", email)]
        
        try:
            start_data = self._request_json("POST", start_url, headers=headers, data=form_data)
            if not start_data:
                record_snovio_usage(
                    endpoint="email-verification",
                    email=email,
                    status="failed",
                    estimated_credits=0,
                )
                return "unknown"
            task_hash = start_data.get("data", {}).get("task_hash")
            if not task_hash:
                record_snovio_usage(
                    endpoint="email-verification",
                    email=email,
                    status="missing_task_hash",
                    estimated_credits=0,
                )
                return "unknown"
        except Exception as e:
            logger.error(f"Snov.io verify_email exception: {e}")
            record_snovio_usage(
                endpoint="email-verification",
                email=email,
                status="exception",
                estimated_credits=0,
                metadata={"error": str(e)[:300]},
            )
            return "unknown"
            
        fallback_url = f"https://api.snov.io/v2/email-verification/result?task_hash={task_hash}"
        result_url = self._result_link(start_data, fallback_url)
        
        res_data = self._poll_result(result_url, headers)
        if res_data.get("status") == "completed":
            data_list = res_data.get("data", [])
            result_status = "unknown"
            if data_list:
                result_status = data_list[0].get("result", {}).get("smtp_status", "unknown")
            record_snovio_usage(
                endpoint="email-verification",
                email=email,
                status=result_status,
                result_count=len(data_list),
                estimated_credits=1,
            )
            return result_status

        record_snovio_usage(
            endpoint="email-verification",
            email=email,
            status=res_data.get("status", "unknown"),
            result_count=0,
            estimated_credits=1,
        )
                
        return "unknown"

    def get_verified_domain_emails(self, domain: str, limit: int = None) -> List[str]:
        """Return verified company-level emails for a domain.

        Snov.io domain email results are unverified, so every returned address is
        passed through the verifier before it is used by the outbound pipeline.
        """
        headers = self._get_headers()
        if not headers:
            return []

        limit = _int_env("SNOVIO_DOMAIN_EMAIL_FALLBACK_LIMIT", 2, 1, 10) if limit is None else max(1, min(int(limit), 10))
        start_url = "https://api.snov.io/v2/domain-search/domain-emails/start"
        start_data = self._request_json("POST", start_url, headers=headers, data=[("domain", domain)])
        if not start_data:
            return []

        task_hash = start_data.get("meta", {}).get("task_hash")
        fallback_url = f"https://api.snov.io/v2/domain-search/domain-emails/result/{task_hash}" if task_hash else None
        result_url = self._result_link(start_data, fallback_url)
        if not result_url:
            return []

        result_data = self._poll_result(result_url, headers)
        if result_data.get("status") != "completed":
            record_snovio_usage(
                endpoint="domain-search/domain-emails",
                domain=domain,
                status=result_data.get("status", "unknown"),
                estimated_credits=0,
            )
            return []

        verified: List[str] = []
        domain_email_items = result_data.get("data", [])
        record_snovio_usage(
            endpoint="domain-search/domain-emails",
            domain=domain,
            status="completed",
            result_count=len(domain_email_items),
            estimated_credits=len(domain_email_items),
        )

        for item in domain_email_items:
            email = item.get("email") if isinstance(item, dict) else None
            if not email or email in verified:
                continue
            if self.verify_email(email) == "valid":
                verified.append(email)
                if len(verified) >= limit:
                    break

        return verified
