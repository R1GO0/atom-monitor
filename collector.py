#!/usr/bin/env python3
"""Collect public RSS/Atom metadata for the Atom-Monitor static site."""

from __future__ import annotations

import hashlib
import html
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.robotparser
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT / "data" / "news.json"
USER_AGENT = "AtomMonitor/1.0 (polite RSS reader; respects robots.txt)"
MAX_ITEMS_PER_SOURCE = 80
MAX_AGE_DAYS = 60
TIMEOUT_SECONDS = 22

SOURCES = [
    {"id": "atomic-energy", "name": "Атомная энергия 2.0", "site": "https://www.atomic-energy.ru", "language": "ru", "feeds": ["https://www.atomic-energy.ru/news/rss.xml"], "public_rss": True},
    {"id": "nuclear-ru", "name": "Nuclear.Ru", "site": "https://www.nuclear.ru", "language": "ru", "feeds": []},
    {"id": "atominfo", "name": "AtomInfo.Ru", "site": "https://www.atominfo.ru", "language": "ru", "feeds": []},
    {"id": "rosatom", "name": "Росатом", "site": "https://www.rosatom.ru", "language": "ru", "feeds": ["https://www.rosatom.ru/upload/iblock_rss_38.xml"], "public_rss": True},
    {"id": "rosenergoatom", "name": "Росэнергоатом", "site": "https://www.rosenergoatom.ru", "language": "ru", "feeds": ["https://www.rosenergoatom.ru/zhurnalistam/news/rss/"], "public_rss": True},
    {"id": "world-nuclear-news", "name": "World Nuclear News", "site": "https://world-nuclear-news.org", "language": "en", "feeds": ["https://world-nuclear-news.org/rss", "https://world-nuclear-news.org/?rss=feed", "https://www.world-nuclear-news.org/rss"], "public_rss": True},
    {"id": "iaea", "name": "IAEA", "site": "https://www.iaea.org", "language": "en", "feeds": ["https://www.iaea.org/feeds/topnews"], "public_rss": True},
    {"id": "nucnet", "name": "NucNet", "site": "https://www.nucnet.org", "language": "en", "feeds": ["https://www.nucnet.org/feed", "https://www.nucnet.org/rss"], "public_rss": True},
]

TOPICS = {
    "construction": ("Строительство", ("строительств", "бетон", "монтаж", "площадк", "стройк", "construction", "concrete", "groundbreaking", "site works")),
    "reactors": ("Реакторы", ("реактор", "ввэр", "смр", "мсмр", "энергоблок", "reactor", "vver", "smr", "unit ", "criticality")),
    "safety": ("Безопасность", ("безопасност", "регулятор", "надзор", "авар", "инцидент", "safety", "regulat", "incident", "emergency")),
    "fuel": ("Топливо", ("топлив", "уран", "обогащ", "изотоп", "ядерный цикл", "fuel", "uranium", "enrichment", "isotope")),
    "economy": ("Экономика", ("финанс", "инвестиц", "экономик", "рынок", "контракт", "стоимост", "finance", "investment", "market", "contract", "cost")),
    "international": ("Международка", ("международ", "сотруднич", "страна", "international", "cooperation", "agreement", "country", "countries")),
}


class TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


class FeedLinkExtractor(HTMLParser):
    def __init__(self, base_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.links: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "link":
            return
        attributes = {key.lower(): value or "" for key, value in attrs}
        rel = attributes.get("rel", "").lower().split()
        media = attributes.get("type", "").lower()
        href = attributes.get("href", "")
        if href and "alternate" in rel and ("rss" in media or "atom" in media or "xml" in media):
            self.links.append(urllib.parse.urljoin(self.base_url, href))


class ListingExtractor(HTMLParser):
    """Read only semantic article cards from an allowed public listing page."""

    def __init__(self, base_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.current: dict | None = None
        self.article_depth = 0
        self.captures: list[dict] = []
        self.articles: list[dict] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        attributes = {key.lower(): value or "" for key, value in attrs}
        if tag == "article":
            if self.current is None:
                self.current = {"title": "", "summary": "", "url": "", "date": ""}
            self.article_depth += 1
        if self.current is None:
            return
        if tag in {"h1", "h2", "h3", "h4"}:
            self.captures.append({"tag": tag, "parts": []})
        elif tag == "p":
            self.captures.append({"tag": tag, "parts": []})
        elif tag == "a" and attributes.get("href"):
            self.captures.append({"tag": tag, "parts": [], "href": urllib.parse.urljoin(self.base_url, attributes["href"])})
        elif tag == "time":
            date_value = attributes.get("datetime", "")
            if date_value:
                self.current["date"] = date_value

    def handle_data(self, data: str) -> None:
        if self.current is None:
            return
        for capture in self.captures:
            capture["parts"].append(data)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if self.current is not None and tag in {"h1", "h2", "h3", "h4", "p", "a"}:
            for index in range(len(self.captures) - 1, -1, -1):
                capture = self.captures[index]
                if capture["tag"] != tag:
                    continue
                text = " ".join(capture["parts"])
                if tag in {"h1", "h2", "h3", "h4"} and not self.current["title"]:
                    self.current["title"] = text
                elif tag == "p" and not self.current["summary"]:
                    self.current["summary"] = text
                elif tag == "a" and not self.current["url"]:
                    self.current["url"] = capture.get("href", "")
                self.captures.pop(index)
                break
        if tag == "article" and self.current is not None:
            self.article_depth = max(0, self.article_depth - 1)
            if self.article_depth == 0:
                self.current["title"] = self.current["title"] or ""
                self.articles.append(self.current)
                self.current = None
                self.captures.clear()


def robots_can_fetch(url: str, cache: dict[str, urllib.robotparser.RobotFileParser | None]) -> bool | None:
    parsed = urllib.parse.urlsplit(url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    if origin not in cache:
        robots_url = urllib.parse.urljoin(origin, "/robots.txt")
        parser = urllib.robotparser.RobotFileParser(robots_url)
        try:
            with urllib.request.urlopen(urllib.request.Request(robots_url, headers={"User-Agent": USER_AGENT}), timeout=TIMEOUT_SECONDS) as response:
                content = response.read(256_000).decode("utf-8", "replace")
            parser.parse(content.splitlines())
            cache[origin] = parser
        except urllib.error.HTTPError as error:
            if error.code == 404:
                parser.parse([])
                cache[origin] = parser
            else:
                print(f"  robots.txt недоступен ({error.code}); пропуск домена")
                cache[origin] = None
        except Exception as error:  # DNS and timeouts: fail closed for this host.
            print(f"  не удалось прочитать robots.txt; пропуск домена: {error}")
            cache[origin] = None
    parser = cache[origin]
    if parser is None:
        return None
    return parser.can_fetch(USER_AGENT, url)


def fetch(url: str, accept: str = "application/atom+xml, application/rss+xml, application/xml, text/xml, text/html;q=0.8") -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": accept})
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
        return response.read(2_500_000)


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].split(":")[-1].lower()


def child_text(node: ET.Element, names: tuple[str, ...]) -> str:
    for child in list(node):
        if local_name(child.tag) in names:
            if child.text and child.text.strip():
                return child.text.strip()
            # Atom links use href instead of text.
            href = child.attrib.get("href")
            if href:
                return href.strip()
            text = "".join(child.itertext()).strip()
            if text:
                return text
    return ""


def clean_text(value: str, limit: int = 560) -> str:
    parser = TextExtractor()
    try:
        parser.feed(value)
        plain = " ".join(parser.parts)
    except Exception:
        plain = value
    plain = html.unescape(plain)
    plain = re.sub(r"\s+", " ", plain).strip()
    if len(plain) > limit:
        sentence_end = max(plain.rfind(". ", 0, limit), plain.rfind("! ", 0, limit), plain.rfind("? ", 0, limit))
        plain = plain[: sentence_end + 1] if sentence_end > limit // 2 else plain[:limit].rsplit(" ", 1)[0].rstrip(" ,;:") + "…"
    return plain


def parse_date(value: str) -> str | None:
    if not value:
        return None
    candidates = [value.strip()]
    if candidates[0].endswith("Z"):
        candidates.append(candidates[0][:-1] + "+00:00")
    for candidate in candidates:
        try:
            parsed = datetime.fromisoformat(candidate)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
        except ValueError:
            pass
        try:
            parsed = parsedate_to_datetime(candidate)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
        except (TypeError, ValueError, OverflowError):
            pass
    return None


def canonical_url(value: str) -> str:
    parsed = urllib.parse.urlsplit(value)
    query = [(key, val) for key, val in urllib.parse.parse_qsl(parsed.query, keep_blank_values=True) if not key.lower().startswith("utm_") and key.lower() not in {"fbclid", "gclid", "ref", "source"}]
    return urllib.parse.urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path.rstrip("/"), urllib.parse.urlencode(sorted(query)), ""))


def classify(title: str, summary: str, feed_category: str = "") -> str:
    text = f"{title} {summary} {feed_category}".casefold()
    for key, (_, keywords) in TOPICS.items():
        if any(keyword in text for keyword in keywords):
            return key
    return "international"


def detect_language(text: str, default: str) -> str:
    cyrillic = len(re.findall(r"[а-яё]", text.casefold()))
    latin = len(re.findall(r"[a-z]", text.casefold()))
    return "ru" if cyrillic >= latin else "en" if latin else default


def get_feed_entries(payload: bytes, feed_url: str, source: dict) -> list[dict]:
    try:
        root = ET.fromstring(payload)
    except ET.ParseError:
        return []
    entries = [element for element in root.iter() if local_name(element.tag) in {"item", "entry"}]
    now = datetime.now(timezone.utc)
    oldest = now.timestamp() - MAX_AGE_DAYS * 86_400
    result = []
    for entry in entries[:MAX_ITEMS_PER_SOURCE]:
        title = clean_text(child_text(entry, ("title",)), 220)
        raw_link = child_text(entry, ("link", "guid", "id"))
        if not raw_link:
            continue
        link = urllib.parse.urljoin(feed_url, html.unescape(raw_link.strip()))
        if urllib.parse.urlsplit(link).scheme not in {"https", "http"}:
            continue
        raw_summary = child_text(entry, ("summary", "description", "encoded", "content", "subtitle"))
        summary = clean_text(raw_summary)
        published = parse_date(child_text(entry, ("published", "pubdate", "updated", "date", "issued")))
        if not title or not published:
            continue
        if datetime.fromisoformat(published.replace("Z", "+00:00")).timestamp() < oldest:
            continue
        category = " ".join((node.text or "") for node in entry.iter() if local_name(node.tag) == "category")
        language = detect_language(f"{title} {summary}", source["language"])
        key = hashlib.sha256(canonical_url(link).encode("utf-8")).hexdigest()[:16]
        result.append({
            "id": key,
            "title": title,
            "summary": summary,
            "url": link,
            "published_at": published,
            "source_id": source["id"],
            "source": source["name"],
            "topic": classify(title, summary, category),
            "language": language,
        })
    return result


def discover_feeds(source: dict, robots_cache: dict) -> list[str]:
    if not robots_can_fetch(source["site"], robots_cache):
        return []
    try:
        page = fetch(source["site"], "text/html,application/xhtml+xml;q=0.9")
        parser = FeedLinkExtractor(source["site"])
        parser.feed(page.decode("utf-8", "replace"))
        return parser.links
    except Exception as error:
        print(f"  автообнаружение RSS недоступно: {error}")
        return []


def collect_source(source: dict, robots_cache: dict) -> tuple[list[dict], dict]:
    urls = list(source["feeds"])
    seen_urls = set(urls)
    articles: list[dict] = []
    feed_succeeded = False
    errors: list[str] = []
    for url in urls:
        permission = robots_can_fetch(url, robots_cache)
        if permission is False:
            print(f"  RSS запрещён robots.txt: {url}")
            continue
        if permission is None:
            if not source.get("public_rss"):
                print(f"  robots.txt недоступен; RSS пропущен: {url}")
                errors.append("robots.txt недоступен")
                continue
            print(f"  robots.txt недоступен; проверяю явно опубликованную RSS-ленту: {url}")
        try:
            items = get_feed_entries(fetch(url), url, source)
            feed_succeeded = True
            if items:
                articles.extend(items)
                break
        except Exception as error:
            print(f"  RSS недоступен: {url} ({error})")
            errors.append(str(error))
        time.sleep(0.15)

    if not articles and not feed_succeeded:
        for url in discover_feeds(source, robots_cache):
            if url in seen_urls:
                continue
            seen_urls.add(url)
            permission = robots_can_fetch(url, robots_cache)
            if permission is not True:
                continue
            try:
                items = get_feed_entries(fetch(url), url, source)
                feed_succeeded = True
                articles.extend(items)
                if articles:
                    break
            except Exception as error:
                print(f"  обнаруженная RSS-лента недоступна: {error}")
                errors.append(str(error))
            time.sleep(0.15)
    if not articles and not feed_succeeded:
        articles = collect_html_listing(source, robots_cache)
    status = "updated" if articles else "empty" if feed_succeeded else "unavailable"
    return articles, {"status": status, "error": "; ".join(errors)[:240]}


def collect_html_listing(source: dict, robots_cache: dict) -> list[dict]:
    """Fallback for sites without RSS: parse dated article cards, not story pages."""
    page_url = source.get("listing", source["site"])
    if not robots_can_fetch(page_url, robots_cache):
        return []
    try:
        payload = fetch(page_url, "text/html,application/xhtml+xml;q=0.9")
        parser = ListingExtractor(page_url)
        parser.feed(payload.decode("utf-8", "replace"))
    except Exception as error:
        print(f"  HTML-лента недоступна: {error}")
        return []

    source_host = (urllib.parse.urlsplit(source["site"]).hostname or "").removeprefix("www.").lower()
    results = []
    oldest = datetime.now(timezone.utc).timestamp() - MAX_AGE_DAYS * 86_400
    for card in parser.articles:
        title = clean_text(card["title"], 220)
        summary = clean_text(card["summary"])
        link = card["url"]
        published = parse_date(card["date"])
        if not title or not link or not published:
            continue
        parsed_link = urllib.parse.urlsplit(link)
        link_host = (parsed_link.hostname or "").removeprefix("www.").lower()
        if parsed_link.scheme not in {"https", "http"} or link_host != source_host:
            continue
        if datetime.fromisoformat(published.replace("Z", "+00:00")).timestamp() < oldest:
            continue
        key = hashlib.sha256(canonical_url(link).encode("utf-8")).hexdigest()[:16]
        results.append({
            "id": key,
            "title": title,
            "summary": summary,
            "url": link,
            "published_at": published,
            "source_id": source["id"],
            "source": source["name"],
            "topic": classify(title, summary),
            "language": detect_language(f"{title} {summary}", source["language"]),
        })
    if results:
        print(f"  использована HTML-лента, карточек: {len(results)}")
    return results[:MAX_ITEMS_PER_SOURCE]


def main() -> None:
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    robots_cache: dict[str, urllib.robotparser.RobotFileParser | None] = {}
    all_articles: list[dict] = []
    source_results: dict[str, dict] = {}
    previous_articles: list[dict] = []
    try:
        previous_payload = json.loads(OUTPUT.read_text(encoding="utf-8"))
        previous_articles = previous_payload.get("articles", []) if isinstance(previous_payload, dict) else []
    except (OSError, json.JSONDecodeError):
        pass

    for source in SOURCES:
        print(f"Проверяю {source['name']}…")
        items, result = collect_source(source, robots_cache)
        cached = [article for article in previous_articles if article.get("source_id") == source["id"]]
        combined: dict[str, dict] = {}
        for article in items + cached:
            url = article.get("url", "")
            published = parse_date(article.get("published_at", ""))
            if not url or not published:
                continue
            timestamp = datetime.fromisoformat(published.replace("Z", "+00:00")).timestamp()
            if timestamp < datetime.now(timezone.utc).timestamp() - MAX_AGE_DAYS * 86_400:
                continue
            key = canonical_url(url)
            combined.setdefault(key, article)
        source_items = sorted(combined.values(), key=lambda item: item["published_at"], reverse=True)[:MAX_ITEMS_PER_SOURCE]
        if result["status"] == "unavailable" and source_items:
            result["status"] = "cached"
            print(f"  источник временно недоступен; сохранено материалов из предыдущего сбора: {len(source_items)}")
        elif source_items:
            print(f"  сохранено материалов: {len(source_items)}")
        else:
            print("  свежих материалов нет")
        result.update({"id": source["id"], "name": source["name"], "site": source["site"], "article_count": len(source_items)})
        source_results[source["id"]] = result
        all_articles.extend(source_items)

    unique: dict[str, dict] = {}
    seen_titles: set[str] = set()
    for article in all_articles:
        # Skip syndicated stories even when publishers use different tracking URLs.
        key = canonical_url(article["url"])
        title_key = re.sub(r"[^\w]+", "", article["title"].casefold())
        if key in unique or (title_key and title_key in seen_titles):
            continue
        unique[key] = article
        if title_key:
            seen_titles.add(title_key)
    articles = sorted(unique.values(), key=lambda item: item["published_at"], reverse=True)
    final_counts: dict[str, int] = {}
    for article in articles:
        source_id = article.get("source_id", "")
        final_counts[source_id] = final_counts.get(source_id, 0) + 1
    active_sources = []
    for source in SOURCES:
        result = source_results[source["id"]]
        result["article_count"] = final_counts.get(source["id"], 0)
        active_sources.append(result)
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "sources": active_sources,
        "articles": articles,
    }
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Записано новостей: {len(articles)} → {OUTPUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
