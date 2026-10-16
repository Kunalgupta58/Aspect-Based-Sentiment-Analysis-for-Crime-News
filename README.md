---
title: Crime News Sentiment API
emoji: 📰
colorFrom: gray
colorTo: red
sdk: docker
app_port: 7860
pinned: false
---

# Crime News Sentiment API

FastAPI backend for the Crime News Sentiment website. The API health check is
available at `/api/health`; API documentation is available at `/docs`.

Set `ALLOWED_ORIGINS` in the Space settings to the exact origin of the Vercel
website, without a trailing slash.