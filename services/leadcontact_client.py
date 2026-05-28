"""
LeadContact API client — email, phone, and employee search via LinkedIn profiles.

API docs: https://api.leadcontact.ai/doc
"""
import logging
import os
from typing import Optional, Dict, Any, List
import requests

logger = logging.getLogger(__name__)

BASE_URL = "https://api.leadcontact.ai"
REQUEST_TIMEOUT = 30  # seconds


class LeadContactClient:
    """Thin wrapper around the LeadContact REST API."""

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.environ.get("LEADCONTACT_API_KEY", "")
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        })

    # ── core request helper ──────────────────────────────────────

    def _post(self, path: str, body: Dict[str, Any]) -> Optional[Dict]:
        try:
            resp = self.session.post(
                f"{BASE_URL}{path}",
                json=body,
                timeout=REQUEST_TIMEOUT,
            )
            if resp.status_code == 200:
                data = resp.json()
                if data.get("code") == 200:
                    return data.get("data")
                logger.warning(f"LeadContact {path} business error: {data.get('msg')}")
            else:
                logger.warning(f"LeadContact {path} HTTP {resp.status_code}: {resp.text[:300]}")
        except requests.RequestException as e:
            logger.error(f"LeadContact {path} request failed: {e}")
        return None

    def _get(self, path: str, params: Optional[Dict] = None) -> Optional[Dict]:
        try:
            resp = self.session.get(
                f"{BASE_URL}{path}",
                params=params,
                timeout=REQUEST_TIMEOUT,
            )
            if resp.status_code == 200:
                data = resp.json()
                if data.get("code") == 200:
                    return data.get("data")
                logger.warning(f"LeadContact {path} business error: {data.get('msg')}")
            else:
                logger.warning(f"LeadContact {path} HTTP {resp.status_code}: {resp.text[:300]}")
        except requests.RequestException as e:
            logger.error(f"LeadContact {path} request failed: {e}")
        return None

    # ── public API methods ────────────────────────────────────────

    def query_email(self, linkedin_url: str) -> Optional[str]:
        """Return a verified email for a LinkedIn profile (10 credits)."""
        data = self._post("/api/rest/email/query", {"profileUrl": linkedin_url})
        if not data:
            return None
        sources = data.get("sources", [])
        for src in sources:
            if src.get("valid") and src.get("email"):
                return src["email"]
        # Fallback: return first email even if not marked valid
        for src in sources:
            if src.get("email"):
                return src["email"]
        return None

    def query_email_with_validation(self, linkedin_url: str) -> Dict[str, Any]:
        """Return email + validation flag (10 credits)."""
        data = self._post("/api/rest/email/query", {"profileUrl": linkedin_url})
        if not data:
            return {"email": None, "valid": False, "error": "api_failed"}
        sources = data.get("sources", [])
        for src in sources:
            if src.get("email"):
                return {
                    "email": src["email"],
                    "valid": bool(src.get("valid")),
                    "source": src.get("name", "leadcontact"),
                }
        return {"email": None, "valid": False, "error": "not_found"}

    def query_phone(self, linkedin_url: str) -> Dict[str, Any]:
        """Return phone numbers for a LinkedIn profile (30 credits)."""
        data = self._post("/api/rest/phone/query", {"profileUrl": linkedin_url})
        if not data:
            return {"phones": [], "error": "api_failed"}
        sources = data.get("sources", [])
        phones = []
        for src in sources:
            if src.get("phone"):
                phones.append({
                    "phone": src["phone"],
                    "valid": bool(src.get("valid")),
                    "source": src.get("name", "leadcontact"),
                })
        return {"phones": phones}

    def search_employees(
        self,
        job_titles: Optional[List[str]] = None,
        companies: Optional[List[str]] = None,
        domains: Optional[List[str]] = None,
        locations: Optional[List[str]] = None,
        industries: Optional[List[str]] = None,
        company_sizes: Optional[List[str]] = None,
        seniorities: Optional[List[str]] = None,
        skills: Optional[List[str]] = None,
        keyword: str = "",
        current_titles_only: bool = True,
        page_token: str = "",
        per_page: int = 25,
    ) -> Dict[str, Any]:
        """Search for employees matching criteria (5 credits per result page)."""
        body: Dict[str, Any] = {
            "currentTitlesOnly": current_titles_only,
            "includeRelatedJobTitles": False,
            "companyFilter": "current",
        }
        if job_titles:
            body["jobTitle"] = job_titles
        if companies:
            body["company"] = companies
        if domains:
            body["domain"] = domains
        if locations:
            body["location"] = locations
        if industries:
            body["industry"] = industries
        if company_sizes:
            body["companySize"] = company_sizes
        if seniorities:
            body["seniority"] = seniorities
        if skills:
            body["skills"] = skills
        if keyword:
            body["keyword"] = keyword
        if page_token:
            body["nextPageToken"] = page_token

        data = self._post("/api/rest/employess/query/advanced", body)
        if not data:
            return {"employees": [], "total_count": 0, "next_page_token": "", "error": "api_failed"}

        employees = data.get("employees", [])
        return {
            "employees": employees,
            "total_count": data.get("totalEmployeeCount", 0),
            "next_page_token": data.get("nextPageToken", ""),
            "current_page": data.get("currentPage", 1),
        }

    def get_employee_profile(self, linkedin_url: str) -> Optional[Dict[str, Any]]:
        """Get detailed profile for a single LinkedIn URL (5 credits)."""
        return self._get(
            "/api/rest/employess/query/linkedin",
            params={"linkedin_url": linkedin_url},
        )

    def get_credits(self) -> int:
        """Return remaining API credits."""
        try:
            resp = self.session.get(
                f"{BASE_URL}/api/rest/credits",
                timeout=REQUEST_TIMEOUT,
            )
            if resp.status_code == 200:
                data = resp.json()
                if data.get("code") == 200 and data.get("data"):
                    return data["data"].get("remainingPoints", 0)
        except requests.RequestException as e:
            logger.error(f"LeadContact credits check failed: {e}")
        return 0