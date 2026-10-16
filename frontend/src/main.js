import "./style.css";

const apiUrl = (import.meta.env.VITE_API_URL || "").replace(/\/+$/, "");
const statusElement = document.querySelector("#status");
const noticeElement = document.querySelector("#notice");
const refreshButton = document.querySelector("#refresh");
let currentResult = null;

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function safeExternalUrl(value) {
  try {
    const url = new URL(value);
    return ["http:", "https:"].includes(url.protocol) ? url.href : null;
  } catch {
    return null;
  }
}

function setStatus(message, isError = false) {
  statusElement.textContent = message;
  statusElement.classList.toggle("error", isError);
}

function showNotice(message, kind = "info") {
  noticeElement.hidden = !message;
  noticeElement.className = `notice ${kind}`;
  noticeElement.textContent = message;
}

const wait = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds));

async function request(path, options = {}) {
  if (!apiUrl) {
    throw new Error("The API URL has not been configured for this website.");
  }
  const isGet = (options.method || "GET").toUpperCase() === "GET";
  const attempts = isGet ? 5 : 1;
  let response;

  for (let attempt = 0; attempt < attempts; attempt += 1) {
    try {
      response = await fetch(`${apiUrl}${path}`, {
        headers: { "Content-Type": "application/json" },
        ...options,
      });
    } catch (error) {
      if (attempt + 1 === attempts) {
        if (error instanceof TypeError) {
          throw new Error(
            "The analysis API is unreachable. Check that the Render service is running and allows this website."
          );
        }
        throw error;
      }
      await wait(1000 * 2 ** attempt);
      continue;
    }

    if (response.status >= 500 && attempt + 1 < attempts) {
      await wait(1000 * 2 ** attempt);
      continue;
    }
    break;
  }

  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(payload.detail || `Request failed (${response.status}).`);
  }
  return payload;
}

function normalizeName(profile) {
  return String(profile.name || "").trim().toLocaleLowerCase();
}

function renderProfiles(profiles, articles) {
  const container = document.querySelector("#profiles");
  const query = document.querySelector("#search").value.trim().toLocaleLowerCase();
  const articleByUrl = new Map(articles.map((article) => [article.url, article]));
  const visible = profiles.filter((profile) => {
    const articleTitle = String(profile.article_title || "").toLocaleLowerCase();
    return (
      !query ||
      String(profile.name || "").toLocaleLowerCase().includes(query) ||
      articleTitle.includes(query)
    );
  });
  container.replaceChildren();
  container.className = visible.length ? "profiles" : "profiles empty-state";
  if (!visible.length) {
    container.textContent = query ? "No profile results match that search." : "No people were identified in these articles.";
    return;
  }

  for (const profile of visible) {
    const card = element("article", "profile-card");
    const top = element("div", "profile-top");
    const name = element("h3", "", profile.name || "Unknown person");
    const sentiment = String(profile.sentiment || "unknown").toLowerCase();
    const badge = element("span", `badge ${sentiment}`, sentiment);
    top.append(name, badge);
    const meta = element(
      "p",
      "profile-meta",
      `${profile.age ? `${profile.age} · ` : ""}${profile.source || "News source"}`
    );
    card.append(top, meta);

    const article = articleByUrl.get(profile.article_url);
    const title = profile.article_title || article?.title;
    if (title) {
      const url = safeExternalUrl(profile.article_url || article?.url);
      if (url) {
        const link = element("a", "profile-article", title);
        link.href = url;
        link.target = "_blank";
        link.rel = "noopener noreferrer";
        card.append(link);
      } else {
        card.append(element("p", "profile-article", title));
      }
    }
    const confidence = Number(profile.confidence);
    if (Number.isFinite(confidence)) {
      card.append(element("p", "confidence", `Model confidence: ${Math.round(confidence * 100)}%`));
    }
    container.append(card);
  }
}

function renderArticles(articles) {
  const container = document.querySelector("#articles");
  container.replaceChildren();
  container.className = articles.length ? "articles" : "articles empty-state";
  if (!articles.length) {
    container.textContent = "No articles are available in this result.";
    return;
  }
  for (const article of articles) {
    const row = element("article", "article-row");
    const sourceAndDate = element(
      "p",
      "article-meta",
      `${article.source || "News source"} · ${article.publication_date || "Date unavailable"}`
    );
    const link = element("a", "article-title", article.title || "Untitled article");
    const url = safeExternalUrl(article.url);
    if (url) {
      link.href = url;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      row.append(sourceAndDate, link);
    } else {
      row.append(sourceAndDate, element("p", "article-title", article.title || "Untitled article"));
    }
    container.append(row);
  }
}

function renderResults(data) {
  currentResult = data;
  const articles = Array.isArray(data.scraped_articles) ? data.scraped_articles : [];
  const profiles = Array.isArray(data.all_profiles) ? data.all_profiles : [];
  const people = new Set(profiles.map(normalizeName).filter(Boolean));
  const negative = profiles.filter((profile) => profile.sentiment === "negative").length;

  document.querySelector("#article-count").textContent = articles.length;
  document.querySelector("#people-count").textContent = people.size;
  document.querySelector("#profile-count").textContent = profiles.length;
  document.querySelector("#negative-count").textContent = negative;
  document.querySelector("#sources").textContent = [...new Set(articles.map((article) => article.source).filter(Boolean))].join(" · ");
  renderProfiles(profiles, articles);
  renderArticles(articles);
  showNotice("Predictions are automated and may be inaccurate. Check the original articles for context.", "info");
}

async function loadResults() {
  try {
    const result = await request("/api/results");
    renderResults(result);
    setStatus("Showing the latest saved analysis.");
  } catch (error) {
    if (error.message.includes("No results yet")) {
      setStatus("No analysis has been run yet.");
      showNotice("Choose a time window and fetch news to create the first analysis.");
      return;
    }
    setStatus(error.message, true);
    showNotice(error.message, "error");
  }
}

async function runAnalysis() {
  refreshButton.disabled = true;
  showNotice("");
  try {
    setStatus("Starting the news analysis…");
    const { job_id: jobId } = await request("/api/jobs", {
      method: "POST",
      body: JSON.stringify({ hours_back: Number(document.querySelector("#hours").value) }),
    });
    for (;;) {
      await wait(2500);
      const job = await request(`/api/jobs/${encodeURIComponent(jobId)}`);
      if (job.status === "completed") {
        renderResults(await request("/api/results"));
        setStatus("Analysis complete. Results updated.");
        break;
      }
      if (job.status === "failed") {
        throw new Error(job.error || "The analysis could not be completed.");
      }
      setStatus(
        job.status === "queued"
          ? "Analysis queued…"
          : job.progress || "Fetching and analyzing articles. This can take a few minutes…"
      );
    }
  } catch (error) {
    setStatus(error.message, true);
    showNotice(error.message, "error");
  } finally {
    refreshButton.disabled = false;
  }
}

refreshButton.addEventListener("click", runAnalysis);
document.querySelector("#search").addEventListener("input", () => {
  if (!currentResult) return;
  const articles = Array.isArray(currentResult.scraped_articles) ? currentResult.scraped_articles : [];
  const profiles = Array.isArray(currentResult.all_profiles) ? currentResult.all_profiles : [];
  renderProfiles(profiles, articles);
});

loadResults();
