"""
Playbook presets for scenario-based workflow creation.
Each playbook defines optimized defaults for a specific use case.
"""

PLAYBOOK_PRESETS = {
    "standard": {
        "key": "standard",
        "name": "🎯 通用获客 (Standard Outreach)",
        "description": "适用于常规的 B2B 开发信外贸获客场景。AI 自动搜索目标客户、深度调研公司背景、撰写高度个性化的开发信。",
        "icon": "target",
        "defaults": {
            "name_prefix": "Outreach",
            "daily_limit": 50,
            "send_interval_min": 300,
            "send_interval_max": 600,
            "auto_followup": True,
            "max_followups": 3,
            "target_customer_type": "distributor, importer, dealer, brand",
            "manual_handoff_triggers": "quote, sample, price, purchase plan, catalog, meeting",
            "search_sources": "web,directories,retail,social",
            "ai_prompt": (
                "We are a professional manufacturer specializing in high-quality products. "
                "Write a personalized cold email introducing our company and highlighting how we can help "
                "the recipient's business. Reference specific details from their company website to show "
                "genuine interest. End with a soft call-to-action asking for a brief call or meeting."
            ),
        },
    },
    "trade_show": {
        "key": "trade_show",
        "name": "🎪 展会营销邀约 (Trade Show Invite)",
        "description": "提前 1 个月自动邀约目标客户到你的展位，提升展会期间的信任度和成交效率。AI 会根据客户背景定制邀约话术。",
        "icon": "calendar",
        "defaults": {
            "name_prefix": "TradeShow",
            "daily_limit": 30,
            "send_interval_min": 600,
            "send_interval_max": 1200,
            "auto_followup": True,
            "max_followups": 2,
            "target_customer_type": "trade show visitor, distributor, importer, brand buyer",
            "search_sources": "trade_shows,web,directories,social",
            "pilot_goal": "Invite target buyers one month before the trade show and hand off meeting requests to sales.",
            "manual_handoff_triggers": "visit booth, meeting, appointment, sample, catalog, quote",
            "ai_prompt": (
                "We will be exhibiting at [TRADE SHOW NAME] on [DATES] at [BOOTH NUMBER]. "
                "Write a warm, personalized invitation email to visit our booth. "
                "Reference something specific about the recipient's company that shows we've done our homework. "
                "Mention 1-2 specific products or innovations we'll be showcasing that would be relevant to them. "
                "Keep the tone professional but enthusiastic. End with a clear CTA to schedule a meeting at the show."
            ),
        },
    },
    "reactivation": {
        "key": "reactivation",
        "name": "♻️ 沉睡客户激活 (Client Reactivation)",
        "description": "自动更新老客户联系方式并重新建立联系。适用于盘活流失客户，重新激活合作意向。",
        "icon": "refresh",
        "defaults": {
            "name_prefix": "Reactivation",
            "daily_limit": 20,
            "send_interval_min": 900,
            "send_interval_max": 1800,
            "auto_followup": True,
            "max_followups": 2,
            "target_customer_type": "old customer, dormant customer, previous buyer",
            "search_sources": "web,social,directories",
            "pilot_goal": "Refresh outdated contact data and reactivate dormant accounts with warm, low-pressure outreach.",
            "manual_handoff_triggers": "new order, reorder, updated contact, procurement plan, sample, quote",
            "ai_prompt": (
                "We previously worked with or contacted this company. Write a warm re-engagement email that: "
                "1) Acknowledges the existing relationship or past interaction "
                "2) Mentions a new development, product upgrade, or special offer that would be relevant to them "
                "3) References something specific and recent from their company website to show we still follow their business "
                "4) Asks if they'd be open to reconnecting for a brief update call. "
                "Tone should be warm and collegial, not salesy."
            ),
        },
    },
    "competitor_mining": {
        "key": "competitor_mining",
        "name": "🔍 竞品对标挖掘 (Competitor Mining)",
        "description": "通过分析竞品的采购商网络，直接联系其客户进行开发。适用于从同行手中抢夺市场份额。",
        "icon": "search",
        "defaults": {
            "name_prefix": "CompetitorMining",
            "daily_limit": 40,
            "send_interval_min": 300,
            "send_interval_max": 600,
            "auto_followup": True,
            "max_followups": 3,
            "target_customer_type": "competitor buyer, importer, distributor",
            "search_sources": "competitors,customs,retail,directories,web",
            "pilot_goal": "Use customs or competitor-buyer clues to validate whether competitor customers are open to alternatives.",
            "manual_handoff_triggers": "current supplier, alternative supplier, sample, trial order, quote",
            "ai_prompt": (
                "This prospect currently buys from our competitor. Write a compelling cold email that: "
                "1) Does NOT mention the competitor by name "
                "2) Positions us as an alternative with specific advantages (quality, pricing, MOQ flexibility, faster delivery) "
                "3) References something specific from their company website "
                "4) Offers a sample or trial order to reduce switching risk "
                "5) Keeps the tone confident but not aggressive. "
                "Focus on the VALUE we bring, not on criticizing their current supplier."
            ),
        },
    },
    "market_validation": {
        "key": "market_validation",
        "name": "🧪 新市场验证 (Market Validation)",
        "description": "针对未开发国家或区域小规模测试几十到几百家客户，用回复和询盘质量决定是否加大投入。",
        "icon": "flask",
        "defaults": {
            "name_prefix": "MarketValidation",
            "daily_limit": 20,
            "send_interval_min": 900,
            "send_interval_max": 1800,
            "auto_followup": True,
            "max_followups": 2,
            "target_customer_type": "distributor, agent, importer, brand buyer",
            "search_sources": "web,customs,directories,trade_shows,retail,social",
            "pilot_goal": "Run a 1-3 month pilot for one target market and compare match rate, reply rate, valid inquiries, and handoff count against manual/LinkedIn development.",
            "manual_handoff_triggers": "price, sample, distributor, agent, purchase plan, quote, catalog, meeting",
            "ai_prompt": (
                "This is a small market-validation pilot. Write a concise, highly personalized email that tests whether "
                "the prospect is open to discussing supply, private label, or distribution opportunities. Reference their "
                "market, product range, and one specific website detail. Ask a soft discovery question instead of pushing a sale."
            ),
        },
    },
    "product_testing": {
        "key": "product_testing",
        "name": "🧵 新品试错 (Product Testing)",
        "description": "围绕滑雪服、功能服、定制服装等细分品类做小范围需求验证，优先捕捉真实采购反馈。",
        "icon": "shirt",
        "defaults": {
            "name_prefix": "ProductTest",
            "daily_limit": 20,
            "send_interval_min": 900,
            "send_interval_max": 1800,
            "auto_followup": True,
            "max_followups": 2,
            "search_keywords": "skiwear distributor, functional apparel importer, outdoor clothing brand",
            "target_positions": "Owner, Founder, Buyer, Purchasing Manager, Product Manager, Category Manager",
            "target_customer_type": "distributor, agent, apparel brand, outdoor retailer",
            "product_focus": "skiwear, functional apparel, custom clothing",
            "search_sources": "web,customs,trade_shows,directories,retail,social",
            "pilot_goal": "Validate product-market fit for one new apparel category before scaling spend or sales headcount.",
            "manual_handoff_triggers": "sample, tech pack, quote, fabric, MOQ, lead time, private label, custom",
            "ai_prompt": (
                "We are validating demand for a focused apparel category. Write a personalized cold email that references "
                "the prospect's current product line and asks whether they are exploring new suppliers for skiwear, functional "
                "apparel, private label, or custom clothing. Keep it consultative and under 90 words."
            ),
        },
    },
    "garment_distributors": {
        "key": "garment_distributors",
        "name": "👕 服装经销商/代理商 (Garment Distributors)",
        "description": "面向海外服装经销商、代理商、品牌商和有采购记录的买家，适合外贸服装常规获客。",
        "icon": "users",
        "defaults": {
            "name_prefix": "GarmentDistributors",
            "daily_limit": 20,
            "send_interval_min": 900,
            "send_interval_max": 1800,
            "auto_followup": True,
            "max_followups": 3,
            "search_keywords": "apparel distributor, clothing importer, fashion wholesaler, outdoor apparel retailer",
            "target_positions": "Owner, Founder, Buyer, Purchasing Manager, Product Manager, Category Manager",
            "target_customer_type": "distributor, agent, wholesaler, importer, apparel brand",
            "product_focus": "custom apparel, functional clothing, skiwear, outdoor apparel",
            "search_sources": "web,customs,trade_shows,directories,competitors,retail,social",
            "pilot_goal": "Build a high-fit garment buyer pool and hand off A-grade or inquiry-positive leads to sales.",
            "manual_handoff_triggers": "quote, sample, catalog, MOQ, private label, custom, factory audit, lead time",
            "ai_prompt": (
                "Write a personalized B2B apparel outreach email. Prioritize distributors, agents, wholesalers, importers, "
                "and apparel brands. Reference the prospect's current products and position our custom manufacturing, "
                "flexible product development, quality control, and reliable delivery as relevant advantages."
            ),
        },
    },
}


def get_all_presets() -> list:
    """Return all playbook presets for the frontend selector."""
    return [
        {
            "key": preset["key"],
            "name": preset["name"],
            "description": preset["description"],
            "icon": preset["icon"],
            "defaults": preset["defaults"],
        }
        for preset in PLAYBOOK_PRESETS.values()
    ]


from typing import Optional

def get_preset(key: str) -> Optional[dict]:
    """Return a single playbook preset by key."""
    return PLAYBOOK_PRESETS.get(key)
