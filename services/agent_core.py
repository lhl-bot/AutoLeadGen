import json
import re
import requests
import os
import logging
from typing import List, Dict, Any
from services.http_client import http as _http
from .snovio_client import SnovioClient
from .search_engine import search_company_results, search_domains

logger = logging.getLogger(__name__)

from dotenv import load_dotenv
load_dotenv(override=True)
LLM_API_KEY = os.environ.get("LLM_API_KEY", os.environ.get("MINIMAX_API_KEY", ""))
LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "https://api.minimaxi.com/v1/chat/completions")
LLM_MODEL = os.environ.get("LLM_MODEL", "MiniMax-M2.7-highspeed")

SNOVIO_ID = os.environ.get("SNOVIO_CLIENT_ID", "")
SNOVIO_SECRET = os.environ.get("SNOVIO_CLIENT_SECRET", "")
snovio = SnovioClient(SNOVIO_ID, SNOVIO_SECRET)

# ─────────────────────────────────────────────
# Helper: call LLM for pure text generation
# ─────────────────────────────────────────────
def _llm_chat(system_prompt: str, user_prompt: str) -> str:
    """Simple LLM call – no tools, just text in/out."""
    import time
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
    max_retries = 3
    delay = 1.0
    for attempt in range(max_retries):
        try:
            resp = _http.post(LLM_BASE_URL, headers=headers, json=payload, timeout=120)
            if resp.status_code == 429:
                if attempt < max_retries - 1:
                    time.sleep(delay)
                    delay *= 2
                    continue
            resp.raise_for_status()
            data = resp.json()
            
            if "base_resp" in data and data["base_resp"] and data["base_resp"].get("status_code", 0) != 0:
                raise Exception(data["base_resp"].get("status_msg", "Unknown error"))
                
            choices = data.get("choices")
            if choices and isinstance(choices, list) and len(choices) > 0:
                msg = choices[0].get("message")
                if msg and isinstance(msg, dict):
                    content_text = msg.get("content", "").strip()
                    # Remove <think> tags
                    content_text = re.sub(r'<think>.*?</think>', '', content_text, flags=re.DOTALL).strip()
                    return content_text
                    
            raise Exception(f"Unexpected API response format: {data}")
        except Exception as e:
            if attempt < max_retries - 1:
                time.sleep(delay)
                delay *= 2
                continue
            logger.error(f"[LLM ERROR] {e}")
            return f"AI 请求出错: {e}"


# ─────────────────────────────────────────────
# Intent detection – figure out what user wants
# ─────────────────────────────────────────────
def _detect_intent(messages: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Ask LLM to classify intent and extract parameters using chat history context. Returns JSON."""
    system = """You are an intent parser. Given a chat history and a user message, output ONLY valid JSON with:
{
  "intent": one of ["search_companies", "search_and_find", "find_at_domain", "extract_persona", "write_email", "send_email", "create_workflow", "save_to_pool", "general_chat"],
  "keywords": "search keywords if intent is search_and_find or create_workflow. If user says 'continue' or 'keep looking', infer the keywords from previous search_and_find intent.",
  "search_offset": integer, default 0. If user asks for 'more', 'another one', 'next', look at the history to see how many times they asked, and set offset to 50, 100, 150 etc. accordingly.
  "workflow_name": "name of the workflow",
  "pool_name": "name of the client pool to save to",
  "daily_limit": "number of emails per day (e.g. 50), if specified",
  "domain": "domain if intent is find_at_domain",
  "positions": "target job titles, comma separated",
  "recipient_name": "name of the person for write_email",
  "recipient_company": "company name for write_email",
  "recipient_email": "email address for send_email",
  "product_or_service": "what is being offered",
  "raw_text": "the original message"
}
Rules:
- "search_companies": user wants a real list of companies/websites, but did not explicitly ask for contact emails.
- "search_and_find": user wants to search for companies AND find contacts/emails. Use context to determine keywords if the user implies continuing a previous search.
- "find_at_domain": user already specified a domain (like apple.com)
- "extract_persona": user pasted a long customer persona/profile description
- "create_workflow": user wants to create an automated workflow or campaign for lead generation
- "save_to_pool": user wants to save or import the previous search results into a client pool
- "write_email": user wants to draft/write a cold outreach email for a specific contact or company
- "send_email": user confirms they want to SEND an email to a specific email address. Must contain an email address.
- "general_chat": anything else
Output ONLY the JSON, no markdown, no explanation.
For write_email, also extract: "recipient_name", "recipient_company", "recipient_email", "product_or_service" from context."""
    
    # Format the last 5 messages to give context
    context = ""
    for msg in messages[-6:-1]:
        role = "User" if msg["role"] == "user" else "Assistant"
        # Truncate assistant messages to avoid exceeding token limits
        content = msg["content"][:200] + "..." if len(msg["content"]) > 200 else msg["content"]
        context += f"{role}: {content}\n"
    
    user_message = messages[-1]["content"] if messages else ""
    prompt = f"Chat History:\n{context}\n\nCurrent User Message:\n{user_message}"
    
    raw = _llm_chat(system, prompt)
    # Strip markdown code fences if present
    raw = raw.strip()
    if raw.startswith("```json"):
        raw = raw[7:]
    elif raw.startswith("```"):
        raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
    if raw.endswith("```"):
        raw = raw[:-3]
    raw = raw.strip()
    
    try:
        data = json.loads(raw)
        return data
    except:
        return {"intent": "general_chat", "raw_text": user_message}


def _heuristic_intent(messages: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Fast deterministic routing for common sales-agent requests.

    This avoids sending obvious real-search requests to a pure chat fallback,
    which was the source of fabricated company lists.
    """
    user_message = messages[-1]["content"] if messages else ""
    text = user_message.strip()
    lower = text.lower()

    domain_match = re.search(r"(?:https?://)?(?:www\.)?([a-z0-9][a-z0-9-]*(?:\.[a-z0-9-]+)+)", lower)
    if domain_match and any(word in lower for word in ["找", "search", "find", "联系人", "email", "邮箱", "采购", "ceo", "founder"]):
        return {
            "intent": "find_at_domain",
            "domain": domain_match.group(1),
            "positions": _extract_positions_from_text(text),
            "raw_text": text,
        }

    search_words = ["找", "搜索", "搜", "真实", "公司", "客户", "名单", "supplier", "distributor", "retailer", "company", "companies", "lead", "leads"]
    asks_for_search = any(word in lower for word in search_words) or any(word in text for word in ["找", "搜", "公司", "客户", "名单", "真实"])
    if not asks_for_search:
        return {}

    contact_words = ["邮箱", "email", "联系人", "联系方式", "采购", "负责人", "决策人", "lead", "leads", "客户"]
    wants_contacts = any(word in lower for word in contact_words) or any(word in text for word in ["邮箱", "联系人", "联系方式", "采购", "负责人", "客户"])
    count = _extract_requested_count(text, default=10)
    keywords = _build_search_keywords(text, wants_contacts=wants_contacts)
    search_offset = _infer_search_offset(messages)

    return {
        "intent": "search_and_find" if wants_contacts else "search_companies",
        "keywords": keywords,
        "positions": _extract_positions_from_text(text),
        "requested_count": count,
        "search_offset": search_offset,
        "raw_text": text,
    }


def _extract_requested_count(text: str, default: int = 10) -> int:
    cn_numbers = {
        "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
        "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
    }
    match = re.search(r"(\d{1,2})\s*(?:个|家|条|companies|leads)?", text, re.IGNORECASE)
    if match:
        return max(1, min(int(match.group(1)), 20))
    for key, value in cn_numbers.items():
        if f"{key}个" in text or f"{key}家" in text or key == text.strip():
            return value
    return default


def _infer_search_offset(messages: List[Dict[str, Any]]) -> int:
    user_message = messages[-1]["content"] if messages else ""
    if not any(word in user_message.lower() for word in ["more", "next", "continue", "继续", "更多", "再找"]):
        return 0
    previous_searches = sum(1 for msg in messages[:-1] if msg.get("role") == "assistant" and "实时搜索" in msg.get("content", ""))
    return previous_searches * 10


def _extract_positions_from_text(text: str) -> str:
    lower = text.lower()
    positions = []
    role_map = [
        ("采购", "Purchasing Manager"),
        ("买手", "Buyer"),
        ("buyer", "Buyer"),
        ("procurement", "Procurement"),
        ("ceo", "CEO"),
        ("founder", "Founder"),
        ("创始", "Founder"),
        ("老板", "Owner"),
        ("owner", "Owner"),
        ("manager", "Manager"),
        ("director", "Director"),
    ]
    for needle, role in role_map:
        if needle in lower or needle in text:
            positions.append(role)
    if not positions:
        positions = ["CEO", "Founder", "Owner", "Managing Director", "Purchasing Manager", "Procurement", "Buyer"]
    return ", ".join(dict.fromkeys(positions))


def _build_search_keywords(text: str, wants_contacts: bool = False) -> str:
    lower = text.lower()
    if "padel" in lower and ("欧洲" in text or "europe" in lower):
        base = "padel equipment retailer distributor shop Europe official website"
    elif "padel" in lower:
        base = "padel equipment retailer distributor shop official website"
    else:
        base = re.sub(r"(帮我|请|找|搜索|搜|一些|真实的|真实|公司|客户|名单|邮箱|联系人|十个|10个|家|个)", " ", text, flags=re.IGNORECASE)
        base = re.sub(r"\s+", " ", base).strip()
    if wants_contacts and "official website" not in base:
        base = f"{base} official website"
    return base.strip() or text


# ─────────────────────────────────────────────
# Pipeline: Search → Snov.io → Format results
# ─────────────────────────────────────────────
def _domain_email_count(domain: str) -> Any:
    """Free Snov.io domain email count. Returns None if unavailable."""
    if not snovio._authenticate():
        return None
    try:
        resp = snovio.session.post(
            "https://api.snov.io/v1/get-domain-emails-count",
            data={"access_token": snovio.access_token, "domain": domain},
            timeout=15,
        )
        if resp.status_code != 200:
            return None
        data = resp.json()
        if data.get("success"):
            return data.get("result")
    except Exception as e:
        logger.warning(f"[SNOVIO COUNT ERROR] {domain}: {e}")
    return None


def _pipeline_search_companies(keywords: str, requested_count: int = 10, offset: int = 0) -> str:
    """Live-search real companies without requiring contact enrichment."""
    requested_count = max(1, min(int(requested_count or 10), 20))
    logger.info(f"[PIPELINE] Company search: {keywords} count={requested_count} offset={offset}")
    results = search_company_results(keywords, count=requested_count, offset=offset)
    if not results:
        return f"没有从实时搜索中找到与 **{keywords}** 匹配的公司。可以换一组关键词，比如：`padel equipment distributor Spain`。"

    lines = [f"## 实时搜索到的真实公司：{keywords}\n"]
    lines.append("| 序号 | 公司/网站 | 官网 | Snov邮箱收录 | 备注 |")
    lines.append("|---:|---|---|---:|---|")

    for idx, item in enumerate(results[:requested_count], start=1):
        domain = item.get("domain", "")
        title = item.get("title") or _display_name_from_domain(domain)
        url = item.get("url") or f"https://{domain}"
        count = _domain_email_count(domain)
        count_label = "—" if count is None else str(count)
        snippet = (item.get("snippet") or "").replace("|", " ").strip()
        if len(snippet) > 80:
            snippet = snippet[:80].rstrip() + "..."
        lines.append(
            f"| {idx} | {title.replace('|', ' ')} | [{domain}]({url}) | {count_label} | {snippet or '来自实时搜索结果'} |"
        )

    lines.append("\n这些不是模型记忆里的名单，而是刚刚通过实时搜索拿到的官网结果；`Snov邮箱收录` 用免费 Snov count 接口做了二次检查。")
    lines.append("如果要继续下一步，可以直接说：`帮我找这些公司的采购/老板邮箱`。")
    return "\n".join(lines)


def _display_name_from_domain(domain: str) -> str:
    if not domain:
        return "Unknown"
    return domain.split(".")[0].replace("-", " ").title()


def _pipeline_search_and_find(keywords: str, positions: str, offset: int = 0) -> str:
    """The REAL pipeline: search engines → Snov.io domain search → email fetch."""
    # Always use a wide net of positions to maximize results
    # Snov.io limits to max 10 positions per request
    DEFAULT_POSITIONS = [
        "CEO", "Owner", "Founder", "Director", "Manager",
        "Purchasing Manager", "Procurement", "Buyer",
        "Partner", "President"
    ]
    user_positions = [p.strip() for p in positions.split(",")] if positions else []
    # Merge user positions with defaults, deduplicating, cap at 10
    pos_list = list(dict.fromkeys(user_positions + DEFAULT_POSITIONS))[:10]
    
    # Step 1: REAL search (offset enables pagination for "find more" requests)
    logger.info(f"[PIPELINE] Step 1 - Searching for: {keywords} (offset={offset})")
    domains = search_domains(keywords, offset=offset)
    
    if not domains:
        return f"❌ 搜索 \"{keywords}\" 没有找到任何公司网站。请尝试更换关键词。"
    
    logger.info(f"[PIPELINE] Found {len(domains)} domains: {domains}")
    
    # Step 2: For each domain, use Snov.io to find prospects
    all_results = []
    found_count = 0
    import time
    start_time = time.time()
    
    for i, domain in enumerate(domains):
        # Prevent reverse proxy timeout by stopping at 45s (allow enough time to search)
        if time.time() - start_time > 45:
            logger.info("[PIPELINE] Reached 45s execution limit to prevent timeout. Stopping early.")
            break
            
        logger.info(f"[PIPELINE] Step 2 - Snov.io searching ({i+1}/{len(domains)}): {domain}")
        prospects = snovio.search_prospects_by_domain(domain, pos_list)
        
        domain_results = {"domain": domain, "prospects": []}
        
        for p in prospects or []:
            # Check timeout again before email fetching which takes longer
            if time.time() - start_time > 45:
                break
                
            name = f"{p.get('first_name', '')} {p.get('last_name', '')}".strip()
            position = p.get("position", "N/A")
            email = None
            
            # Check if email is already provided in the prospect object
            if p.get("emails") and isinstance(p.get("emails"), list) and len(p["emails"]) > 0:
                valid_emails = [e.get("email") for e in p["emails"] if e.get("smtp_status") == "valid" and e.get("email")]
                if valid_emails:
                    email = valid_emails[0]
                else:
                    email = p["emails"][0].get("email")
            
            search_url = p.get("search_emails_start")
            if not email and search_url:
                logger.info(f"[PIPELINE] Step 3 - Fetching email for {name} at {domain}")
                email = snovio.get_prospect_email(search_url)
            
            # ONLY add prospects that actually have an email
            if email:
                domain_results["prospects"].append({
                    "name": name,
                    "position": position,
                    "email": email,
                    "linkedin_url": p.get("source_page")
                })
                
            # Move to next domain if we found 3 good prospects here
            if len(domain_results["prospects"]) >= 3:
                break

        if not domain_results["prospects"] and os.environ.get("SNOVIO_ALLOW_VERIFIED_DOMAIN_EMAIL_FALLBACK", "true").lower() not in {"0", "false", "no", "off"}:
            for email in snovio.get_verified_domain_emails(domain, limit=2):
                domain_results["prospects"].append({
                    "name": "Company contact",
                    "position": "Verified company email",
                    "email": email,
                    "linkedin_url": None,
                })
        
        # Only add the company if we found at least one prospect with an email
        if domain_results["prospects"]:
            found_count += len(domain_results["prospects"])
            all_results.append(domain_results)
        
        # Stop once we have found 10 companies with valid prospects
        if len(all_results) >= 10:
            break

    
    # Step 3: Format into readable output
    return _format_results(keywords, all_results)


def _pipeline_find_at_domain(domain: str, positions: str) -> str:
    """Find prospects at a specific domain."""
    pos_list = [p.strip() for p in positions.split(",")] if positions else ["CEO", "Owner", "Manager", "Purchasing Manager"]
    
    domain = domain.replace("www.", "").replace("https://", "").replace("http://", "").strip("/")
    
    logger.info(f"[PIPELINE] Searching domain: {domain} for positions: {pos_list}")
    prospects = snovio.search_prospects_by_domain(domain, pos_list)
    
    results = []
    for p in (prospects[:5] if prospects else []):
        name = f"{p.get('first_name', '')} {p.get('last_name', '')}".strip()
        position = p.get("position", "N/A")
        email = None
        
        search_url = p.get("search_emails_start")
        if search_url:
            logger.info(f"[PIPELINE] Fetching email for {name}")
            email = snovio.get_prospect_email(search_url)
        
        results.append({
            "name": name,
            "position": position,
            "email": email or "未找到邮箱",
            "linkedin_url": p.get("source_page")
        })

    if not any("@" in r["email"] for r in results):
        for email in snovio.get_verified_domain_emails(domain, limit=3):
            results.append({
                "name": "Company contact",
                "position": "Verified company email",
                "email": email,
                "linkedin_url": None,
            })

    if not results:
        return f"在 **{domain}** 上没有找到可用联系人或已验证公司邮箱。Snov.io 可能没有收录该公司的数据。"
    
    # Format
    lines = [f"## 🔍 {domain} 的搜索结果\n"]
    lines.append("| 姓名 | 职位 | 邮箱 | 领英 |")
    lines.append("|------|------|------|------|")
    for r in results:
        safe_name = str(r['name']).replace('|', '&#124;')
        safe_pos = str(r['position']).replace('|', '&#124;')
        safe_email = str(r['email']).replace('|', '&#124;')
        safe_linkedin = f"[主页]({r['linkedin_url']})" if r.get('linkedin_url') else "—"
        lines.append(f"| {safe_name} | {safe_pos} | {safe_email} | {safe_linkedin} |")
    
    return "\n".join(lines)


def _format_results(keywords: str, all_results: List[Dict]) -> str:
    """Format search+find results into a clean report."""
    lines = [f"## 🔍 搜索结果：\"{keywords}\"\n"]
    
    found_any = False
    for dr in all_results:
        domain = dr["domain"]
        prospects = dr["prospects"]
        
        if prospects:
            found_any = True
            lines.append(f"### 🏢 {domain}")
            lines.append(f"**官网链接**: [https://{domain}](https://{domain})")
            lines.append("")
            lines.append("| 姓名 | 职位 | 邮箱 | 领英 |")
            lines.append("|------|------|------|------|")
            for p in prospects:
                safe_name = str(p['name']).replace('|', '&#124;')
                safe_pos = str(p['position']).replace('|', '&#124;')
                safe_email = str(p['email']).replace('|', '&#124;')
                safe_linkedin = f"[主页]({p['linkedin_url']})" if p.get('linkedin_url') else "—"
                lines.append(f"| {safe_name} | {safe_pos} | {safe_email} | {safe_linkedin} |")
            lines.append("")
        else:
            lines.append(f"### 🏢 {domain}\n**官网链接**: [https://{domain}](https://{domain})\n_Snov.io 未收录该公司的联系人数据_\n")
    
    if not found_any:
        lines.append("\n⚠️ 以上公司在 Snov.io 数据库中均未找到联系人。建议尝试更宽泛的关键词或不同的目标市场。")
    
    lines.append("\n---\n_以上数据全部来自真实的搜索引擎结果和 Snov.io API 实时查询，非 AI 编造。_")
    return "\n".join(lines)


# ─────────────────────────────────────────────
# Main entry point
# ─────────────────────────────────────────────
def run_agent_loop(messages: List[Dict[str, Any]], depth: int = 0, user_id: int = 1) -> Dict[str, Any]:
    """Main agent entry: detect intent → run real pipeline → return results."""
    # Get the last user message
    user_msg = ""
    for m in reversed(messages):
        if m.get("role") == "user":
            user_msg = m.get("content", "")
            break
    
    if not user_msg:
        return {"role": "assistant", "content": "请输入您的需求。"}
    
    # Detect intent
    logger.info(f"[AGENT] Detecting intent for: {user_msg[:100]}...")
    intent_data = _heuristic_intent(messages) or _detect_intent(messages)
    intent = intent_data.get("intent", "general_chat")
    logger.info(f"[AGENT] Intent: {intent}")
    
    if intent == "search_companies":
        keywords = intent_data.get("keywords", user_msg)
        requested_count = int(intent_data.get("requested_count", 10))
        search_offset = int(intent_data.get("search_offset", 0))
        result = _pipeline_search_companies(keywords, requested_count=requested_count, offset=search_offset)
        return {"role": "assistant", "content": result}

    if intent == "search_and_find":
        keywords = intent_data.get("keywords", user_msg)
        positions = intent_data.get("positions", "CEO, Owner, Manager, Purchasing Manager, Buyer")
        search_offset = int(intent_data.get("search_offset", 0))
        logger.info(f"[AGENT] search_offset={search_offset}")
        result = _pipeline_search_and_find(keywords, positions, offset=search_offset)
        return {"role": "assistant", "content": result}
    
    elif intent == "find_at_domain":
        domain = intent_data.get("domain", "")
        positions = intent_data.get("positions", "CEO, Owner, Manager, Purchasing Manager")
        if not domain:
            return {"role": "assistant", "content": "请提供您想搜索的公司域名（如 example.com）。"}
        result = _pipeline_find_at_domain(domain, positions)
        return {"role": "assistant", "content": result}
    
    elif intent == "extract_persona":
        # Use LLM to extract persona, but this is real extraction not fabrication
        extracted = _llm_chat(
            "You are a data extractor. Extract the customer persona from the text below and return a clean JSON with keys: target_industry, target_countries, target_positions, keywords, negative_keywords, email_template_notes. Respond in the same language as the input. Only output valid JSON without markdown wrapping.",
            user_msg
        )
        
        # Try to parse and save to DB
        import json
        from database import SessionLocal
        from models import CustomerPersona
        
        saved_name = "AI 提取画像"
        try:
            # Clean markdown if present
            clean_json = extracted.strip()
            if clean_json.startswith("```json"):
                clean_json = clean_json[7:]
            if clean_json.startswith("```"):
                clean_json = clean_json[3:]
            if clean_json.endswith("```"):
                clean_json = clean_json[:-3]
                
            data = json.loads(clean_json.strip())
            
            db = SessionLocal()
            try:
                # Generate a short name based on industry or keywords
                industry = data.get('target_industry') or ''
                countries = data.get('target_countries') or ''
                if industry or countries:
                    saved_name = f"{countries} {industry}".strip()
                
                new_persona = CustomerPersona(
                    user_id=user_id,
                    name=saved_name or "AI 提取画像",
                    target_industry=data.get('target_industry', ''),
                    target_countries=data.get('target_countries', ''),
                    target_roles=data.get('target_positions', ''),
                    target_keywords=data.get('keywords', ''),
                    negative_keywords=data.get('negative_keywords', ''),
                    ai_prompt_template=data.get('email_template_notes', '')
                )
                db.add(new_persona)
                db.commit()
            finally:
                db.close()
                
            persona_msg = f"✅ 已成功提取并保存客户画像：**{saved_name}**\n\n```json\n" + json.dumps(data, indent=2, ensure_ascii=False) + "\n```\n\n您可以在左侧边栏的「👥 客户画像配置」中查看和编辑它，或者直接说'用这个画像搜索'来启动自动化流程。"
        except Exception as e:
            persona_msg = "⚠️ 提取客户画像时解析失败，请尝试重新发送。\n\n原始数据：\n" + extracted
            
        return {"role": "assistant", "content": persona_msg}
    
    elif intent == "write_email":
        recipient_name = intent_data.get("recipient_name", "")
        recipient_company = intent_data.get("recipient_company", "")
        product_or_service = intent_data.get("product_or_service", "our product/service")
        
        prompt = f"""Write a professional B2B cold outreach email with the following context:
- Recipient: {recipient_name} at {recipient_company}
- Product/Service being offered: {product_or_service}
- Tone: Professional yet personalized, warm, not pushy
- Length: 150-200 words
- Language: Same as the user's input language
- Include a clear CTA (call to action)
- Do NOT use generic templates, make it sound authentic

Output the email in this format:
**Subject:** [subject line]

[email body]

User's original request: {user_msg}"""
        
        email_draft = _llm_chat(
            "You are a world-class B2B copywriter specializing in cold outreach emails. Write compelling, personalized emails that get responses. Always respond in the same language as the user.",
            prompt
        )
        return {"role": "assistant", "content": f"## ✉️ AI 开发信草稿\n\n{email_draft}\n\n---\n_如需修改，请告诉我具体要调整的部分。确认后可以说「发送这封邮件到 xxx@example.com」_"}
    
    elif intent == "send_email":
        to_email = intent_data.get("recipient_email", "")
        if not to_email or "@" not in to_email:
            return {"role": "assistant", "content": "❌ 请提供有效的收件人邮箱地址。例如：「发送邮件到 john@example.com」"}
        
        # Try to get the last email draft from conversation history
        last_draft = ""
        for m in reversed(messages):
            if m.get("role") == "assistant" and "AI 开发信草稿" in m.get("content", ""):
                last_draft = m["content"]
                break
        
        if not last_draft:
            # Generate a quick email based on context
            last_draft = _llm_chat(
                "You are a B2B email writer. Write a short, professional outreach email based on the user's request. Respond in the same language as the user.",
                user_msg
            )
        
        # Extract subject and body from the draft
        subject = "Business Inquiry"
        body = last_draft
        if "**Subject:**" in last_draft or "**主题:**" in last_draft or "**Subject：**" in last_draft:
            lines = last_draft.split("\n")
            for i, line in enumerate(lines):
                if "subject" in line.lower() or "主题" in line.lower():
                    subject = line.split(":**")[-1].split("：**")[-1].strip().strip("*").strip()
                    body = "\n".join(lines[i+1:])
                    break
        
        # Clean markdown from body for HTML email
        body_html = body.replace("\n", "<br>").replace("**", "<strong>").replace("*", "<em>")
        
        # Send email directly (no localhost self-call)
        try:
            from database import SessionLocal
            from models import EmailAccount as EmailAccountModel
            from .email_sender import send_email as _send_email
            
            db = SessionLocal()
            try:
                account = db.query(EmailAccountModel).first()
                if not account:
                    return {"role": "assistant", "content": "## ❌ 发送失败\n\n请先在侧边栏配置发件邮箱 (SMTP)。"}
                
                result = _send_email(
                    smtp_host=account.smtp_host,
                    smtp_port=account.smtp_port,
                    smtp_user=account.smtp_user,
                    smtp_pass=account.smtp_pass,
                    use_ssl=account.use_ssl,
                    use_tls=account.use_tls,
                    from_email=account.email,
                    to_email=to_email,
                    subject=subject,
                    body_html=body_html,
                    body_text=body
                )
            finally:
                db.close()
            
            if result.get("success"):
                return {"role": "assistant", "content": f"## ✅ 邮件已发送！\n\n- **收件人**: {to_email}\n- **主题**: {subject}\n\n邮件已通过您配置的 SMTP 服务器成功发出。"}
            else:
                return {"role": "assistant", "content": f"## ❌ 发送失败\n\n错误信息：{result.get('message', '未知错误')}\n\n请检查侧边栏的 SMTP 邮箱配置是否正确。"}
        except Exception as e:
            return {"role": "assistant", "content": f"邮件发送失败: {e}"}

    elif intent == "create_workflow":
        workflow_name = intent_data.get("workflow_name") or "AI 生成工作流"
        keywords = intent_data.get("keywords") or "Padel equipment"
        daily_limit = intent_data.get("daily_limit") or 50
        positions = intent_data.get("positions") or "CEO, Founder, Manager, Director"
        try:
            from database import SessionLocal
            from models import Workflow
            db = SessionLocal()
            try:
                wf = Workflow(
                    user_id=user_id,
                    name=workflow_name,
                    search_keywords=keywords,
                    target_positions=positions,
                    daily_limit=int(daily_limit),
                    send_interval_min=60,
                    send_interval_max=300,
                    status="paused"
                )
                db.add(wf)
                db.commit()
                return {"role": "assistant", "content": f"## ✅ 工作流创建成功！\n\n**{workflow_name}** 已添加至您的系统。\n\n- **搜索关键词**: {keywords}\n- **目标职位**: {positions}\n- **每日发送上限**: {daily_limit} 封\n\n👉 **下一步**：请点击侧边栏的【自动化工作流】，为该工作流**绑定发件邮箱**和**客户库**，即可开启全自动运行！"}
            finally:
                db.close()
        except Exception as e:
            return {"role": "assistant", "content": f"创建工作流失败：{str(e)}"}

    elif intent == "save_to_pool":
        pool_name = intent_data.get("pool_name") or "AI 保存记录"
        
        # Look backwards in messages to find the last markdown table with leads
        leads_to_save = []
        for msg in reversed(messages):
            if msg.get("role") != "assistant":
                continue
            content = msg.get("content", "")
            if "## 🔍 搜索结果" in content or "的搜索结果" in content:
                current_domain = None
                for line in content.split("\n"):
                    if line.startswith("### 🏢"):
                        match = re.search(r'\[(.*?)\]', line)
                        if match:
                            current_domain = match.group(1)
                        else:
                            current_domain = line.replace("### 🏢", "").strip()
                    elif line.startswith("|") and "姓名" not in line and "---" not in line and current_domain:
                        parts = [p.strip() for p in line.split("|") if p.strip()]
                        if len(parts) >= 3:
                            name = parts[0].replace('&#124;', '|')
                            pos = parts[1].replace('&#124;', '|')
                            email = parts[2].replace('&#124;', '|')
                            linkedin_url = None
                            if len(parts) >= 4 and "](http" in parts[3]:
                                match_url = re.search(r'\((http.*?)\)', parts[3])
                                if match_url:
                                    linkedin_url = match_url.group(1)
                                
                            if email != "未找到邮箱" and "@" in email:
                                leads_to_save.append({
                                    "domain": current_domain,
                                    "name": name,
                                    "position": pos,
                                    "email": email,
                                    "source_page": linkedin_url
                                })
                if leads_to_save:
                    break
                    
        if not leads_to_save:
            return {"role": "assistant", "content": "没有在最近的上下文中找到任何有效的搜索结果。请先进行一次搜索。"}
            
        try:
            from database import SessionLocal
            from models import ClientPool, Lead
            db = SessionLocal()
            try:
                pool = ClientPool(name=pool_name, description="由 AI 助手导入的客户", user_id=user_id)
                db.add(pool)
                db.commit()
                db.refresh(pool)
                
                db_leads = []
                for ld in leads_to_save:
                    name_parts = ld["name"].split(" ", 1)
                    first_name = name_parts[0] if name_parts else ld["name"]
                    last_name = name_parts[1] if len(name_parts) > 1 else ""
                    
                    db_leads.append(Lead(
                        client_pool_id=pool.id,
                        workflow_id=None,
                        domain=ld["domain"],
                        company_name=ld["domain"],
                        email=ld["email"],
                        first_name=first_name,
                        last_name=last_name,
                        job_title=ld["position"],
                        linkedin_url=ld.get("source_page"),
                        status="found"
                    ))
                if db_leads:
                    db.bulk_save_objects(db_leads)
                    db.commit()
                return {"role": "assistant", "content": f"✅ 已成功将刚才搜索到的 {len(db_leads)} 个联系人保存至客户库 **{pool_name}** 中！\n您现在可以前往侧边栏的【客户库管理】查看。"}
            finally:
                db.close()
        except Exception as e:
            return {"role": "assistant", "content": f"保存到客户库失败: {e}"}

    else:
        # Fallback to general chat
        logger.info("[INTENT] General chat fallback")
        response = _llm_chat(
            "You are 海外客 Agent, a B2B sales automation assistant. Answer the user's question helpfully. Always respond in the same language as the user. If the user asks you to search for companies or find contacts, tell them to describe what they need (e.g. 'search for Padel equipment companies in Europe').",
            user_msg
        )
        return {"role": "assistant", "content": response}
