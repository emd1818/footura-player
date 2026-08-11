#!/usr/bin/env python3
"""
FOOTURA Football Trials Crawler
- Scans 7 language spaces: Bulgarian, English, Spanish, French,
  Portuguese, Russian and Chinese.
- Keeps curated sources from clubs, academies, agencies, showcases,
  camps and player-management companies.
- Updates data/football_trials.json.
- Intended to run from GitHub Actions every 24 hours.
"""

import hashlib
import html
import json
import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "football_trials.json"
USER_AGENT = "FOOTURA-PLAYER-TrialsCrawler/1.0"

LANGS = {
    "bg": {
        "hl": "bg",
        "gl": "BG",
        "ceid": "BG:bg",
        "queries": [
            "футболни проби",
            "футболен кастинг",
            "футболна селекция",
            "пробна тренировка футбол",
            "футболни проби деца",
            "футболни проби жени",
        ],
    },
    "en": {
        "hl": "en",
        "gl": "US",
        "ceid": "US:en",
        "queries": [
            "football trials",
            "soccer tryouts",
            "open football trials",
            "academy trials",
            "youth football trials",
            "women football trials",
            "professional football trials",
            "football showcase",
            "football player internship",
            "football agency trials",
        ],
    },
    "es": {
        "hl": "es",
        "gl": "ES",
        "ceid": "ES:es",
        "queries": [
            "pruebas de fútbol",
            "captación jugadores fútbol",
            "selección fútbol base",
            "pruebas fútbol femenino",
            "showcase fútbol",
            "agencia fútbol pruebas",
        ],
    },
    "fr": {
        "hl": "fr",
        "gl": "FR",
        "ceid": "FR:fr",
        "queries": [
            "détection football",
            "essais football jeunes",
            "détection football féminin",
            "recrutement jeunes football",
            "showcase football",
            "agence football détection",
        ],
    },
    "pt": {
        "hl": "pt-BR",
        "gl": "BR",
        "ceid": "BR:pt-419",
        "queries": [
            "peneira futebol",
            "avaliação atletas futebol",
            "seletiva futebol",
            "captação jogadores futebol",
            "peneira futebol feminino",
            "showcase futebol",
        ],
    },
    "ru": {
        "hl": "ru",
        "gl": "RU",
        "ceid": "RU:ru",
        "queries": [
            "футбольные просмотры",
            "просмотр футбол академия",
            "отбор футболистов",
            "селекция футболистов",
            "футбольные пробы дети",
            "футбольные просмотры женщины",
        ],
    },
    "zh": {
        "hl": "zh-CN",
        "gl": "CN",
        "ceid": "CN:zh-Hans",
        "queries": [
            "足球试训",
            "足球俱乐部试训",
            "足球青训选拔",
            "足球青训招生",
            "足球运动员选拔",
            "足球试训机构",
        ],
    },
}

KEYWORDS = re.compile(
    r"""
    trial|trials|tryout|try-outs|showcase|internship|player placement|
    football camp|soccer camp|academy trial|open trial|selection|
    recruitment|scouting|talent identification|проб|кастинг|селек|
    просмотр|отбор|пруба|prueba|captación|selección|détection|essai|
    recrutement|peneira|seletiva|avaliação|captação|试训|选拔|青训|招生|招募
    """,
    re.I | re.X,
)

CURATED_SOURCES = [
    {
        "url": "https://soccercampsinternational.com/",
        "name": "Soccer Camps International",
        "lang": "en",
        "provider_type": "Management / camp provider",
        "program_type": "International football camp / club pathway",
    },
    {
        "url": "https://www.futbollab.com/en/internship/player",
        "name": "FutbolLab",
        "lang": "en",
        "provider_type": "Management / football education company",
        "program_type": "Player internship / managed football stay",
    },
    {
        "url": "https://www.pscsocceracademy.com/pro-soccer-tryouts",
        "name": "PSC Soccer Academy",
        "lang": "en",
        "provider_type": "Football academy / management",
        "program_type": "Paid professional tryout / player placement",
    },
    {
        "url": "https://www.pscsocceracademy.com/womens-pro-soccer-tryouts",
        "name": "PSC Women",
        "lang": "en",
        "provider_type": "Football academy / management",
        "program_type": "Professional women tryout / agency pathway",
    },
    {
        "url": "https://futedu.es/futedu-soccer-showcase-2026",
        "name": "Futedu",
        "lang": "es",
        "provider_type": "Football management / showcase provider",
        "program_type": "Paid showcase / scouting event",
    },
    {
        "url": "https://www.golafly.com/trial-showcase",
        "name": "Golafly",
        "lang": "en",
        "provider_type": "Football management / showcase provider",
        "program_type": "Paid trial / showcase",
    },
    {
        "url": "https://jnmfootball.com/",
        "name": "JNM Football",
        "lang": "en",
        "provider_type": "Football management / agency",
        "program_type": "Managed club trial placement",
    },
    {
        "url": "https://footballtryouts.eu/en/",
        "name": "Football Tryouts Prague",
        "lang": "en",
        "provider_type": "Football scouting / event provider",
        "program_type": "Scouting event / trial pathway",
    },
    {
        "url": "https://wsfc7.com/",
        "name": "WS FC7",
        "lang": "en",
        "provider_type": "Football agency / management",
        "program_type": "Football tests / player pathway",
    },
]


def fetch_url(url, timeout=25):
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.geturl(), response.read()


def clean_text(value):
    value = html.unescape(value or "")
    value = re.sub(r"<script[\s\S]*?</script>", " ", value, flags=re.I)
    value = re.sub(r"<style[\s\S]*?</style>", " ", value, flags=re.I)
    value = re.sub(r"<[^>]+>", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def canonical_url(url, raw_html):
    match = re.search(
        r'<link[^>]+rel=["\']canonical["\'][^>]+href=["\']([^"\']+)',
        raw_html,
        flags=re.I,
    )
    if match:
        return urllib.parse.urljoin(url, html.unescape(match.group(1)))
    return url


def extract_title(raw_html):
    match = re.search(r"<title[^>]*>(.*?)</title>", raw_html, flags=re.I | re.S)
    return clean_text(match.group(1)) if match else ""


def extract_dates(text):
    found = []

    patterns = [
        r"\b(20\d{2})[-/.](\d{1,2})[-/.](\d{1,2})\b",
        r"\b(\d{1,2})[-/.](\d{1,2})[-/.](20\d{2})\b",
    ]

    for pattern in patterns:
        for match in re.finditer(pattern, text):
            try:
                groups = match.groups()
                if groups[0].startswith("20"):
                    y, m, d = map(int, groups)
                else:
                    d, m, y = int(groups[0]), int(groups[1]), int(groups[2])
                found.append(date(y, m, d))
            except ValueError:
                pass

    return sorted(set(found))


def extract_age_range(text):
    match = re.search(
        r"\bU[- ]?(\d{1,2})\b|\b(?:ages?|age)\s*(\d{1,2})\s*[-–]\s*(\d{1,2})\b",
        text,
        flags=re.I,
    )

    if not match:
        return None, None

    values = [int(value) for value in match.groups() if value]
    if len(values) == 1:
        return values[0], values[0]
    return min(values), max(values)


def make_id(source_url, start_date):
    raw = f"{source_url}|{start_date or ''}".encode("utf-8")
    return "trial-" + hashlib.sha1(raw).hexdigest()[:14]


def verify_source(source, discovered_description=""):
    try:
        final_url, body = fetch_url(source["url"])
        raw_html = body.decode("utf-8", errors="ignore")[:500_000]
        page_text = clean_text(raw_html)

        title = extract_title(raw_html)
        evidence = (
            f"{source['name']} {source['program_type']} "
            f"{title} {discovered_description} {page_text[:150_000]}"
        )

        if not KEYWORDS.search(evidence):
            return None

        dates = [d for d in extract_dates(page_text) if d >= date.today()]
        age_min, age_max = extract_age_range(page_text)
        final_url = canonical_url(final_url, raw_html)

        start_date = dates[0].isoformat() if dates else None
        end_date = dates[-1].isoformat() if dates else None

        return {
            "id": make_id(final_url, start_date),
            "title": title or source["name"],
            "description": page_text[:900],
            "source_url": final_url,
            "source_name": source["name"],
            "language": source["lang"],
            "trial_start": start_date,
            "trial_end": end_date,
            "age_min": age_min,
            "age_max": age_max,
            "status": "ACTIVE" if dates else "REVIEW",
            "verified_at": datetime.now(timezone.utc).isoformat(),
            "provider_type": source["provider_type"],
            "program_type": source["program_type"],
        }

    except Exception as exc:
        print(f"VERIFY ERROR: {source['url']} -> {exc}")
        return None


def google_news_rss(language_code, query):
    cfg = LANGS[language_code]

    url = (
        "https://news.google.com/rss/search?q="
        + urllib.parse.quote(query)
        + "&hl="
        + urllib.parse.quote(cfg["hl"])
        + "&gl="
        + urllib.parse.quote(cfg["gl"])
        + "&ceid="
        + urllib.parse.quote(cfg["ceid"])
    )

    _, body = fetch_url(url)
    root = ET.fromstring(body)

    results = []

    for item in root.findall(".//item"):
        item_title = html.unescape(item.findtext("title") or "")
        item_url = item.findtext("link") or ""
        description = clean_text(item.findtext("description") or "")

        if item_url and KEYWORDS.search(item_title + " " + description):
            results.append((item_title, item_url, description))

    return results


def load_database():
    if not DB.exists():
        return {"records": []}

    try:
        data = json.loads(DB.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    except Exception as exc:
        print(f"WARNING: Could not read database: {exc}")

    return {"records": []}


def main():
    database = load_database()
    records = database.get("records", [])

    if not isinstance(records, list):
        records = []

    indexed = {
        (record.get("source_url"), record.get("trial_start")): record
        for record in records
        if record.get("source_url")
    }

    discovered = 0
    verified = 0

    # 1. Always check curated sources.
    for source in CURATED_SOURCES:
        discovered += 1
        record = verify_source(source, "Curated FOOTURA opportunity source")

        if record:
            verified += 1
            key = (record["source_url"], record["trial_start"])
            indexed[key] = record

    # 2. Search seven language spaces.
    for language_code, cfg in LANGS.items():
        for query in cfg["queries"]:
            try:
                results = google_news_rss(language_code, query)

                for item_title, item_url, description in results:
                    discovered += 1

                    source = {
                        "url": item_url,
                        "name": item_title[:200],
                        "lang": language_code,
                        "provider_type": "Discovered football opportunity source",
                        "program_type": "Club / academy / federation / agency trial",
                    }

                    record = verify_source(source, description)

                    if record:
                        verified += 1
                        key = (record["source_url"], record["trial_start"])
                        indexed[key] = record

            except Exception as exc:
                print(
                    f"SEARCH ERROR: language={language_code}, "
                    f"query={query!r} -> {exc}"
                )

    # 3. Recalculate ACTIVE / PAST status.
    today = date.today()

    for record in indexed.values():
        try:
            if record.get("trial_end"):
                end_date = date.fromisoformat(record["trial_end"])
                if end_date < today:
                    record["status"] = "PAST"
        except (ValueError, TypeError):
            pass

    output = {
        "records": list(indexed.values()),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "last_full_scan": today.isoformat(),
        "last_scan_discovered": discovered,
        "last_scan_verified": verified,
        "crawler_version": "3.0",
    }

    DB.parent.mkdir(parents=True, exist_ok=True)
    DB.write_text(
        json.dumps(output, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(
        "FOOTURA scan complete: "
        f"{discovered} candidates, "
        f"{verified} verified, "
        f"{len(indexed)} total records."
    )


if __name__ == "__main__":
    main()
