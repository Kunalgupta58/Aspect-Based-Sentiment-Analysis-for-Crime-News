import html
import json
import logging
import re
import unicodedata
import warnings
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from email.utils import parsedate_to_datetime
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

import requests
from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning
from dateutil.parser import parse as parse_datetime

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT = 15
MAX_ARTICLES = 40
DEFAULT_RSS_FEEDS = (
    "https://timesofindia.indiatimes.com/rssfeeds/-2128936835.cms",
    "https://www.hindustantimes.com/feeds/rss/india-news/rssfeed.xml",
)
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36"
)
CRIME_KEYWORDS = {
    "abduction", "abuse", "accused", "acid", "alleged", "arrest", "arrested",
    "assault", "attack", "beaten", "booked", "bribery", "burglary", "charge",
    "cheat", "crime", "custody", "dead", "detain", "extortion", "forgery",
    "fraud", "gang", "harass", "held", "homicide", "hostage", "illegal",
    "injured", "investigation", "jail", "kidnap", "kill", "loot", "lynch",
    "missing", "mob", "molest", "murder", "probe", "rape", "robbery", "scam",
    "seized", "shot", "smuggling", "snatch", "stabbing", "stab", "suicide",
    "suspect", "terror", "theft", "threat", "torture", "victim", "violence",
    "wanted",
}
def normalize_title(title: str) -> str:
    normalized = unicodedata.normalize("NFKD", title.casefold())
    ascii_title = normalized.encode("ascii", "ignore").decode("ascii")
    return " ".join(re.findall(r"[a-z0-9]+", ascii_title))


def canonicalize_url(url: str) -> str:
    parts = urlsplit(url.strip())
    host = (parts.hostname or "").casefold()
    if host.startswith("www."):
        host = host[4:]
    path = re.sub(r"/+$", "", parts.path) or "/"
    query = urlencode(
        sorted(
            (key, value)
            for key, value in parse_qsl(parts.query, keep_blank_values=True)
            if not key.casefold().startswith("utm_")
            and key.casefold() not in {"fbclid", "gclid", "output"}
        )
    )
    return urlunsplit((parts.scheme.casefold(), host, path, query, ""))


def parse_publication_date(value: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError, OverflowError):
        try:
            parsed = parse_datetime(value)
        except (TypeError, ValueError, OverflowError):
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def local_name(tag: object) -> str:
    return str(getattr(tag, "name", "")).split(":")[-1].casefold()


def entry_value(entry: BeautifulSoup, names: set[str]) -> str:
    for child in entry.find_all(recursive=False):
        if local_name(child) not in names:
            continue
        if child.has_attr("href"):
            value = str(child["href"]).strip()
        else:
            value = child.get_text(" ", strip=True)
        if value:
            return value
    return ""


def entry_url(entry: BeautifulSoup, feed_url: str) -> str:
    for child in entry.find_all(recursive=False):
        if local_name(child) != "link":
            continue
        link = str(child.get("href", "") or child.get_text(" ", strip=True)).strip()
        if link:
            return urljoin(feed_url, link)

    description = entry.find(
        lambda tag: local_name(tag) in {"description", "summary", "content"}
    )
    if description:
        decoded_description = BeautifulSoup(
            html.unescape(description.get_text()),
            "html.parser",
        )
        anchor = decoded_description.find("a", href=True)
        if anchor:
            return urljoin(feed_url, str(anchor["href"]))

    guid = entry_value(entry, {"guid", "id"})
    if guid.startswith(("http://", "https://")):
        return guid
    return ""


def entry_description(entry: BeautifulSoup) -> str:
    for child in entry.find_all(recursive=False):
        if local_name(child) not in {"description", "summary", "encoded", "content"}:
            continue
        text = BeautifulSoup(
            html.unescape(child.get_text()),
            "html.parser",
        ).get_text(" ", strip=True)
        if text:
            return text
    return ""


def entry_source(entry: BeautifulSoup, fallback_url: str) -> str:
    source_tag = entry.find(lambda tag: local_name(tag) == "source")
    if source_tag:
        source = source_tag.get_text(" ", strip=True)
        if source:
            return source
    description = entry.find(
        lambda tag: local_name(tag) in {"description", "summary", "content"}
    )
    if description:
        decoded_description = BeautifulSoup(
            html.unescape(description.get_text()),
            "html.parser",
        )
        publisher = decoded_description.find("font")
        if publisher:
            source = publisher.get_text(" ", strip=True)
            if source:
                return source
    return publisher_name(fallback_url)


def safe_feed_name(feed_url: str) -> str:
    parts = urlsplit(feed_url)
    return urlunsplit((parts.scheme, parts.hostname or "", parts.path, "", ""))


def publisher_name(url: str) -> str:
    host = (urlsplit(url).hostname or "").casefold()
    if host.startswith("www."):
        host = host[4:]
    known_publishers = {
        "hindustantimes.com": "Hindustan Times",
        "timesofindia.indiatimes.com": "Times of India",
    }
    return known_publishers.get(host, host or "News feed")


def is_crime_related(title: str) -> bool:
    words = set(normalize_title(title).split())
    return any(
        word in CRIME_KEYWORDS
        or any(
            len(keyword) >= 5
            and word.startswith(keyword)
            and len(word) - len(keyword) <= 4
            for keyword in CRIME_KEYWORDS
        )
        for word in words
    )


def _json_ld_articles(soup: BeautifulSoup) -> list[dict[str, object]]:
    found: list[dict[str, object]] = []
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            payload = json.loads(script.string or script.get_text())
        except (json.JSONDecodeError, TypeError):
            continue
        candidates = payload if isinstance(payload, list) else [payload]
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            graph = candidate.get("@graph", [])
            candidates_to_check = graph if isinstance(graph, list) else [candidate]
            for item in candidates_to_check:
                if not isinstance(item, dict):
                    continue
                type_value = item.get("@type", [])
                types = type_value if isinstance(type_value, list) else [type_value]
                if any(
                    str(value).casefold() in {"article", "newsarticle", "report"}
                    for value in types
                ):
                    found.append(item)
    return found


def extract_article_page(
    response: requests.Response,
    fallback_title: str,
    fallback_content: str,
    fallback_date: datetime,
) -> dict[str, str]:
    soup = BeautifulSoup(response.content, "html.parser")
    json_ld = _json_ld_articles(soup)

    title = ""
    date_value = ""
    content = ""
    for article_data in json_ld:
        title = title or str(article_data.get("headline", "")).strip()
        date_value = date_value or str(article_data.get("datePublished", "")).strip()
        article_body = article_data.get("articleBody", "")
        if isinstance(article_body, str):
            content = content or article_body.strip()

    if not title:
        meta_title = soup.find("meta", property="og:title") or soup.find(
            "meta", attrs={"name": "twitter:title"}
        )
        if meta_title:
            title = str(meta_title.get("content", "")).strip()
    if not title:
        heading = soup.find("h1")
        title = heading.get_text(" ", strip=True) if heading else ""

    if not date_value:
        for attrs in (
            {"property": "article:published_time"},
            {"name": "pubdate"},
            {"name": "date"},
            {"itemprop": "datePublished"},
        ):
            date_meta = soup.find("meta", attrs=attrs)
            if date_meta and date_meta.get("content"):
                date_value = str(date_meta["content"]).strip()
                break

    if not content:
        content_selectors = (
            '[itemprop="articleBody"]',
            '[data-articlebody="1"]',
            ".articlebodycontent",
            "article",
            "main",
        )
        for selector in content_selectors:
            containers = soup.select(selector)
            if not containers:
                continue
            candidates = []
            for container in containers:
                paragraphs = []
                for paragraph in container.find_all("p"):
                    text = paragraph.get_text(" ", strip=True)
                    if text and (not paragraphs or paragraphs[-1] != text):
                        paragraphs.append(text)
                candidate = "\n".join(paragraphs)
                if len(candidate) > len(content):
                    content = candidate
            if len(content) >= 200:
                break

    if len(content) < len(fallback_content):
        content = fallback_content

    parsed_date = parse_publication_date(date_value) if date_value else None
    published = parsed_date or fallback_date
    publisher = publisher_name(response.url)

    return {
        "title": title or fallback_title,
        "content": content.strip(),
        "publication_date": published.isoformat(),
        "url": response.url,
        "source": publisher,
    }


def fetch_article(
    session: requests.Session,
    url: str,
    title: str,
    description: str,
    published: datetime,
    source: str,
) -> dict[str, str] | None:
    try:
        article_response = session.get(url, timeout=REQUEST_TIMEOUT)
        article_response.raise_for_status()
        article = extract_article_page(
            article_response,
            fallback_title=title,
            fallback_content=description,
            fallback_date=published,
        )
    except requests.RequestException as error:
        logger.warning(
            "Could not retrieve article from %s: %s",
            safe_feed_name(url),
            error,
        )
        if len(description) < 80:
            return None
        article = {
            "title": title,
            "content": description,
            "publication_date": published.isoformat(),
            "url": url,
            "source": source or publisher_name(url),
        }

    if len(article["content"]) < 80:
        logger.warning("Skipping article with no usable text: %s", title)
        return None
    article["title"] = title or article["title"]
    article["source"] = source or article["source"]
    article["publication_date"] = published.isoformat()
    return article


def fetch_json_feed(
    response: requests.Response,
    session: requests.Session,
    cutoff: datetime,
    now: datetime,
) -> list[dict[str, str]]:
    payload = response.json()
    if isinstance(payload, list):
        records = payload
    elif isinstance(payload, dict):
        records = next(
            (
                payload[key]
                for key in ("articles", "results", "items", "data")
                if isinstance(payload.get(key), list)
            ),
            [],
        )
        if not records and isinstance(payload.get("data"), dict):
            data = payload["data"]
            records = next(
                (
                    data[key]
                    for key in ("articles", "results", "items")
                    if isinstance(data.get(key), list)
                ),
                [],
            )
    else:
        raise ValueError("News API response must contain a list of articles.")

    articles: list[dict[str, str]] = []
    for record in records:
        if not isinstance(record, dict):
            continue
        title = str(record.get("title") or record.get("headline") or "").strip()
        url = str(
            record.get("url")
            or record.get("link")
            or record.get("web_url")
            or ""
        ).strip()
        date_value = str(
            record.get("publishedAt")
            or record.get("published_at")
            or record.get("datePublished")
            or record.get("pubDate")
            or record.get("date")
            or record.get("created_at")
            or ""
        )
        published = parse_publication_date(date_value)
        if (
            not title
            or not url
            or published is None
            or published < cutoff
            or published > now
            or not is_crime_related(title)
        ):
            continue

        source_value = record.get("source", record.get("publisher", ""))
        if isinstance(source_value, dict):
            source = str(source_value.get("name") or source_value.get("title") or "")
        else:
            source = str(source_value)
        description = str(
            record.get("description")
            or record.get("summary")
            or record.get("content")
            or record.get("snippet")
            or ""
        ).strip()
        article = fetch_article(
            session,
            url,
            title,
            description,
            published,
            source,
        )
        if article:
            articles.append(article)
    return articles


def fetch_rss_feed(
    feed_url: str,
    session: requests.Session,
    cutoff: datetime,
    now: datetime,
) -> list[dict[str, str]]:
    response = session.get(feed_url, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    content_type = response.headers.get("content-type", "").casefold()
    if "json" in content_type or response.content.lstrip().startswith((b"{", b"[")):
        return fetch_json_feed(response, session, cutoff, now)

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", XMLParsedAsHTMLWarning)
        feed = BeautifulSoup(response.content, "html.parser")
    entries = feed.find_all(lambda tag: local_name(tag) in {"item", "entry"})
    articles: list[dict[str, str]] = []

    for entry in entries:
        title = entry_value(entry, {"title"})
        link = entry_url(entry, response.url)
        if not title or not link:
            continue
        link = urljoin(response.url, link)

        published_value = entry_value(
            entry,
            {"pubdate", "published", "updated", "date"},
        )
        published = parse_publication_date(published_value)
        if published is None or published < cutoff or published > now:
            continue
        if not is_crime_related(title):
            continue

        article = fetch_article(
            session=session,
            url=link,
            title=title,
            description=entry_description(entry),
            published=published,
            source=entry_source(entry, response.url),
        )
        if article:
            articles.append(article)

    return articles


def deduplicate_articles(articles: list[dict[str, str]]) -> list[dict[str, str]]:
    unique: list[dict[str, str]] = []
    seen_urls: set[str] = set()
    seen_titles: list[str] = []

    for article in sorted(
        articles,
        key=lambda item: item.get("publication_date", ""),
        reverse=True,
    ):
        url = canonicalize_url(article.get("url", ""))
        title = normalize_title(article.get("title", ""))
        if not title:
            continue
        if url and url in seen_urls:
            continue
        if title in seen_titles:
            continue
        if len(title.split()) >= 6 and any(
            SequenceMatcher(None, title, previous).ratio() >= 0.92
            for previous in seen_titles
            if len(previous.split()) >= 6
        ):
            continue

        seen_titles.append(title)
        if url:
            seen_urls.add(url)
        article["url"] = article.get("url", "").strip()
        unique.append(article)
    return unique


def get_articles(
    feed_urls: list[str] | None = None,
    hours_back: int = 24,
    max_articles: int = MAX_ARTICLES,
) -> list[dict[str, str]]:
    if hours_back < 1:
        raise ValueError("hours_back must be at least 1.")
    if max_articles < 1:
        raise ValueError("max_articles must be at least 1.")

    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=hours_back)
    urls = list(dict.fromkeys(url.strip() for url in (feed_urls or []) if url.strip()))
    if not urls:
        urls = list(DEFAULT_RSS_FEEDS)

    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, */*",
    }
    articles: list[dict[str, str]] = []
    feed_failures: list[str] = []
    with requests.Session() as session:
        session.headers.update(headers)
        for feed_url in urls:
            try:
                articles.extend(fetch_rss_feed(feed_url, session, cutoff, now))
            except (requests.RequestException, ValueError) as error:
                feed_name = safe_feed_name(feed_url)
                logger.error(
                    "Could not fetch news feed %s: %s",
                    feed_name,
                    type(error).__name__,
                )
                feed_failures.append(f"{feed_name}: {type(error).__name__}")

    unique_articles = deduplicate_articles(articles)[:max_articles]
    if not unique_articles and feed_failures:
        raise RuntimeError(
            "No articles were fetched because every news feed failed: "
            + "; ".join(feed_failures)
        )
    logger.info(
        "Fetched %d crime articles published in the last %d hours from %d feed(s).",
        len(unique_articles),
        hours_back,
        len(urls),
    )
    for article in unique_articles:
        print(f"Added: {article['title']} ({article['source']})")
    if feed_failures:
        logger.warning("Some news feeds failed: %s", "; ".join(feed_failures))
    return unique_articles
