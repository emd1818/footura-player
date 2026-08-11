#!/usr/bin/env python3
"""
FOOTURA Football Trials Crawler - production data-quality version

Rules:
- A record is ACTIVE only when a real future event date is found.
- Pages without a future event date are NOT inserted as trials.
- Old records are removed from the active database.
- Google News is used for discovery, but the linked page must itself contain
  evidence of a future football trial/showcase/camp/selection.
- Curated management-company / paid-showcase sources are included.
- Network requests are bounded and run in parallel.
- The JSON schema keeps the fields used by the FOOTURA application.
"""

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import quote, urljoin
from urllib.request import Request, urlopen
import hashlib
import html
import json
import re
import urllib.parse
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "football_trials.json"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
HTTP_TIMEOUT = 12
MAX_BYTES = 500_000
MAX_WORKERS = 8

TRIAL_WORDS = re.compile(
    r"\b("
    r"trial|trials|tryout|try-outs|showcase|"
    r"football camp|soccer camp|academy trial|open trial|"
    r"selection|recruitment|scouting|talent identification|"
    r"player placement|internship|"
    r"проб|проби|кастинг|селек|селекция|просмотр|отбор|"
    r"prueba|pruebas|captación|selección|"
    r"détection|essai|recrutement|"
    r"peneira|seletiva|avaliação|captação|"
    r"футбольные просмотры|отбор футболистов"
    r")\b",
    re.I,
)

MONTHS = {
    # English
    "january": 1, "february": 2, "march": 3, "april": 4,
    "may": 5, "june": 6, "july": 7, "august": 8,
    "september": 9, "october": 10, "november": 11, "december": 12,
    # Spanish
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5,
    "junio": 6, "julio": 7, "agosto": 8, "septiembre": 9,
    "setiembre": 9, "octubre": 10, "noviembre": 11, "diciembre": 12,
    # French
    "janvier": 1, "février": 2, "fevrier": 2, "mars": 3, "avril": 4,
    "mai": 5, "juin": 6, "juillet": 7, "août": 8, "aout": 8,
    "septembre": 9, "octobre": 10, "novembre": 11, "décembre": 12,
    "decembre": 12,
    # Portuguese
    "janeiro": 1, "fevereiro": 2, "março": 3, "marco": 3, "abril": 4,
    "maio": 5, "junho": 6, "julho": 7, "agosto": 8, "setembro": 9,
    "outubro": 10, "novembro": 11, "dezembro": 12,
    # Bulgarian
    "януари": 1, "февруари": 2, "март": 3, "април": 4, "май": 5,
    "юни": 6, "юли": 7, "август": 8, "септември": 9,
    "октомври": 10, "ноември": 11, "декември": 12,
    # Russian (nominative and genitive forms, as used in dates)
    "январь": 1, "января": 1, "февраль": 2, "февраля": 2,
    "март": 3, "марта": 3, "апрель": 4, "апреля": 4,
    "май": 5, "мая": 5, "июнь": 6, "июня": 6,
    "июль": 7, "июля": 7, "август": 8, "августа": 8,
    "сентябрь": 9, "сентября": 9, "октябрь": 10, "октября": 10,
    "ноябрь": 11, "ноября": 11, "декабрь": 12, "декабря": 12,
}

LANGS = {
    "bg": ("bg", "BG", "BG:bg", [
        "футболни проби", "футболен кастинг", "футболна селекция",
        "пробна тренировка футбол", "футболни проби деца",
        "футболни проби жени",
    ]),
    "en": ("en", "US", "US:en", [
        "football trials", "soccer tryouts", "open football trials",
        "academy trials", "youth football trials", "women football trials",
        "professional football trials", "football showcase",
        "football player internship", "football agency trials",
    ]),
    "es": ("es", "ES", "ES:es", [
        "pruebas de fútbol", "captación jugadores fútbol",
        "selección fútbol base", "pruebas fútbol femenino",
        "showcase fútbol", "agencia fútbol pruebas",
    ]),
    "fr": ("fr", "FR", "FR:fr", [
        "détection football", "essais football jeunes",
        "détection football féminin", "recrutement jeunes football",
        "showcase football", "agence football détection",
    ]),
    "pt": ("pt-BR", "BR", "BR:pt-419", [
        "peneira futebol", "avaliação atletas futebol",
        "seletiva futebol", "captação jogadores futebol",
        "peneira futebol feminino", "showcase futebol",
    ]),
    "ru": ("ru", "RU", "RU:ru", [
        "футбольные просмотры", "просмотр футбол академия",
        "отбор футболистов", "селекция футболистов",
        "футбольные пробы дети", "футбольные просмотры женщины",
    ]),
}

# Clubs/academies AND paid management/showcase providers.
CURATED_SOURCES = [
    ("https://soccercampsinternational.com/", "Soccer Camps International",
     "en", "Management / camp provider", "International football camp / pathway"),
    ("https://www.futbollab.com/en/internship/player", "FutbolLab",
     "en", "Management / football education company", "Player internship / managed football stay"),
    ("https://www.pscsocceracademy.com/pro-soccer-tryouts", "PSC Soccer Academy",
     "en", "Football academy / management", "Paid professional tryout / player placement"),
    ("https://www.pscsocceracademy.com/womens-pro-soccer-tryouts", "PSC Women",
     "en", "Football academy / management", "Professional women tryout / pathway"),
    ("https://futedu.es/futedu-soccer-showcase-2026", "Futedu",
     "es", "Football management / showcase provider", "Paid showcase / scouting event"),
    ("https://www.golafly.com/trial-showcase", "Golafly",
     "en", "Football management / showcase provider", "Paid trial / showcase"),
    ("https://jnmfootball.com/", "JNM Football",
     "en", "Football management / agency", "Managed club trial placement"),
    ("https://footballtryouts.eu/en/", "Football scouting / event provider",
     "en", "Football scouting / event provider", "Scouting event / trial pathway"),
    ("https://wsfc7.com/", "WS FC7",
     "en", "Football agency / management", "Football tests / player pathway"),
]


def fetch_url(url, timeout=HTTP_TIMEOUT):
    req = Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        },
    )
    with urlopen(req, timeout=timeout) as response:
        content_type = response.headers.get("Content-Type", "")
        return response.geturl(), response.read(MAX_BYTES), content_type


def decode_body(body, content_type=""):
    """Decode HTML bytes using the declared charset when available.

    Defaulting everything to UTF-8 silently mangles pages served as
    GBK/GB2312/Windows-1251 (common for zh/ru/bg sources), which then
    fails every downstream date/keyword regex on that page.
    """
    candidates = []

    m = re.search(r"charset=([\w-]+)", content_type, re.I)
    if m:
        candidates.append(m.group(1))

    head = body[:2048].decode("ascii", errors="ignore")
    m = re.search(r'charset=["\']?\s*([\w-]+)', head, re.I)
    if m:
        candidates.append(m.group(1))

    candidates += ["utf-8", "gb18030", "windows-1251", "windows-1250"]

    for enc in candidates:
        try:
            return body.decode(enc)
        except (LookupError, UnicodeDecodeError):
            continue

    return body.decode("utf-8", errors="ignore")


def clean_text(value):
    value = html.unescape(value or "")
    value = re.sub(r"<script[\s\S]*?</script>", " ", value, flags=re.I)
    value = re.sub(r"<style[\s\S]*?</style>", " ", value, flags=re.I)
    value = re.sub(r"<[^>]+>", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def extract_title(raw):
    m = re.search(r"<title[^>]*>(.*?)</title>", raw, re.I | re.S)
    return clean_text(m.group(1)) if m else ""


def canonical_url(url, raw):
    m = re.search(
        r'<link[^>]+rel=["\']canonical["\'][^>]+href=["\']([^"\']+)',
        raw, re.I
    )
    return urljoin(url, html.unescape(m.group(1))) if m else url


def safe_date(y, m, d):
    try:
        result = date(int(y), int(m), int(d))
        return result
    except (ValueError, TypeError):
        return None


def extract_jsonld_dates(raw):
    """Extract Event startDate/endDate from JSON-LD when available."""
    dates = []

    for block in re.findall(
        r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        raw, re.I | re.S
    ):
        try:
            data = json.loads(html.unescape(block))
        except Exception:
            continue

        stack = data if isinstance(data, list) else [data]
        while stack:
            item = stack.pop()
            if isinstance(item, dict):
                typ = str(item.get("@type", "")).lower()
                if "event" in typ or any(
                    k in item for k in ("startDate", "endDate")
                ):
                    for key in ("startDate", "endDate"):
                        value = item.get(key)
                        if isinstance(value, str):
                            m = re.search(r"(20\d{2})-(\d{1,2})-(\d{1,2})", value)
                            if m:
                                d = safe_date(*m.groups())
                                if d:
                                    dates.append(d)
                for value in item.values():
                    if isinstance(value, (dict, list)):
                        stack.append(value)
            elif isinstance(item, list):
                stack.extend(item)

    return sorted(set(dates))


def extract_dates(text, raw_html):
    dates = extract_jsonld_dates(raw_html)

    # ISO dates: 2026-08-20
    for m in re.finditer(r"\b(20\d{2})[-/.](\d{1,2})[-/.](\d{1,2})\b", text):
        d = safe_date(*m.groups())
        if d:
            dates.append(d)

    # D/M/Y or D-M-Y
    for m in re.finditer(r"\b(\d{1,2})[-/.](\d{1,2})[-/.](20\d{2})\b", text):
        d = safe_date(m.group(3), m.group(2), m.group(1))
        if d:
            dates.append(d)

    # 20 August 2026 / August 20, 2026 etc.
    month_pattern = "|".join(re.escape(x) for x in sorted(MONTHS, key=len, reverse=True))
    patterns = [
        rf"\b(\d{{1,2}})\s+({month_pattern})\s*,?\s+(20\d{{2}})\b",
        rf"\b({month_pattern})\s+(\d{{1,2}})(?:st|nd|rd|th)?\s*,?\s+(20\d{{2}})\b",
    ]

    for pattern in patterns:
        for m in re.finditer(pattern, text, re.I):
            groups = m.groups()
            if groups[0].lower() in MONTHS:
                month = MONTHS[groups[0].lower()]
                day = groups[1]
                year = groups[2]
            else:
                day = groups[0]
                month = MONTHS[groups[1].lower()]
                year = groups[2]
            d = safe_date(year, month, day)
            if d:
                dates.append(d)

    return sorted(set(dates))


def extract_age_range(text):
    m = re.search(
        r"\bU[- ]?(\d{1,2})\b|"
        r"\b(?:ages?|age)\s*(\d{1,2})\s*[-–]\s*(\d{1,2})\b",
        text, re.I
    )
    if not m:
        return None, None
    vals = [int(x) for x in m.groups() if x]
    if len(vals) == 1:
        return vals[0], vals[0]
    return min(vals), max(vals)


def extract_location(text):
    patterns = [
        r"(?:location|venue|city|place|место|град|ubicación|lieu)\s*[:\-]\s*([^.;|]{3,100})",
    ]
    for pattern in patterns:
        m = re.search(pattern, text, re.I)
        if m:
            return m.group(1).strip()
    return None


def make_id(url, start):
    return "trial-" + hashlib.sha1(
        f"{url}|{start}".encode("utf-8")
    ).hexdigest()[:14]


def resolve_google_news_link(url, raw, body):
    """Google News RSS links point at an interstitial on news.google.com
    that needs JavaScript to redirect to the real article. A plain HTTP
    fetch never leaves that interstitial, so every discovered article was
    being read as an empty Google shell page (no article text, no date).

    Try, in order:
    1. The `data-n-au="..."` attribute Google embeds with the real URL.
    2. A `<meta http-equiv="refresh" ... url=...>` redirect.
    3. The first external (non-google) link on the interstitial page.
    """
    if "news.google.com" not in url:
        return None

    m = re.search(r'data-n-au="(https?://[^"]+)"', raw)
    if m:
        return html.unescape(m.group(1))

    m = re.search(
        r'<meta[^>]+http-equiv=["\']refresh["\'][^>]+content=["\'][^"\']*url=([^"\'>]+)',
        raw, re.I,
    )
    if m:
        return html.unescape(m.group(1))

    m = re.search(r'href="(https?://(?!(?:www\.)?google\.com|news\.google\.com)[^"]+)"', raw)
    if m:
        return html.unescape(m.group(1))

    return None


def verify_source(source, discovered_description=""):
    url = source["url"]
    try:
        final_url, body, content_type = fetch_url(url)
    except Exception as exc:
        return None, "fetch_error", url, f"{exc}"

    try:
        raw = decode_body(body, content_type)

        # Google News RSS links are an interstitial, not the real article.
        # Resolve to the actual publisher URL and re-fetch it.
        resolved = resolve_google_news_link(final_url, raw, body)
        if resolved:
            try:
                final_url, body, content_type = fetch_url(resolved)
                raw = decode_body(body, content_type)
            except Exception as exc:
                return None, "fetch_error", resolved, f"{exc}"

        text = clean_text(raw)

        title = extract_title(raw) or source["name"]
        evidence = (
            f"{source['name']} {source['program_type']} "
            f"{title} {discovered_description} {text[:180000]}"
        )

        # First gate: it must actually discuss a football trial/opportunity.
        if not TRIAL_WORDS.search(evidence):
            return None, "no_trial_words", final_url, title[:120]

        # Second, decisive gate: a REAL FUTURE DATE is mandatory.
        dates = [d for d in extract_dates(text, raw) if d >= date.today()]
        if not dates:
            reason = "no_future_date_google_unresolved" if "news.google.com" in final_url else "no_future_date"
            return None, reason, final_url, title[:120]

        start_date = dates[0].isoformat()
        end_date = dates[-1].isoformat()

        age_min, age_max = extract_age_range(text)
        location = extract_location(text)
        final_url = canonical_url(final_url, raw)

        record = {
            "id": make_id(final_url, start_date),
            "title": title[:250],
            "description": text[:900],
            "source_url": final_url,
            "source_name": source["name"],
            "language": source["lang"],
            "trial_start": start_date,
            "trial_end": end_date,
            "age_min": age_min,
            "age_max": age_max,
            "status": "ACTIVE",
            "verified_at": datetime.now(timezone.utc).isoformat(),
            "provider_type": source["provider_type"],
            "program_type": source["program_type"],
            "location": location,
        }
        return record, "ok", final_url, title[:120]

    except Exception as exc:
        return None, "parse_error", url, f"{exc}"




def google_news_rss(language_code, query):
    hl, gl, ceid, _ = LANGS[language_code]
    url = (
        "https://news.google.com/rss/search?q=" + quote(query)
        + "&hl=" + quote(hl)
        + "&gl=" + quote(gl)
        + "&ceid=" + quote(ceid)
    )

    _, body, _ = fetch_url(url)
    root = ET.fromstring(body)

    results = []
    for item in root.findall(".//item"):
        title = html.unescape(item.findtext("title") or "")
        link = item.findtext("link") or ""
        description = clean_text(item.findtext("description") or "")
        if link and TRIAL_WORDS.search(title + " " + description):
            results.append((title, link, description))
    return results


def load_database():
    if not DB.exists():
        return {"records": []}
    try:
        data = json.loads(DB.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {"records": []}
    except Exception:
        return {"records": []}


def main():
    today = date.today()
    started = datetime.now(timezone.utc)

    # IMPORTANT: start from a clean current database.
    # This deliberately removes the 1,910 previously polluted records.
    indexed = {}

    candidates = []
    errors = []

    # Curated providers are always scanned.
    for url, name, lang, provider_type, program_type in CURATED_SOURCES:
        candidates.append({
            "url": url,
            "name": name,
            "lang": lang,
            "provider_type": provider_type,
            "program_type": program_type,
        })

    # Discover candidate URLs.
    discovery_jobs = []
    for lang, (_, _, _, queries) in LANGS.items():
        for query in queries:
            discovery_jobs.append((lang, query))

    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {
            pool.submit(google_news_rss, lang, query): (lang, query)
            for lang, query in discovery_jobs
        }

        for future in as_completed(futures):
            lang, query = futures[future]
            try:
                for title, url, description in future.result():
                    candidates.append({
                        "url": url,
                        "name": title[:200],
                        "lang": lang,
                        "provider_type": "Discovered football opportunity source",
                        "program_type": "Club / academy / federation / agency / management trial",
                        "description": description,
                    })
            except Exception as exc:
                errors.append(f"SEARCH {lang}/{query}: {exc}")

    # Deduplicate candidate URLs before visiting them.
    unique = {}
    for item in candidates:
        unique[item["url"]] = item

    verified = 0
    reason_counts = {}
    samples = {"no_future_date": [], "no_future_date_google_unresolved": [], "no_trial_words": [], "fetch_error": [], "parse_error": []}

    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {
            pool.submit(
                verify_source,
                item,
                item.get("description", "")
            ): item
            for item in unique.values()
        }

        for future in as_completed(futures):
            try:
                record, reason, seen_url, note = future.result()
                reason_counts[reason] = reason_counts.get(reason, 0) + 1
                if record:
                    verified += 1
                    key = (record["source_url"], record["trial_start"])
                    indexed[key] = record
                elif reason in samples and len(samples[reason]) < 8:
                    samples[reason].append(f"{seen_url}  |  {note}")
            except Exception as exc:
                errors.append(f"VERIFY WORKER: {exc}")

    records = list(indexed.values())

    # Defensive final filter: only future records can enter ACTIVE database.
    records = [
        r for r in records
        if r.get("trial_start")
        and r.get("trial_end")
        and r.get("status") == "ACTIVE"
        and date.fromisoformat(r["trial_end"]) >= today
    ]

    finished = datetime.now(timezone.utc)

    output = {
        "records": records,
        "updated_at": finished.isoformat(),
        "last_full_scan": today.isoformat(),
        "last_scan_discovered": len(candidates),
        "last_scan_verified": verified,
        "last_scan_errors": len(errors),
        "last_scan_duration_seconds": round(
            (finished - started).total_seconds(), 2
        ),
        "crawler_version": "4.1-language-fix",
    }

    DB.parent.mkdir(parents=True, exist_ok=True)
    DB.write_text(
        json.dumps(output, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print("=" * 60)
    print("FOOTURA FOOTBALL TRIALS CRAWLER 4.1")
    print("=" * 60)
    print(f"Candidates discovered : {len(candidates)}")
    print(f"Verified with future date: {verified}")
    print(f"ACTIVE records saved  : {len(records)}")
    print(f"Errors                : {len(errors)}")
    print(f"Duration              : {output['last_scan_duration_seconds']} sec")
    print(f"Database              : {DB}")
    print("=" * 60)
    print("Reason breakdown      :", reason_counts)
    print("=" * 60)

    for reason, urls in samples.items():
        if urls:
            print(f"Samples for '{reason}':")
            for u in urls:
                print("  -", u)

    if errors:
        print("First errors:")
        for error in errors[:20]:
            print(" -", error)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
