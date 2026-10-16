import json
import os
import re
import subprocess
import sys
import time
import unicodedata
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st


PROJECT_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = PROJECT_DIR / "outputs"


def validate_data(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise ValueError("The JSON root must be an object.")

    for key in ("scraped_articles", "all_profiles"):
        records = data.get(key, [])
        if not isinstance(records, list) or any(
            not isinstance(record, dict) for record in records
        ):
            raise ValueError(f'"{key}" must be a list of objects.')
        data[key] = records

    return data


def load_json_file(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as json_file:
        return validate_data(json.load(json_file))


def format_article_option(article: dict[str, Any]) -> str:
    date = article.get("publication_date", "")
    title = article.get("title", "Untitled article")
    return f"{date[:10]} | {title}"


def normalize_name(profile: dict[str, Any]) -> str:
    return " ".join(str(profile.get("name", "")).split()).casefold()


def unique_articles(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: dict[str, dict[str, Any]] = {}
    unique: list[dict[str, Any]] = []
    for article in records:
        url = str(article.get("url", "")).strip().casefold()
        title = re.sub(
            r"[^a-z0-9]+",
            " ",
            unicodedata.normalize(
                "NFKD",
                str(article.get("title", "")).casefold(),
            ).encode("ascii", "ignore").decode("ascii"),
        ).strip()
        content = re.sub(
            r"\s+",
            " ",
            str(article.get("content", "")).casefold(),
        ).strip()
        keys = [
            f"url:{url}" if url else "",
            f"title:{title}" if title else "",
            f"content:{content}" if content else "",
        ]
        duplicate = next((seen[key] for key in keys if key in seen), None)
        if duplicate is not None:
            if url and url not in duplicate["_duplicate_urls"]:
                duplicate["_duplicate_urls"].append(url)
            for key in keys:
                if key:
                    seen[key] = duplicate
            continue

        unique_article = dict(article)
        unique_article["_duplicate_urls"] = [url] if url else []
        unique.append(unique_article)
        for key in keys:
            if key:
                seen[key] = unique_article
    return unique


def run_latest_analysis(hours_back: int, feed_urls: list[str]) -> Path:
    if os.name != "nt":
        raise OSError("The short-path refresh workaround is currently for Windows.")

    import ctypes

    used_drives = ctypes.windll.kernel32.GetLogicalDrives()
    drive_letter = next(
        (
            letter
            for letter in "ZYXWVUTSRQP"
            if not used_drives & (1 << (ord(letter) - ord("A")))
        ),
        None,
    )
    if drive_letter is None:
        raise OSError("No unused drive letter is available for the short-path run.")

    drive = f"{drive_letter}:"
    short_project_dir = Path(f"{drive}\\")
    subprocess.run(
        ["subst", drive, str(PROJECT_DIR)],
        check=True,
        capture_output=True,
        text=True,
    )

    started_at = time.time_ns()
    try:
        try:
            executable = Path(sys.executable).resolve().relative_to(
                PROJECT_DIR.resolve()
            )
            python_executable = str(short_project_dir / executable)
        except ValueError:
            python_executable = sys.executable

        environment = os.environ.copy()
        environment["PYTHONIOENCODING"] = "utf-8"
        environment["ABSA_SHORT_PATH_RELAUNCH"] = "1"
        command = [
            python_executable,
            "-u",
            "main.py",
            "--hours-back",
            str(hours_back),
        ]
        for feed_url in feed_urls:
            command.extend(["--feed-url", feed_url])
        result = subprocess.run(
            command,
            cwd=short_project_dir,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=environment,
        )
    finally:
        subprocess.run(
            ["subst", drive, "/d"],
            check=True,
            capture_output=True,
            text=True,
        )

    if result.returncode:
        raise RuntimeError(
            f"Analysis exited with code {result.returncode}; no new results were loaded."
        )

    result_files = [
        path
        for path in OUTPUT_DIR.glob("detailed_results_*.json")
        if path.stat().st_mtime_ns >= started_at
    ]
    if not result_files:
        raise FileNotFoundError(
            "The analysis completed but did not create a new results JSON."
        )

    return max(result_files, key=lambda path: path.stat().st_mtime)


st.set_page_config(
    page_title="Crime News ABSA Dashboard",
    page_icon="📰",
    layout="wide",
)

st.title("Crime News Sentiment Dashboard")
st.caption("Explore scraped crime news articles and sentiment toward people mentioned.")

st.sidebar.header("Choose data")
uploaded_json = st.sidebar.file_uploader("Upload a results JSON file", type="json")
hours_back = st.sidebar.select_slider(
    "News published within",
    options=[6, 12, 24, 48, 72],
    value=24,
    format_func=lambda hours: f"{hours} hours",
)
feed_url_text = st.sidebar.text_area(
    "Custom RSS/Atom or JSON API URLs (one per line)",
    placeholder="https://news.example.com/rss.xml",
    help=(
        "Leave blank to use the default Times of India and Hindustan Times feeds. "
        "Custom URLs replace the defaults. JSON APIs should return article records "
        "in an articles, results, items, or data list."
    ),
)
custom_feed_urls = list(
    dict.fromkeys(
        line.strip()
        for line in feed_url_text.splitlines()
        if line.strip()
    )
)

if st.sidebar.button("Fetch latest news and analyze", type="primary"):
    try:
        with st.spinner(
            f"Fetching articles from the last {hours_back} hours and running sentiment analysis. This can take a few minutes..."
        ):
            latest_file = run_latest_analysis(hours_back, custom_feed_urls)
        st.session_state["latest_result_file"] = str(latest_file)
        st.rerun()
    except Exception as error:
        st.error(f"Could not fetch and analyze the latest news: {error}")

data: dict[str, Any] | None = None
selected_file: Path | None = None

if uploaded_json is not None:
    try:
        data = validate_data(json.loads(uploaded_json.getvalue()))
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as error:
        st.error(f"Could not read the uploaded JSON: {error}")
        st.stop()
else:
    result_files = sorted(
        OUTPUT_DIR.glob("detailed_results_*.json"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    if not result_files:
        st.info("No results JSON files were found in the outputs folder. Upload one to begin.")
        st.stop()

    selected_file = st.sidebar.selectbox(
        "Results file",
        result_files,
        format_func=lambda path: path.name,
        index=(
            next(
                (
                    index
                    for index, path in enumerate(result_files)
                    if str(path) == st.session_state.get("latest_result_file")
                ),
                0,
            )
        ),
    )
    try:
        data = load_json_file(selected_file)
    except (OSError, json.JSONDecodeError, UnicodeDecodeError, ValueError) as error:
        st.error(f"Could not read {selected_file.name}: {error}")
        st.stop()

assert data is not None

original_article_count = len(data["scraped_articles"])
articles = data["scraped_articles"]
profiles = data["all_profiles"]
articles = unique_articles(articles)
duplicate_articles_removed = original_article_count - len(articles)
unique_people = {normalize_name(profile) for profile in profiles}
unique_people.discard("")
negative_mentions = sum(
    profile.get("sentiment") == "negative" for profile in profiles
)
sources = sorted({article.get("source", "Unknown") for article in articles})

if selected_file is not None:
    fetched_at = pd.Timestamp.fromtimestamp(selected_file.stat().st_mtime).strftime(
        "%Y-%m-%d %H:%M:%S"
    )
    st.caption(
        f"Showing `{selected_file.name}` · Last fetched: {fetched_at}. "
        "Use “Fetch latest news and analyze” to fetch live results."
    )
    if selected_file.stat().st_mtime < time.time() - hours_back * 3600:
        st.warning(
            "This saved result is older than the selected news window. "
            "Fetch latest news to replace this view with current feed data."
        )
if duplicate_articles_removed:
    st.caption(f"Removed {duplicate_articles_removed} duplicate article entries.")

metric_columns = st.columns(4)
metric_columns[0].metric("Articles", len(articles))
metric_columns[1].metric("Unique people", len(unique_people))
metric_columns[2].metric("Profile mentions", len(profiles))
metric_columns[3].metric("Negative mentions", negative_mentions)

sentiment_counts = (
    pd.Series([profile.get("sentiment", "unknown").title() for profile in profiles])
    .value_counts()
    .rename_axis("Sentiment")
    .to_frame("Profiles")
)

summary_left, summary_right = st.columns(2)
with summary_left:
    st.subheader("Sentiment across article mentions")
    if sentiment_counts.empty:
        st.info("There are no profile results to chart.")
    else:
        st.bar_chart(sentiment_counts, color="#d95f59")

with summary_right:
    st.subheader("Articles by source")
    source_counts = (
        pd.Series([article.get("source", "Unknown") for article in articles])
        .value_counts()
        .rename_axis("Source")
        .to_frame("Articles")
    )
    if source_counts.empty:
        st.info("There are no articles to chart.")
    else:
        st.bar_chart(source_counts, color="#4c78a8")

profiles_tab, articles_tab = st.tabs(["People and sentiment", "Articles"])

with profiles_tab:
    st.subheader("Profile results")
    filter_columns = st.columns([2, 1, 1])
    search = filter_columns[0].text_input(
        "Search people or article titles",
        key="profile_search",
    ).strip().casefold()
    sentiment_options = sorted(
        {profile.get("sentiment", "unknown").title() for profile in profiles}
    )
    selected_sentiments = filter_columns[1].multiselect(
        "Sentiment",
        sentiment_options,
        default=sentiment_options,
    )
    selected_sources = filter_columns[2].multiselect(
        "Source",
        sources,
        default=sources,
    )

    filtered_profiles = [
        profile
        for profile in profiles
        if profile.get("sentiment", "unknown").title() in selected_sentiments
        and profile.get("source", "Unknown") in selected_sources
        and (
            not search
            or search in profile.get("name", "").casefold()
            or search in profile.get("article_title", "").casefold()
        )
    ]

    people: dict[str, list[dict[str, Any]]] = {}
    for profile in filtered_profiles:
        key = normalize_name(profile)
        if key:
            people.setdefault(key, []).append(profile)

    display_profiles = pd.DataFrame(
        [
            {
                "Person": person_profiles[0].get("name", ""),
                "Age": next(
                    (
                        profile.get("age")
                        for profile in person_profiles
                        if profile.get("age") is not None
                    ),
                    None,
                ),
                "Sentiment": " / ".join(
                    sorted(
                        {
                            profile.get("sentiment", "unknown").title()
                            for profile in person_profiles
                        }
                    )
                ),
                "Articles": len(
                    {
                        str(profile.get("article_url", "")).strip().casefold()
                        or str(profile.get("article_title", "")).strip().casefold()
                        for profile in person_profiles
                    }
                ),
                "Avg. confidence": (
                    sum(
                        float(profile.get("confidence", 0) or 0)
                        for profile in person_profiles
                    )
                    / len(person_profiles)
                    * 100
                ),
                "News": " | ".join(
                    dict.fromkeys(
                        str(profile.get("article_title", "")).strip()
                        for profile in person_profiles
                        if profile.get("article_title")
                    )
                ),
            }
            for person_profiles in people.values()
        ]
    )
    st.caption(
        f"{len(people)} unique people across "
        f"{len(filtered_profiles)} matching article mentions"
    )
    st.dataframe(
        display_profiles,
        use_container_width=True,
        hide_index=True,
        column_config={
            "Avg. confidence": st.column_config.NumberColumn(
                "Average confidence",
                format="%.1f%%",
            ),
        },
    )

    if not display_profiles.empty:
        st.download_button(
            "Download filtered profiles as CSV",
            display_profiles.to_csv(index=False).encode("utf-8-sig"),
            file_name="filtered_profiles.csv",
            mime="text/csv",
        )

with articles_tab:
    st.subheader("Full news articles")
    st.caption("Expand any article to read its complete scraped text and related profiles.")
    article_search = st.text_input(
        "Search article titles or full text",
        key="article_search",
    ).strip().casefold()
    filtered_articles = [
        article
        for article in articles
        if not article_search
        or article_search in article.get("title", "").casefold()
        or article_search in article.get("content", "").casefold()
    ]

    if not filtered_articles:
        st.info("No articles match that search.")
    else:
        for article in filtered_articles:
            article_url = str(article.get("url", "")).strip()
            article_urls = set(article.get("_duplicate_urls", []))
            if article_url:
                article_urls.add(article_url.casefold())
            article_title = article.get("title", "Untitled article")
            article_profiles = [
                profile
                for profile in profiles
                if str(profile.get("article_url", "")).strip().casefold()
                in article_urls
            ]

            with st.expander(format_article_option(article), expanded=False):
                st.markdown(f"### {article_title}")
                st.caption(
                    " · ".join(
                        value
                        for value in (
                            article.get("source", ""),
                            article.get("publication_date", ""),
                        )
                        if value
                    )
                )
                if article_url:
                    st.link_button(
                        "Open original article",
                        article_url,
                    )

                person_mentions: dict[str, dict[str, Any]] = {}
                for profile in article_profiles:
                    key = normalize_name(profile)
                    if key and key not in person_mentions:
                        person_mentions[key] = profile

                st.write(f"**Unique people analyzed:** {len(person_mentions)}")
                if person_mentions:
                    st.dataframe(
                        pd.DataFrame(
                            [
                                {
                                    "Person": profile.get("name", ""),
                                    "Age": profile.get("age"),
                                    "Sentiment": profile.get(
                                        "sentiment", "unknown"
                                    ).title(),
                                    "Confidence": profile.get("confidence", 0),
                                }
                                for profile in person_mentions.values()
                            ]
                        ),
                        use_container_width=True,
                        hide_index=True,
                        column_config={
                            "Confidence": st.column_config.NumberColumn(
                                "Confidence",
                                format="%.3f",
                            )
                        },
                    )

                st.markdown("**Full article text**")
                article_content = article.get("content", "")
                if article_content:
                    st.text(article_content)
                else:
                    st.info("No article text is available for this item.")
