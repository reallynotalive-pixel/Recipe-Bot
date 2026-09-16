"""
parser.py — heuristic, rule-based parser that turns a raw social media
caption into structured recipe data (title, servings, times, ingredients,
steps). No LLM involved — pure regex/structural heuristics.
"""

import re
from dataclasses import dataclass, field


class ParseError(Exception):
    """Raised when the caption has no usable recipe structure at all."""


@dataclass
class RecipeData:
    title: str
    servings: str | None
    prep_time: str | None
    cook_time: str | None
    ingredients: list[str]
    steps: list[str]
    needs_review: bool = False
    review_reason: str | None = None


_INGREDIENT_HEADER = re.compile(
    r"^(ingredients|you'?ll need|what you need|you will need|shopping list)\s*:?\s*$",
    re.IGNORECASE,
)
_STEP_HEADER = re.compile(
    r"^(instructions|directions|steps|method|preparation|how to make(?:\s*it)?)\s*:?\s*$",
    re.IGNORECASE,
)

_BULLET_PREFIX = re.compile(r"^[\s\-*•✔️➡️👉📍◦‣⁃▪️◾◽🔸🔹✅]+")
_STEP_NUMBER_PREFIX = re.compile(
    r"^\s*(?:step\s*\d+\s*[:\.\-]?|\d+\s*[\.\):])\s*", re.IGNORECASE
)
_HASHTAG_LINE = re.compile(r"^\s*(#\w+\s*)+$")
_TRAILING_HASHTAGS = re.compile(r"(?:\s*#\w+)+\s*$")

_UNITS = (
    r"cups?|tbsp|tablespoons?|tsp|teaspoons?|oz|ounces?|lbs?|pounds?|"
    r"g|kg|ml|l|liters?|cloves?|pinch(?:es)?|dash(?:es)?|"
    r"eggs?|yolks?|whites?|bananas?|apples?|onions?|potatoes?|tomatoes?|"
    r"avocados?|tortillas?|slices?|pieces?|cans?|packets?|sticks?"
)
_QTY_UNIT_RE = re.compile(
    rf"\d+(?:\s+\d+)?(?:/\d+)?\s*(?:{_UNITS})\b", re.IGNORECASE
)
_FLAT_SPLIT_MARKER = re.compile(r"\.\s*\*\s*")
_INSTRUCTION_STARTERS = re.compile(
    r"\b(preheat|in a (?:large|medium|small|microwave)|combine|whisk|mix|bake|"
    r"melt|microwave|heat|stir|fold|let (?:it|this)|set (?:this|it)|scoop|"
    r"place|chill|freeze|refrigerate)\b",
    re.IGNORECASE,
)

# Verbs that plausibly open a new instruction step. A sentence that doesn't
# start with one of these (after stripping a leading "in a ... bowl,"-style
# clause and any leading adverb) is treated as a continuation of the
# previous step rather than a new one.
_STEP_ACTION_VERBS = (
    r"preheat|mix|whisk|add|combine|stir|fold|pour|melt|heat|bake|boil|"
    r"simmer|drain|chop|dice|mince|slice|cut|place|set|put|transfer|remove|"
    r"let|chill|freeze|refrigerate|cover|uncover|season|sprinkle|garnish|"
    r"serve|scoop|spread|roll|knead|cream|beat|fry|sau[tc]é?|grill|roast|"
    r"steam|blend|process|crack|peel|grate|zest|squeeze|marinate|rest|cool|"
    r"warm|reheat|toss|coat|layer|assemble|top|finish|bring|reduce|line|"
    r"grease|flour|whip|drizzle|brush|dust|arrange|divide|repeat|check|"
    r"microwave"
)
_STEP_NEW_ACTION_RE = re.compile(
    rf"^(?:then\s+|next\s+|once\s+|after\s+(?:that\s+)?|and\s+)?"
    rf"(?:\w+ly\s+)?(?:{_STEP_ACTION_VERBS})\b",
    re.IGNORECASE,
)

_SERVINGS_RE = re.compile(r"\b(?:serves|servings?|makes)\b\s*[:\-]?\s*(\d+)", re.IGNORECASE)
_PREP_RE = re.compile(
    r"\bprep(?:aration)?\b\s*(?:time)?\s*[:\-]?\s*(\d+\s*(?:min(?:ute)?s?|hr?s?|hours?))",
    re.IGNORECASE,
)
_COOK_RE = re.compile(
    r"\bcook(?:ing)?\b\s*(?:time)?\s*[:\-]?\s*(\d+\s*(?:min(?:ute)?s?|hr?s?|hours?))",
    re.IGNORECASE,
)


def _is_meta_only_line(line: str) -> bool:
    stripped = _SERVINGS_RE.sub("", line)
    stripped = _PREP_RE.sub("", stripped)
    stripped = _COOK_RE.sub("", stripped)
    stripped = re.sub(r"[\s.,;:]+", "", stripped)
    return stripped == ""


def _clean_lines(caption: str) -> list[str]:
    lines = [ln.strip() for ln in caption.splitlines()]
    return [
        ln
        for ln in lines
        if ln and not _HASHTAG_LINE.match(ln) and not _is_meta_only_line(ln)
    ]


def _strip_ingredient_marker(line: str) -> str:
    return _BULLET_PREFIX.sub("", line).strip()


def _strip_step_marker(line: str) -> str:
    stripped = _STEP_NUMBER_PREFIX.sub("", line).strip()
    return _BULLET_PREFIX.sub("", stripped).strip()


def _find_header(lines: list[str], pattern: re.Pattern) -> int | None:
    for i, line in enumerate(lines):
        if pattern.match(line):
            return i
    return None


def _extract_meta(caption: str) -> tuple[str | None, str | None, str | None]:
    servings_match = _SERVINGS_RE.search(caption)
    prep_match = _PREP_RE.search(caption)
    cook_match = _COOK_RE.search(caption)
    servings = servings_match.group(1) if servings_match else None
    prep_time = prep_match.group(1).strip() if prep_match else None
    cook_time = cook_match.group(1).strip() if cook_match else None
    return servings, prep_time, cook_time


def _split_flat_ingredients(text: str) -> list[str]:
    """Split an ingredients-only blob into individual ingredient lines by
    finding each quantity+unit occurrence and taking the text up to the
    next one as that ingredient's full entry. Quantities inside parentheses
    (gram-weight annotations like "(340g)") are ignored so they don't get
    treated as the start of a new ingredient."""
    paren_spans = [(m.start(), m.end()) for m in re.finditer(r"\([^)]*\)", text)]

    def _in_parens(pos: int) -> bool:
        return any(start <= pos < end for start, end in paren_spans)

    matches = [m for m in _QTY_UNIT_RE.finditer(text) if not _in_parens(m.start())]
    if not matches:
        return []
    bounds = [m.start() for m in matches] + [len(text)]
    ingredients = []
    for i in range(len(matches)):
        chunk = text[bounds[i] : bounds[i + 1]].strip().rstrip(".").strip()
        if chunk:
            ingredients.append(chunk)
    return ingredients


def _looks_like_new_step(sentence: str) -> bool:
    """True if a sentence opens with a real instruction verb (allowing a
    leading transition word/adverb, or a leading 'in a ... bowl,' clause
    before the verb). False means it's likely a continuation/elaboration
    of the previous step rather than a new action."""
    # Drop a leading prepositional clause up to the first comma, if any,
    # e.g. "in a large bowl, whisk together..." -> "whisk together...".
    core = sentence.split(",", 1)[1].strip() if "," in sentence[:40] else sentence
    return bool(_STEP_NEW_ACTION_RE.match(core)) or bool(_STEP_NEW_ACTION_RE.match(sentence))


def _split_flat_steps(text: str) -> list[str]:
    """Split an instructions-only blob into steps by sentence boundaries,
    merging sentences that don't open with a new action verb into the
    previous step (they're elaborating on it, not introducing a new one)."""
    text = text.lstrip("*").strip()
    sentences = re.split(r"\.\s+", text)
    steps = []
    for s in sentences:
        s = s.strip()
        if not s:
            continue
        if s[-1] not in ".!?":
            s += "."
        if steps and not _looks_like_new_step(s):
            steps[-1] = steps[-1] + " " + s
        else:
            steps.append(s)
    return steps


def _parse_flat_caption(caption: str, fallback_title: str | None) -> RecipeData | None:
    """
    Fallback for captions with no line breaks at all — ingredients and
    steps run together as one paragraph. Splits on a '.  *' marker if
    present, otherwise on the first instruction-verb keyword. Returns
    None (rather than raising) if this heuristic can't find a confident
    split, so the caller can raise the standard ParseError.
    """
    text = _TRAILING_HASHTAGS.sub("", caption).strip()

    split_match = _FLAT_SPLIT_MARKER.search(text)
    if split_match:
        ing_text = text[: split_match.start() + 1]
        instr_text = text[split_match.end() :]
    else:
        starter_match = _INSTRUCTION_STARTERS.search(text)
        if not starter_match:
            return None
        ing_text = text[: starter_match.start()]
        instr_text = text[starter_match.start() :]

    ingredients = _split_flat_ingredients(ing_text)
    steps = _split_flat_steps(instr_text)
    if not ingredients or not steps:
        return None

    # Title is whatever text precedes the first ingredient quantity.
    first_qty = _QTY_UNIT_RE.search(ing_text)
    title_text = ing_text[: first_qty.start()].strip() if first_qty else ""
    title = title_text.title() if title_text.islower() else title_text
    title = title or (fallback_title or "Untitled Recipe")

    servings, prep_time, cook_time = _extract_meta(caption)

    return RecipeData(
        title=title,
        servings=servings,
        prep_time=prep_time,
        cook_time=cook_time,
        ingredients=ingredients,
        steps=steps,
        needs_review=True,
        review_reason=(
            "Caption had no line breaks — ingredients and steps were split "
            "from one continuous paragraph. Double-check this one closely."
        ),
    )


def parse_recipe(caption: str, fallback_title: str | None = None) -> RecipeData:
    """
    Parse a raw caption into structured recipe data using header/structural
    heuristics. Raises ParseError if no usable recipe structure is found.
    """
    lines = _clean_lines(caption)
    if not lines:
        raise ParseError("Caption has no usable text after removing hashtags/blank lines.")

    servings, prep_time, cook_time = _extract_meta(caption)

    ing_header_idx = _find_header(lines, _INGREDIENT_HEADER)
    step_header_idx = _find_header(lines, _STEP_HEADER)

    needs_review = False
    review_reason = None

    if ing_header_idx is not None and step_header_idx is not None and step_header_idx > ing_header_idx:
        # Clean case: explicit "Ingredients" / "Instructions" headers found.
        title_lines = lines[:ing_header_idx]
        raw_ingredients = lines[ing_header_idx + 1 : step_header_idx]
        raw_steps = lines[step_header_idx + 1 :]
        ingredients = [_strip_ingredient_marker(ln) for ln in raw_ingredients]
        steps = [_strip_step_marker(ln) for ln in raw_steps]
    else:
        # Fallback: no clear headers. Use structural cues instead —
        # bullet-prefixed lines are ingredients, numbered lines are steps.
        needs_review = True
        review_reason = "No explicit 'Ingredients'/'Instructions' headers found — used structural guesses instead."

        ingredients, steps, title_lines = [], [], []
        for line in lines:
            if _STEP_NUMBER_PREFIX.match(line):
                steps.append(_strip_step_marker(line))
            elif _BULLET_PREFIX.match(line):
                ingredients.append(_strip_ingredient_marker(line))
            elif not ingredients and not steps:
                # Lines before anything is classified are treated as the title.
                title_lines.append(line)

        if not ingredients or not steps:
            flat_result = _parse_flat_caption(caption, fallback_title)
            if flat_result is not None:
                return flat_result
            raise ParseError(
                "Could not confidently identify separate ingredients and steps in this caption."
            )

    title = title_lines[0] if title_lines else (fallback_title or "Untitled Recipe")

    ingredients = [i for i in ingredients if i]
    steps = [s for s in steps if s]

    if not ingredients or not steps:
        raise ParseError("Parsed recipe is missing ingredients or steps.")

    return RecipeData(
        title=title,
        servings=servings,
        prep_time=prep_time,
        cook_time=cook_time,
        ingredients=ingredients,
        steps=steps,
        needs_review=needs_review,
        review_reason=review_reason,
    )


if __name__ == "__main__":
    sample = """Brown Butter Herb Gnocchi 🧈🌿

Ingredients:
- 1 lb potato gnocchi
- 4 tbsp unsalted butter
- 2 cloves garlic, minced
- 2 tbsp fresh sage, chopped

Instructions:
1. Boil the gnocchi according to package directions.
2. Melt the butter in a skillet until golden brown.
3. Add garlic and sage, cook 30 seconds.
4. Toss gnocchi in the butter and serve.

Serves 4. Prep time: 10 min. Cook time: 15 min.

#recipe #pasta #dinner
"""
    result = parse_recipe(sample)
    print(result)
