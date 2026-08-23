#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Cyberpent - Automated Threat Intelligence Pipeline
==================================================
Serverless ingestion engine designed to run inside GitHub Actions every
2 hours. It will:

  1. INGEST      - Pull 17 RSS/Atom feeds spanning government advisories
                   (CISA, UK NCSC), security media (BleepingComputer, The
                   Hacker News, Dark Reading, SecurityWeek, KrebsOnSecurity),
                   vendor/threat research (Talos, Unit 42, Securelist,
                   Microsoft, Project Zero), exploit trackers (Exploit-DB,
                   SANS ISC) and infosec researcher feeds on the Fediverse.
                   Each feed is fetched with retry + backoff on failure.
  2. NORMALIZE   - Strip HTML to plain text, clamp publication dates,
                   canonicalize URLs.
  3. DEDUPLICATE - Skip anything already recorded in scripts/state.json,
                   already published under any post's `source_url`, or a
                   near-identical headline published within 7 days.
  4. ENRICH      - Extract CVE IDs, file hashes, public IPv4 addresses,
                   suspicious domains; derive categories, tags, severity
                   and a heuristic confidence score.
  5. SYNTHESIZE  - Call OpenAI (gpt-4o-mini) to draft a structured
                   intelligence report (Executive Summary / Technical
                   Impact & Root Cause / Mitigation & IOCs).
  6. PUBLISH     - Write `_posts/YYYY-MM-DD-slug-title.md` with complete
                   YAML front matter and persist processed URLs back to
                   scripts/state.json.

Required environment:
    GEMINI_API_KEY   Google AI Studio API key (aistudio.google.com/apikey)
                     stored as a GitHub Actions secret.

Optional environment:
    GEMINI_MODEL         (default: gemini-2.5-flash)
    MAX_POSTS_PER_RUN    (default: 6)
    FEED_LOOKBACK_HOURS  (default: 48)

Exit codes: 0 = success, 1 = fatal configuration error.
"""

import hashlib
import html as htmllib
import ipaddress
import json
import logging
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Optional, Set, Tuple

import feedparser
import frontmatter
import requests
from google import genai
from google.genai import types as genai_types
from pydantic import BaseModel, Field

# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #

ROOT = Path(__file__).resolve().parents[1]
POSTS_DIR = ROOT / "_posts"
STATE_FILE = ROOT / "scripts" / "state.json"

FEEDS = {
    # 1. Government Advisories & CSIRTs (High Confidence)
    "CISA": "https://www.cisa.gov/cybersecurity-advisories/all.xml",
    "UK NCSC": "https://www.ncsc.gov.uk/api/1/services/v1/report-rss-feed.xml",

    # 2. Breaking Cybersecurity News & Journalism
    "BleepingComputer": "https://www.bleepingcomputer.com/feed/",
    "The Hacker News": "https://feeds.feedburner.com/TheHackersNews",
    "Dark Reading": "https://www.darkreading.com/rss.xml",
    "SecurityWeek": "https://www.securityweek.com/feed/",
    "KrebsOnSecurity": "https://krebsonsecurity.com/feed/",

    # 3. Threat Intelligence, Malware & Vendor Research
    "Talos Intelligence": "https://blog.talosintelligence.com/rss/",
    "Unit 42": "https://unit42.paloaltonetworks.com/feed/",
    "Securelist": "https://securelist.com/feed/",
    "Microsoft Security": "https://www.microsoft.com/en-us/security/blog/feed/",
    "Project Zero": "https://googleprojectzero.blogspot.com/feeds/posts/default",

    # 4. Vulnerabilities, Exploits & Incident Handlers
    "Exploit-DB": "https://www.exploit-db.com/rss.xml",
    "SANS ISC": "https://isc.sans.edu/rssfeed.xml",

    # 5. Open-Source Social & Fediverse (Threat Researchers)
    "@GossiTheDog": "https://infosec.exchange/@GossiTheDog.rss",
    "@MalwareTech": "https://infosec.exchange/@MalwareTech.rss",
    "@SwiftOnSecurity": "https://infosec.exchange/@SwiftOnSecurity.rss",

    # 6. Community Aggregators (Reddit)
    "Reddit CyberSec": "https://www.reddit.com/r/cybersecurity/new/.rss",
    "Reddit Malware": "https://www.reddit.com/r/Malware/new/.rss",
    "Reddit BlueTeam": "https://www.reddit.com/r/blueteamsec/new/.rss",

    # 7. Telegram Channels (web-preview scraper)
    # (handled by fetch_telegram below - see TELEGRAM_CHANNELS)

    # 8. Aggregated News Search (Google News RSS - pre-filtered queries)
    "Google News Attacks": (
        "https://news.google.com/rss/search?q="
        "ransomware+OR+%22data+breach%22+OR+%22cyber+attack%22"
        "&hl=en-US&gl=US&ceid=US:en"
    ),
    "Google News Vulns": (
        "https://news.google.com/rss/search?q="
        "%22zero-day%22+OR+CVE+OR+%22security+flaw%22+when:2d"
        "&hl=en-US&gl=US&ceid=US:en"
    ),

    # 9. Industrial / OT (ICS & SCADA)
    "CISA ICS Advisories": (
        "https://www.cisa.gov/cybersecurity-advisories/ics-advisories.xml"
    ),
    "Claroty Team82": "https://claroty.com/team82/disclosure-dashboard/feed",
}

# Telegram channels scraped from the public t.me/s/ web preview.
TELEGRAM_CHANNELS = {
    "TG VXUnderground": "vxunderground",
}

# Bluesky researcher accounts (free public API - no auth needed).
BSKY_ACCOUNTS = {
    "deepdarkCTI Bluesky": "fastfire.bsky.social",
    "Perimetered TI Bluesky": "sen-perimetered.bsky.social",
    "r1cksec Bluesky": "r1cksec.bsky.social",
}

USER_AGENT = (
    "Mozilla/5.0 (compatible; CyberpentIntelBot/1.0; "
    "+https://www.cyberpent.cc.cd)"
)
FETCH_TIMEOUT = 30          # seconds per HTTP request
MAX_RETRIES = 3             # attempts per feed
RETRY_BACKOFF_SECONDS = 5   # multiplied by attempt number

GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.6-flash")
MAX_POSTS_PER_RUN = int(os.getenv("MAX_POSTS_PER_RUN", "6"))
LOOKBACK_HOURS = int(os.getenv("FEED_LOOKBACK_HOURS", "48"))
MAX_CONTENT_CHARS = 6000    # article text handed to the LLM
MAX_STATE_ENTRIES = 5000    # trim state.json to this many URLs
LLM_THROTTLE_SECONDS = 2    # pause between OpenAI calls

REQUIRED_SECTIONS = [
    "### Executive Summary",
    "### Technical Impact & Root Cause",
    "### Mitigation & IOCs",
]

# --------------------------------------------------------------------------- #
# Extraction patterns
# --------------------------------------------------------------------------- #

CVE_RE = re.compile(r"\bCVE-\d{4}-\d{4,7}\b", re.IGNORECASE)
SHA256_RE = re.compile(r"\b[0-9a-fA-F]{64}\b")
SHA1_RE = re.compile(r"\b[0-9a-fA-F]{40}\b")
MD5_RE = re.compile(r"\b[0-9a-fA-F]{32}\b")
IPV4_RE = re.compile(
    r"\b(?:(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)\.){3}"
    r"(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)\b"
)
DOMAIN_RE = re.compile(
    r"\b(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]*[a-zA-Z0-9])?\.)+"
    r"(?:com|net|org|io|xyz|top|info|ru|su|cn|cc|to|me|biz|online|site|shop|link|click|space|pw|club)\b",
    re.IGNORECASE,
)
URL_RE = re.compile(r"https?://[^\s<>\"')\]]+", re.IGNORECASE)

# An item must mention at least one of these (case-insensitive) to qualify.
RELEVANCE_KEYWORDS = (
    "cve-", "vulnerab", "zero-day", "zeroday", "zero day", "exploit",
    "ransomware", "malware", "breach", "backdoor", "botnet", "phishing",
    "credential", "apt ", "threat actor", "data leak", "leak", "cyber attack",
    "cyberattack", "patch now", "actively exploited", "in the wild",
    "infostealer", "spyware", "rootkit", "trojan", "ddos", "supply chain",
    "hacked", "hackers", "hack", "security flaw", "flaw", "attack",
    "extortion", "dark web",
)

# Never report these as IOCs - they are our own sources / big platforms.
SOURCE_DOMAINS = {
    "bleepingcomputer.com", "thehackernews.com", "feedburner.com",
    "cisa.gov", "us-cert.gov", "cyberpent.cc.cd", "google.com",
    "github.io", "github.com", "twitter.com", "x.com", "t.co",
    "reddit.com", "cloudflare.com", "akamai.com", "mitre.org",
    "nist.gov", "nvd.nist.gov",
    # Expanded source network (group 1-5)
    "ncsc.gov.uk", "darkreading.com", "securityweek.com",
    "krebsonsecurity.com", "talosintelligence.com", "cisco.com",
    "paloaltonetworks.com", "unit42.paloaltonetworks.com",
    "securelist.com", "microsoft.com", "msrc-blog.microsoft.com",
    "blogspot.com", "blog.google", "exploit-db.com",
    "sans.edu", "isc.sans.edu", "infosec.exchange",
}

TAG_VOCAB = {
    "windows": "windows", "microsoft": "microsoft", "apple": "apple",
    "ios": "ios", "macos": "macos", "android": "android", "linux": "linux",
    "chrome": "chrome", "chromium": "chromium", "firefox": "firefox",
    "safari": "safari", "edge": "edge", "google": "google",
    "fortinet": "fortinet", "fortigate": "fortigate", "cisco": "cisco",
    "vmware": "vmware", "ivanti": "ivanti", "citrix": "citrix",
    "atlassian": "atlassian", "confluence": "confluence", "zimbra": "zimbra",
    "wordpress": "wordpress", "openssl": "openssl", "oracle": "oracle",
    "sap": "sap", "jenkins": "jenkins", "gitlab": "gitlab",
    "juniper": "juniper", "palo alto": "palo-alto", "sonicwall": "sonicwall",
    "solarwinds": "solarwinds", "progress telerik": "telerik",
    "moveit": "moveit", "phishing": "phishing", "ransomware": "ransomware",
    "lockbit": "lockbit", "blackcat": "blackcat", "alphv": "alphv",
    "cl0p": "cl0p", "lazarus": "lazarus", "apt28": "apt28", "apt29": "apt29",
    "fancy bear": "apt28", "cozy bear": "apt29", "volt typhoon": "volt-typhoon",
    "infostealer": "infostealer", "zero-day": "zero-day",
}

SLUG_STOPWORDS = {
    "a", "an", "the", "of", "in", "on", "for", "to", "and", "or", "is",
    "are", "as", "at", "by", "with", "from", "that", "this", "it", "its",
    "new", "via", "over", "after", "amid", "into", "up",
}

CRITICAL_SIGNALS = (
    "critical", "actively exploited", "in the wild", "zero-day",
    "emergency patch", "kev", "known exploited",
)
HIGH_SIGNALS = (
    "ransomware", "data breach", "exploited", "severe", "high severity",
    "backdoor", "supply chain",
)

# Ransomware coverage often names the gang or describes extortion tactics
# without ever using the word "ransomware".
RANSOMWARE_SIGNALS = (
    "ransomware", "lockbit", "blackcat", "alphv", "cl0p", "cl0p^_-",
    "akira", "qilin", "medusa", "ransomhub", "bianlian", "8base",
    "rhysida", "hunters international", "black basta", "play crew",
    "inc ransom", "lynx", "dragonforce", "ransom demand", "ransom note",
    "double extortion", "data extortion", "leak site", "leaked victims",
    "decryptor", "encrypted files",
)

# Industrial / OT (ICS-SCADA) signals -> "ot-security" category.
OT_SIGNALS = (
    "scada", "modbus", "dnp3", "s7comm", "opc ua", "ethercat",
    "hmi", "plc ", "plcs", " plc,", "industrial control", "ics-cert",
    "ics advisory", "icsa-", "operational technology", "ot security",
    "ot environment", "critical infrastructure", "power grid", "substation",
    "water utility", "water treatment", "energy facility", "field device",
    "engineering workstation", "rtu",
)

SYSTEM_PROMPT = """You are the automated senior threat-intelligence analyst \
for Cyberpent (cyberpent.cc.cd), a cybersecurity news service.

STRICT RULES:
1. Use ONLY facts contained in the provided source material. NEVER invent \
CVE IDs, IOCs, dates, company names, statistics, or quotes.
2. If a detail is unknown or unverified, omit it or write "not yet confirmed". \
Never speculate or editorialize.
3. Output EXACTLY these three H3 sections, in this order, with nothing above \
the first heading:
### Executive Summary
### Technical Impact & Root Cause
### Mitigation & IOCs
4. Each section contains 2-4 short paragraphs or bullet points written in a \
concise, professional SOC tone.
5. In "Mitigation & IOCs", list concrete mitigations from the source, then \
any provided CVE IDs / hashes / IPs / domains under bold labels. If no IOCs \
are available, say so explicitly instead of inventing them.
6. Do not wrap the output in Markdown code fences."""

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%SZ",
    stream=sys.stdout,
)
log = logging.getLogger("cyberpent")

# --------------------------------------------------------------------------- #
# Data models
# --------------------------------------------------------------------------- #


class RawEntry(BaseModel):
    """A normalized item pulled from an RSS/Atom feed."""
    source: str
    title: str
    url: str
    summary: str
    published: datetime


class EnrichedEntry(RawEntry):
    """A raw entry plus extraction results and classification."""
    cves: List[str] = Field(default_factory=list)
    hashes: List[str] = Field(default_factory=list)
    ips: List[str] = Field(default_factory=list)
    domains: List[str] = Field(default_factory=list)
    categories: List[str] = Field(default_factory=list)
    tags: List[str] = Field(default_factory=list)
    severity: str = "medium"
    confidence: int = 50


# --------------------------------------------------------------------------- #
# Small utilities
# --------------------------------------------------------------------------- #


def uniq(items) -> List[str]:
    """Order-preserving de-duplication."""
    seen: Set[str] = set()
    out: List[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def normalize_url(url: str) -> str:
    """Canonical form: lowercase host part, drop tracking params/fragments."""
    url = (url or "").strip()
    url = re.sub(r"[?&](utm_[^=&]*|fbclid|gclid|ref)=[^&]*", "", url)
    url = url.split("#", 1)[0].rstrip("/")
    return url


def strip_html(raw: Optional[str]) -> str:
    """Convert HTML fragments to clean single-line plain text."""
    text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", raw or "")
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = htmllib.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def slugify(title: str) -> str:
    words = re.findall(r"[a-z0-9]+", title.lower())
    kept = [w for w in words if w not in SLUG_STOPWORDS] or words
    return ("-".join(kept)[:60].rstrip("-")) or "intel-alert"


def parse_published(entry) -> datetime:
    struct = entry.get("published_parsed") or entry.get("updated_parsed")
    if struct:
        dt = datetime(*struct[:6], tzinfo=timezone.utc)
    else:
        dt = datetime.now(timezone.utc)
    now = datetime.now(timezone.utc)
    if dt > now + timedelta(minutes=30):  # future-dated feeds
        dt = now
    return dt


def is_public_ip(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return addr.version == 4 and not addr.is_private and not addr.is_loopback \
        and not addr.is_link_local and not addr.is_multicast \
        and not addr.is_reserved


def domain_is_source(domain: str) -> bool:
    return any(domain == d or domain.endswith("." + d) for d in SOURCE_DOMAINS)


# --------------------------------------------------------------------------- #
# State persistence
# --------------------------------------------------------------------------- #


def load_state() -> Set[str]:
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return {str(u) for u in data}
    except FileNotFoundError:
        pass
    except json.JSONDecodeError:
        log.warning("state.json is corrupted - starting a fresh state")
    return set()


def save_state(state: Set[str]) -> None:
    ordered = sorted(state)[-MAX_STATE_ENTRIES:]
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(ordered, indent=2) + "\n", encoding="utf-8")
    log.info("state.json updated (%d tracked URLs)", len(ordered))


def existing_source_urls() -> Set[str]:
    """URLs already cited by any committed post (belt-and-braces dedup)."""
    urls: Set[str] = set()
    if not POSTS_DIR.exists():
        return urls
    for path in POSTS_DIR.glob("*.md"):
        try:
            meta = frontmatter.load(path).metadata
            src = meta.get("source_url") or meta.get("original_url")
            if src:
                urls.add(normalize_url(str(src)))
        except Exception:  # noqa: BLE001 - never fail the run over one bad file
            continue
    return urls


def recent_post_slug_words(days: int = 7) -> List[Set[str]]:
    """Word-sets from slugs of posts published in the last `days` days."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    result: List[Set[str]] = []
    if not POSTS_DIR.exists():
        return result
    for path in POSTS_DIR.glob("*.md"):
        match = re.match(r"^(\d{4}-\d{2}-\d{2})-(.+)$", path.stem)
        if not match:
            continue
        try:
            day = datetime.strptime(match.group(1), "%Y-%m-%d").replace(
                tzinfo=timezone.utc
            )
        except ValueError:
            continue
        if day >= cutoff:
            result.append(set(match.group(2).split("-")))
    return result


def is_duplicate_story(slug_words: Set[str], recent: List[Set[str]]) -> bool:
    """Jaccard >= 0.85 against a recent slug => same story, other outlet."""
    for other in recent:
        union = slug_words | other
        if union and len(slug_words & other) / len(union) >= 0.85:
            return True
    return False


# --------------------------------------------------------------------------- #
# Ingestion
# --------------------------------------------------------------------------- #


def fetch_feed(name: str, url: str) -> List[RawEntry]:
    last_error: Optional[Exception] = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.get(
                url,
                headers={"User-Agent": USER_AGENT},
                timeout=FETCH_TIMEOUT,
            )
            resp.raise_for_status()
            parsed = feedparser.parse(resp.content)

            entries: List[RawEntry] = []
            for e in parsed.entries:
                link = normalize_url(e.get("link", ""))
                title = strip_html(e.get("title", ""))
                if not link or not title:
                    continue
                summary = strip_html(
                    e.get("summary") or e.get("description") or ""
                )
                entries.append(
                    RawEntry(
                        source=name,
                        title=title,
                        url=link,
                        summary=summary,
                        published=parse_published(e),
                    )
                )
            log.info("%-18s fetched %2d entries", name, len(entries))
            return entries
        except Exception as exc:  # noqa: BLE001 - retry any transport error
            last_error = exc
            wait = RETRY_BACKOFF_SECONDS * attempt
            log.warning("%s: attempt %d/%d failed: %s (retry in %ds)",
                        name, attempt, MAX_RETRIES, exc, wait)
            time.sleep(wait)

    log.error("%s: giving up after %d attempts (%s)", name, MAX_RETRIES,
              last_error)
    return []


def fetch_bluesky(name: str, handle: str) -> List[RawEntry]:
    """Pull recent posts from a Bluesky account via the public AppView API."""
    url = ("https://public.api.bsky.app/xrpc/app.bsky.feed.getAuthorFeed"
           f"?actor={handle}&limit=15")
    try:
        resp = requests.get(url, timeout=FETCH_TIMEOUT)
        resp.raise_for_status()
        entries: List[RawEntry] = []
        for item in resp.json().get("feed", []):
            post = item.get("post", {})
            record = post.get("record", {})
            text = (record.get("text") or "").strip()
            uri = post.get("uri", "")
            if not text or not uri.startswith("at://"):
                continue
            rkey = uri.rsplit("/", 1)[-1]
            author_did = uri.split("/")[2]
            link = normalize_url(
                f"https://bsky.app/profile/{author_did}/post/{rkey}"
            )
            created = record.get("createdAt") or post.get("indexedAt") or ""
            try:
                published = datetime.fromisoformat(
                    created.replace("Z", "+00:00"))
            except ValueError:
                published = datetime.now(timezone.utc)
            first_line = text.splitlines()[0][:90]
            entries.append(
                RawEntry(
                    source=name,
                    title=first_line,
                    url=link,
                    summary=text[:1500],
                    published=published,
                )
            )
        log.info("%-18s fetched %2d posts", name, len(entries))
        return entries
    except Exception as exc:  # noqa: BLE001 - degrade like any other feed
        log.warning("%s: fetch failed (%s)", name, exc)
        return []


def fetch_telegram(name: str, channel: str) -> List[RawEntry]:
    """Scrape a public Telegram channel via its t.me/s/ web preview."""
    url = f"https://t.me/s/{channel}"
    try:
        resp = requests.get(
            url,
            headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64)"},
            timeout=FETCH_TIMEOUT,
        )
        resp.raise_for_status()
        html = resp.text
        entries: List[RawEntry] = []
        segments = html.split('class="tgme_widget_message ')
        for seg in segments[1:]:
            id_match = re.search(r'data-post="' + channel + r'/(\d+)"', seg)
            time_match = re.search(r'<time[^>]+datetime="([^"]+)"', seg)
            text_match = re.search(
                r'class="tgme_widget_message_text[^"]*"[^>]*>(.*?)<time',
                seg, re.DOTALL)
            if not (id_match and time_match):
                continue
            try:
                published = datetime.fromisoformat(
                    time_match.group(1).replace("Z", "+00:00"))
            except ValueError:
                continue
            body = strip_html(text_match.group(1)) if text_match else ""
            if not body:
                continue
            link = normalize_url(f"https://t.me/{channel}/{id_match.group(1)}")
            entries.append(
                RawEntry(
                    source=name,
                    title=body.splitlines()[0][:90],
                    url=link,
                    summary=body[:1500],
                    published=published,
                )
            )
        log.info("%-18s fetched %2d posts", name, len(entries))
        return entries
    except Exception as exc:  # noqa: BLE001 - degrade like any other feed
        log.warning("%s: fetch failed (%s)", name, exc)
        return []


# --------------------------------------------------------------------------- #
# Enrichment
# --------------------------------------------------------------------------- #


def extract_cves(text: str) -> List[str]:
    return uniq(m.group(0).upper() for m in CVE_RE.finditer(text))


def extract_hashes(text: str) -> List[str]:
    found = uniq(m.group(0).lower() for m in SHA256_RE.finditer(text))
    found += uniq(m.group(0).lower() for m in SHA1_RE.finditer(text))
    found += uniq(m.group(0).lower() for m in MD5_RE.finditer(text))
    return uniq(found)


def extract_ips(text: str) -> List[str]:
    candidates = uniq(m.group(0) for m in IPV4_RE.finditer(text))
    return [ip for ip in candidates if is_public_ip(ip)]


def extract_domains(text: str) -> List[str]:
    candidates = uniq(m.group(0).lower().rstrip(".") for m in DOMAIN_RE.finditer(text))
    return [d for d in candidates if not domain_is_source(d)]


def derive_categories(text_lower: str, cves: List[str]) -> List[str]:
    cats = ["threat-intel"]
    if cves or "vulnerab" in text_lower or "zero-day" in text_lower \
            or "exploit" in text_lower:
        cats.insert(0, "cve")
    if any(signal in text_lower for signal in RANSOMWARE_SIGNALS):
        cats.append("ransomware")
    if any(signal in text_lower for signal in OT_SIGNALS):
        cats.append("ot-security")
    return uniq(cats)


def derive_tags(text_lower: str, cves: List[str]) -> List[str]:
    tags = [tag for needle, tag in TAG_VOCAB.items() if needle in text_lower]
    tags.extend(cve.lower() for cve in cves[:3])
    return uniq(tags)[:8]


def score_severity(text_lower: str, cves: List[str]) -> Tuple[str, int]:
    score = 35
    if cves:
        score += 20
    if any(sig in text_lower for sig in CRITICAL_SIGNALS):
        score += 25
    if "cisa" in text_lower or " kev" in text_lower:
        score += 10
    if any(sig in text_lower for sig in HIGH_SIGNALS):
        score += 15
    score = min(score, 100)

    if score >= 80:
        severity = "critical"
    elif score >= 60:
        severity = "high"
    elif score >= 40:
        severity = "medium"
    else:
        severity = "low"
    return severity, score


def enrich(entry: RawEntry) -> EnrichedEntry:
    blob = f"{entry.title}\n{entry.summary}"
    text_lower = blob.lower()
    cves = extract_cves(blob)

    return EnrichedEntry(
        **entry.model_dump(),
        cves=cves,
        hashes=extract_hashes(blob),
        ips=extract_ips(blob),
        domains=extract_domains(blob),
        categories=derive_categories(text_lower, cves),
        tags=derive_tags(text_lower, cves),
        **dict(zip(("severity", "confidence"), score_severity(text_lower, cves))),
    )


# --------------------------------------------------------------------------- #
# LLM synthesis
# --------------------------------------------------------------------------- #


def build_user_prompt(entry: EnrichedEntry) -> str:
    content = entry.summary[:MAX_CONTENT_CHARS]
    return (
        f"Source outlet : {entry.source}\n"
        f"Published UTC : {entry.published:%Y-%m-%d %H:%M}\n"
        f"Title         : {entry.title}\n"
        f"URL           : {entry.url}\n\n"
        f"Detected CVE IDs : {', '.join(entry.cves) if entry.cves else 'none detected'}\n"
        f"Detected hashes  : {', '.join(entry.hashes[:10]) if entry.hashes else 'none detected'}\n"
        f"Detected IPs     : {', '.join(entry.ips[:10]) if entry.ips else 'none detected'}\n"
        f"Detected domains : {', '.join(entry.domains[:10]) if entry.domains else 'none detected'}\n\n"
        "Raw article content (HTML stripped):\n"
        '"""\n'
        f"{content}\n"
        '"""'
    )


def finalize_body(body: str, entry: EnrichedEntry) -> str:
    """Clean up LLM output and guarantee structure + source attribution."""
    body = body.strip()

    # Drop a wrapping code fence, if the model added one.
    if body.startswith("```"):
        newline = body.find("\n")
        body = body[newline + 1:] if newline != -1 else body.strip("` \n")
    if body.rstrip().endswith("```"):
        body = body.rstrip()[:-3].rstrip()

    # Drop any headings above the first required section (stray H1/H2 titles).
    lines = body.splitlines()
    while lines and lines[0].lstrip().startswith("#") \
            and not any(lines[0].strip().startswith(s) for s in REQUIRED_SECTIONS):
        lines.pop(0)
    body = "\n".join(lines).strip()

    missing = [s for s in REQUIRED_SECTIONS if s not in body]
    if missing:
        log.warning("LLM output missing sections %s - appending fallback",
                    missing)
        for section in missing:
            body += (f"\n\n{section}\n\nDetails pending verification - "
                     f"consult the original source advisory.")

    body += (
        f"\n\n---\n\n**Original source:** [{entry.source}]({entry.url})"
        "\n\n> This report was auto-generated by the Cyberpent intelligence "
        "pipeline. Always verify against official vendor advisories and "
        "[NVD](https://nvd.nist.gov) before acting.\n"
    )
    return body


def synthesize_report(entry: EnrichedEntry, api_key: str) -> str:
    client = genai.Client(api_key=api_key)

    config_kwargs = {
        "system_instruction": SYSTEM_PROMPT,
        "temperature": 0.2,
        "max_output_tokens": 3000,
    }
    # Thinking tokens consume the output budget and add latency; disable them
    # for Gemini 2.5 Flash-class models where they are optional.
    if "2.5" in GEMINI_MODEL:
        config_kwargs["thinking_config"] = genai_types.ThinkingConfig(
            thinking_budget=0
        )

    response = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=build_user_prompt(entry),
        config=genai_types.GenerateContentConfig(**config_kwargs),
    )
    content = response.text or ""
    if not content.strip():
        raise RuntimeError("Gemini returned an empty completion")
    return finalize_body(content, entry)


# --------------------------------------------------------------------------- #
# Publishing
# --------------------------------------------------------------------------- #


def write_post(entry: EnrichedEntry, body: str) -> Path:
    POSTS_DIR.mkdir(parents=True, exist_ok=True)
    date_str = entry.published.strftime("%Y-%m-%d")
    stem = f"{date_str}-{slugify(entry.title)}"
    path = POSTS_DIR / f"{stem}.md"

    # Same-day filename collision from a different story -> disambiguate.
    if path.exists():
        digest = hashlib.sha1(entry.url.encode("utf-8")).hexdigest()[:8]
        path = POSTS_DIR / f"{stem}-{digest}.md"

    post = frontmatter.Post(
        body,
        layout="radar_post",
        title=entry.title,
        date=entry.published.isoformat(),
        categories=entry.categories,
        tags=entry.tags,
        cves=entry.cves,
        iocs={
            "hashes": entry.hashes,
            "ips": entry.ips,
            "domains": entry.domains,
        },
        severity=entry.severity,
        confidence=entry.confidence,
        source=entry.source,
        source_url=entry.url,
    )
    path.write_text(frontmatter.dumps(post) + "\n", encoding="utf-8")
    log.info("Published %-45s [%s/%d]", path.name, entry.severity,
             entry.confidence)
    return path


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #


def main() -> int:
    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key:
        log.error("GEMINI_API_KEY is not set. Get a free key at "
                  "https://aistudio.google.com/apikey and add it via repo "
                  "Settings -> Secrets and variables -> Actions.")
        return 1

    cutoff = datetime.now(timezone.utc) - timedelta(hours=LOOKBACK_HOURS)
    state = load_state()
    already_published = existing_source_urls()
    recent_slugs = recent_post_slug_words()

    # ---- 1. Ingest ---------------------------------------------------------
    raw_entries: List[RawEntry] = []
    seen_this_run: Set[str] = set()
    for name, url in FEEDS.items():
        for entry in fetch_feed(name, url):
            if entry.url not in seen_this_run:
                seen_this_run.add(entry.url)
                raw_entries.append(entry)
    for name, handle in BSKY_ACCOUNTS.items():
        for entry in fetch_bluesky(name, handle):
            if entry.url not in seen_this_run:
                seen_this_run.add(entry.url)
                raw_entries.append(entry)
    for name, channel in TELEGRAM_CHANNELS.items():
        for entry in fetch_telegram(name, channel):
            if entry.url not in seen_this_run:
                seen_this_run.add(entry.url)
                raw_entries.append(entry)
    log.info("Ingested %d unique raw entries across %d feeds",
             len(raw_entries), len(FEEDS))

    # ---- 2. Filter ---------------------------------------------------------
    candidates: List[RawEntry] = []
    for entry in raw_entries:
        if entry.url in state or entry.url in already_published:
            continue
        if entry.published < cutoff:
            continue
        blob = f"{entry.title} {entry.summary}".lower()
        if not any(keyword in blob for keyword in RELEVANCE_KEYWORDS):
            continue
        candidates.append(entry)

    candidates.sort(key=lambda e: e.published, reverse=True)
    candidates = candidates[:MAX_POSTS_PER_RUN]
    log.info("%d candidate(s) queued for synthesis", len(candidates))

    # ---- 3. Synthesize & publish -------------------------------------------
    published = 0
    linkedin_queue: List[dict] = []
    for entry in candidates:
        enriched = enrich(entry)

        slug_words = set(slugify(enriched.title).split("-"))
        if is_duplicate_story(slug_words, recent_slugs):
            log.info("Skipping probable duplicate story: %s", enriched.title)
            state.add(enriched.url)
            continue

        try:
            body = synthesize_report(enriched, api_key)
        except Exception as exc:  # noqa: BLE001 - keep pipeline alive
            log.error("Synthesis failed for '%s': %s (will retry next run)",
                      enriched.title, exc)
            continue

        path = write_post(enriched, body)
        state.add(enriched.url)
        recent_slugs.append(slug_words)
        published += 1
        linkedin_queue.append({
            "title": enriched.title,
            "severity": enriched.severity,
            "confidence": enriched.confidence,
            "source": enriched.source,
            "cves": enriched.cves,
            "stem": path.name,
        })
        time.sleep(LLM_THROTTLE_SECONDS)

    # ---- 4. Persist --------------------------------------------------------
    save_state(state)
    if linkedin_queue:
        try:
            from linkedin_publish import share_all
            shared = share_all(linkedin_queue)
            log.info("LinkedIn: %d/%d report(s) shared.", shared,
                     len(linkedin_queue))
        except Exception as exc:  # noqa: BLE001 - never block the radar
            log.error("LinkedIn sharing skipped: %s", exc)
    log.info("Run complete - %d new report(s) published.", published)
    return 0


if __name__ == "__main__":
    sys.exit(main())
