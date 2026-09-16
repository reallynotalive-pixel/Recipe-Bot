"""
render.py — renders structured recipe data into a formatted PDF using
the Jinja2 HTML template and WeasyPrint.
"""

from pathlib import Path

from jinja2 import Environment, FileSystemLoader
from weasyprint import HTML

from parser import RecipeData

_TEMPLATE_DIR = Path(__file__).parent / "templates"
_TEMPLATE_NAME = "recipe.html.j2"


class RenderError(Exception):
    """Raised when rendering the recipe to PDF fails."""


def render_pdf(
    recipe: RecipeData,
    platform: str,
    handle: str,
    source_url: str,
    output_path: Path,
) -> Path:
    """
    Render a RecipeData object to a PDF at output_path. Returns the
    resolved output path on success. Raises RenderError on failure.
    """
    try:
        env = Environment(loader=FileSystemLoader(str(_TEMPLATE_DIR)))
        template = env.get_template(_TEMPLATE_NAME)

        html_string = template.render(
            title=recipe.title,
            platform=platform,
            handle=handle,
            source_url=source_url,
            servings=recipe.servings,
            prep_time=recipe.prep_time,
            cook_time=recipe.cook_time,
            ingredients=recipe.ingredients,
            steps=recipe.steps,
            needs_review=recipe.needs_review,
            review_reason=recipe.review_reason,
        )

        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        # base_url anchors relative font paths in the template's @font-face rules.
        HTML(string=html_string, base_url=str(_TEMPLATE_DIR)).write_pdf(str(output_path))
    except Exception as e:  # noqa: BLE001 — surface anything unexpected via RenderError
        raise RenderError(f"Failed to render PDF: {e}") from e

    return output_path
