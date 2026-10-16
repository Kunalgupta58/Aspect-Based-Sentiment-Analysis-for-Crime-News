import re
from nltk.tokenize import sent_tokenize

AGE_PATTERNS = [
    r'(\d{1,3})\s*(?:years?\s*old|yr|yrs)',
    r'age\s*(\d{1,3})',
    r'aged\s*(\d{1,3})',
    r'(\d{1,3})\s*-\s*year\s*-\s*old',
    r'\((\d{1,3})\)',
]

def clean_text(txt):
    txt = re.sub(r'<[^>]+>', '', txt)
    txt = re.sub(r'\s+', ' ', txt)
    patterns = [
        r'Advertisement', r'Subscribe to.*?Newsletter', r'Follow us on.*?Twitter',
        r'Like us on.*?Facebook', r'Share.*?WhatsApp', r'Download.*?app',
        r'Copyright.*?\d{4}', r'All rights reserved', r'Terms.*?Conditions', r'Privacy.*?Policy'
    ]
    for pat in patterns:
        txt = re.sub(pat, '', txt, flags=re.IGNORECASE)
    return txt.strip()

def break_into_sentences(text):
    return [s.strip() for s in sent_tokenize(text) if s.strip()]

def extract_entities_spacy(text, nlp, parsed=None):
    data = {'persons': [], 'ages': []}
    if not nlp:
        return data
    if parsed is None:
        parsed = nlp(text)
    dateline = re.match(r"^\s*(?P<location>[A-Z][A-Za-z .'-]*),\s+The\b", text)
    for ent in parsed.ents:
        if ent.label_ == "PERSON":
            nm = ent.text.strip()
            if (
                dateline
                and ent.start_char == dateline.start("location")
                and ent.end_char == dateline.end("location")
                and nm.casefold() == dateline.group("location").strip().casefold()
            ):
                continue
            if len(nm) > 2 and nm not in data['persons']:
                data['persons'].append(nm)
    for pat in AGE_PATTERNS:
        found = re.findall(pat, text, re.IGNORECASE)
        for x in found:
            try:
                num = int(x)
                if 1 <= num <= 120 and num not in data['ages']:
                    data['ages'].append(num)
            except Exception:
                continue
    return data

def analyze_person_sentiment(full_txt, person, pipe):
    if not pipe:
        return {"sentiment": "neutral", "confidence": 0.0}

    return analyze_people_sentiment(
        break_into_sentences(full_txt),
        [person],
        pipe,
    )[person]


def analyze_people_sentiment(sentences, persons, pipe):
    if not pipe:
        return {
            person: {"sentiment": "neutral", "confidence": 0.0}
            for person in persons
        }

    predictions_by_person = {person: [] for person in persons}
    inputs = []
    for person in persons:
        person_pattern = re.compile(
            rf"(?<!\w){re.escape(person)}(?!\w)",
            re.IGNORECASE,
        )
        for sentence in sentences:
            if person_pattern.search(sentence):
                inputs.append((person, sentence))

    batch_size = 8
    for start in range(0, len(inputs), batch_size):
        batch = inputs[start : start + batch_size]
        results = pipe(
            [
                {"text": sentence, "text_pair": person}
                for person, sentence in batch
            ],
            batch_size=batch_size,
            truncation=True,
            max_length=256,
        )
        if len(results) != len(batch):
            raise RuntimeError(
                "Sentiment model returned a different number of results "
                "than inputs."
            )
        for (person, _), result in zip(batch, results):
            if isinstance(result, list):
                if not result:
                    continue
                result = result[0]

            label = str(result.get("label", ""))
            normalized_label = label.lower()
            confidence = float(result.get("score", 0.0))
            if "positive" in normalized_label or "pos" in normalized_label:
                sentiment = "positive"
            elif "negative" in normalized_label or "neg" in normalized_label:
                sentiment = "negative"
            else:
                sentiment = "neutral"
            predictions_by_person[person].append(
                (sentiment, confidence, label)
            )

    results_by_person = {}
    for person, predictions in predictions_by_person.items():
        if not predictions:
            results_by_person[person] = {
                "sentiment": "neutral",
                "confidence": 0.0,
            }
            continue

        sentiment_counts = {
            sentiment: sum(
                prediction[0] == sentiment for prediction in predictions
            )
            for sentiment in ("positive", "negative", "neutral")
        }
        sentiment = max(sentiment_counts, key=sentiment_counts.get)
        matching_predictions = [
            prediction
            for prediction in predictions
            if prediction[0] == sentiment
        ]
        results_by_person[person] = {
            "sentiment": sentiment,
            "confidence": sum(
                prediction[1] for prediction in matching_predictions
            )
            / len(matching_predictions),
            "raw_label": matching_predictions[0][2],
        }
    return results_by_person
