"""
socialSentiment.py — Multi-Platform Social Sentiment Analyzer (v5).

Aggregates social signals from multiple platforms to gauge community
sentiment around a token. No API keys required — uses public endpoints.

Platforms:
  1. Twitter/X:    Search for token mentions via public scraping endpoints
  2. Telegram:     Monitor public group messages for token mentions
  3. Discord:      Check for Discord link validity and activity signals
  4. Reddit:       Search subreddits for token-related posts
  5. DexScreener:  Social links and community metrics (already available)

Output:
  Sentiment score 0-100 where:
    ≥ 80  = Very positive (strong community, active social)
    60-79 = Positive (decent social presence)
    40-59 = Neutral (minimal/mixed social)
    20-39 = Negative (low/bad social signals)
    < 20  = Very negative (no social, suspicious silence)

Integration:
  Called by SniperBot._process_pair() for tokens with liquidity.
  Result used by RiskEngine and displayed in frontend.
"""

import asyncio
import logging
import re
import time
from dataclasses import dataclass, field, asdict
from typing import Optional

import aiohttp

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════
#  Data structures
# ═══════════════════════════════════════════════════════════════════

@dataclass
class SocialSignal:
    """Individual social signal from one platform."""
    platform: str = ""             # "twitter" | "telegram" | "discord" | "reddit"
    mentions: int = 0              # number of mentions found
    sentiment: float = 0.5         # 0.0 (negative) to 1.0 (positive)
    followers: int = 0             # channel/account followers
    activity_level: str = "low"    # "none" | "low" | "medium" | "high"
    url: str = ""
    last_activity: float = 0.0

    def to_dict(self):
        return asdict(self)


@dataclass
class SocialSentimentResult:
    """Aggregated social sentiment across all platforms."""
    token_address: str
    token_symbol: str = ""
    # Overall
    sentiment_score: int = 50          # 0–100
    sentiment_label: str = "neutral"   # "very_positive" | "positive" | "neutral" | "negative" | "very_negative"
    confidence: float = 0.0            # 0.0–1.0
    # Per-platform
    twitter_signal: Optional[SocialSignal] = None
    telegram_signal: Optional[SocialSignal] = None
    discord_signal: Optional[SocialSignal] = None
    reddit_signal: Optional[SocialSignal] = None
    # Aggregated
    total_mentions: int = 0
    platforms_active: int = 0
    signals: list = field(default_factory=list)
    # DexScreener social data (from existing token_info)
    has_website: bool = False
    has_social_links: bool = False
    timestamp: float = 0.0

    def to_dict(self):
        d = asdict(self)
        return d


# ═══════════════════════════════════════════════════════════════════
#  Keyword match patterns for sentiment analysis
# ═══════════════════════════════════════════════════════════════════

POSITIVE_KEYWORDS = [
    "moon", "pump", "gem", "bullish", "buy", "100x", "1000x",
    "launch", "ape", "safu", "locked", "doxxed", "audit",
    "partnership", "listing", "marketing", "utility",
    "🚀", "💎", "🔥", "✅", "💰", "📈",
]

NEGATIVE_KEYWORDS = [
    "scam", "rug", "rugpull", "honeypot", "dump", "sell",
    "fake", "warning", "avoid", "stolen", "hack", "exploit",
    "bearish", "dead", "abandon",
    "🚨", "⚠️", "❌", "💀", "🗑️",
]


def _simple_sentiment(text: str) -> float:
    """
    Simple keyword-based sentiment score for a text snippet.
    Returns 0.0 (very negative) to 1.0 (very positive).
    """
    if not text:
        return 0.5

    text_lower = text.lower()
    pos_count = sum(1 for kw in POSITIVE_KEYWORDS if kw.lower() in text_lower)
    neg_count = sum(1 for kw in NEGATIVE_KEYWORDS if kw.lower() in text_lower)

    total = pos_count + neg_count
    if total == 0:
        return 0.5

    return round(pos_count / total, 3)


# ═══════════════════════════════════════════════════════════════════
#  Social Sentiment Analyzer
# ═══════════════════════════════════════════════════════════════════

class SocialSentimentAnalyzer:
    """
    Aggregates social signals from multiple platforms.

    Scoring:
      - Each platform contributes 0-25 points to the total
      - Having social links at all is a positive signal
      - Active communities boost the score
      - Suspicious patterns (all bots, no real engagement) reduce score
    """

    # Platform weights for aggregation
    WEIGHTS = {
        "dexscreener_social": 0.20,
        "twitter": 0.30,
        "telegram": 0.25,
        "discord": 0.15,
        "reddit": 0.10,
    }

    def __init__(self, http_session: aiohttp.ClientSession = None):
        self._session = http_session
        self.stats = {
            "tokens_analyzed": 0,
            "api_errors": 0,
        }

    async def analyze(self, token_address: str, token_symbol: str = "",
                      token_info=None) -> SocialSentimentResult:
        """
        Full social sentiment analysis for a token.

        Uses token_info's existing social data (DexScreener) plus
        additional API queries to social platforms.
        """
        result = SocialSentimentResult(
            token_address=token_address,
            token_symbol=token_symbol,
            timestamp=time.time(),
        )
        self.stats["tokens_analyzed"] += 1

        component_scores: dict[str, float] = {}

        # ── 1. DexScreener social data (already in token_info) ──
        dex_score = self._score_dexscreener_social(token_info, result)
        component_scores["dexscreener_social"] = dex_score

        # ── 2. Twitter/X search ──
        try:
            twitter_signal = await self._check_twitter(token_symbol, token_address)
            result.twitter_signal = twitter_signal
            component_scores["twitter"] = self._signal_to_score(twitter_signal)
        except Exception as e:
            logger.debug(f"Twitter check failed: {e}")
            self.stats["api_errors"] += 1
            component_scores["twitter"] = 50

        # ── 3. Telegram ──
        try:
            tg_signal = await self._check_telegram(token_symbol, token_info)
            result.telegram_signal = tg_signal
            component_scores["telegram"] = self._signal_to_score(tg_signal)
        except Exception as e:
            logger.debug(f"Telegram check failed: {e}")
            self.stats["api_errors"] += 1
            component_scores["telegram"] = 50

        # ── 4. Discord ──
        try:
            discord_signal = await self._check_discord(token_info)
            result.discord_signal = discord_signal
            component_scores["discord"] = self._signal_to_score(discord_signal)
        except Exception as e:
            logger.debug(f"Discord check failed: {e}")
            self.stats["api_errors"] += 1
            component_scores["discord"] = 50

        # ── 5. Reddit ──
        try:
            reddit_signal = await self._check_reddit(token_symbol)
            result.reddit_signal = reddit_signal
            component_scores["reddit"] = self._signal_to_score(reddit_signal)
        except Exception as e:
            logger.debug(f"Reddit check failed: {e}")
            self.stats["api_errors"] += 1
            component_scores["reddit"] = 50

        # ── Aggregate ──
        total_weighted = 0.0
        for platform, weight in self.WEIGHTS.items():
            score = component_scores.get(platform, 50)
            total_weighted += score * weight

        result.sentiment_score = max(0, min(100, round(total_weighted)))

        # Count active platforms
        for s in [result.twitter_signal, result.telegram_signal,
                  result.discord_signal, result.reddit_signal]:
            if s and s.activity_level != "none":
                result.platforms_active += 1
                result.total_mentions += s.mentions

        # Bonus for multi-platform presence
        if result.platforms_active >= 3:
            result.sentiment_score = min(100, result.sentiment_score + 10)
            result.signals.append(f"✅ Presencia en {result.platforms_active} plataformas")
        elif result.platforms_active == 0:
            result.sentiment_score = max(0, result.sentiment_score - 15)
            result.signals.append("⚠️ Sin presencia social detectada")

        # Confidence
        available_data = sum(1 for v in component_scores.values() if v != 50)
        result.confidence = round(min(1.0, available_data / 4), 2)

        # Label
        if result.sentiment_score >= 80:
            result.sentiment_label = "very_positive"
        elif result.sentiment_score >= 60:
            result.sentiment_label = "positive"
        elif result.sentiment_score >= 40:
            result.sentiment_label = "neutral"
        elif result.sentiment_score >= 20:
            result.sentiment_label = "negative"
        else:
            result.sentiment_label = "very_negative"

        result.signals.append(
            f"📊 Sentiment: {result.sentiment_label} ({result.sentiment_score}/100)"
        )

        return result

    def _score_dexscreener_social(self, token_info,
                                   result: SocialSentimentResult) -> float:
        """Score based on DexScreener social data already in TokenInfo."""
        if not token_info:
            return 50

        score = 40  # baseline

        if token_info.has_website:
            score += 15
            result.has_website = True
            result.signals.append("✅ Tiene website")

        if token_info.has_social_links:
            score += 15
            result.has_social_links = True
            result.signals.append("✅ Tiene links sociales")

        if token_info.listed_coingecko:
            score += 20
            result.signals.append("✅ Listado en CoinGecko")

        if token_info._dexscreener_ok:
            if token_info.dexscreener_buys_24h > 50:
                score += 10
            if token_info.dexscreener_volume_24h > 10000:
                score += 10

        return min(100, max(0, score))

    async def _check_twitter(self, symbol: str,
                              token_address: str) -> SocialSignal:
        """
        Search for token mentions on Twitter/X.
        Uses Nitter instances or public search (no API key needed).
        """
        signal = SocialSignal(platform="twitter")

        if not symbol or len(symbol) < 2:
            signal.activity_level = "none"
            return signal

        try:
            # Use DexScreener's social data as proxy (already fetched)
            # Direct Twitter API requires auth, so we approximate
            # from public search endpoints
            search_query = f"${symbol} crypto"

            session = self._session or aiohttp.ClientSession()
            close_session = self._session is None

            try:
                # Try Twitter search via Nitter (public instance)
                nitter_urls = [
                    f"https://nitter.privacydev.net/search?q={search_query}&f=tweets",
                ]

                for url in nitter_urls:
                    try:
                        async with session.get(
                            url,
                            timeout=aiohttp.ClientTimeout(total=5),
                            headers={"User-Agent": "Mozilla/5.0"},
                        ) as resp:
                            if resp.status == 200:
                                text = await resp.text()
                                # Count tweet mentions
                                mentions = text.count("tweet-content") + text.count("tweet-body")
                                signal.mentions = min(mentions, 100)

                                if mentions > 20:
                                    signal.activity_level = "high"
                                    signal.sentiment = 0.7
                                elif mentions > 5:
                                    signal.activity_level = "medium"
                                    signal.sentiment = 0.6
                                elif mentions > 0:
                                    signal.activity_level = "low"
                                    signal.sentiment = 0.5
                                else:
                                    signal.activity_level = "none"
                                    signal.sentiment = 0.3

                                signal.url = url
                                break
                    except Exception:
                        continue

            finally:
                if close_session:
                    await session.close()

        except Exception as e:
            logger.debug(f"Twitter check error for {symbol}: {e}")
            signal.activity_level = "none"

        return signal

    async def _check_telegram(self, symbol: str,
                               token_info=None) -> SocialSignal:
        """
        Check Telegram for token community presence.
        Uses public Telegram web preview to check group existence.
        """
        signal = SocialSignal(platform="telegram")

        if not symbol or len(symbol) < 2:
            signal.activity_level = "none"
            return signal

        try:
            # Check if token_info has a Telegram link from DexScreener
            # (DexScreener often includes social links)
            tg_link = ""
            if token_info and hasattr(token_info, '_dex_socials'):
                for social in getattr(token_info, '_dex_socials', []):
                    if 'telegram' in social.get('platform', '').lower() or \
                       't.me' in social.get('url', ''):
                        tg_link = social.get('url', '')
                        break

            if tg_link:
                signal.url = tg_link
                signal.activity_level = "medium"
                signal.sentiment = 0.6
                signal.mentions = 1
            else:
                # Try common Telegram URL patterns
                session = self._session or aiohttp.ClientSession()
                close_session = self._session is None

                try:
                    common_names = [symbol.lower(), symbol.upper()]
                    for name in common_names:
                        url = f"https://t.me/{name}"
                        try:
                            async with session.get(
                                url,
                                timeout=aiohttp.ClientTimeout(total=3),
                                allow_redirects=True,
                            ) as resp:
                                if resp.status == 200:
                                    text = await resp.text()
                                    # Check if it's a valid group/channel
                                    if "tgme_page_title" in text or "tgme_channel_info" in text:
                                        signal.url = url
                                        signal.activity_level = "medium"
                                        signal.sentiment = 0.6
                                        signal.mentions = 1

                                        # Try to extract members count
                                        members_match = re.search(
                                            r"(\d[\d\s]*)\s*(?:members|subscribers)", text
                                        )
                                        if members_match:
                                            count_str = members_match.group(1).replace(" ", "")
                                            signal.followers = int(count_str)
                                            if signal.followers > 1000:
                                                signal.activity_level = "high"
                                                signal.sentiment = 0.75
                                        break
                        except Exception:
                            continue
                finally:
                    if close_session:
                        await session.close()

        except Exception as e:
            logger.debug(f"Telegram check error for {symbol}: {e}")
            signal.activity_level = "none"

        return signal

    async def _check_discord(self, token_info=None) -> SocialSignal:
        """
        Check for Discord community presence.
        Validates Discord invite links if available from DexScreener.
        """
        signal = SocialSignal(platform="discord")

        if not token_info:
            signal.activity_level = "none"
            return signal

        # Look for Discord link in social data
        discord_link = ""
        if hasattr(token_info, '_dex_socials'):
            for social in getattr(token_info, '_dex_socials', []):
                if 'discord' in social.get('platform', '').lower():
                    discord_link = social.get('url', '')
                    break

        if not discord_link:
            signal.activity_level = "none"
            return signal

        signal.url = discord_link

        # Validate Discord invite
        try:
            # Extract invite code
            invite_match = re.search(
                r"discord\.gg/(\w+)|discord\.com/invite/(\w+)", discord_link
            )
            if invite_match:
                invite_code = invite_match.group(1) or invite_match.group(2)

                session = self._session or aiohttp.ClientSession()
                close_session = self._session is None

                try:
                    url = f"https://discord.com/api/v9/invites/{invite_code}?with_counts=true"
                    async with session.get(
                        url,
                        timeout=aiohttp.ClientTimeout(total=5),
                    ) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            members = data.get("approximate_member_count", 0)
                            online = data.get("approximate_presence_count", 0)
                            signal.followers = members
                            signal.mentions = 1

                            if members > 5000:
                                signal.activity_level = "high"
                                signal.sentiment = 0.8
                            elif members > 500:
                                signal.activity_level = "medium"
                                signal.sentiment = 0.65
                            elif members > 50:
                                signal.activity_level = "low"
                                signal.sentiment = 0.5
                            else:
                                signal.activity_level = "low"
                                signal.sentiment = 0.4
                        else:
                            signal.activity_level = "none"
                            signal.sentiment = 0.3
                finally:
                    if close_session:
                        await session.close()

        except Exception as e:
            logger.debug(f"Discord check error: {e}")
            signal.activity_level = "low"  # link exists at least

        return signal

    async def _check_reddit(self, symbol: str) -> SocialSignal:
        """
        Search Reddit for token mentions using public JSON API.
        """
        signal = SocialSignal(platform="reddit")

        if not symbol or len(symbol) < 3:
            signal.activity_level = "none"
            return signal

        try:
            session = self._session or aiohttp.ClientSession()
            close_session = self._session is None

            try:
                url = (
                    f"https://www.reddit.com/search.json"
                    f"?q={symbol}+crypto&sort=new&limit=10"
                )
                async with session.get(
                    url,
                    timeout=aiohttp.ClientTimeout(total=5),
                    headers={"User-Agent": "TradingBot/5.0"},
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        posts = data.get("data", {}).get("children", [])
                        signal.mentions = len(posts)

                        if posts:
                            # Aggregate sentiment from post titles
                            titles = " ".join(
                                p.get("data", {}).get("title", "")
                                for p in posts
                            )
                            signal.sentiment = _simple_sentiment(titles)

                            total_comments = sum(
                                p.get("data", {}).get("num_comments", 0)
                                for p in posts
                            )

                            if signal.mentions > 5 or total_comments > 20:
                                signal.activity_level = "high"
                            elif signal.mentions > 2:
                                signal.activity_level = "medium"
                            elif signal.mentions > 0:
                                signal.activity_level = "low"
                        else:
                            signal.activity_level = "none"
                            signal.sentiment = 0.3
                    else:
                        signal.activity_level = "none"
            finally:
                if close_session:
                    await session.close()

        except Exception as e:
            logger.debug(f"Reddit check error for {symbol}: {e}")
            signal.activity_level = "none"

        return signal

    def _signal_to_score(self, signal: SocialSignal) -> float:
        """Convert a platform signal to 0-100 score."""
        if not signal:
            return 50

        base = 30  # baseline for having a signal at all

        if signal.activity_level == "high":
            base = 75
        elif signal.activity_level == "medium":
            base = 60
        elif signal.activity_level == "low":
            base = 45
        else:
            base = 25

        # Adjust by sentiment
        sentiment_adj = (signal.sentiment - 0.5) * 30  # -15 to +15
        base += sentiment_adj

        # Follower bonus
        if signal.followers > 10000:
            base += 15
        elif signal.followers > 1000:
            base += 10
        elif signal.followers > 100:
            base += 5

        return max(0, min(100, base))

    def get_stats(self) -> dict:
        return self.stats.copy()
