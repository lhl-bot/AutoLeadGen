import os
import json
import logging
import requests
import re
from sqlalchemy.orm import Session

import models
from database import db_retry

logger = logging.getLogger("preference_learner")
logger.setLevel(logging.INFO)
ch = logging.StreamHandler()
ch.setFormatter(logging.Formatter("[PREFERENCE LEARNER] %(message)s"))
if not logger.handlers:
    logger.addHandler(ch)

LLM_API_KEY = os.environ.get("LLM_API_KEY", os.environ.get("MINIMAX_API_KEY", ""))
LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "https://api.minimaxi.com/v1/chat/completions")
LLM_MODEL = os.environ.get("LLM_MODEL", "MiniMax-M2.7-highspeed")

@db_retry(max_attempts=3, delay=1, backoff=1)
def learn_preferences_for_persona(db: Session, persona_id: int) -> bool:
    """
    Analyzes positive and negative user feedback for a persona,
    uses MiniMax to extract patterns, and updates the CustomerPersona rules.
    """
    persona = db.query(models.CustomerPersona).filter(models.CustomerPersona.id == persona_id).first()
    if not persona:
        logger.warning(f"Persona with ID {persona_id} not found.")
        return False

    # 1. Query all feedbacks related to this persona (through workflows)
    feedbacks = (
        db.query(models.LeadFeedback)
        .join(models.Lead, models.LeadFeedback.lead_id == models.Lead.id)
        .join(models.Workflow, models.Lead.workflow_id == models.Workflow.id)
        .filter(models.Workflow.persona_id == persona_id)
        .order_by(models.LeadFeedback.created_at.desc())
        .all()
    )

    if not feedbacks:
        logger.info(f"No feedbacks found for persona {persona.name} (ID: {persona_id}). Skipping learning.")
        return False

    logger.info(f"Analyzing {len(feedbacks)} feedbacks for persona '{persona.name}' (ID: {persona_id})...")

    # 2. Structure feedback sample data for LLM
    positive_samples = []
    negative_samples = []

    for f in feedbacks:
        lead = db.query(models.Lead).filter(models.Lead.id == f.lead_id).first()
        if not lead:
            continue
        brief = db.query(models.LeadBrief).filter(models.LeadBrief.lead_id == lead.id).first()
        
        sample = {
            "company_name": lead.company_name,
            "domain": lead.domain,
            "job_title": lead.job_title,
            "reason": f.reason or "No reason provided",
            "overview": brief.company_overview if brief else "N/A"
        }
        
        if f.rating == "positive":
            positive_samples.append(sample)
        elif f.rating == "negative":
            negative_samples.append(sample)

    logger.info(f"Positive samples: {len(positive_samples)}, Negative samples: {len(negative_samples)}")

    # If feedback is too sparse (e.g. less than 1), we don't need to ask LLM, but for early testing we allow it.
    if not positive_samples and not negative_samples:
        logger.info("No valid samples extracted. Skipping LLM request.")
        return False

    # 3. Assemble Prompt
    system_prompt = """You are an expert B2B Sourcing and ICP (Ideal Customer Profile) Optimization Analyst. 
Analyze the provided user feedback on leads and optimize the Customer Persona matching rules.
You will be given the current rules and the positive/negative feedback.

Your task is to merge the feedback into the persona rules:
- "qualification_rules": Refine or add rules based on positive feedback. Keep it concise.
- "disqualification_rules": Refine or add rules based on negative feedback. Keep it concise.
- "negative_keywords": Add specific comma-separated keywords/phrases to exclude (e.g. "manufacturer, wholesale, agency").
- "positive_examples": Add company name or industry examples (comma separated).
- "negative_examples": Add company name or industry examples (comma separated).

IMPORTANT: Return ONLY a valid JSON object without markdown formatting (do not wrap in ```json or ```). The JSON must contain exactly these keys:
"qualification_rules", "disqualification_rules", "negative_keywords", "positive_examples", "negative_examples"
"""

    current_state = {
        "persona_name": persona.name,
        "current_qualification_rules": persona.qualification_rules or "N/A",
        "current_disqualification_rules": persona.disqualification_rules or "N/A",
        "current_negative_keywords": persona.negative_keywords or "N/A",
        "current_positive_examples": persona.positive_examples or "N/A",
        "current_negative_examples": persona.negative_examples or "N/A"
    }

    user_prompt = f"""
CURRENT PERSONA RULES:
{json.dumps(current_state, ensure_ascii=False, indent=2)}

USER FEEDBACK RECEIVED:
- POSITIVE SAMPLES (Liked by user):
{json.dumps(positive_samples, ensure_ascii=False, indent=2)}

- NEGATIVE SAMPLES (Disliked/Rejected by user):
{json.dumps(negative_samples, ensure_ascii=False, indent=2)}
"""

    headers = {
        "Authorization": f"Bearer {LLM_API_KEY}",
        "Content-Type": "application/json"
    }
    
    payload = {
        "model": LLM_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ]
    }

    try:
        response = requests.post(LLM_BASE_URL, headers=headers, json=payload, timeout=60)
        response.raise_for_status()
        data = response.json()
        
        if "base_resp" in data and data["base_resp"] and data["base_resp"].get("status_code", 0) != 0:
            logger.error(f"LLM API Error: {data['base_resp'].get('status_msg')}")
            return False
            
        choices = data.get("choices")
        if choices and isinstance(choices, list) and len(choices) > 0:
            msg = choices[0].get("message")
            if msg and isinstance(msg, dict):
                content_text = msg.get("content", "").strip()
                content_text = re.sub(r'<think>.*?</think>', '', content_text, flags=re.DOTALL).strip()
                # Remove code block formatting if LLM added it
                content_text = content_text.replace('```json', '').replace('```', '').strip()
                
                result = json.loads(content_text)
                
                # Update persona fields
                persona.qualification_rules = result.get("qualification_rules", persona.qualification_rules)
                persona.disqualification_rules = result.get("disqualification_rules", persona.disqualification_rules)
                persona.negative_keywords = result.get("negative_keywords", persona.negative_keywords)
                persona.positive_examples = result.get("positive_examples", persona.positive_examples)
                persona.negative_examples = result.get("negative_examples", persona.negative_examples)
                
                db.commit()
                logger.info(f"Successfully updated CustomerPersona '{persona.name}' rules based on feedback!")
                return True
                
        raise ValueError("Invalid response from LLM API")
    except Exception as e:
        logger.error(f"Failed to learn preferences for persona {persona_id}: {e}")
        return False
