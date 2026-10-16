FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    NLTK_DATA=/app/nltk_data

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt backend-requirements.txt ./

RUN python -m pip install --no-cache-dir --upgrade pip \
    && python -m pip install --no-cache-dir \
        --index-url https://download.pytorch.org/whl/cpu torch==2.7.1 \
    && python -m pip install --no-cache-dir \
        -r requirements.txt -r backend-requirements.txt \
    && python -m nltk.downloader -d "$NLTK_DATA" punkt punkt_tab

COPY api.py main.py scraper.py text_processing.py ./
COPY outputs/ ./outputs/

EXPOSE 7860

CMD ["uvicorn", "api:app", "--host", "0.0.0.0", "--port", "7860"]