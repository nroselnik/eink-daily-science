"""Add a Bahasa Melayu translation ("ms") to every archive paper that lacks one.

Each translated paper gains:
    "ms": {"title": ..., "summary": ..., "key_points": [...], "why_it_matters": ...}
The English fields are untouched, so readers that don't know "ms" (the Pi's e-ink
display) are unaffected. A paper whose translation fails simply stays English-only
and is retried on the next run.

    python scripts/translate.py            # translate everything missing
    python scripts/translate.py --limit 3  # just a few (for checking quality)
"""
import argparse
import json
import os

import anthropic
from pydantic import BaseModel

MODEL = "claude-sonnet-5-5"
DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
ARCHIVE_PATH = os.path.join(DATA_DIR, "archive.json")
SUMMARY_PATH = os.path.join(DATA_DIR, "summary.json")
SAVE_EVERY = 10

INSTRUCTIONS = """Translate this sea-turtle research summary from English into Malaysian Malay \
(Bahasa Melayu as used in Malaysia, standard Dewan Bahasa dan Pustaka spelling - not Indonesian).

It is read by curious non-scientists on a small display, so keep the same plain, friendly tone \
and roughly the same length; do not add or drop information.

Terms:
- sea turtle = penyu; green turtle = penyu agar; hawksbill = penyu karah;
  leatherback = penyu belimbing; olive ridley = penyu lipas; loggerhead = penyu tempayan;
  Kemp's ridley = penyu lipas Kemp; hatchling = anak penyu; nesting beach = pantai pendaratan penyu.
- Keep Latin species names (e.g. Chelonia mydas), gene/protein names, acronyms, numbers and units \
exactly as they are.
- Translate the title too.
- Return exactly as many key points as the English has, in the same order."""


class MalayVersion(BaseModel):
    title: str
    summary: str
    key_points: list[str]
    why_it_matters: str


def translate_paper(paper: dict, client) -> dict | None:
    source = {
        "title": paper["title"],
        "summary": paper.get("summary", ""),
        "key_points": paper.get("key_points", []),
        "why_it_matters": paper.get("why_it_matters", ""),
    }
    response = client.messages.parse(
        model=MODEL,
        max_tokens=16000,
        messages=[{
            "role": "user",
            "content": INSTRUCTIONS + "\n\nEnglish (JSON):\n" + json.dumps(source, ensure_ascii=False, indent=2),
        }],
        output_format=MalayVersion,
    )
    if response.stop_reason != "end_turn" or response.parsed_output is None:
        print(f"  not translated (stop_reason={response.stop_reason})")
        return None
    ms = response.parsed_output.model_dump()
    if len(ms["key_points"]) != len(source["key_points"]):
        print(f"  not translated (got {len(ms['key_points'])} key points, expected {len(source['key_points'])})")
        return None
    return ms


def save(archive):
    with open(ARCHIVE_PATH, "w") as f:
        json.dump(archive, f, indent=2)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="translate at most this many papers (0 = all)")
    args = ap.parse_args()

    with open(ARCHIVE_PATH) as f:
        archive = json.load(f)
    todo = [p for p in archive if isinstance(p, dict) and p.get("title") and "ms" not in p]
    if args.limit:
        todo = todo[: args.limit]
    print(f"{len(todo)} papers to translate ({sum('ms' in p for p in archive)} already done)")
    if not todo:
        return

    client = anthropic.Anthropic()
    done = 0
    for n, paper in enumerate(todo, 1):
        print(f"[{n}/{len(todo)}] {paper['title'][:70]}")
        try:
            ms = translate_paper(paper, client)
        except anthropic.APIError as exc:          # one failure shouldn't stop the batch
            print(f"  failed: {exc}")
            continue
        if ms:
            paper["ms"] = ms
            done += 1
            print(f"  -> {ms['title'][:70]}")
            if done % SAVE_EVERY == 0:
                save(archive)
    save(archive)

    # keep summary.json (latest paper) in step if it was just translated
    if archive and "ms" in archive[-1]:
        with open(SUMMARY_PATH, "w") as f:
            json.dump(archive[-1], f, indent=2)
    print(f"Translated {done} of {len(todo)}. Archive: {sum('ms' in p for p in archive)}/{len(archive)} in Malay.")


if __name__ == "__main__":
    main()
