from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
import hashlib
from pathlib import Path
import re


PROFILE_NAME = "antique-paper"
FONT_FAMILY = "IM FELL English"
PAGE_BACKGROUND = "#FFFFFF"
TEXT_PRIMARY = "#000000"
TEXT_SECONDARY = "#000000"
HEADING_COLOR = "#000000"
COVER_BACKGROUND = "#FFFFFF"
COVER_TEXT_PRIMARY = "#3B2A1F"
COVER_TEXT_SECONDARY = "#6B5140"
COVER_HEADING_COLOR = "#4A2F22"
COVER_BORDER_COLOR = "#B89B72"
COVER_ACCENT_COLOR = "#8C5A2B"

ASSETS_ROOT = Path(__file__).resolve().parent.parent / "assets"
FONT_ROOT = ASSETS_ROOT / "fonts" / "im-fell-english"
COVER_IMAGE_PATH = "images/editorial-cover.jpg"
COVER_DOCUMENT_PATH = "text/000-cover.xhtml"


@dataclass(frozen=True)
class PresentationResource:
    identifier: str
    source_path: Path
    epub_path: str
    media_type: str

    @property
    def sha256(self) -> str:
        return sha256_bytes(self.source_path.read_bytes())


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def normalized_text(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def default_visual_profile() -> dict:
    return {
        "name": PROFILE_NAME,
        "cover": {
            "mode": "editorial",
        },
    }


def normalize_visual_profile(value: object) -> dict | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise RuntimeError("epub manifest visual_profile must be an object")
    if value.get("name") != PROFILE_NAME:
        raise RuntimeError(f"Unsupported EPUB visual profile: {value.get('name')}")
    cover = value.get("cover")
    if not isinstance(cover, dict) or cover.get("mode") != "editorial":
        raise RuntimeError("antique-paper visual profile requires cover.mode to be editorial")
    return default_visual_profile()


def profile_resources(profile: dict | None) -> list[PresentationResource]:
    if profile is None:
        return []
    resources = [
        PresentationResource(
            "font-im-fell-english-regular",
            FONT_ROOT / "IMFeENrm28P.ttf",
            "fonts/IMFeENrm28P.ttf",
            "font/ttf",
        ),
        PresentationResource(
            "font-im-fell-english-italic",
            FONT_ROOT / "IMFeENit28P.ttf",
            "fonts/IMFeENit28P.ttf",
            "font/ttf",
        ),
        PresentationResource(
            "font-im-fell-english-license",
            FONT_ROOT / "OFL.txt",
            "fonts/OFL.txt",
            "text/plain",
        ),
    ]
    missing = [str(resource.source_path) for resource in resources if not resource.source_path.is_file()]
    if missing:
        raise RuntimeError(f"EPUB presentation assets are missing: {', '.join(missing)}")
    return resources


def profile_stylesheet(profile: dict | None) -> str:
    if profile is None:
        return "\n".join(
            [
                "html { color: #000000; background: #ffffff; }",
                "body { margin: 0; padding: 0 1.25rem 2rem; background: #ffffff; color: #000000; font-family: Georgia, 'Times New Roman', serif; line-height: 1.58; }",
                "section { max-width: 42rem; margin: 0 auto; }",
                "h1, h2, h3 { color: #000000; }",
                "h1 { margin: 2rem 0 1.5rem; font-size: 1.55rem; text-align: center; font-weight: 700; }",
                "p { margin: 0 0 1rem; text-align: justify; text-indent: 1.4rem; }",
                ".legacy-layout p:first-of-type { text-indent: 0; }",
                ".source-heading { margin: 2rem 0 1.35rem; text-align: center; font-weight: 700; }",
                ".heading-line, .verse-line { display: block; }",
                ".quotation { margin: 0 16% 1rem; text-align: justify; }",
                ".quotation p { margin: 0; text-indent: 0; }",
                ".dialogue { margin-left: 16%; text-align: right; text-indent: 0; font-style: italic; }",
                ".verse { margin: 1.35rem auto; max-width: 100%; text-align: center; }",
                "nav#toc ol { margin: 0; padding-left: 1.5rem; }",
                "nav#toc li { margin: 0.2rem 0; }",
                ".title-page { padding-top: 8vh; text-align: center; }",
                ".title-page .source-heading { margin: 1.4rem 0 1rem; text-align: center; }",
                ".title-page p { margin: 0.8rem 0; text-align: center; text-indent: 0; }",
                ".title-page .illustration { break-before: page; page-break-before: always; }",
                ".illustration { margin: 1.5rem auto; max-width: 32rem; text-align: center; }",
                ".illustration img { display: block; width: auto; max-width: 100%; height: auto; margin: 0 auto; }",
            ]
        )
    return "\n".join(
        [
            "@font-face {",
            f'  font-family: "{FONT_FAMILY}";',
            '  src: url("../fonts/IMFeENrm28P.ttf");',
            "  font-style: normal;",
            "  font-weight: 400;",
            "}",
            "@font-face {",
            f'  font-family: "{FONT_FAMILY}";',
            '  src: url("../fonts/IMFeENit28P.ttf");',
            "  font-style: italic;",
            "  font-weight: 400;",
            "}",
            ":root {",
            f"  --page-background: {PAGE_BACKGROUND};",
            f"  --text-primary: {TEXT_PRIMARY};",
            f"  --text-secondary: {TEXT_SECONDARY};",
            f"  --heading-color: {HEADING_COLOR};",
            "}",
            "html { background-color: var(--page-background); color: var(--text-primary); }",
            f'body {{ margin: 0; padding: 0 1.5rem 2.5rem; background-color: var(--page-background); color: var(--text-primary); font-family: "{FONT_FAMILY}", Georgia, "Times New Roman", serif; line-height: 1.64; }}',
            "section { max-width: 42rem; margin: 0 auto; }",
            "h1 { margin: 2.4rem 0 1.7rem; color: var(--heading-color); font-size: 1.7rem; font-weight: 400; text-align: center; }",
            "h2, h3 { color: var(--heading-color); font-weight: 400; }",
            "p { margin: 0 0 1.05rem; text-align: justify; text-indent: 1.5rem; }",
            ".legacy-layout p:first-of-type { text-indent: 0; }",
            "em, i { color: inherit; }",
            ".source-heading { margin: 2.4rem 0 1.55rem; color: var(--heading-color); text-align: center; font-weight: 400; }",
            ".source-heading .heading-line, .verse-line { display: block; }",
            ".semantic-layout .quotation { margin: 0 16% 1.05rem; text-align: justify; }",
            ".semantic-layout .quotation p { margin: 0; text-indent: 0; }",
                ".semantic-layout .dialogue { margin-left: 16%; text-align: right; text-indent: 0; font-style: italic; }",
                ".verse { margin: 1.55rem auto; max-width: 100%; text-align: center; }",
                "nav#toc ol { margin: 0; padding-left: 1.5rem; }",
                "nav#toc li { margin: 0.2rem 0; }",
                ".title-page { padding-top: 8vh; text-align: center; }",
                ".title-page .source-heading { margin: 1.5rem 0 1rem; text-align: center; }",
                ".title-page p { margin: 0.85rem 0; text-align: center; text-indent: 0; }",
                ".title-page .illustration { break-before: page; page-break-before: always; }",
                ".footnote { margin: 1.35rem 0; padding-top: 0.75rem; border-top: 1px solid #000000; }",
                ".footnote p { font-size: 0.9em; text-align: left; text-indent: 0; }",
                ".illustration { margin: 1.75rem auto; max-width: 32rem; text-align: center; }",
            ".illustration img { display: block; width: auto; max-width: 100%; height: auto; margin: 0 auto; }",
            "body.cover-page { margin: 0; padding: 0; }",
            ".editorial-cover { margin: 0; max-width: none; padding: 0; text-align: center; }",
            ".editorial-cover img { display: block; height: auto; margin: 0 auto; max-width: 100%; width: 100%; }",
        ]
    )


def _require_pillow() -> tuple[object, object, object]:
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError as error:
        raise RuntimeError(
            "Pillow is required to generate the antique-paper EPUB cover. "
            "Run this script with the Codex bundled Python."
        ) from error
    return Image, ImageDraw, ImageFont


def _wrapped_lines(draw: object, text: str, font: object, max_width: int) -> list[str]:
    words = normalized_text(text).split()
    if not words:
        return []
    lines: list[str] = []
    current = words[0]
    for word in words[1:]:
        candidate = f"{current} {word}"
        bounds = draw.textbbox((0, 0), candidate, font=font)
        if bounds[2] - bounds[0] <= max_width:
            current = candidate
        else:
            lines.append(current)
            current = word
    lines.append(current)
    return lines


def _fitted_lines(
    draw: object,
    text: str,
    font_path: Path,
    max_width: int,
    max_lines: int,
    max_height: int,
    maximum_size: int,
    minimum_size: int,
    spacing: int,
    field_name: str,
) -> tuple[object, list[str]]:
    _, _, image_font = _require_pillow()
    for size in range(maximum_size, minimum_size - 1, -2):
        font = image_font.truetype(str(font_path), size=size)
        lines = _wrapped_lines(draw, text, font, max_width)
        rendered = "\n".join(lines)
        bounds = draw.multiline_textbbox((0, 0), rendered, font=font, spacing=spacing, align="center")
        widest_line = max(
            (draw.textbbox((0, 0), line, font=font)[2] for line in lines),
            default=0,
        )
        if len(lines) <= max_lines and bounds[3] - bounds[1] <= max_height and widest_line <= max_width:
            return font, lines
    raise RuntimeError(
        f"antique-paper editorial cover {field_name} cannot fit within the fixed cover layout"
    )


def _draw_centered(
    draw: object,
    lines: list[str],
    font: object,
    color: str,
    canvas_width: int,
    top: int,
    spacing: int,
) -> int:
    if not lines:
        return top
    text = "\n".join(lines)
    bounds = draw.multiline_textbbox((0, 0), text, font=font, spacing=spacing, align="center")
    text_width = bounds[2] - bounds[0]
    text_height = bounds[3] - bounds[1]
    draw.multiline_text(
        ((canvas_width - text_width) / 2 - bounds[0], top - bounds[1]),
        text,
        fill=color,
        font=font,
        spacing=spacing,
        align="center",
    )
    return top + text_height


def cover_alt_text(book: dict) -> str:
    title = normalized_text(book.get("title"))
    author = normalized_text(book.get("author"))
    return f"Capa editorial: {title}" + (f", por {author}." if author else ".")


def cover_image(book: dict, format_label: str = "EPUB") -> bytes:
    title = normalized_text(book.get("title"))
    if not title:
        raise RuntimeError("antique-paper editorial cover requires a non-empty book title")

    image_module, image_draw, image_font = _require_pillow()
    width, height = 1200, 1800
    image = image_module.new("RGB", (width, height), COVER_BACKGROUND)
    draw = image_draw.Draw(image)
    draw.rectangle((54, 54, width - 54, height - 54), outline=COVER_BORDER_COLOR, width=5)
    draw.rectangle((78, 78, width - 78, height - 78), outline=COVER_ACCENT_COLOR, width=2)

    regular_path = FONT_ROOT / "IMFeENrm28P.ttf"
    italic_path = FONT_ROOT / "IMFeENit28P.ttf"
    label_font = image_font.truetype(str(regular_path), size=38)

    top = 170
    normalized_label = normalized_text(format_label).upper()
    if not normalized_label or len(normalized_label) > 24:
        raise RuntimeError("editorial cover format label must contain 1 to 24 characters")
    label = f"EDICAO {normalized_label}"
    label_bounds = draw.textbbox((0, 0), label, font=label_font)
    draw.text(
        ((width - (label_bounds[2] - label_bounds[0])) / 2, top),
        label,
        fill=COVER_ACCENT_COLOR,
        font=label_font,
    )
    draw.line((width * 0.28, top + 70, width * 0.72, top + 70), fill=COVER_BORDER_COLOR, width=3)

    title_font, title_lines = _fitted_lines(
        draw,
        title,
        regular_path,
        int(width * 0.76),
        max_lines=5,
        max_height=500,
        maximum_size=112,
        minimum_size=42,
        spacing=20,
        field_name="title",
    )
    _draw_centered(
        draw,
        title_lines,
        title_font,
        COVER_HEADING_COLOR,
        width,
        430,
        spacing=20,
    )

    subtitle = normalized_text(book.get("subtitle"))
    if subtitle:
        subtitle_font, subtitle_lines = _fitted_lines(
            draw,
            subtitle,
            italic_path,
            int(width * 0.68),
            max_lines=3,
            max_height=170,
            maximum_size=58,
            minimum_size=28,
            spacing=14,
            field_name="subtitle",
        )
        _draw_centered(
            draw,
            subtitle_lines,
            subtitle_font,
            COVER_TEXT_SECONDARY,
            width,
            1000,
            spacing=14,
        )

    author = normalized_text(book.get("author"))
    if author:
        fitted_author_font, author_lines = _fitted_lines(
            draw,
            author,
            regular_path,
            int(width * 0.76),
            max_lines=2,
            max_height=150,
            maximum_size=62,
            minimum_size=28,
            spacing=12,
            field_name="author",
        )
        _draw_centered(
            draw,
            author_lines,
            fitted_author_font,
            COVER_TEXT_PRIMARY,
            canvas_width=width,
            top=1240,
            spacing=12,
        )

    publication_values = [
        normalized_text(book.get("publication_place")),
        str(book.get("publication_year") or "").strip(),
    ]
    publication = " / ".join(value for value in publication_values if value)
    if publication:
        fitted_publication_font, publication_lines = _fitted_lines(
            draw,
            publication,
            italic_path,
            int(width * 0.72),
            max_lines=2,
            max_height=110,
            maximum_size=42,
            minimum_size=24,
            spacing=10,
            field_name="publication line",
        )
        _draw_centered(
            draw,
            publication_lines,
            fitted_publication_font,
            COVER_TEXT_SECONDARY,
            canvas_width=width,
            top=1530,
            spacing=10,
        )

    draw.line((width * 0.28, height - 170, width * 0.72, height - 170), fill=COVER_BORDER_COLOR, width=3)
    output = BytesIO()
    image.save(output, format="JPEG", quality=92, optimize=True)
    return output.getvalue()
