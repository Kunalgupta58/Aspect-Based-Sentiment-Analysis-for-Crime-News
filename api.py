import ctypes
import json
import logging
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from uuid import uuid4

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
                "-m",
                "uvicorn",
                "api:app",
                "--host",
                "127.0.0.1",
                "--port",
                "8000",
            ],
            cwd=short_project_dir,
            env=environment,
        )
    finally:
        subprocess.run(["subst", drive, "/d"], check=True)
    sys.exit(result.returncode)

from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from main import main as run_analysis


logger = logging.getLogger(__name__)
PROJECT_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = PROJECT_DIR / "outputs"
COOLDOWN_SECONDS = 300
jobs: dict[str, dict] = {}
jobs_lock = threading.Lock()
last_job_started = 0.0
latest_result: dict | None = None

app = FastAPI(title="Crime News Sentiment API", version="1.0.0")
allowed_origins = [
    origin.strip()
    for origin in os.getenv("ALLOWED_ORIGINS", "*").split(",")
    if origin.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)


class AnalysisRequest(BaseModel):
    hours_back: int = Field(default=24, ge=6, le=72)


def run_job(job_id: str, hours_back: int) -> None:
    global latest_result

    with jobs_lock:
        jobs[job_id]["status"] = "running"

    def report_progress(message: str) -> None:
        logger.info("News analysis job %s: %s", job_id, message)
        with jobs_lock:
            job = jobs.get(job_id)
            if job is not None:
                job["progress"] = message

    try:
        result = run_analysis(
            hours_back=hours_back,
            progress_callback=report_progress,
        )
        with jobs_lock:
            latest_result = result
            jobs[job_id].update(status="completed")
    except Exception as error:
        logger.exception("News analysis job %s failed", job_id)
        if isinstance(error, LookupError) or (
            isinstance(error, RuntimeError) and "NLTK punkt tokenizer" in str(error)
        ):
            public_error = (
                "The analysis server could not find or download the NLTK punkt "
                "tokenizer. Check the backend NLTK_DATA setting and service logs."
            )
        else:
            public_error = "Analysis failed. Check the backend service logs for details."
        with jobs_lock:
            jobs[job_id].update(status="failed", error=public_error)


def read_latest_saved_result() -> dict | None:
    result_files = sorted(
        OUTPUT_DIR.glob("detailed_results_*.json"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    if not result_files:
        return None
    with result_files[0].open(encoding="utf-8") as result_file:
        result = json.load(result_file)
    if not isinstance(result, dict):
        raise ValueError("The latest saved analysis result is not a JSON object.")
    return result


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/")
def root() -> dict[str, str]:
    return {
        "message": "Crime News Sentiment API is running.",
        "health": "/api/health",
        "docs": "/docs",
    }


@app.get("/api/results")
def get_results() -> dict:
    global latest_result

    if latest_result is None:
        try:
            latest_result = read_latest_saved_result()
        except (OSError, json.JSONDecodeError, ValueError):
            logger.exception("Could not load the latest saved analysis result")
            raise HTTPException(
                status_code=500,
                detail="Could not load the saved analysis results.",
            ) from None
    if latest_result is None:
        raise HTTPException(
            status_code=404,
            detail="No results yet. Start an analysis to fetch current news.",
        )
    public_result = dict(latest_result)
    public_result["scraped_articles"] = [
        {
            key: value
            for key, value in article.items()
            if key != "content"
        }
        for article in latest_result.get("scraped_articles", [])
    ]
    return public_result


@app.post("/api/jobs", status_code=202)
def create_job(
    request: AnalysisRequest,
    background_tasks: BackgroundTasks,
) -> dict[str, str]:
    global last_job_started
    now = time.monotonic()

    with jobs_lock:
        active_job = next(
            (
                job
                for job in jobs.values()
                if job["status"] in {"queued", "running"}
            ),
            None,
        )
        if active_job:
            raise HTTPException(
                status_code=409,
                detail="An analysis is already running. Please check back shortly.",
            )
        remaining_cooldown = COOLDOWN_SECONDS - (now - last_job_started)
        if remaining_cooldown > 0:
            raise HTTPException(
                status_code=429,
                detail=(
                    "To protect the analysis service, please wait "
                    f"{int(remaining_cooldown)} seconds before starting another run."
                ),
                headers={"Retry-After": str(int(remaining_cooldown))},
            )

        job_id = str(uuid4())
        jobs[job_id] = {"status": "queued"}
        last_job_started = now
        for old_job_id in list(jobs)[:-20]:
            del jobs[old_job_id]

    background_tasks.add_task(run_job, job_id, request.hours_back)
    return {"job_id": job_id, "status": "queued"}


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str) -> dict:
    with jobs_lock:
        job = jobs.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Analysis job not found.")
        return {"job_id": job_id, **job}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)
