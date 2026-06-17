import requests
import logging
from typing import List, Dict, Any, Optional
from services.http_client import http as _http

logger = logging.getLogger("leadcontact_client")
logger.setLevel(logging.INFO)

class LeadContactClient:
    """
    Client for interacting with the LeadContact API to search contacts,
    query phone numbers, and emails using LinkedIn profile URLs.
    """
    
    def __init__(self, token: str):
        self.token = token.strip()
        self.base_url = "https://api.leadcontact.ai/api/rest"
        self.headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
            "Accept": "application/json"
        }

    def get_credits(self) -> int:
        """
        Query remaining API points.
        Endpoint: GET /credits
        """
        url = f"{self.base_url}/credits"
        try:
            logger.info(f"[LeadContact] Checking remaining credits...")
            resp = requests.get(url, headers=self.headers, timeout=5)
            if resp.status_code == 200:
                data = resp.json()
                points = data.get("data", {}).get("remainingPoints", 0)
                logger.info(f"[LeadContact] Remaining points: {points}")
                return int(points)
            else:
                logger.error(f"[LeadContact] Credits API error {resp.status_code}: {resp.text}")
                return 0
        except Exception as e:
            logger.error(f"[LeadContact] Exception in get_credits: {e}")
            return 0

    def get_credit_details(self) -> Dict[str, Any]:
        """
        Query remaining API points with error context for admin dashboards.
        Endpoint: GET /credits
        """
        url = f"{self.base_url}/credits"
        try:
            logger.info("[LeadContact] Checking remaining credits...")
            resp = _http.get(url, headers=self.headers, timeout=15)
            if resp.status_code == 200:
                return resp.json()
            logger.error(f"[LeadContact] Credits API error {resp.status_code}: {resp.text[:300]}")
            return {
                "error": f"HTTP {resp.status_code}",
                "status_code": resp.status_code,
            }
        except Exception as e:
            logger.error(f"[LeadContact] Exception in get_credit_details: {e}")
            return {"error": str(e)}

    def query_email_with_validation(self, linkedin_url: str) -> Dict[str, Any]:
        """
        Query email based on LinkedIn profile URL.
        Endpoint: POST /email/query
        Returns: {"email": str or None, "valid": bool, "error": str or None}
        """
        url = f"{self.base_url}/email/query"
        payload = {
            "profileUrl": linkedin_url
        }
        try:
            logger.info(f"[LeadContact] Querying email for URL: {linkedin_url}")
            resp = _http.post(url, headers=self.headers, json=payload, timeout=20)
            if resp.status_code == 200:
                data = resp.json()
                sources = data.get("data", {}).get("sources", [])
                if sources:
                    best_source = sources[0]  # Take the first matched email source
                    return {
                        "email": best_source.get("email"),
                        "valid": bool(best_source.get("valid", False)),
                        "error": None
                    }
                return {"email": None, "valid": False, "error": "No email found"}
            else:
                logger.error(f"[LeadContact] Email query API error {resp.status_code}: {resp.text}")
                return {"email": None, "valid": False, "error": f"API error {resp.status_code}"}
        except Exception as e:
            logger.error(f"[LeadContact] Exception in query_email_with_validation: {e}")
            return {"email": None, "valid": False, "error": str(e)}

    def query_phone(self, linkedin_url: str) -> Dict[str, Any]:
        """
        Query phone numbers based on LinkedIn profile URL.
        Endpoint: POST /phone/query
        Returns: {"phones": [{"phone": str, "valid": bool}], "error": str or None}
        """
        url = f"{self.base_url}/phone/query"
        payload = {
            "profileUrl": linkedin_url
        }
        try:
            logger.info(f"[LeadContact] Querying phone for URL: {linkedin_url}")
            resp = _http.post(url, headers=self.headers, json=payload, timeout=20)
            if resp.status_code == 200:
                data = resp.json()
                sources = data.get("data", {}).get("sources", [])
                phones = []
                for s in sources:
                    if s.get("phone"):
                        phones.append({
                            "phone": s.get("phone"),
                            "valid": bool(s.get("valid", False))
                        })
                return {
                    "phones": phones,
                    "error": None if phones else "No phone numbers found"
                }
            else:
                logger.error(f"[LeadContact] Phone query API error {resp.status_code}: {resp.text}")
                return {"phones": [], "error": f"API error {resp.status_code}"}
        except Exception as e:
            logger.error(f"[LeadContact] Exception in query_phone: {e}")
            return {"phones": [], "error": str(e)}

    def search_employees(
        self,
        job_titles: Optional[List[str]] = None,
        locations: Optional[List[str]] = None,
        industries: Optional[List[str]] = None,
        keyword: Optional[str] = None,
        current_titles_only: bool = True,
        per_page: int = 10,
        next_page_token: Optional[str] = None,
        company_size: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """
        Advanced employee search using filters.
        Endpoint: POST /employess/query/advanced

        Pagination is cursor-based: the response returns ``nextPageToken``; pass it
        back as ``next_page_token`` to fetch the next page (empty token = no more
        pages). See the LeadContact API docs.
        """
        # Note the spelling "/employess/query/advanced" is used as per API doc.
        url = f"{self.base_url}/employess/query/advanced"
        payload = {}

        if job_titles:
            payload["jobTitle"] = job_titles
        if locations:
            payload["location"] = locations
        if industries:
            payload["industry"] = industries
        if company_size:
            payload["companySize"] = company_size
        if keyword:
            payload["keyword"] = keyword
        if next_page_token:
            payload["nextPageToken"] = next_page_token

        payload["currentTitlesOnly"] = current_titles_only
        payload["perPage"] = per_page
        
        try:
            logger.info(f"[LeadContact] Searching employees: payload={payload}")
            resp = _http.post(url, headers=self.headers, json=payload, timeout=30)
            if resp.status_code == 200:
                data = resp.json()
                # Return the data object which contains 'employees', 'totalEmployeeCount', etc.
                return data.get("data", {})
            else:
                logger.error(f"[LeadContact] Employee search API error {resp.status_code}: {resp.text}")
                return {"employees": [], "error": f"API error {resp.status_code}"}
        except Exception as e:
            logger.error(f"[LeadContact] Exception in search_employees: {e}")
            return {"employees": [], "error": str(e)}
