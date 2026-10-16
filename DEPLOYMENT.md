# Deploy the News and Sentiment Website

This guide deploys the public website to **Vercel** and the Python news/sentiment API to **Render**. The project root is the folder containing `main.py`, `api.py`, `requirements.txt`, and `frontend/`.

## How the deployment is split

- **Vercel** serves the website files in `frontend/`. Anything sent to a visitor's browser, including JavaScript, can be inspected.
- **Render** runs `api.py`, the scraper, spaCy, and the sentiment model. The Python source and model files are not sent to website visitors' browsers.
- Keep the GitHub repository **private** if you do not want the source repository to be publicly visible. Vercel and Render still need repository access to build and run it, and those providers can access the deployed code.
- The API returns article metadata and original story links, not the scraped article body. It does return the analysis results requested by the public website.

## 1. Prepare and test the project locally (Windows)

Open two PowerShell terminals in VS Code, with the first terminal in the project root (the folder containing `api.py`).

### Start the API

```powershell
python -m pip install -r requirements.txt -r backend-requirements.txt
python api.py
```

`api.py` applies the short-path workaround for the Windows spaCy import error. Keep this terminal open. Check these URLs in a browser:

- `http://1
27.0.0.1:8000/api/health` should show `{"status":"ok"}`.
- `http://127.0.0.1:8000/docs` should show the API documentation.

### Start the website

In the second terminal:

```powershell
Set-Location .\frontend
Set-Content -Path .env.local -Value 'VITE_API_URL=http://localhost:8000'
```

This writes the API setting into `.env.local`; do not enter the `VITE_API_URL=...` line by itself at the PowerShell prompt. Then run:

```powershell
npm install
npm run dev
```

Open the local address Vite prints (usually `http://localhost:5173`). Click **Fetch and analyze** to test an analysis. The first analysis downloads the model and may take several minutes. Stop the API and website with **Ctrl+C** in their respective terminals.

## 2. Keep the source repository private

1. Make sure your GitHub repository contains this project and its `frontend/` folder. In the repository root, you should be able to find `main.py`, `api.py`, `requirements.txt`, and `frontend/package.json`.
2. In GitHub, open the repository's **Settings → General → Danger Zone → Change repository visibility** and set it to **Private**, if it is not already private. You must have permission to change this setting.
3. In VS Code's **Source Control** view, review the changed files, stage the intended project files, commit, and push/sync them to GitHub.
4. Do not commit `.env.local`, passwords, access tokens, or other secrets. The project ignores `frontend/.env.local`. Do not put secrets in variables whose names start with `VITE_`; Vite embeds those values in public website files.

If you create a new GitHub repository, create it as **Private**, then push the project files to it. Do not upload `.venv/` or `frontend/node_modules/`; these are local dependencies and are ignored.

## 3. Deploy the Python API on Render

1. Sign in to Render and create a **Web Service** connected to the private GitHub repository. Authorize Render to access that repository if prompted.
2. Set **Root Directory** to the project folder containing `api.py` and `requirements.txt`. If those files are at the repository root, leave this field blank. If the repository has an outer folder around the project, enter that folder's repository-relative path.
3. Set the runtime to **Python 3**. This repository includes a `.python-version` file pinned to Python **3.11.11**; confirm the Render build log says it is using Python 3.11.x, not the default Python 3.14.
4. Set the **Build Command** to:

   ```bash
   python -m pip install --upgrade pip && python -m pip install --index-url https://download.pytorch.org/whl/cpu torch==2.7.1 && python -m pip install -r requirements.txt -r backend-requirements.txt && python -m nltk.downloader -d /opt/render/nltk_data punkt punkt_tab
   ```

5. Set the **Start Command** to:

   ```bash
   uvicorn api:app --host 0.0.0.0 --port $PORT
   ```

6. Choose an instance with enough RAM and disk for PyTorch, spaCy, and the sentiment model. Check Render's current plans and limits; model download and analysis need more resources than the static website.
7. In the Render service's **Environment** settings, add:
   - **Key:** `NLTK_DATA`
   - **Value:** `/opt/render/nltk_data`
   The API also attempts to download `punkt` into this directory when an analysis starts if the build-time download was unavailable.
8. Create the service. The first build installs large dependencies and can take several minutes. Copy the public service URL once it is deployed, for example `https://your-api-name.onrender.com`.
9. Test `https://your-api-name.onrender.com/`; it should show a message confirming the API is running, with links to health and documentation. `https://your-api-name.onrender.com/api/health` should return `{"status":"ok"}`, and `/docs` should show the API endpoints.

No API key is required by the current code. Do not add one as a frontend variable.

## 4. Deploy the website on Vercel

1. In Vercel, choose **Add New → Project** and import the same private GitHub repository.
2. Set **Root Directory** to `frontend` (or to the repository-relative path ending in `/frontend` if the project is nested inside another folder). Vercel should detect Vite.
3. Use these build settings:
   - **Build Command:** `npm run build`
   - **Output Directory:** `dist`
   - **Install Command:** `npm install`
4. Under **Environment Variables**, add:
   - **Name:** `VITE_API_URL`
   - **Value:** the Render URL copied above, with no trailing slash. Example: `https://your-api-name.onrender.com`
   - Enable it for the deployment environments you use (Production, and Preview if needed).
5. Deploy the Vercel project. Copy the website's production origin, for example `https://your-site.vercel.app`. The origin is the scheme and hostname only; do not include a path or trailing slash.

`VITE_API_URL` is intentionally public: it tells the browser where the API lives. It is not a password or secret.

## 5. Allow the website to call the API

1. Return to the Render service and open **Environment**.
2. Add or edit:
   - **Key:** `ALLOWED_ORIGINS`
   - **Value:** your exact Vercel production origin, for example `https://your-site.vercel.app`
3. If you use multiple site domains, list them separated by commas, with no trailing slash, for example:

   ```text
   https://your-site.vercel.app,https://www.example.com
   ```

4. Save the environment change and restart/redeploy the Render service.
5. Open the Vercel site, confirm that the saved results load or that it explains there are no results yet, then click **Fetch and analyze**. The first request may take several minutes while the API downloads and loads the model.

If you add a Vercel preview deployment, its hostname is different. Add that exact origin to `ALLOWED_ORIGINS` if browser requests from that preview need to work.

## Updating the site later

1. Make and test changes locally.
2. Commit and push them to the connected private GitHub branch.
3. Vercel and Render will build/deploy from that branch automatically. Check both deployment logs if a change affects both the website and API.

## Troubleshooting

| Symptom | What to check |
|---|---|
| Render build cannot find `requirements.txt` or `api.py` | Its **Root Directory** must point to the folder containing those files. |
| Render says no matching `torch==2.7.1` distribution is available | The build is using an incompatible Python version (the log showed Python 3.14). Ensure `.python-version` containing `3.11.11` is committed in the Render service's root directory and deployed. Alternatively, set Render's `PYTHON_VERSION` environment variable to `3.11.11`, then redeploy. Confirm the next build log uses Python 3.11.x. |
| Analysis reports that NLTK `punkt` is missing | Set Render environment variable `NLTK_DATA` to `/opt/render/nltk_data` and make sure the build command downloads to that same directory with `python -m nltk.downloader -d /opt/render/nltk_data punkt punkt_tab`. Save the settings, redeploy, and rerun the analysis. |
| Render reports out-of-memory or the analysis is terminated | Use an instance with more memory. PyTorch, spaCy, and the transformer model are resource-heavy. |
| `/api/health` works, but the website reports a network/CORS error | Set Render's `ALLOWED_ORIGINS` to the exact Vercel origin, save, and restart Render. Check for a typo or trailing slash. |
| Website says the API URL is not configured | Set Vercel's `VITE_API_URL`, then redeploy the website. |
| Clicking fetch returns a cooldown or “already running” message | Wait for the active analysis to finish. The API intentionally allows one analysis at a time and enforces a five-minute cooldown. |
| Analysis fails while loading the model | Check the Render logs and available memory/disk. The first analysis must download the model and needs a working outbound connection. |
| Analysis finishes, but results disappear after a restart/redeploy | Current results/jobs are held in process memory and files in `outputs/`. Add persistent storage or a database before relying on results surviving restarts. |
| Local Windows API fails importing spaCy with a filename/path-too-long message | Run `python api.py` from the project root. It uses a temporary short drive path automatically. |

## Public-use and data notes

- Sentiment is an automated prediction, not a verified fact, accusation, or legal conclusion. The page displays a reminder to check the linked article.
- The API's analysis endpoint is public. Its one-job-at-a-time check and five-minute cooldown are basic safeguards held in one running process; they are not robust authentication or distributed rate limiting. For a larger public audience, protect analysis requests with authentication or an admin-only control, and use a persistent job queue and database.
- A hosting provider can access code and data deployed to its service. A private GitHub repository hides the repository from the general public, not from providers with authorized access.
- News text and names are processed by the backend and sentiment model; the site exposes the resulting headlines, source links, and profiles to its visitors.
