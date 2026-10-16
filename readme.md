# News Crime ABSA Project

## Overview

This project fetches recent crime-related news from live publisher feeds, then uses aspect-based sentiment analysis (ABSA) to determine sentiment towards people mentioned in those articles.

---

## Features

- Fetches news published within the last 24 hours from live Times of India and Hindustan Times feeds by default
- Accepts custom RSS/Atom feed URLs and common JSON news API endpoints
- Identifies articles on crimes such as fraud, murder, and scams
- Extracts metadata: title, content, date/time, URL, named persons, and age (if available)
- Cleans and tokenizes the article text
- Detects sentences mentioning individuals
- Uses only sentences mentioning each individual as context for the ABSA model, with the individual supplied as the aspect
- Assesses sentiment (positive, negative, or neutral) towards each individual in the articles
- Outputs negative profiles (individuals discussed negatively) in CSV and JSON formats
- Modular and well-commented scripts for ease of understanding

---

## Setup Instructions

1. **Install Dependencies:**

   It's recommended to perform this in a new virtual environment.

   ```bash
   python -m venv venv
   source venv/bin/activate
   ```

   ```bash
   pip install -r requirements.txt
   ```

   The requirements file includes the spaCy English model used for person extraction.

2. **Run the Pipeline:**

   ```bash
   python main.py
   ```

   The default run fetches current stories from publisher feeds and excludes articles older than 24 hours. To change the time window or use a custom feed/API URL:

   ```bash
   python main.py --hours-back 12
   python main.py --feed-url "https://news.example.com/rss.xml"
   ```

   Repeat `--feed-url` to provide multiple feeds. JSON API responses should contain article lists under `articles`, `results`, `items`, or `data`, with fields such as `title`, `url`, and `publishedAt` or `date`. APIs that require authentication headers may need an adapter for that provider.

3. **View Outputs:**

   All output files (scraped_articles_*.csv, negative_profiles_*.csv, detailed_results_*.json) will be located in the `outputs` folder.

4. **Open the Results Dashboard:**

   Run the dashboard from the project folder after installing the dependencies:

   ```bash
   python -m streamlit run dashboard.py
   ```

   The dashboard initially displays the newest saved results file. Click **Fetch latest news and analyze** to retrieve current articles from live feeds and replace the view with a new run. The sidebar lets you choose the maximum age of articles and provide custom RSS/Atom or JSON API URLs. Without custom URLs, the Times of India and Hindustan Times feeds are used.

   On Windows, `python main.py` automatically relaunches using a temporary short drive path to avoid spaCy's Windows path-length import error. You can run the analysis directly as above; no manual drive mapping is required. Start the dashboard with `python -m streamlit run dashboard.py`. Its **Fetch latest news and analyze** button also uses a short drive path when running the pipeline.

## Public Website Deployment

The public website is a separate Vite frontend in `frontend/`. The Python API and sentiment model run on a backend host such as Render. This keeps Python/model code out of the browser bundle; users can still inspect the frontend files sent to their browser. Keep the GitHub repository **private** to avoid publishing the source repository itself. The hosting providers can access code deployed to their services.

For the complete step-by-step guide, including local testing, GitHub privacy, Render API configuration, Vercel project settings, and troubleshooting, follow [DEPLOYMENT.md](./DEPLOYMENT.md).

### Deploy the Python API

1. Create a Render **Web Service** from the private GitHub repository and select this project folder as its root directory.
2. Choose Python 3.11 and a service with enough memory for the transformer model (4 GB RAM is a practical starting point; check current provider pricing before choosing a plan).
3. Use this build command:

   ```bash
   python -m pip install --upgrade pip && python -m pip install --index-url https://download.pytorch.org/whl/cpu torch==2.7.1 && python -m pip install -r requirements.txt -r backend-requirements.txt && python -m nltk.downloader -d /opt/render/nltk_data punkt punkt_tab
   ```

4. Use this start command:

   ```bash
   uvicorn api:app --host 0.0.0.0 --port $PORT
   ```

5. Set `ALLOWED_ORIGINS` to your Vercel website origin, for example `https://your-project.vercel.app`. Do not add a trailing slash. After deploying, verify `https://YOUR-API.onrender.com/api/health` returns `{"status":"ok"}`.

### Deploy the Vercel website

1. Import the same private repository into Vercel and set **Root Directory** to `frontend`.
2. Select Vite as the framework. Use `npm run build` as the build command and `dist` as the output directory.
3. Add the environment variable `VITE_API_URL` with the Render API base URL, for example `https://your-api.onrender.com`.
4. Deploy, then add the exact Vercel site origin to the Render `ALLOWED_ORIGINS` setting and redeploy/restart the API.

For local frontend development, first install API dependencies from the project folder with `pip install -r backend-requirements.txt`. Create `frontend/.env.local` from `frontend/.env.example` and set `VITE_API_URL=http://localhost:8000`. In one terminal run `python api.py` from the project folder; in another run `npm install` and `npm run dev` from `frontend`. The API script applies the Windows short-path workaround automatically.

The public site shows article headlines, publication/source details, links to the original stories, and sentiment profiles; the API does not publish scraped article bodies. It initially shows saved analysis when available. Visitors can request a new analysis for the previous 6–72 hours; to limit model usage, the API runs one job at a time and applies a five-minute cooldown between runs. The first model load can be slow. Results are held in process memory and the service's local `outputs` directory, so configure persistent storage or a database if results must survive backend restarts or deployments.

---

## Notes

- The project utilizes a free HuggingFace ABSA model and spaCy for NLP tasks.
- The first run can take longer because the transformer model must be downloaded; later runs reuse the local model cache. Analysis batches spaCy entity extraction and transformer sentiment predictions to reduce per-sentence overhead.
- Output columns in the negative profiles file include: name, age, article_url, article_title, article_date.
- If a person's age isn’t detected, the age field remains empty.
- Crime-related articles are selected by title keywords in `scraper.py`; feed publication dates are checked against the selected time window.
- Custom APIs must return common article fields; provider-specific authentication headers or response formats require a provider adapter.
- To add more sources or crime keywords, edit `scraper.py` or provide a custom feed URL in the dashboard.

---

## Limitations and Improvements

- Crime articles are detected by certain keywords in the title.
- Article structure may change, potentially breaking some data extraction if the website layout is updated.
- Sentiment analysis is contingent on the quality of the ABSA model.
- The ABSA model may misclassify some sentences.

## How can we improve this project?

- Using zero-shot transformer models for smarter crime news detection.
- Can Upgrade NER with spaCy’s large or transformer models for more accurate entity extraction.