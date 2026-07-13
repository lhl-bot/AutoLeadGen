import os
import requests
import re
import logging
from typing import Optional
from services.http_client import http as _http
from sqlalchemy.orm import Session
import models

logger = logging.getLogger(__name__)

LLM_API_KEY = os.environ.get("LLM_API_KEY", os.environ.get("MINIMAX_API_KEY", ""))
LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "https://api.minimaxi.com/v1/chat/completions")
LLM_MODEL = os.environ.get("LLM_MODEL", "MiniMax-M2.7-highspeed")
# Note: The actual endpoint might vary based on MiniMax documentation, assuming v2 chat completion.

# Ordered country list used for deterministic search-keyword rotation.
# Each cycle, the engine moves to the next country in this list (wrapping around).
# Order is curated by market relevance for padel/sports B2B sourcing.
TARGET_COUNTRIES = [
    "Spain", "Italy", "Germany", "France", "Netherlands",
    "Belgium", "Portugal", "Sweden", "Denmark", "Norway",
    "Finland", "Poland", "Austria", "Switzerland", "Ireland",
    "United Kingdom", "Czech Republic", "Greece", "Romania", "Hungary",
    "Mexico", "Argentina", "Brazil", "Chile", "Colombia",
    "United States", "Canada", "Australia", "UAE", "Saudi Arabia",
]


def rotate_country_in_keyword(current_keyword: str) -> str:
    """Deterministically rotate the country qualifier in a search keyword.

    Finds the current country (longest-match, word-bounded) and replaces it with
    the next country in TARGET_COUNTRIES. If no country is present, appends the
    first one. Guarantees diversity instead of relying on an LLM to pick.
    """
    if not current_keyword:
        return current_keyword

    sorted_countries = sorted(TARGET_COUNTRIES, key=len, reverse=True)
    current_country = None
    for country in sorted_countries:
        pattern = re.compile(r'\b' + re.escape(country) + r'\b', re.IGNORECASE)
        if pattern.search(current_keyword):
            current_country = country
            break

    base_keyword = current_keyword
    for country in sorted_countries:
        base_keyword = re.sub(r'\b' + re.escape(country) + r'\b', ' ', base_keyword, flags=re.IGNORECASE)
    base_keyword = re.sub(r'\s+', ' ', base_keyword).strip(" ,;-")

    if current_country:
        idx = TARGET_COUNTRIES.index(current_country)
        next_country = TARGET_COUNTRIES[(idx + 1) % len(TARGET_COUNTRIES)]
    else:
        next_country = TARGET_COUNTRIES[0]

    return f"{base_keyword} {next_country}".strip()


def build_persona_few_shot(db: Session, persona_id: int) -> str:
    """Build a few-shot prompt fragment from historically successful emails for a persona.

    This performs all DB access up-front so callers can close their session
    *before* the slow LLM call, avoiding holding a MySQL connection idle for the
    full generation (which triggers "Lost connection during query" timeouts).
    """
    if not (db and persona_id):
        return ""
    try:
        successful_leads = (
            db.query(models.Lead)
            .join(models.Workflow, models.Lead.workflow_id == models.Workflow.id)
            .filter(
                models.Workflow.persona_id == persona_id,
                models.Lead.ai_draft.isnot(None),
                models.Lead.ai_draft != "",
                (models.Lead.user_rating == 'positive')
                | (models.Lead.reply_intent.in_(("interested", "more_info")))
                | ((models.Lead.status == 'replied') & (models.Lead.reply_intent.is_(None)))
            )
            .order_by(models.Lead.id.desc())
            .limit(2)
            .all()
        )

        examples_text = []
        for i, lead in enumerate(successful_leads, 1):
            brief = db.query(models.LeadBrief).filter(models.LeadBrief.lead_id == lead.id).first()
            brief_summary = brief.company_overview if brief else "N/A"
            examples_text.append(
                f"Example {i} (High Response Rate):\n"
                f"- Recipient: {lead.first_name} ({lead.job_title}) at {lead.company_name}\n"
                f"- Company Research:\n{brief_summary}\n"
                f"- Generated Email:\n{lead.ai_draft}\n"
            )

        if examples_text:
            return (
                "\n\nTo ensure tone consistency and maintain high reply rates, reference these historical cold emails sent under this persona that successfully generated positive responses:\n\n"
                + "\n---\n".join(examples_text)
                + "\n---\nWrite the email for the new recipient following a similar tone, brevity, and structure as the examples above."
            )
    except Exception as ex:
        logger.warning(f"Error querying successful examples for few-shot email: {ex}")
    return ""


def generate_email(
    first_name: str,
    last_name: str,
    company_name: str,
    target_role: str,
    website_summary: str,
    template: str,
    db: Optional[Session] = None,
    persona_id: Optional[int] = None,
    few_shot_prompt: Optional[str] = None,
) -> str:
    """Generate personalized cold email using MiniMax API.

    Pass a precomputed ``few_shot_prompt`` (via :func:`build_persona_few_shot`)
    so the caller can release its DB connection before this slow LLM call. When
    ``few_shot_prompt`` is not supplied, it is built lazily from ``db``/``persona_id``
    for backward compatibility.
    """

    # Build greeting
    greeting_name = first_name.strip() if first_name and first_name.strip() else "there"

    # Few-Shot prompting: prefer precomputed fragment; otherwise build from DB.
    if few_shot_prompt is None:
        few_shot_prompt = build_persona_few_shot(db, persona_id) if (db and persona_id) else ""

    system_prompt = """You are an elite B2B sales copywriter. You write short, crisp, highly engaging emails that read like they were written by a thoughtful colleague. Your tone is professional yet casual, direct, and completely devoid of corporate fluff, marketing buzzwords, or cheesy sales pitches. You focus on starting conversations, not pitching products on the first touch."""

    prompt = f"""Write a highly personalized, natural B2B cold email.

RECIPIENT INFO:
- Name: {greeting_name}
- Role: {target_role}
- Company: {company_name}

DETAILED COMPANY RESEARCH:
{website_summary}

SENDER CONTEXT / PITCH:
{template}

MANDATORY RULES:
1. FORMAT: Output MUST strictly follow this structure:
   Line 1: Subject: <specific, intriguing subject line>
   Line 2: (empty)
   Line 3+: Email body

2. GREETING: Start the body with "Hi {greeting_name},"

3. NATURAL TONE & BREVITY:
   - Max 75 words. Short emails get replied to.
   - Speak like a real human. No buzzwords ("synergy", "revolutionize", "cutting-edge", "game-changing", "seamless", "delighted").
   - Do NOT start with "I hope this email finds you well" or "My name is... and I am...".
   - Start directly with an observation about their company/work.

4. ORGANIC PERSONALIZATION:
   - Reference a specific detail from the company research (e.g., a specific product line, a recent project, or their customer demographic) in a very natural way.
   - Do NOT make it feel forced. Avoid generic compliments like "I was impressed by your work".
   - GOOD: "I saw you recently launched the [Product Name]." or "Since you guys carry [Specific Product Group]..."

5. CALL TO ACTION:
   - A single, low-friction, casual question (e.g., "Are you open to a quick email with our catalog, or is this not on your radar right now?" or "Do you have any capacity for new supplier trials next month?").

6. SUBJECT LINE:
   - Make it specific and curiosity-inducing.
   - NEVER use generic clickbait like "Partnership opportunity", "Introduction", or "Collaboration".
   - GOOD: "Question about {company_name}'s sourcing" or "Quick question on your [Specific Product Line]"

7. NO SIGNATURE & NO PLACEHOLDERS:
   - Do NOT include any closing sign-off (e.g., "Best regards", "Thanks") or name/title. The email body must end immediately after the final sentence.
   - Never use placeholder brackets like [Name], [Your Name], etc.
   - Write in English."""

    if few_shot_prompt:
        prompt += few_shot_prompt
    
    headers = {
        "Authorization": f"Bearer {LLM_API_KEY}",
        "Content-Type": "application/json"
    }
    
    payload = {
        "model": LLM_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt}
        ]
    }
    
    try:
        response = _http.post(LLM_BASE_URL, headers=headers, json=payload, timeout=120)
        response.raise_for_status()
        data = response.json()
        if "base_resp" in data and data["base_resp"] and data["base_resp"].get("status_code", 0) != 0:
            logger.error(f"LLM API Error: {data['base_resp'].get('status_msg', 'Unknown error')}")
            return ""
            
        choices = data.get("choices")
        if choices and isinstance(choices, list) and len(choices) > 0:
            msg = choices[0].get("message")
            if msg and isinstance(msg, dict):
                content_text = msg.get("content", "").strip()
                content_text = re.sub(r'<think>.*?</think>', '', content_text, flags=re.DOTALL).strip()
                return content_text
        logger.error(f"LLM API unexpected response: {data}")
        return ""
    except Exception as e:
        logger.error(f"Error generating email with LLM: {e}")
        return ""

def generate_linkedin_invite(first_name: str, company_name: str, job_title: str, brief_summary: str, template: str) -> str:
    """Generate a short, personalized LinkedIn connection request message (max 200 chars — LinkedIn free-tier limit)."""
    
    greeting_name = first_name.strip() if first_name and first_name.strip() else "there"
    
    system_prompt = "You are an expert B2B networker. You write LinkedIn connection requests that feel genuine and get accepted."
    
    prompt = f"""Write a LinkedIn connection request note for:
- Name: {greeting_name}
- Title: {job_title}
- Company: {company_name}
- What their company does: {brief_summary}

SENDER CONTEXT:
{template or "I'd like to connect and explore potential collaboration."}

RULES:
1. MAXIMUM 180 characters (LinkedIn free-tier limit is 200; leave buffer). This is critical — count carefully.
2. Be warm, professional, NOT salesy.
3. Reference something specific about their company from the info above.
4. End with a soft ask (e.g., "Would love to connect and exchange ideas").
5. Do NOT include greetings like "Dear" or sign-offs like "Best regards".
6. Start directly with "Hi {greeting_name},"
7. Output ONLY the message text, nothing else.
8. Write in English."""

    headers = {
        "Authorization": f"Bearer {LLM_API_KEY}",
        "Content-Type": "application/json"
    }
    
    payload = {
        "model": LLM_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt}
        ]
    }
    
    try:
        response = _http.post(LLM_BASE_URL, headers=headers, json=payload, timeout=60)
        response.raise_for_status()
        data = response.json()
        if "base_resp" in data and data["base_resp"] and data["base_resp"].get("status_code", 0) != 0:
            return ""
        choices = data.get("choices")
        if choices and isinstance(choices, list) and len(choices) > 0:
            msg = choices[0].get("message")
            if msg and isinstance(msg, dict):
                content_text = msg.get("content", "").strip()
                content_text = re.sub(r'<think>.*?</think>', '', content_text, flags=re.DOTALL).strip()
                # Enforce LinkedIn free-tier 200-char limit (hard cap with safety buffer)
                if len(content_text) > 200:
                    content_text = content_text[:197].rstrip() + "..."
                return content_text
        return ""
    except Exception as e:
        logger.error(f"Error generating LinkedIn invite with LLM: {e}")
        return ""


def generate_whatsapp_message(first_name: str, company_name: str, brief_summary: str, template: str) -> str:
    """Generate a short, friendly WhatsApp opening message."""
    
    greeting_name = first_name.strip() if first_name and first_name.strip() else "there"
    
    system_prompt = "You are a friendly B2B sales professional. You write WhatsApp messages that feel personal and conversational, like texting a colleague."
    
    prompt = f"""Write a WhatsApp opening message for a business prospect:
- Name: {greeting_name}
- Company: {company_name}
- What their company does: {brief_summary}

SENDER CONTEXT:
{template or "Introduce ourselves briefly and ask if they'd be open to a quick chat."}

RULES:
1. Keep it under 150 words. WhatsApp messages should be SHORT.
2. Use a casual, friendly tone — no corporate jargon.
3. Reference something specific about their company.
4. Include a clear but soft call-to-action.
5. Start with "Hi {greeting_name} 👋"
6. You may use 1-2 relevant emojis but don't overdo it.
7. Do NOT include any sign-off or signature.
8. Output ONLY the message text, nothing else.
9. Write in English."""

    headers = {
        "Authorization": f"Bearer {LLM_API_KEY}",
        "Content-Type": "application/json"
    }
    
    payload = {
        "model": LLM_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt}
        ]
    }
    
    try:
        response = _http.post(LLM_BASE_URL, headers=headers, json=payload, timeout=60)
        response.raise_for_status()
        data = response.json()
        if "base_resp" in data and data["base_resp"] and data["base_resp"].get("status_code", 0) != 0:
            return ""
        choices = data.get("choices")
        if choices and isinstance(choices, list) and len(choices) > 0:
            msg = choices[0].get("message")
            if msg and isinstance(msg, dict):
                content_text = msg.get("content", "").strip()
                content_text = re.sub(r'<think>.*?</think>', '', content_text, flags=re.DOTALL).strip()
                return content_text
        return ""
    except Exception as e:
        logger.error(f"Error generating WhatsApp message with LLM: {e}")
        return ""


def generate_search_keywords(persona_details: str) -> list:
    """Generate search keywords based on customer persona details using the LLM."""
    system_prompt = "You are an expert in B2B lead generation and SEO search strategies."
    prompt = f"""Based on the following customer persona description, generate 8 highly effective B2B search keyword phrases. 
These phrases will be used to search on Google, Maps, Apollo, or directories to find potential B2B target companies.

CUSTOMER PERSONA DETAILS:
{persona_details}

RULES:
1. Output ONLY the 8 keyword phrases, one per line. No numbering, no prefixes, no explanations.
2. Keep each phrase short and search-friendly (2-4 words maximum). E.g., "padel club spain", "furniture wholesaler germany", "sports gear distributor".
3. Do NOT include broad search words like "importer" alone. Pair them with a product and region if relevant, but do NOT hardcode a specific country if the persona targets multiple countries (the search engine will auto-rotate countries anyway).
4. Focus on the core business type/niche (e.g. "outdoor furniture retailer", "design hotel supply").
5. Output must be in English.
"""

    headers = {
        "Authorization": f"Bearer {LLM_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": LLM_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt}
        ]
    }
    
    try:
        response = _http.post(LLM_BASE_URL, headers=headers, json=payload, timeout=60)
        response.raise_for_status()
        data = response.json()
        if "base_resp" in data and data["base_resp"] and data["base_resp"].get("status_code", 0) != 0:
            return []
        choices = data.get("choices")
        if choices and len(choices) > 0:
            msg = choices[0].get("message")
            if msg:
                content = msg.get("content", "").strip()
                content = re.sub(r'<think>.*?</think>', '', content, flags=re.DOTALL).strip()
                # Split by newline and clean
                lines = [line.strip().strip('"\'*-.') for line in content.split("\n")]
                return [line for line in lines if line]
        return []
    except Exception as e:
        logger.error(f"Error generating search keywords: {e}")
        return []
