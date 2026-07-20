"""
News Flow Bot — auto-posts economic & market news to the RainX community.
"""
import asyncio
import logging
import os
from datetime import datetime, timezone
import httpx

logger = logging.getLogger(__name__)

SUPABASE_URL = os.getenv("SUPABASE_URL", "https://fsndqkacfizulovhfldz.supabase.co")
NEWS_FLOW_EMAIL = "newsflow@rainx.app"
NEWS_FLOW_BIO = "Tracking the news that moves the markets. CPI • FOMC • NFP • Crypto • Macro 📈🌍"
NEWS_FLOW_NAME = "News Flow"
NEWS_FLOW_HANDLE = "newsflow"

_news_flow_user_id = None
_posting_task = None
_last_post_hours = {}

NEWS_POSTS = [
    ("CPI", "🔥 CPI DATA ALERT", "US Consumer Price Index data is out! Core CPI drives USD, Gold, and crypto. Higher than expected = USD up, risk-off. Lower = USD down, possible Fed pivot. Watch: XAUUSD, EURUSD, BTCUSD

#CPI #Forex #Macro #Trading"),
    ("NFP", "📈 NON-FARM PAYROLLS", "US Non-Farm Payrolls (NFP) day! Jobs data is the biggest monthly market mover. Strong jobs = USD bullish. Weak jobs = rate cut bets rise. Key pairs: EURUSD, USDJPY, XAUUSD

#NFP #Jobs #USD #Trading"),
    ("FOMC", "🏦 FOMC DECISION", "Federal Reserve interest rate decision incoming! All eyes on the Fed today. Watch for rate path guidance and Chair Powell press conference. Impact on: USD, Gold, S&P 500, Crypto

#FOMC #FederalReserve #InterestRates"),
    ("CRYPTO", "₿ CRYPTO MARKET UPDATE", "Crypto markets are active today. BTC and ETH price action closely watched by institutional traders. Key levels and sentiment shifting. Trade with proper risk management.

#Bitcoin #Crypto #BTC #ETH"),
    ("GOLD", "🥇 GOLD MARKET UPDATE", "XAU/USD (Gold) is in focus today. Key driver: Fed rate expectations + geopolitical risk. Gold tends to rally on USD weakness and risk-off sentiment.

#Gold #XAUUSD #Commodities #Trading"),
]

SCHEDULE = {"CRYPTO": [0, 6, 12, 18], "GOLD": [7, 14], "CPI": [8], "NFP": [8], "FOMC": [8]}


async def _skey():
    return os.getenv("SUPABASE_SERVICE_KEY", "")


async def _ensure_account():
    global _news_flow_user_id
    sk = await _skey()
    if not sk:
        return None
    hdr = {"apikey": sk, "Authorization": "Bearer " + sk, "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.get(SUPABASE_URL + "/rest/v1/profiles?handle=eq." + NEWS_FLOW_HANDLE + "&select=id", headers=hdr)
    if r.status_code == 200 and r.json():
        _news_flow_user_id = r.json()[0]["id"]
        return _news_flow_user_id
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.post(SUPABASE_URL + "/auth/v1/admin/users", headers=hdr, json={"email": NEWS_FLOW_EMAIL, "password": os.urandom(32).hex(), "email_confirm": True, "user_metadata": {"name": NEWS_FLOW_NAME, "is_bot": True}})
    uid = r.json().get("id") if r.status_code in (200, 201) else None
    if not uid:
        return None
    async with httpx.AsyncClient(timeout=10) as c:
        await c.post(SUPABASE_URL + "/rest/v1/profiles", headers={**hdr, "Prefer": "resolution=merge-duplicates"}, json={"id": uid, "name": NEWS_FLOW_NAME, "display_name": NEWS_FLOW_NAME, "handle": NEWS_FLOW_HANDLE, "bio": NEWS_FLOW_BIO, "subscription": "premium", "is_active": True, "is_bot": True, "badge": "gold"})
    _news_flow_user_id = uid
    logger.info("News Flow account created: " + uid)
    return uid


async def post_news(category, title, body):
    global _news_flow_user_id
    if not _news_flow_user_id:
        _news_flow_user_id = await _ensure_account()
    if not _news_flow_user_id:
        return False
    sk = await _skey()
    if not sk:
        return False
    hdr = {"apikey": sk, "Authorization": "Bearer " + sk, "Content-Type": "application/json", "Prefer": "return=minimal"}
    text = title + "\n\n" + body
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.post(SUPABASE_URL + "/rest/v1/posts", headers=hdr, json={"user_id": _news_flow_user_id, "text": text, "category": category, "is_bot_post": True, "likes_count": 0, "comments_count": 0, "created_at": datetime.now(timezone.utc).isoformat()})
    return r.status_code in (200, 201, 204)


async def _generate_ai(category, title, fallback_body):
    oai = os.getenv("OPENAI_API_KEY", "")
    if not oai:
        return title, fallback_body
    prompts = {"CPI": "Write a 150-word community post about CPI inflation data impact on forex/crypto. Use emojis. Sound like an expert trader.", "NFP": "Write a 150-word community post about Non-Farm Payrolls data and forex market impact. Use emojis.", "FOMC": "Write a 150-word community post about the Federal Reserve FOMC decision. Use emojis.", "CRYPTO": "Write a 150-word community post about current crypto market conditions mentioning BTC and ETH. Use emojis.", "GOLD": "Write a 150-word community post about gold (XAUUSD) and key market drivers. Use emojis."}
    try:
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.post("https://api.openai.com/v1/chat/completions", headers={"Authorization": "Bearer " + oai, "Content-Type": "application/json"}, json={"model": "gpt-4o-mini", "messages": [{"role": "system", "content": "You are News Flow, a financial news bot for the RainX trading community."}, {"role": "user", "content": prompts.get(category, prompts["CRYPTO"])}], "max_tokens": 250, "temperature": 0.7})
        if r.status_code == 200:
            ai_text = r.json()["choices"][0]["message"]["content"]
            return title, ai_text
    except Exception as e:
        logger.warning("OpenAI news gen failed: " + str(e))
    return title, fallback_body


async def _scheduler():
    await _ensure_account()
    while True:
        try:
            h = datetime.now(timezone.utc).hour
            for (cat, title, body) in NEWS_POSTS:
                if h in SCHEDULE.get(cat, []) and _last_post_hours.get(cat) != h:
                    t, b = await _generate_ai(cat, title, body)
                    if await post_news(cat, t, b):
                        _last_post_hours[cat] = h
        except Exception as e:
            logger.error("News scheduler error: " + str(e))
        await asyncio.sleep(3600)


def start_news_flow():
    global _posting_task
    _posting_task = asyncio.create_task(_scheduler())
    logger.info("News Flow bot started")


def stop_news_flow():
    global _posting_task
    if _posting_task:
        _posting_task.cancel()
        _posting_task = None
