import os
import json
import asyncio
import logging
from bs4 import BeautifulSoup
import httpx
from database import SessionLocal
from models import Lead, LeadBrief

logger = logging.getLogger("research_agent")
logger.setLevel(logging.INFO)
ch = logging.StreamHandler()
ch.setFormatter(logging.Formatter("[RESEARCH AGENT] %(message)s"))
if not logger.handlers:
    logger.addHandler(ch)

async def _fetch_html(url: str) -> str:
    """Basic HTTP fetch with common headers to avoid blocks."""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
    }
    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            resp = await client.get(url, headers=headers)
            resp.raise_for_status()
            return resp.text
    except Exception as e:
        logger.warning(f"Failed to fetch {url}: {e}")
        return ""

async def scrape_company_data(domain: str) -> str:
    """
    Attempts to scrape the homepage and up to 2 subpages (like About/Products) to gather company info.
    """
    from urllib.parse import urljoin
    url = f"https://{domain}"
    if not url.startswith("http"):
        url = "https://" + url

    html = await _fetch_html(url)
    if not html:
        return f"Could not access website for {domain}."

    soup = BeautifulSoup(html, 'html.parser')
    
    # Find links that might be useful, expanding scope
    useful_links = []
    for a in soup.find_all('a', href=True):
        href = a['href'].lower()
        if any(kw in href for kw in ['about', 'product', 'service', 'solution', 'news', 'blog', 'team', 'partner', 'brand', 'collection', 'catalog', 'range']):
            full_url = urljoin(url, a['href'])
            if full_url.startswith(url) and full_url not in useful_links:
                useful_links.append(full_url)
                
    # Extract meta info for extra context
    meta_desc = soup.find('meta', attrs={'name': 'description'})
    title = soup.find('title')
    meta_info = f"Title: {title.string if title else 'N/A'}\nDescription: {meta_desc['content'] if meta_desc and meta_desc.has_attr('content') else 'N/A'}\n\n"

    # Remove script and style elements from home page
    for script in soup(["script", "style", "nav", "footer"]):
        script.extract()
    home_text = soup.get_text(separator=' ', strip=True)
    
    combined_text = f"--- HOMEPAGE META ---\n{meta_info}--- HOMEPAGE ---\n{home_text[:3000]}\n"
    
    # Prioritize product and news pages
    product_links = [l for l in useful_links if 'product' in l.lower() or 'collection' in l.lower() or 'catalog' in l.lower()]
    other_links = [l for l in useful_links if l not in product_links]
    ordered_links = product_links[:2] + other_links[:2]
    
    # Scrape up to 3 subpages (increased from 2)
    for sub_url in ordered_links[:3]:
        sub_html = await _fetch_html(sub_url)
        if sub_html:
            sub_soup = BeautifulSoup(sub_html, 'html.parser')
            for script in sub_soup(["script", "style", "nav", "footer"]):
                script.extract()
            sub_text = sub_soup.get_text(separator=' ', strip=True)
            combined_text += f"\n--- {sub_url} ---\n{sub_text[:2000]}\n"

    # limit to first 8000 chars to provide rich context but fit in context window
    return combined_text[:8000]

async def generate_brief_from_llm(domain: str, scraped_text: str) -> dict:
    """Uses LLM to analyze the scraped text and generate a structured brief."""
    import requests
    import re
    llm_key = os.environ.get("LLM_API_KEY") or os.environ.get("MINIMAX_API_KEY")
    if not llm_key:
        raise RuntimeError("LLM_API_KEY or MINIMAX_API_KEY must be set in .env")
    llm_url = os.environ.get("LLM_BASE_URL", "https://api.minimaxi.com/v1/chat/completions")
    llm_model = os.environ.get("LLM_MODEL", "MiniMax-M2.7-highspeed")
    
    system_prompt = """You are an expert B2B Sales Researcher. 
Analyze the provided text scraped from a target company's website.
Return a JSON object with the following keys:
- "company_overview": A 2-sentence summary of what they do.
- "specific_products": List 2-3 specific product names, materials, or product lines they sell (use exact names from their site). If none found, return "None found".
- "recent_activity": Any recent launches, events, partnerships, or awards mentioned. If none found, return "None found".
- "pain_points": 2-3 likely business pain points they might face.
- "personalization_hook": One specific, concrete detail from their website that a salesperson could reference in the first sentence of an email to show they did their homework (e.g., a specific product name, a recent blog post topic, a new market they entered, a unique feature they advertise).
- "value_proposition_alignment": How a generic B2B solution might help them.
IMPORTANT: Return ONLY a valid JSON string without markdown formatting."""

    user_prompt = f"Target Domain: {domain}\n\nWebsite Content:\n{scraped_text}"

    headers = {
        "Authorization": f"Bearer {llm_key}",
        "Content-Type": "application/json"
    }
    
    payload = {
        "model": llm_model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ]
    }

    try:
        response = await asyncio.to_thread(
            requests.post,
            llm_url,
            headers=headers,
            json=payload,
            timeout=60
        )
        response.raise_for_status()
        data = response.json()
        
        choices = data.get("choices")
        if choices and isinstance(choices, list) and len(choices) > 0:
            msg = choices[0].get("message")
            if msg and isinstance(msg, dict):
                content_text = msg.get("content", "").strip()
                content_text = re.sub(r'<think>.*?</think>', '', content_text, flags=re.DOTALL).strip()
                # Clean markdown blocks if present
                content_text = content_text.replace('```json', '').replace('```', '').strip()
                return json.loads(content_text)
                
        raise ValueError("Invalid response from LLM")
    except Exception as e:
        logger.error(f"Error generating brief from LLM for {domain}: {e}")
        return {
            "company_overview": "Information unavailable.",
            "pain_points": "Unknown.",
            "recent_news": "None found.",
            "value_proposition_alignment": "Unknown."
        }

def _normalize_brief_data(brief_data: dict) -> dict:
    pain_points = brief_data.get("pain_points", "")
    if isinstance(pain_points, list):
        pain_points = ", ".join(str(p) for p in pain_points)
        
    specific_products = brief_data.get("specific_products", "")
    if isinstance(specific_products, list):
        specific_products = ", ".join(str(p) for p in specific_products)

    return {
        "company_overview": brief_data.get("company_overview", ""),
        "pain_points": pain_points,
        "recent_news": brief_data.get("recent_news", brief_data.get("recent_activity", "")),
        "value_proposition_alignment": brief_data.get("value_proposition_alignment", ""),
        "specific_products": specific_products,
        "recent_activity": brief_data.get("recent_activity", ""),
        "personalization_hook": brief_data.get("personalization_hook", ""),
    }

async def research_company(domain: str) -> dict:
    """Scrape and analyze a company domain without writing sandbox data to the database."""
    logger.info(f"Running sandbox research for {domain}...")
    scraped_text = await scrape_company_data(domain)
    brief_data = await generate_brief_from_llm(domain, scraped_text)
    return _normalize_brief_data(brief_data)

async def build_and_save_lead_brief(lead_id: int, domain: str) -> bool:
    """
    Main entry point: Scrapes the site, asks LLM for a brief, and saves it to DB.
    """
    db = SessionLocal()
    try:
        # Check if already exists
        existing = db.query(LeadBrief).filter(LeadBrief.lead_id == lead_id).first()
        if existing:
            return True

        logger.info(f"Building brief for {domain} (Lead {lead_id})...")
        brief_data = await research_company(domain)

        new_brief = LeadBrief(
            lead_id=lead_id,
            company_overview=brief_data.get("company_overview", ""),
            pain_points=brief_data.get("pain_points", ""),
            recent_news=brief_data.get("recent_news", ""),
            value_proposition_alignment=brief_data.get("value_proposition_alignment", ""),
            specific_products=brief_data.get("specific_products", ""),
            recent_activity=brief_data.get("recent_activity", ""),
            personalization_hook=brief_data.get("personalization_hook", "")
        )
        db.add(new_brief)
        db.commit()
        logger.info(f"Successfully saved brief for {domain}.")
        return True
    except Exception as e:
        logger.error(f"Failed to build brief for lead {lead_id}: {e}")
        return False
    finally:
        db.close()
