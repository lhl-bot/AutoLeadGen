import requests
import logging
from typing import Any, List, Dict, Optional

logger = logging.getLogger("outbound_engine")

class ApolloClient:
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.session = requests.Session()
        self.session.headers.update({
            "Cache-Control": "no-cache",
            "Content-Type": "application/json",
            "accept": "application/json",
            "X-Api-Key": api_key
        })

    def search_people(self, keywords: str, titles: List[str], page: int = 1, per_page: int = 25) -> List[Dict]:
        """
        Search for people matching keywords and job titles using Apollo's mixed_people/api_search.
        This endpoint is optimized for searching and does not consume credits.
        """
        url = "https://api.apollo.io/api/v1/mixed_people/api_search"
        payload = {
            "q_keywords": keywords,
            "page": page,
            "per_page": per_page
        }
        if titles:
            payload["person_titles"] = titles

        try:
            logger.info(f"[APOLLO] Searching people for keywords: '{keywords}', titles: {titles}, page: {page}")
            resp = self.session.post(url, json=payload, timeout=30)
            if resp.status_code != 200:
                logger.error(f"[APOLLO] Search API Error {resp.status_code}: {resp.text}")
                return []
                
            data = resp.json()
            people = data.get("people", [])
            logger.info(f"[APOLLO] Found {len(people)} people on page {page}")
            return people
            
        except Exception as e:
            logger.error(f"[APOLLO] Exception in search_people: {e}")
            return []

    def enrich_person(self, person_id: str) -> Optional[Dict]:
        """
        Enrich a person's profile to get their email address using Apollo's people/match.
        WARNING: This endpoint consumes Apollo export/enrichment credits.
        """
        url = "https://api.apollo.io/api/v1/people/match"
        payload = {
            "id": person_id,
            "reveal_personal_emails": False,
            "reveal_phone_number": False
        }

        try:
            logger.info(f"[APOLLO] Enriching person ID: {person_id} to fetch email")
            resp = self.session.post(url, json=payload, timeout=30)
            if resp.status_code != 200:
                logger.error(f"[APOLLO] Enrich API Error {resp.status_code}: {resp.text}")
                return None
                
            data = resp.json()
            person = data.get("person")
            return person
            
        except Exception as e:
            logger.error(f"[APOLLO] Exception in enrich_person: {e}")
            return None

    def get_usage_stats(self) -> Optional[Dict[str, Any]]:
        """
        View Apollo API usage stats and rate limits.
        Requires an Apollo master API key.
        """
        url = "https://api.apollo.io/api/v1/usage_stats/api_usage_stats"
        headers = dict(self.session.headers)
        headers["Authorization"] = f"Bearer {self.api_key}"

        try:
            resp = self.session.post(url, headers=headers, json={}, timeout=8)
            if resp.status_code != 200:
                logger.error(f"[APOLLO] Usage stats API Error {resp.status_code}: {resp.text[:300]}")
                return {
                    "error": f"HTTP {resp.status_code}",
                    "status_code": resp.status_code,
                }
            return resp.json()
        except Exception as e:
            logger.error(f"[APOLLO] Exception in get_usage_stats: {e}")
            return {"error": str(e)}
