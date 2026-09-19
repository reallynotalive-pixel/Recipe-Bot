"""
fetch_web.py — fetches a recipe article page and extracts structured
recipe data. Tries schema.org/Recipe JSON-LD first (most recipe blogs
embed this for Google's rich results), then falls back to a heading/list
based HTML heuristic scan.
"""

import ipaddress
import json
import re
import socket
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from parser import RecipeData


class WebFetchError(Exception):
    """Raised for any failure while fetching or extracting a recipe from a web page."""


_REQUEST_TIMEOUT = 10
_MAX_REDIRECTS = 5
_USER_AGENT = "Mozilla/5.0 (compatible; recipe-pdf/1.0)"

_ISO8601_DURATION_RE = re.compile(
    r"P(?:\d+D)?T?(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?(?:\d+S)?"
)


# ---------------------------------------------------------------------------
# SSRF protection — the server fetches arbitrary user-submitted URLs, so
# every hostname (including each redirect hop) must resolve to a public IP.
# ---------------------------------------------------------------------------

def _is_public_ip(host: str) -> bool:
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return False
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        ):
            return False
    return True


def _validate_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise WebFetchError("Only http/https URLs are supported.")
    if not parsed.hostname:
        raise WebFetchError("URL has no hostname.")
    if not _is_public_ip(parsed.hostname):
        raise WebFetchError(
            "This URL resolves to a private or internal address and cannot be fetched."
        )


def _fetch_html(url: str) -> tuple[str, str]:
    """Fetches the page, following redirects manually so every hop is
    re-validated against SSRF rules. Returns (final_url, html)."""
    current_url = url
    for _ in range(_MAX_REDIRECTS):
        _validate_url(current_url)
        try:
            resp = requests.get(
                current_url,
                headers={"User-Agent": _USER_AGENT},
                timeout=_REQUEST_TIMEOUT,
                allow_redirects=False,
            )
        except requests.RequestException as e:
            raise WebFetchError(f"Could not fetch page: {e}") from e

        if resp.is_redirect or resp.is_permanent_redirect:
            location = resp.headers.get("Location")
            if not location:
                raise WebFetchError("Redirect response had no Location header.")
            current_url = urljoin(current_url, location)
            continue

        if resp.status_code != 200:
            raise WebFetchError(f"Page returned HTTP {resp.status_code}.")

        return current_url, resp.text

    raise WebFetchError("Too many redirects.")


# ---------------------------------------------------------------------------
# schema.org/Recipe JSON-LD extraction
# ---------------------------------------------------------------------------

def _search_jsonld_node(node) -> dict | None:
    if isinstance(node, dict):
        node_type = node.get("@type")
        types = node_type if isinstance(node_type, list) else [node_type]
        if "Recipe" in types:
            return node
        if "@graph" in node:
            for child in node["@graph"]:
                found = _search_jsonld_node(child)
                if found:
                    return found
    return None


def _find_jsonld_recipe(soup: BeautifulSoup) -> dict | None:
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string or "")
        except (json.JSONDecodeError, TypeError):
            continue
        candidates = data if isinstance(data, list) else [data]
        for item in candidates:
            found = _search_jsonld_node(item)
            if found:
                return found
    return None


def _parse_duration(value) -> str | None:
    if not value or not isinstance(value, str):
        return None
    match = _ISO8601_DURATION_RE.fullmatch(value.strip())
    if not match:
        return None
    parts = []
    if match.group("hours"):
        parts.append(f"{match.group('hours')} hr")
    if match.group("minutes"):
        parts.append(f"{match.group('minutes')} min")
    return " ".join(parts) if parts else None


def _normalize_author(author) -> str | None:
    if isinstance(author, list):
        author = author[0] if author else None
    if isinstance(author, dict):
        name = author.get("name")
        return name.strip() if name else None
    if isinstance(author, str):
        return author.strip()
    return None


def _normalize_instructions(instructions) -> list[str]:
    if not instructions:
        return []
    if isinstance(instructions, str):
        parts = [p.strip() for p in re.split(r"\n+", instructions) if p.strip()]
        return parts or [instructions.strip()]
    steps = []
    for item in instructions:
        if isinstance(item, str):
            if item.strip():
                steps.append(item.strip())
        elif isinstance(item, dict):
            if item.get("@type") == "HowToSection" and "itemListElement" in item:
                steps.extend(_normalize_instructions(item["itemListElement"]))
            else:
                text = item.get("text") or item.get("name")
                if text and text.strip():
                    steps.append(text.strip())
    return steps


def _normalize_yield(value) -> str | None:
    if isinstance(value, list):
        value = value[0] if value else None
    if not value:
        return None
    text = str(value)
    servings_match = re.search(r"(\d+)\s*servings?\b", text, re.IGNORECASE)
    if servings_match:
        return servings_match.group(1)
    match = re.search(r"\d+", text)
    return match.group(0) if match else text.strip()


def _recipe_from_jsonld(node: dict) -> RecipeData | None:
    title = (node.get("name") or "").strip() or "Untitled Recipe"
    ingredients = [i.strip() for i in node.get("recipeIngredient", []) if i and i.strip()]
    steps = _normalize_instructions(node.get("recipeInstructions"))
    if not ingredients or not steps:
        return None
    return RecipeData(
        title=title,
        servings=_normalize_yield(node.get("recipeYield")),
        prep_time=_parse_duration(node.get("prepTime")),
        cook_time=_parse_duration(node.get("cookTime")),
        ingredients=ingredients,
        steps=steps,
        needs_review=False,
        review_reason=None,
    )


# ---------------------------------------------------------------------------
# HTML heuristic fallback — for pages with no schema.org markup at all
# ---------------------------------------------------------------------------

_HEADING_TAGS = ["h1", "h2", "h3", "h4", "strong", "b"]
_INGREDIENT_HEADING_RE = re.compile(r"ingredients", re.IGNORECASE)
_STEP_HEADING_RE = re.compile(r"instructions|directions|method|steps", re.IGNORECASE)


def _find_heading_list(soup: BeautifulSoup, pattern: re.Pattern) -> list[str]:
    for tag in soup.find_all(_HEADING_TAGS):
        if pattern.search(tag.get_text(strip=True)):
            list_tag = tag.find_next(["ul", "ol"])
            if list_tag:
                items = [li.get_text(strip=True) for li in list_tag.find_all("li")]
                items = [i for i in items if i]
                if items:
                    return items
    return []


def _extract_title(soup: BeautifulSoup) -> str:
    og = soup.find("meta", property="og:title")
    if og and og.get("content"):
        return og["content"].strip()
    if soup.title and soup.title.string:
        return soup.title.string.strip()
    return "Untitled Recipe"


def _recipe_from_heuristic(soup: BeautifulSoup) -> RecipeData | None:
    ingredients = _find_heading_list(soup, _INGREDIENT_HEADING_RE)
    steps = _find_heading_list(soup, _STEP_HEADING_RE)
    if not ingredients or not steps:
        return None
    return RecipeData(
        title=_extract_title(soup),
        servings=None,
        prep_time=None,
        cook_time=None,
        ingredients=ingredients,
        steps=steps,
        needs_review=True,
        review_reason=(
            "No structured recipe data (schema.org) was found on this page — "
            "ingredients/steps were guessed from page headings. Double-check this one."
        ),
    )


def _extract_meta_author(soup: BeautifulSoup) -> str | None:
    meta = soup.find("meta", attrs={"name": "author"})
    if meta and meta.get("content"):
        return meta["content"].strip()
    return None


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def fetch_recipe_from_article(url: str) -> tuple[RecipeData, str | None, str]:
    """
    Fetches a recipe article page and returns (RecipeData, author, final_url).
    author is None if no author could be found anywhere on the page.
    Raises WebFetchError if the page can't be fetched or has no recipe on it.
    """
    final_url, html = _fetch_html(url)
    soup = BeautifulSoup(html, "lxml")

    jsonld_node = _find_jsonld_recipe(soup)
    if jsonld_node:
        recipe = _recipe_from_jsonld(jsonld_node)
        if recipe is not None:
            author = _normalize_author(jsonld_node.get("author")) or _extract_meta_author(soup)
            return recipe, author, final_url

    recipe = _recipe_from_heuristic(soup)
    if recipe is not None:
        author = _extract_meta_author(soup)
        return recipe, author, final_url

    raise WebFetchError(
        "Could not find a recipe on this page (no structured recipe data, "
        "and no clear ingredients/instructions sections)."
    )


if __name__ == "__main__":
    import sys

    if len(sys.argv) != 2:
        print("Usage: python3 fetch_web.py <url>")
        sys.exit(1)

    try:
        recipe, author, final_url = fetch_recipe_from_article(sys.argv[1])
        print(f"Title:    {recipe.title}")
        print(f"Author:   {author}")
        print(f"Servings: {recipe.servings} | Prep: {recipe.prep_time} | Cook: {recipe.cook_time}")
        print(f"Ingredients ({len(recipe.ingredients)}):")
        for i in recipe.ingredients:
            print(f"  - {i}")
        print(f"Steps ({len(recipe.steps)}):")
        for i, s in enumerate(recipe.steps, 1):
            print(f"  {i}. {s}")
    except WebFetchError as e:
        print(f"Error: {e}")
        sys.exit(1)
