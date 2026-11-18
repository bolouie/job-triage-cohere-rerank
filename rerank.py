#!/usr/bin/env python3
"""Score job postings against my candidate profile with Cohere Rerank.

The job-search agent (Claude, on a weekday schedule) collects postings and
writes each one as a JSON record. This script sends every record to Cohere's
Rerank endpoint in one request, with my profile as the query, and writes back
a relevance score (0-1) for each posting. The triage dashboard blends that
score with rule-based checks (level, work mode, deadlines, degree gates).

Usage:
    export COHERE_API_KEY=...            # from dashboard.cohere.com
    python3 rerank.py data/jobs          # a folder of <id>.json job records
    python3 rerank.py data/jobs --out scores.json --top 15

No third-party packages: it calls the REST API (POST /v2/rerank) directly.
"""

import argparse
import json
import os
import pathlib
import sys
import urllib.error
import urllib.request

API_URL = "https://api.cohere.com/v2/rerank"
MODEL = "rerank-v5.0-pro"
PROFILE_PATH = pathlib.Path(__file__).with_name("profile.md")

# Fields that describe what the job is. Scheduling and bookkeeping fields
# (run date, URLs, verification status) are left out so they can't sway relevance,
# and so are the agent's caveats: rules already score those, and in testing they
# dragged a referral role from #25 to #70 by restating what the posting lacked.
DOC_FIELDS = [
    ("title", "Title"),
    ("company", "Company"),
    ("location", "Location"),
    ("mode", "Work mode"),
    ("level", "Level"),
    ("career", "Program"),
    ("years", "Experience"),
    ("stack", "Stack"),
    ("education", "Education"),
    ("sector", "Sector"),
    ("why", "Summary"),
    ("posting", "Posting text"),  # full posting text, when the agent saved it
]


def to_document(job):
    """Render a job record as YAML-style text, which Rerank handles well for structured data."""
    lines = []
    for key, label in DOC_FIELDS:
        value = job.get(key)
        if isinstance(value, list):
            value = ", ".join(value)
        if value:
            lines.append(f"{label}: {value}")
    return "\n".join(lines)


def load_api_key():
    """COHERE_API_KEY from the environment, else from a gitignored .env next to this script.

    Scheduled runs use a non-interactive shell that doesn't read ~/.zshrc, so the file fallback matters.
    """
    key = os.environ.get("COHERE_API_KEY")
    env_path = pathlib.Path(__file__).with_name(".env")
    if not key and env_path.exists():
        for line in env_path.read_text().splitlines():
            name, _, value = line.removeprefix("export ").partition("=")
            if name.strip() == "COHERE_API_KEY":
                key = value.strip().strip("\"'")
    return key


def load_jobs(folder):
    jobs = []
    for path in sorted(pathlib.Path(folder).glob("*.json")):
        if path.name.startswith("_"):  # scratch files, not job records
            continue
        data = json.loads(path.read_text())
        jobs.append((path.stem, data.get("data", data)))
    return jobs


def rerank(query, documents, api_key):
    body = json.dumps({"model": MODEL, "query": query, "documents": documents}).encode()
    req = urllib.request.Request(API_URL, data=body, headers={
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.load(resp)["results"]
    except urllib.error.HTTPError as e:
        sys.exit(f"Cohere API error {e.code}: {e.read().decode()[:500]}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("folder", help="folder of <id>.json job records")
    ap.add_argument("--out", default="scores.json", help="where to write {id: score}")
    ap.add_argument("--top", type=int, default=10, help="how many to print")
    args = ap.parse_args()

    api_key = load_api_key()
    if not api_key:
        sys.exit("Set COHERE_API_KEY, or put COHERE_API_KEY=... in .env next to this script.")

    jobs = load_jobs(args.folder)
    if not jobs:
        sys.exit(f"No .json files in {args.folder}")
    if len(jobs) > 1000:
        sys.exit("Rerank takes up to 1,000 documents per request; split the folder.")

    query = PROFILE_PATH.read_text().strip()
    results = rerank(query, [to_document(j) for _, j in jobs], api_key)

    scores = {jobs[r["index"]][0]: round(r["relevance_score"], 4) for r in results}
    pathlib.Path(args.out).write_text(json.dumps(scores, indent=2) + "\n")

    print(f"Reranked {len(jobs)} postings with {MODEL} -> {args.out}\n")
    for rank, r in enumerate(results[:args.top], 1):
        _, job = jobs[r["index"]]
        print(f"{rank:>3}. {r['relevance_score']:.3f}  {job.get('title')} · {job.get('company')}")


if __name__ == "__main__":
    main()
