import os
import json
import random
import xml.etree.ElementTree as ET
from datetime import datetime

import requests
import anthropic

PUBMED_ESEARCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
PUBMED_EFETCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
SEARCH_TERM = "turtle[Title/Abstract]"
MAX_AGE_DAYS = 365
POOL_SIZE = 20  # pick randomly among this many of the newest papers
OUTPUT_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "summary.json")


def fetch_latest_paper():
    search = requests.get(
        PUBMED_ESEARCH,
        params={
            "db": "pubmed",
            "term": SEARCH_TERM,
            "sort": "date",
            "retmax": POOL_SIZE,
            "datetype": "pdat",
            "reldate": MAX_AGE_DAYS,
            "retmode": "json",
        },
        timeout=15,
    )
    search.raise_for_status()
    id_list = search.json()["esearchresult"]["idlist"]
    if not id_list:
        raise ValueError(
            f"No papers found for '{SEARCH_TERM}' in the last {MAX_AGE_DAYS} days"
        )
    pmid = random.choice(id_list)

    fetch = requests.get(
        PUBMED_EFETCH,
        params={"db": "pubmed", "id": pmid, "retmode": "xml"},
        timeout=15,
    )
    fetch.raise_for_status()

    article = ET.fromstring(fetch.content).find(".//Article")
    if article is None:
        raise ValueError(f"Could not parse PubMed record for PMID {pmid}")

    title = " ".join("".join(article.find("ArticleTitle").itertext()).split())
    abstract = " ".join(
        " ".join(t.itertext()) for t in article.findall(".//AbstractText")
    ).strip()
    if not abstract:
        raise ValueError(f"PMID {pmid} has no abstract to summarize")
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


def summarize(paper: dict) -> dict:
    client = anthropic.Anthropic()

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
    # strip markdown code fences if present
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
        "date": datetime.utcnow().strftime("%Y-%m-%d"),
        "summary": parsed["summary"],
        "key_points": parsed["key_points"],
        "why_it_matters": parsed["why_it_matters"],
    }


if __name__ == "__main__":
    paper = fetch_latest_paper()
    result = summarize(paper)

    out = os.path.abspath(OUTPUT_PATH)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w") as f:
        json.dump(result, f, indent=2)

    print(f"Wrote summary for: {result['title']}")
