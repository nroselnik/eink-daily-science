import os
import json
import random
import xml.etree.ElementTree as ET
from datetime import datetime

import requests
import anthropic

ARXIV_FEED = "https://rss.arxiv.org/rss/cs.AI"
OUTPUT_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "summary.json")


def fetch_random_paper():
    response = requests.get(ARXIV_FEED, timeout=15)
    response.raise_for_status()

    root = ET.fromstring(response.content)
    ns = {"dc": "http://purl.org/dc/elements/1.1/"}
    items = root.findall(".//item")
    if not items:
        raise ValueError("No papers found in arXiv feed")

    item = random.choice(items)

    title = item.findtext("title", "").strip()
    link = item.findtext("link", "").strip()
    authors_el = item.findall("dc:creator", ns)
    authors = ", ".join(a.text.strip() for a in authors_el if a.text) or "Unknown"
    description = item.findtext("description", "").strip()

    return {"title": title, "url": link, "authors": authors, "abstract": description}


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
    paper = fetch_random_paper()
    result = summarize(paper)

    out = os.path.abspath(OUTPUT_PATH)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w") as f:
        json.dump(result, f, indent=2)

    print(f"Wrote summary for: {result['title']}")
