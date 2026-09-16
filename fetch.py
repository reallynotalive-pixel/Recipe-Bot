"""
fetch.py — pulls caption/description text and source metadata from a
social media video/photo link, without downloading any media.
"""

import re
from dataclasses import dataclass
from urllib.parse import urlparse

import yt_dlp


class FetchError(Exception):
    """Raised for any failure while fetching caption/metadata for a URL."""


# Map recognizable domains to a clean display name for the PDF header.
_PLATFORM_DOMAINS = {
    "tiktok.com": "TikTok",
    "instagram.com": "Instagram",
    "youtube.com": "YouTube",
    "youtu.be": "YouTube",
    "pinterest.com": "Pinterest",
    "pin.it": "Pinterest",
}

# Pulls the @username segment out of a profile/post URL, e.g.
# "https://www.tiktok.com/@cookwithmira/video/123" -> "cookwithmira".
_HANDLE_FROM_URL_RE = re.compile(r"/@([\w.\-]+)")


@dataclass
class CaptionResult:
    platform: str       # e.g. "Instagram"
    handle: str         # e.g. "@cookwithmira"
    caption: str        # raw caption/description text
    source_url: str      # canonical URL, for reference


def _detect_platform(url: str) -> str:
    host = urlparse(url).netloc.lower().removeprefix("www.")
    for domain, name in _PLATFORM_DOMAINS.items():
        if host == domain or host.endswith("." + domain):
            return name
    raise FetchError(f"Unsupported or unrecognized platform for URL: {url}")


def _is_numeric_id(value: str) -> bool:
    """True for bare internal user IDs like '6755984001709147141', which
    some platforms return instead of the readable @username."""
    return bool(re.fullmatch(r"\d+", value.lstrip("@")))


def _extract_handle(info: dict) -> str:
    """
    Picks the best available creator handle, preferring a real @username
    over a platform's internal numeric user ID.

    Some extractors (notably TikTok) put a numeric ID in uploader_id and
    leave the actual @username only in the profile/webpage URL, so URLs
    are checked first.
    """
    for url_field in ("uploader_url", "channel_url", "webpage_url"):
        url = info.get(url_field)
        if url:
            match = _HANDLE_FROM_URL_RE.search(url)
            if match and not _is_numeric_id(match.group(1)):
                return f"@{match.group(1)}"

    for field in ("uploader_id", "uploader", "channel", "creator"):
        value = info.get(field)
        if value and not _is_numeric_id(str(value)):
            value = str(value).strip()
            if " " in value:
                return value  # display name, not a @handle — leave as-is
            return value if value.startswith("@") else f"@{value}"

    # Nothing non-numeric found anywhere — fall back to whatever exists.
    for field in ("uploader_id", "uploader", "channel"):
        value = info.get(field)
        if value:
            value = str(value)
            return value if value.startswith("@") else f"@{value}"

    return "@unknown"


def fetch_caption(url: str) -> CaptionResult:
    """
    Fetch the caption/description and creator handle for a social media
    post, without downloading the video or image itself.

    Raises FetchError with a clear message on any failure: unsupported
    platform, private/deleted post, network issue, or a post with no
    usable caption text.
    """
    platform = _detect_platform(url)

    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "extract_flat": False,
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except yt_dlp.utils.DownloadError as e:
        raise FetchError(f"Could not fetch this {platform} link: {e}") from e
    except Exception as e:  # noqa: BLE001 — surface anything unexpected via FetchError
        raise FetchError(f"Unexpected error fetching {platform} link: {e}") from e

    if info is None:
        raise FetchError(f"No data returned for this {platform} link.")

    caption = (info.get("description") or "").strip()
    if not caption:
        raise FetchError(
            f"This {platform} post has no caption/description text to extract from."
        )

    handle = _extract_handle(info)

    return CaptionResult(
        platform=platform,
        handle=handle,
        caption=caption,
        source_url=info.get("webpage_url", url),
    )


if __name__ == "__main__":
    import sys

    if len(sys.argv) != 2:
        print("Usage: python3 fetch.py <url>")
        sys.exit(1)

    try:
        result = fetch_caption(sys.argv[1])
        print(f"Platform: {result.platform}")
        print(f"Handle:   {result.handle}")
        print(f"Caption:\n{result.caption}")
    except FetchError as e:
        print(f"Error: {e}")
        sys.exit(1)
