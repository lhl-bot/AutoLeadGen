import os
import json
import asyncio
import logging
from bs4 import BeautifulSoup
import httpx
from database import SessionLocal
from models import CustomerPersona, EmailLog, Lead, LeadBrief, Workflow
from services.research_quality import (
    assess_research,
    sanitize_brief_data,
    target_terms_for,
    utcnow,
)

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
        max_attempts = max(1, int(os.environ.get("RESEARCH_LLM_MAX_RETRIES", "3")))
    except (TypeError, ValueError):
        max_attempts = 3

    last_err = None
    for attempt in range(1, max_attempts + 1):
        try:
            response = await asyncio.to_thread(
                requests.post,
                llm_url,
                headers=headers,
                json=payload,
                timeout=60
            )
            # Retry transient throttling / server errors instead of saving an empty brief.
            if response.status_code == 429 or response.status_code >= 500:
                last_err = f"HTTP {response.status_code}"
                if attempt < max_attempts:
                    await asyncio.sleep(min(2 ** attempt, 20))
                    continue
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
        except requests.RequestException as e:
            # Network/throttle error — back off and retry.
            last_err = str(e)
            if attempt < max_attempts:
                await asyncio.sleep(min(2 ** attempt, 20))
                continue
            break
        except Exception as e:
            # Parsing / non-retryable error.
            last_err = str(e)
            break

    logger.error(f"Error generating brief from LLM for {domain}: {last_err}")
    return {
        "company_overview": "Information unavailable.",
        "pain_points": "Unknown.",
        "recent_news": "None found.",
        "value_proposition_alignment": "Unknown."
    }

def _normalize_brief_data(brief_data: dict) -> dict:
    normalized, _ = sanitize_brief_data(brief_data)
    return normalized

async def research_company(domain: str) -> dict:
    """Scrape and analyze a company domain without writing sandbox data to the database."""
    logger.info(f"Running sandbox research for {domain}...")
    scraped_text = await scrape_company_data(domain)
    brief_data = await generate_brief_from_llm(domain, scraped_text)
    return _normalize_brief_data(brief_data)

async def build_and_save_lead_brief(lead_id: int, domain: str, *, force: bool = False) -> bool:
    """
    Main entry point: Scrapes the site, asks LLM for a brief, and saves it to DB.
    """
    db = SessionLocal()
    try:
        lead = db.query(Lead).filter(Lead.id == lead_id).first()
        if not lead:
            return False
        workflow = db.query(Workflow).filter(Workflow.id == lead.workflow_id).first() if lead.workflow_id else None
        persona = None
        if workflow and workflow.persona_id:
            persona = db.query(CustomerPersona).filter(CustomerPersona.id == workflow.persona_id).first()
        target_terms = target_terms_for(workflow, persona)
        source_labels = [part.strip() for part in (lead.data_sources or "").split(",") if part.strip()]

        # Existing briefs are classified once without repeating paid/LLM work.
        existing = db.query(LeadBrief).filter(LeadBrief.lead_id == lead_id).first()
        if existing and not force:
            if not existing.research_status or existing.research_status == "pending":
                existing_data = {
                    "company_overview": existing.company_overview,
                    "pain_points": existing.pain_points,
                    "recent_news": existing.recent_news,
                    "value_proposition_alignment": existing.value_proposition_alignment,
                    "specific_products": existing.specific_products,
                    "recent_activity": existing.recent_activity,
                    "personalization_hook": existing.personalization_hook,
                }
                cleaned, placeholder_flags = sanitize_brief_data(existing_data)
                assessment = assess_research(
                    domain=domain,
                    brief_data=cleaned,
                    target_terms=target_terms,
                    source_labels=source_labels,
                )
                for field, value in cleaned.items():
                    setattr(existing, field, value)
                existing.research_status = assessment.status
                existing.quality_flags = list(dict.fromkeys(placeholder_flags + assessment.flags))
                existing.evidence_sources = assessment.evidence_sources
                existing.researched_at = utcnow()
                db.commit()
            return existing.research_status == "valid"

        logger.info(f"Building brief for {domain} (Lead {lead_id})...")
        scraped_text = await scrape_company_data(domain)
        raw_brief_data = await generate_brief_from_llm(domain, scraped_text)
        brief_data, placeholder_flags = sanitize_brief_data(raw_brief_data)
        assessment = assess_research(
            domain=domain,
            brief_data=brief_data,
            target_terms=target_terms,
            scraped_text=scraped_text,
            source_labels=source_labels,
        )

        new_brief = existing or LeadBrief(lead_id=lead_id)
        new_brief.company_overview = brief_data.get("company_overview")
        new_brief.pain_points = brief_data.get("pain_points")
        new_brief.recent_news = brief_data.get("recent_news")
        new_brief.value_proposition_alignment = brief_data.get("value_proposition_alignment")
        new_brief.specific_products = brief_data.get("specific_products")
        new_brief.recent_activity = brief_data.get("recent_activity")
        new_brief.personalization_hook = brief_data.get("personalization_hook")
        new_brief.research_status = assessment.status
        new_brief.quality_flags = list(dict.fromkeys(placeholder_flags + assessment.flags))
        new_brief.evidence_sources = assessment.evidence_sources
        new_brief.researched_at = utcnow()
        if not existing:
            db.add(new_brief)
        try:
            db.commit()
            logger.info(f"Successfully saved brief for {domain}.")
            return assessment.status == "valid"
        except Exception as commit_exc:
            db.rollback()
            # Double check if someone else inserted it in the meantime
            existing_again = db.query(LeadBrief).filter(LeadBrief.lead_id == lead_id).first()
            if existing_again:
                logger.info(f"Brief for {domain} (Lead {lead_id}) was saved by another thread/task concurrently.")
                return True
            else:
                raise commit_exc
    except Exception as e:
        logger.error(f"Failed to build brief for lead {lead_id}: {e}")
        return False
    finally:
        db.close()


async def refresh_lead_research(lead_id: int) -> bool:
    """Force one human-requested research refresh without any contact lookup."""
    db = SessionLocal()
    try:
        lead = db.query(Lead).filter(Lead.id == lead_id).first()
        if not lead or not lead.domain:
            return False
        domain = lead.domain
    finally:
        db.close()

    is_valid = await build_and_save_lead_brief(lead_id, domain, force=True)
    db = SessionLocal()
    try:
        lead = db.query(Lead).filter(Lead.id == lead_id).first()
        if not lead:
            return False
        has_outbound = db.query(Lead).filter(
            Lead.id == lead_id,
            Lead.email_logs.any(EmailLog.direction == "outbound"),
        ).first() is not None
        if is_valid:
            lead.automation_block_reason = None
            lead.automation_blocked_at = None
            if not has_outbound and lead.status == "needs_research":
                lead.status = "found"
        else:
            lead.automation_block_reason = "research_not_valid"
            lead.automation_blocked_at = utcnow()
            if not has_outbound:
                lead.status = "needs_research"
        db.commit()
        return is_valid
    finally:
        db.close()
