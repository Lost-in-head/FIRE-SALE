"""Mechanical website check used by the web-design campaign.

A higher score means a weaker site, which is a better fit for that offer.
The model still writes the qualification. This score is blended in only
when the campaign asks for missing or weak websites.
"""

import re
from dataclasses import dataclass
from datetime import date


_COPYRIGHT_YEAR = re.compile(
    r"(?:©|\(c\)|copyright)\s*(?:19|20)\d{2}|(?:19|20)\d{2}\s*(?:©|\(c\)|copyright)",
    re.IGNORECASE,
)
_YEAR = re.compile(r"(19|20)\d{2}")


@dataclass(frozen=True)
class WebsiteQuality:
    score: int
    flags: tuple[str, ...]

    def as_text(self) -> str:
        flags = ", ".join(self.flags) if self.flags else "none"
        return f"{self.score} ({flags})"


def copyright_year(html: str) -> int | None:
    match = _COPYRIGHT_YEAR.search(html or "")
    if not match:
        return None
    year = _YEAR.search(match.group(0))
    if not year:
        return None
    return int(year.group(0))


def assess_website(
    url: str | None,
    *,
    status: int | None = None,
    html: str | None = None,
    elapsed_seconds: float | None = None,
    today: date | None = None,
    slow_seconds: float = 4.0,
    copyright_age_years: int = 5,
) -> WebsiteQuality:
    """Score one site from 0 (fine) to 10 (missing or unusable)."""
    if url is None or not str(url).strip():
        return WebsiteQuality(10, ("no_site",))
    flags: list[str] = []
    score = 0
    broken = status is None or status >= 400 or status == 0
    slow = elapsed_seconds is not None and elapsed_seconds > slow_seconds
    if broken or slow:
        flags.append("slow_or_broken")
        score += 6
    if str(url).strip().lower().startswith("http://"):
        flags.append("no_https")
        score += 3
    page = html or ""
    if page and "viewport" not in page.casefold():
        flags.append("not_mobile")
        score += 3
    elif not page and not broken:
        flags.append("not_mobile")
        score += 3
    year = copyright_year(page)
    current = (today or date.today()).year
    if year is not None and year <= current - copyright_age_years:
        flags.append("old_copyright")
        score += 2
    return WebsiteQuality(min(10, score), tuple(flags))


def blend_score(model_score: int, quality: WebsiteQuality | None, *, use_quality: bool) -> int:
    """Fold the mechanical score into the model score for website-design leads."""
    model = max(1, min(10, int(model_score)))
    if not use_quality or quality is None:
        return model
    blended = round(0.6 * model + 0.4 * quality.score)
    return max(1, min(10, blended))
