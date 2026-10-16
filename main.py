import argparse
import ctypes
import json
import logging
import os
import subprocess
import sys
from datetime import datetime
from functools import lru_cache
from pathlib import Path

if os.name == "nt" and __name__ == "__main__" and not os.environ.get(
    "ABSA_SHORT_PATH_RELAUNCH"
):
    project_dir = Path(__file__).resolve().parent
    used_drives = ctypes.windll.kernel32.GetLogicalDrives()
    drive_letter = next(
        (
            letter
            for letter in "ZYXWVUTSRQPONMLKJIHGFEDCBA"
            if not used_drives & (1 << (ord(letter) - ord("A")))
        ),
        None,
    )
    if drive_letter is None:
        raise OSError("No unused drive letter is available for the short-path run.")

    drive = f"{drive_letter}:"
    short_project_dir = Path(f"{drive}\\")
    subprocess.run(["subst", drive, str(project_dir)], check=True)
    environment = os.environ.copy()
    environment["ABSA_SHORT_PATH_RELAUNCH"] = "1"
    try:
        try:
            executable = Path(sys.executable).resolve().relative_to(project_dir)
            python_executable = str(short_project_dir / executable)
        except ValueError:
            python_executable = sys.executable

        result = subprocess.run(
            [
                python_executable,
                str(short_project_dir / Path(__file__).name),
                *sys.argv[1:],
            ],
            cwd=short_project_dir,
            env=environment,
        )
    finally:
        subprocess.run(["subst", drive, "/d"], check=True)
    sys.exit(result.returncode)

import nltk
import pandas as pd
import spacy
from transformers import (
    AutoModelForSequenceClassification,
    AutoTokenizer,
    pipeline,
)

from scraper import get_articles
from text_processing import (
    analyze_people_sentiment,
    break_into_sentences,
    clean_text,
    extract_entities_spacy,
)


@lru_cache(maxsize=1)
def load_analysis_models():
    model_name = "yangheng/deberta-v3-base-absa-v1.1"
    tokenizer = AutoTokenizer.from_pretrained(model_name, use_fast=False)
    absa_model = AutoModelForSequenceClassification.from_pretrained(model_name)
    sentiment_pipeline = pipeline(
        "text-classification",
        model=absa_model,
        tokenizer=tokenizer,
    )
    nlp = spacy.load("en_core_web_sm")
    return sentiment_pipeline, nlp


def main(
    feed_urls: list[str] | None = None,
    hours_back: int = 24,
) -> dict:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    nltk_data_dir = Path(
        os.environ.get(
            "NLTK_DATA",
            Path(__file__).resolve().parent / "nltk_data",
        )
    )
    nltk.data.path.insert(0, str(nltk_data_dir))
    try:
        nltk.data.find("tokenizers/punkt")
    except LookupError as error:
        raise RuntimeError(
            "The NLTK punkt tokenizer is missing. Install it during deployment "
            "with `python -m nltk.downloader -d nltk_data punkt punkt_tab`."
        ) from error

    print(f">>> Fetching live crime news from the last {hours_back} hours >>>")
    articles = get_articles(feed_urls=feed_urls, hours_back=hours_back)
    if not articles:
        raise RuntimeError(
            "No recent crime articles were found in the selected feeds. "
            "Try a longer time window or different RSS/Atom feed URLs."
        )

    print("loading transformer and spaCy models")
    absapipe, nlp = load_analysis_models()

    all_profiles = []
    negative_profiles = []
    for art in articles:
        title = art["title"]
        content = clean_text(art["content"])
        article_text = f"{title}. {content}"
        sentences = break_into_sentences(content)
        parsed_docs = iter(nlp.pipe([title, *sentences], batch_size=32))
        title_entities = extract_entities_spacy(title, nlp, next(parsed_docs))
        persons = title_entities["persons"]
        known_persons = {person.casefold() for person in persons}
        person_sentences = []
        for sentence, parsed in zip(sentences, parsed_docs):
            entities = extract_entities_spacy(sentence, nlp, parsed)
            if entities["persons"]:
                person_sentences.append((entities["persons"], entities["ages"]))
                for person in entities["persons"]:
                    if person.casefold() not in known_persons:
                        persons.append(person)
                        known_persons.add(person.casefold())

        sentiment_results = analyze_people_sentiment(
            break_into_sentences(article_text),
            persons,
            absapipe,
        )
        for person in persons:
            age = next(
                (
                    ages[0]
                    for sentence_persons, ages in person_sentences
                    if ages
                    and any(
                        person.casefold() == sentence_person.casefold()
                        for sentence_person in sentence_persons
                    )
                ),
                None,
            )
            sentiment_result = sentiment_results[person]
            profile = {
                "name": person,
                "age": age,
                "article_url": art["url"],
                "article_title": title,
                "article_publication_date": art["publication_date"],
                "sentiment": sentiment_result["sentiment"],
                "confidence": round(sentiment_result.get("confidence", 0.0), 3),
                "source": art["source"],
            }
            all_profiles.append(profile)
            if profile["sentiment"] == "negative":
                negative_profiles.append(profile)

    # Save output
    time_now = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S_%f")
    output_dir = Path(__file__).resolve().parent / "outputs"
    output_dir.mkdir(parents=True, exist_ok=True)

    pd.DataFrame(articles).to_csv(
        output_dir / f"scraped_articles_{time_now}.csv",
        index=False,
    )
    pd.DataFrame(negative_profiles).to_csv(
        output_dir / f"negative_profiles_{time_now}.csv",
        index=False,
    )

    result = {
        "scraped_articles": articles,
        "all_profiles": all_profiles,
        "negative_profiles": negative_profiles,
        "statistics": {
            "total_articles_scraped": len(articles),
            "total_profiles": len(all_profiles),
            "negative_profiles": len(negative_profiles),
            "sources": list({article["source"] for article in articles}),
        }
    }
    with (output_dir / f"detailed_results_{time_now}.json").open(
        "w",
        encoding="utf-8",
    ) as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    stats = result["statistics"]
    print("\n--- SUMMARY ---")
    print(f"Articles scraped: {stats['total_articles_scraped']}")
    print(f"Total profiles analyzed: {stats['total_profiles']}")
    print(f"Negative profiles: {stats['negative_profiles']}")
    print(f"Sources: {', '.join(stats['sources'])}")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Fetch recent crime news from live RSS/Atom feeds and analyze it."
    )
    parser.add_argument(
        "--feed-url",
        action="append",
        dest="feed_urls",
        help="RSS/Atom feed URL to fetch; repeat to add multiple feeds.",
    )
    parser.add_argument(
        "--hours-back",
        type=int,
        default=24,
        help="Only include articles published in this many recent hours (default: 24).",
    )
    arguments = parser.parse_args()
    main(feed_urls=arguments.feed_urls, hours_back=arguments.hours_back)