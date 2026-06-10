import os
import json
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta

import requests
import anthropic

ARXIV_API = "http://export.arxiv.org/api/query"
SEARCH_QUERY = 'ti:"sea turtle" OR abs:"sea turtle"'
MAX_AGE_DAYS = 365
OUTPUT_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "summary.json")


def fetch_latest_paper():
    response = requests.get(
        ARXIV_API,
        params={
            "search_query": SEARCH_QUERY,
            "sortBy": "submittedDate",
            "sortOrder": "descending",
            "max_results": 1,
        },
        timeout=15,
    )
    response.raise_for_status()

    root = ET.fromstring(response.content)
    ns = {"atom": "http://www.w3.org/2005/Atom"}
    entry = root.find("atom:entry", ns)
    if entry is None:
        raise ValueError(f"No papers found for query: {SEARCH_QUERY}")

    published = entry.findtext("atom:published", "", ns).strip()
    published_date = datetime.strptime(published, "%Y-%m-%dT%H:%M:%SZ")
    if datetime.utcnow() - published_date > timedelta(days=MAX_AGE_DAYS):
        raise ValueError(
            f"Latest matching paper is older than {MAX_AGE_DAYS} days "
            f"(published {published_date:%Y-%m-%d})"
        )

    title = " ".join(entry.findtext("atom:title", "", ns).split())
    link = entry.findtext("atom:id", "", ns).strip()
    authors = ", ".join(
        name.text.strip()
        for name in entry.findall("atom:author/atom:name", ns)
        if name.text
    ) or "Unknown"
    abstract = " ".join(entry.findtext("atom:summary", "", ns).split())

    return {"title": title, "url": link, "authors": authors, "abstract": abstract}


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
