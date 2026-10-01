import os
import json
import random
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

import requests
import anthropic

PUBMED_ESEARCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
PUBMED_EFETCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
SEARCH_TERM = "sea turtle[Title/Abstract]"
MAX_AGE_DAYS = 365
POOL_SIZE = 120         # pick randomly among this many of the newest papers
PAPERS_PER_RUN = 10     # how many new papers to add per run
MAX_MISSES = 15         # give up after this many picks with no usable abstract
REQUEST_DELAY = 0.4     # PubMed allows ~3 requests/sec without an API key
DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
ARCHIVE_PATH = os.path.join(DATA_DIR, "archive.json")
SUMMARY_PATH = os.path.join(DATA_DIR, "summary.json")   # latest, for compatibility


def _pool():
    search = requests.get(
        PUBMED_ESEARCH,
        params={
            "db": "pubmed", "term": SEARCH_TERM, "sort": "date",
            "retmax": POOL_SIZE, "datetype": "pdat", "reldate": MAX_AGE_DAYS,
            "retmode": "json",
        },
        timeout=15,
    )
    search.raise_for_status()
    ids = search.json()["esearchresult"]["idlist"]
    if not ids:
        raise ValueError(f"No papers found for '{SEARCH_TERM}' in the last {MAX_AGE_DAYS} days")
    return ids


def _fetch(pmid):
    fetch = requests.get(
        PUBMED_EFETCH,
        params={"db": "pubmed", "id": pmid, "retmode": "xml"},
        timeout=15,
    )
    fetch.raise_for_status()
    article = ET.fromstring(fetch.content).find(".//Article")
    if article is None:
        return None
    title = " ".join("".join(article.find("ArticleTitle").itertext()).split())
    abstract = " ".join(
        " ".join(t.itertext()) for t in article.findall(".//AbstractText")
    ).strip()
    if not abstract:
        return None
    authors = ", ".join(
        f"{a.findtext('ForeName', '')} {a.findtext('LastName', '')}".strip()
        for a in article.findall(".//Author")
        if a.find("LastName") is not None
    ) or "Unknown"
    return {
        "title": title,
        "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
        "authors": authors,
        "abstract": abstract,
    }


def fetch_new_papers(known_urls, count):
    """Pick up to `count` recent papers (each with an abstract) not already in the archive.

    The pool is fetched once and walked, so a batch costs one search rather than
    one per paper. Returns fewer than `count` if the pool runs dry.
    """
    ids = _pool()
    random.shuffle(ids)
    papers = []
    misses = 0
    for pmid in ids:
        if len(papers) >= count or misses >= MAX_MISSES:
            break
        url = f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"
        if url in known_urls:
            continue
        time.sleep(REQUEST_DELAY)
        paper = _fetch(pmid)
        if paper:
            papers.append(paper)
        else:
            misses += 1
    if not papers:
        raise ValueError("Could not find any new papers with abstracts in the pool")
    return papers


def summarize(paper: dict, client) -> dict:
    prompt = f"""You are explaining science to a curious non-scientist. Given this paper:

Title: {paper['title']}
Authors: {paper['authors']}
Abstract: {paper['abstract']}

Return ONLY valid JSON with this exact structure (no markdown, no extra text):
{{
  "summary": "<2-3 sentence plain-English explanation>",
  "key_points": ["<point 1>", "<point 2>", "<point 3>"],
  "why_it_matters": "<one sentence on real-world relevance>"
}}"""
    message = client.messages.create(
        model="claude-haiku-4-5",
        max_tokens=512,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = message.content[0].text.strip()
    if raw.startswith("```"):
        raw = raw.split("```", 2)[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()
    parsed = json.loads(raw)
    return {
        "title": paper["title"],
        "authors": paper["authors"],
        "url": paper["url"],
        "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "summary": parsed["summary"],
        "key_points": parsed["key_points"],
        "why_it_matters": parsed["why_it_matters"],
    }


def load_archive():
    try:
        with open(ARCHIVE_PATH) as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except FileNotFoundError:
        return []


if __name__ == "__main__":
    os.makedirs(DATA_DIR, exist_ok=True)
    archive = load_archive()
    known = {p.get("url") for p in archive}

    papers = fetch_new_papers(known, PAPERS_PER_RUN)
    client = anthropic.Anthropic()

    added = []
    for paper in papers:
        try:
            added.append(summarize(paper, client))
        except Exception as exc:                 # one bad summary shouldn't void the run
            print(f"skipped {paper['title'][:60]!r}: {exc}")

    if not added:
        raise SystemExit("No papers summarized; leaving the archive untouched.")

    archive.extend(added)                        # grow the list
    with open(ARCHIVE_PATH, "w") as f:
        json.dump(archive, f, indent=2)
    with open(SUMMARY_PATH, "w") as f:           # keep latest for compatibility
        json.dump(added[-1], f, indent=2)

    print(f"Archive now has {len(archive)} papers. Added {len(added)}:")
    for p in added:
        print(f"  - {p['title']}")
