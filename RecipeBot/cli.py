#!/usr/bin/env python3
"""
cli.py — recipe-pdf: pull a recipe from a social media link and save it
as a clean, formatted PDF.

Usage:
    python3 cli.py <url>
"""

import argparse
import re
import sys
from pathlib import Path

from fetch import fetch_caption, FetchError
from parser import parse_recipe, ParseError
from render import render_pdf, RenderError

_RECIPES_DIR = Path(__file__).parent / "recipes"


def _slugify(title: str) -> str:
    slug = re.sub(r"[^\w\s-]", "", title).strip().lower()
    slug = re.sub(r"[\s_-]+", "-", slug)
    return slug or "recipe"


def run(url: str) -> int:
    """
    Runs the full fetch -> parse -> render pipeline for a single URL.
    Returns a process exit code (0 on success, 1 on failure).
    """
    try:
        caption_result = fetch_caption(url)
    except FetchError as e:
        print(f"Error fetching link: {e}", file=sys.stderr)
        return 1

    try:
        recipe = parse_recipe(caption_result.caption)
    except ParseError as e:
        print(f"Error parsing recipe: {e}", file=sys.stderr)
        return 1

    if recipe.needs_review:
        print(f"Warning: {recipe.review_reason}", file=sys.stderr)

    output_path = _RECIPES_DIR / f"{_slugify(recipe.title)}.pdf"

    try:
        render_pdf(
            recipe=recipe,
            platform=caption_result.platform,
            handle=caption_result.handle,
            source_url=caption_result.source_url,
            output_path=output_path,
        )
    except RenderError as e:
        print(f"Error rendering PDF: {e}", file=sys.stderr)
        return 1

    print(f"Saved: {output_path}")
    return 0


def main() -> None:
    parser_args = argparse.ArgumentParser(
        description="Turn a social media recipe link into a formatted PDF."
    )
    parser_args.add_argument("url", help="Link to a recipe video/photo post")
    args = parser_args.parse_args()

    sys.exit(run(args.url))


if __name__ == "__main__":
    main()
