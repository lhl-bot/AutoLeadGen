import json
import re
from typing import Dict, Any, List, Optional
from .agent_core import _llm_chat

def analyze_reply_intent(reply_text: str) -> Dict[str, Any]:
    """
    Analyze customer reply to determine their intent.
    Returns JSON with intent and summary.
    """
    system_prompt = """You are a B2B sales analyst. Analyze the following email reply from a prospect.
Categorize the intent into one of:
- "interested": Positive, asking for more info, pricing, or a meeting.
- "not_interested": Clear rejection, asking to be removed, not a fit.
- "more_info": Neutral, wants a presentation, brochure, or specific question answered before deciding.
- "other": Out of office, bounced, or unrecognizable.

Output ONLY valid JSON:
{
  "intent": "interested|not_interested|more_info|other",
  "summary": "Short 1-sentence summary of what they said in their language"
}
"""
    raw = _llm_chat(system_prompt, reply_text)
    try:
        # cleanup markdown
        raw = raw.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
        if raw.endswith("```"):
            raw = raw[:-3]
        return json.loads(raw.strip())
    except:
        return {"intent": "other", "summary": "Failed to parse intent."}

def draft_followup_email(
    lead_data: dict,
    reply_text: str,
    intent: str,
    conversation_history: Optional[List[dict]] = None,
    followup_round: int = 1,
) -> str:
    """
    Draft a follow-up email based on the user's intent and full conversation context.
    
    Args:
        lead_data: Dict with first_name, company_name, etc.
        reply_text: The most recent reply from the prospect.
        intent: Classified intent (interested, more_info, etc.)
        conversation_history: List of dicts [{direction, subject, body, sent_at}, ...]
        followup_round: Which followup round this is (1, 2, 3...), used to vary strategy.
    """
    if intent == "not_interested":
        return "" # No follow up for rejected
    
    # Build conversation context string
    history_text = ""
    if conversation_history:
        history_text = "\n--- CONVERSATION HISTORY (oldest first) ---\n"
        for msg in conversation_history[-6:]:  # Last 6 messages max to fit context
            direction = "WE SENT" if msg.get("direction") == "outbound" else "THEY REPLIED"
            subject = msg.get("subject", "")
            body = (msg.get("body", "") or "")[:500]
            sent_at = msg.get("sent_at", "")
            history_text += f"\n[{direction}] ({sent_at})\nSubject: {subject}\n{body}\n"
        history_text += "--- END HISTORY ---\n"
    
    # Strategy varies by followup round
    round_strategies = {
        1: "Focus on reaffirming the core value proposition. Answer any implied questions from their reply. Propose a clear next step (e.g. a 10 min call, sending a catalog).",
        2: "Use social proof — mention a similar client success story or case study. Address any remaining hesitation. Offer something tangible (sample, quote, spec sheet).",
        3: "Create gentle urgency — mention limited capacity, a seasonal deadline, or a time-bound offer. Keep it respectful, not pushy. This is likely the final follow-up.",
    }
    strategy = round_strategies.get(followup_round, round_strategies[3])
    
    system_prompt = f"""You are an expert B2B sales closer. Draft a short, professional follow-up email replying to a prospect.

CRITICAL RULES:
- Review the full conversation history below to understand what has already been discussed.
- Do NOT repeat any product features, selling points, or arguments that were already mentioned in previous emails.
- Each follow-up must bring NEW value or a NEW angle.
- Be polite, concise, and helpful.
- Keep it under 120 words.
- Language: Respond in the same language as the prospect's reply.

FOLLOW-UP ROUND: #{followup_round}
STRATEGY FOR THIS ROUND: {strategy}

Context:
Lead Name: {lead_data.get("first_name", "there")}
Company: {lead_data.get("company_name", "your company")}
Their Latest Reply: {reply_text}
Intent Classified As: {intent}

{history_text}

Output format:
Subject: Re: [Appropriate Subject]

[Email Body — no signature, no sign-off]
"""
    
    draft = _llm_chat("You are a professional B2B copywriter.", system_prompt)
    return draft


def draft_cold_followup_email(
    lead_data: dict,
    conversation_history: List[dict],
    followup_round: int = 1,
    ai_prompt: str = "",
) -> str:
    """
    Draft a cold follow-up email for a lead who has not replied.
    
    Args:
        lead_data: Dict with first_name, company_name, etc.
        conversation_history: List of dicts [{direction, subject, body, sent_at}, ...]
        followup_round: Which followup round this is (1, 2, 3...)
        ai_prompt: Optional custom instructions from the workflow
    """
    # Build conversation context string
    history_text = ""
    last_subject = "Outreach"
    if conversation_history:
        history_text = "\n--- PREVIOUS EMAILS SENT (oldest first) ---\n"
        for msg in conversation_history:
            subject = msg.get("subject", "")
            if subject:
                last_subject = subject
            body = (msg.get("body", "") or "")[:500]
            sent_at = msg.get("sent_at", "")
            history_text += f"\n[SENT] ({sent_at})\nSubject: {subject}\n{body}\n"
        history_text += "--- END HISTORY ---\n"
    
    # Subject line logic: thread it by replying to the same subject
    if last_subject and not last_subject.lower().startswith("re:"):
        subject_line = f"Re: {last_subject}"
    else:
        subject_line = last_subject

    # Strategies by round
    round_strategies = {
        1: "Keep it extremely short (under 50 words). Do a quick check-in to bump the thread. Offer a low-friction hook. e.g., 'Just wanted to see if you had a chance to review my last email? We recently helped X improve Y. Open to a quick look?'",
        2: "Introduce a brief, specific benefit or a concrete case study/social proof. Do not repeat the exact message from the first email. e.g., 'Wanted to share a quick metric: we helped [similar brand] reduce sourcing costs by 15% last quarter. Let me know if you want the details.'",
        3: "Polite close-out (the 'break-up' email). Acknowledge that they are busy or this isn't a priority right now, and let them know we won't crowd their inbox. e.g., 'Since I haven't heard back, I'll assume this isn't a priority right now. I won't crowd your inbox. Feel free to reach out if things change.'",
    }
    strategy = round_strategies.get(followup_round, round_strategies[3])

    system_prompt = f"""You are an elite B2B sales copywriter. You write natural, low-pressure, conversational cold follow-up emails.
Your goal is to get a reply by being helpful and brief, never pushy or salesy.

CRITICAL RULES:
1. REVIEW the previous email(s) sent in the history. Do NOT repeat the same arguments, features, or greetings.
2. Keep it incredibly short:
   - Round 1: under 50 words.
   - Round 2: under 80 words.
   - Round 3: under 60 words.
3. Speak like a real human. Absolutely NO marketing buzzwords ("synergy", "revolutionize", "cutting-edge", "game-changing", "seamless").
4. Never start with "I hope this email finds you well", "Just following up", or "I wanted to follow up". Start directly and conversationally.
5. NO SIGNATURE & NO PLACEHOLDERS:
   - Do NOT include any closing sign-off (e.g., "Best regards", "Thanks") or name/title. The email body must end immediately after the final sentence.
   - Never use placeholder brackets like [Name], [Your Name], etc.
6. The subject line is pre-determined as: {subject_line}
7. Language: Write in English."""

    user_prompt = f"""Draft the body for Cold Follow-up #{followup_round}.
    
Lead Name: {lead_data.get("first_name", "there")}
Company: {lead_data.get("company_name", "your company")}
Workflow custom prompt/context: {ai_prompt}

{history_text}

STRATEGY FOR THIS ROUND:
{strategy}

Output format:
Subject: {subject_line}

[Email Body — no signature, no sign-off]"""

    draft = _llm_chat(system_prompt, user_prompt)
    
    # If the draft doesn't contain Subject line, prepend it
    draft_clean = draft.strip()
    # Remove thoughts
    draft_clean = re.sub(r'<think>.*?</think>', '', draft_clean, flags=re.DOTALL).strip()
    
    if not draft_clean.lower().startswith("subject:"):
        draft_clean = f"Subject: {subject_line}\n\n{draft_clean}"
        
    return draft_clean
