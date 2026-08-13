from __future__ import annotations

import argparse
from io import BytesIO
import html
import os
from pathlib import Path
import re
import sys

from book_layout import resolve_book_paths
from publication_selection import uses_unsuffixed_fluid_export_name
from epub_presentation import (
    FONT_ROOT,
    PROFILE_NAME,
    cover_image,
    normalize_visual_profile,
    profile_resources,
)
from export_epub import (
    IMAGE_EDITIONS,
    TEXT_EDITIONS,
    _attached_note_matches,
    _layout_text_values,
    cached_export_is_current,
    export_fingerprint_payload,
    export_input_fingerprint,
    heading_markup,
    is_fluid_supplementary_document,
    is_fluid_supplementary_title,
    join_semantic_values,
    load_export_context,
    normalize_space,
    paragraphs_from_text,
    relative_to_book,
    require_text,
    safe_segment,
    selected_asset,
    semantic_block_groups,
    sha256_file,
    temporary_output_path,
    validate_documents,
    write_json,
)


def _require_reportlab() -> dict[str, object]:
    try:
        import reportlab
        from reportlab.lib import colors
        from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT, TA_RIGHT
        from reportlab.lib.pagesizes import A5
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
        from reportlab.lib.units import mm
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
        from reportlab.platypus import (
            BaseDocTemplate,
            CondPageBreak,
            Frame,
            FrameActionFlowable,
            Image,
            PageBreak,
            PageTemplate,
            Paragraph,
            Spacer,
        )
        from reportlab.platypus.tableofcontents import TableOfContents
    except ImportError as error:
        raise RuntimeError(
            "ReportLab is required for PDF export. Run this script with the Codex bundled Python."
        ) from error
    return locals()


def export_directory(book_root: Path) -> Path:
    return (book_root / "exports" / "pdf").resolve()


def resolve_export_output(
    book_root: Path,
    raw_output: Path | None,
    default_name: str,
) -> Path:
    output = (
        raw_output.expanduser().resolve()
        if raw_output
        else export_directory(book_root) / default_name
    )
    try:
        output.relative_to(export_directory(book_root))
    except ValueError as error:
        raise RuntimeError(
            f"PDF output must remain under {export_directory(book_root)}: {output}"
        ) from error
    if output.suffix.lower() != ".pdf":
        raise RuntimeError("PDF output must use the .pdf extension")
    return output


def current_renderer() -> dict:
    return {
        "name": "reportlab",
        "version": str(_require_reportlab()["reportlab"].Version),
    }


def edition_label(text_edition: str, image_edition: str, classic: bool) -> str:
    if text_edition == "original":
        value = "fiel" if image_edition == "original" else "restaurada"
    elif text_edition == "revised-pt-br":
        value = "revisada" if image_edition == "original" else "revisada-restaurada"
    elif text_edition == "fluid-pt-br":
        value = "fluida" if image_edition == "original" else "fluida-restaurada"
    else:
        value = "pt-br" if image_edition == "original" else "pt-br-restaurada"
    return f"{value}-classico" if classic else value


def book_metadata(book_map: dict, manifest: dict) -> dict:
    book = manifest.get("book") if isinstance(manifest.get("book"), dict) else {}
    if not require_text(book.get("title")):
        source_book = (
            book_map.get("book") if isinstance(book_map.get("book"), dict) else {}
        )
        book = {**source_book, **book}
    return {
        "title": str(book.get("title") or "Untitled"),
        "subtitle": str(book.get("subtitle") or ""),
        "author": str(book.get("author") or ""),
        "publication_year": book.get("publication_year"),
        "publication_place": str(book.get("publication_place") or ""),
    }


def _rich_text(value: str, note_ids: dict[str, str]) -> str:
    text = normalize_space(value)
    if not note_ids:
        return html.escape(text)
    parts: list[str] = []
    cursor = 0
    for start, end, marker, note_id in _attached_note_matches(text, note_ids):
        parts.append(html.escape(text[cursor:start]))
        parts.append(
            f'<super><link href="#{html.escape(note_id)}" color="#000000">'
            f"{html.escape(marker)}</link></super>"
        )
        cursor = end
    parts.append(html.escape(text[cursor:]))
    return "".join(parts)


def _fit_image(path: Path, maximum_width: float, maximum_height: float) -> tuple[float, float]:
    try:
        from PIL import Image as PillowImage
    except ImportError as error:
        raise RuntimeError(
            "Pillow is required for PDF image layout. Run this script with the Codex bundled Python."
        ) from error
    with PillowImage.open(path) as image:
        width, height = image.size
    if width <= 0 or height <= 0:
        raise RuntimeError(f"PDF image has invalid dimensions: {path}")
    scale = min(maximum_width / width, maximum_height / height, 1.0)
    return width * scale, height * scale


DIALOGUE_LEFT_INDENT_MM = 18
DIALOGUE_FIRST_LINE_INDENT_MM = 0
QUOTATION_INDENT_MM = 18
FOOTNOTE_FONT_SIZE = 8.5
FOOTNOTE_LEADING = 10.2
FOOTNOTE_SEPARATOR_WIDTH_MM = 50
FOOTNOTE_SEPARATOR_GAP_MM = 2
FOOTNOTE_SPACE_MM = 1
_URL_PARAGRAPH = re.compile(r"^(?:https?://|www\.)\S+$", re.IGNORECASE)


def _dialogue_paragraph_style(
    paragraph_style: type,
    parent: object,
    italic_font: str,
    mm: float,
    ta_right: int,
) -> object:
    return paragraph_style(
        "Dialogue",
        parent=parent,
        fontName=italic_font,
        alignment=ta_right,
        leftIndent=DIALOGUE_LEFT_INDENT_MM * mm,
        rightIndent=0,
        firstLineIndent=DIALOGUE_FIRST_LINE_INDENT_MM * mm,
    )


def _quotation_paragraph_style(
    paragraph_style: type,
    parent: object,
    mm: float,
    ta_justify: int,
) -> object:
    return paragraph_style(
        "Quotation",
        parent=parent,
        alignment=ta_justify,
        leftIndent=QUOTATION_INDENT_MM * mm,
        rightIndent=QUOTATION_INDENT_MM * mm,
        firstLineIndent=0,
    )


def _verse_paragraph_style(
    paragraph_style: type,
    parent: object,
    ta_center: int,
) -> object:
    return paragraph_style(
        "Verse",
        parent=parent,
        alignment=ta_center,
        leftIndent=0,
        rightIndent=0,
        firstLineIndent=0,
        spaceAfter=0,
    )


def _footnote_paragraph_style(
    paragraph_style: type,
    parent: object,
    font_name: str,
    ta_left: int,
) -> object:
    return paragraph_style(
        "Footnote",
        parent=parent,
        fontName=font_name,
        fontSize=FOOTNOTE_FONT_SIZE,
        leading=FOOTNOTE_LEADING,
        alignment=ta_left,
        leftIndent=0,
        rightIndent=0,
        firstLineIndent=0,
        borderWidth=0,
        borderPadding=0,
        spaceBefore=0,
        spaceAfter=0,
    )


def _url_paragraph_style(
    paragraph_style: type,
    parent: object,
    ta_left: int,
) -> object:
    return paragraph_style(
        "URL",
        parent=parent,
        fontSize=8.7,
        leading=10.8,
        alignment=ta_left,
        firstLineIndent=0,
        spaceBefore=0,
        spaceAfter=3,
        splitLongWords=True,
        wordWrap="CJK",
    )


def _is_url_paragraph(value: str) -> bool:
    return _URL_PARAGRAPH.fullmatch(normalize_space(value)) is not None


def _referenced_note_ids(value: str, note_ids: dict[str, str]) -> tuple[str, ...]:
    references: list[str] = []
    for _start, _end, _marker, note_id in _attached_note_matches(
        normalize_space(value),
        note_ids,
    ):
        if note_id not in references:
            references.append(note_id)
    return tuple(references)


def write_pdf(
    output: Path,
    book_root: Path,
    book: dict,
    language: str,
    text_edition: str,
    documents: list[dict],
    selected_assets_by_document: dict[str, list[dict]],
    visual_profile: dict | None,
) -> tuple[list[dict], dict | None, int, str]:
    rl = _require_reportlab()
    reportlab = rl["reportlab"]
    colors = rl["colors"]
    A5 = rl["A5"]
    mm = rl["mm"]
    TA_CENTER = rl["TA_CENTER"]
    TA_JUSTIFY = rl["TA_JUSTIFY"]
    TA_LEFT = rl["TA_LEFT"]
    TA_RIGHT = rl["TA_RIGHT"]
    ParagraphStyle = rl["ParagraphStyle"]
    getSampleStyleSheet = rl["getSampleStyleSheet"]
    pdfmetrics = rl["pdfmetrics"]
    TTFont = rl["TTFont"]
    BaseDocTemplate = rl["BaseDocTemplate"]
    CondPageBreak = rl["CondPageBreak"]
    Frame = rl["Frame"]
    FrameActionFlowable = rl["FrameActionFlowable"]
    PageTemplate = rl["PageTemplate"]
    Image = rl["Image"]
    PageBreak = rl["PageBreak"]
    Paragraph = rl["Paragraph"]
    Spacer = rl["Spacer"]
    TableOfContents = rl["TableOfContents"]

    regular_font = "Times-Roman"
    italic_font = "Times-Italic"
    presentation_resources = profile_resources(visual_profile)
    if visual_profile is not None:
        regular_font = "IMFellEnglish"
        italic_font = "IMFellEnglishItalic"
        pdfmetrics.registerFont(
            TTFont(regular_font, str(FONT_ROOT / "IMFeENrm28P.ttf"))
        )
        pdfmetrics.registerFont(
            TTFont(italic_font, str(FONT_ROOT / "IMFeENit28P.ttf"))
        )

    page_width, page_height = A5
    left_margin = 19 * mm
    right_margin = 17 * mm
    top_margin = 18 * mm
    bottom_margin = 18 * mm
    available_width = page_width - left_margin - right_margin
    available_height = page_height - top_margin - bottom_margin
    note_records: dict[str, dict[str, str]] = {}

    class FootnoteReserve(FrameActionFlowable):
        _ZEROSIZE = True
        width = 0
        height = 0

        def __init__(self, reserve_height: float) -> None:
            self.reserve_height = reserve_height

        def frameAction(self, frame: object) -> None:
            if not hasattr(frame, "_footnote_base_y1"):
                frame._footnote_base_y1 = frame._y1
                frame._footnote_base_height = frame._height
                frame._footnote_reserved_height = 0
            frame._footnote_reserved_height += self.reserve_height
            frame.__dict__["_y1"] = (
                frame._footnote_base_y1 + frame._footnote_reserved_height
            )
            frame.__dict__["_height"] = (
                frame._footnote_base_height - frame._footnote_reserved_height
            )
            frame._geom()

    class EditorialDocTemplate(BaseDocTemplate):
        def __init__(self, filename: str) -> None:
            super().__init__(
                filename,
                pagesize=A5,
                leftMargin=left_margin,
                rightMargin=right_margin,
                topMargin=top_margin,
                bottomMargin=bottom_margin,
                title=book["title"],
                author=book["author"],
            )
            frame = Frame(
                left_margin,
                bottom_margin,
                available_width,
                available_height,
                id="body",
            )
            self.addPageTemplates(
                [
                    PageTemplate(
                        id="editorial",
                        frames=[frame],
                        onPage=self._page,
                        onPageEnd=self._page_end,
                    )
                ]
            )
            self.page_footnotes: dict[int, list[str]] = {}

        def beforeDocument(self) -> None:
            self.page_footnotes = {}

        def beforePage(self) -> None:
            for frame in self.pageTemplate.frames:
                if not hasattr(frame, "_footnote_base_y1"):
                    continue
                frame.__dict__["_y1"] = frame._footnote_base_y1
                frame.__dict__["_height"] = frame._footnote_base_height
                frame._footnote_reserved_height = 0
                frame._geom()
                frame._reset()

        def _page(self, canvas: object, doc: object) -> None:
            canvas.setTitle(book["title"])
            if book["author"]:
                canvas.setAuthor(book["author"])
            canvas.setSubject(f"Semantic {language} book edition")
            if canvas.getPageNumber() > 2:
                canvas.saveState()
                canvas.setFillColor(colors.black)
                canvas.setFont(regular_font, 8)
                canvas.drawCentredString(page_width / 2, 9 * mm, str(canvas.getPageNumber()))
                canvas.restoreState()

        def _page_end(self, canvas: object, doc: object) -> None:
            footnote_ids = self.page_footnotes.get(canvas.getPageNumber(), [])
            if not footnote_ids:
                return
            paragraphs = [
                footnote_paragraph(note_records[note_id])
                for note_id in footnote_ids
            ]
            measured = [
                (paragraph, paragraph.wrap(available_width, available_height)[1])
                for paragraph in paragraphs
            ]
            content_height = sum(height for _paragraph, height in measured)
            content_height += max(0, len(measured) - 1) * FOOTNOTE_SPACE_MM * mm
            separator_y = (
                bottom_margin
                + content_height
                + FOOTNOTE_SEPARATOR_GAP_MM * mm
            )
            canvas.saveState()
            canvas.setStrokeColor(colors.black)
            canvas.setLineWidth(0.5)
            canvas.line(
                left_margin,
                separator_y,
                left_margin + FOOTNOTE_SEPARATOR_WIDTH_MM * mm,
                separator_y,
            )
            cursor_y = separator_y - FOOTNOTE_SEPARATOR_GAP_MM * mm
            for paragraph, height in measured:
                cursor_y -= height
                paragraph.drawOn(canvas, left_margin, cursor_y)
                cursor_y -= FOOTNOTE_SPACE_MM * mm
            canvas.restoreState()

        def afterFlowable(self, flowable: object) -> None:
            for note_id in getattr(flowable, "footnote_ids", ()):
                page_notes = self.page_footnotes.setdefault(self.page, [])
                if note_id not in page_notes:
                    page_notes.append(note_id)
            level = getattr(flowable, "outline_level", None)
            if level is None:
                return
            text = getattr(flowable, "outline_text", "")
            key = getattr(flowable, "outline_key", "")
            self.canv.bookmarkPage(key)
            self.canv.addOutlineEntry(text, key, level=level, closed=False)
            if getattr(flowable, "include_in_toc", True):
                self.notify("TOCEntry", (level, text, self.page, key))

    base = getSampleStyleSheet()
    body = ParagraphStyle(
        "BookBody",
        parent=base["BodyText"],
        fontName=regular_font,
        fontSize=10.8,
        leading=15.5,
        textColor=colors.black,
        alignment=TA_JUSTIFY,
        firstLineIndent=7 * mm,
        spaceAfter=3.2 * mm,
        language=language,
        splitLongWords=False,
    )
    dialogue = _dialogue_paragraph_style(
        ParagraphStyle,
        body,
        italic_font,
        mm,
        TA_RIGHT,
    )
    quotation = _quotation_paragraph_style(
        ParagraphStyle,
        body,
        mm,
        TA_JUSTIFY,
    )
    verse = _verse_paragraph_style(
        ParagraphStyle,
        body,
        TA_CENTER,
    )
    note = _footnote_paragraph_style(
        ParagraphStyle,
        body,
        regular_font,
        TA_LEFT,
    )
    url = _url_paragraph_style(
        ParagraphStyle,
        body,
        TA_LEFT,
    )
    heading_styles = {
        level: ParagraphStyle(
            f"Heading{level}",
            parent=base["Heading1" if level == 1 else "Heading2"],
            fontName=regular_font,
            fontSize=max(12, 19 - (level - 1) * 1.5),
            leading=max(15, 23 - (level - 1) * 1.5),
            textColor=colors.black,
            alignment=TA_CENTER if level <= 2 else TA_LEFT,
            spaceBefore=6 * mm,
            spaceAfter=5 * mm,
            keepWithNext=True,
        )
        for level in range(1, 7)
    }
    title_style = ParagraphStyle(
        "TitlePageTitle",
        parent=heading_styles[1],
        fontSize=25,
        leading=30,
        spaceBefore=35 * mm,
    )
    subtitle_style = ParagraphStyle(
        "TitlePageSubtitle",
        parent=body,
        fontName=italic_font,
        fontSize=13,
        leading=18,
        alignment=TA_CENTER,
        firstLineIndent=0,
    )
    centered = ParagraphStyle(
        "Centered",
        parent=body,
        alignment=TA_CENTER,
        firstLineIndent=0,
    )
    toc_style = ParagraphStyle(
        "TOC",
        parent=body,
        fontName=regular_font,
        alignment=TA_LEFT,
        leftIndent=0,
        rightIndent=0,
        firstLineIndent=0,
        fontSize=9.5,
        leading=11.5,
        spaceBefore=0,
        spaceAfter=0.8 * mm,
    )
    toc_heading_style = ParagraphStyle(
        "TOCHeading",
        parent=heading_styles[1],
        fontSize=20,
        leading=24,
        spaceBefore=4 * mm,
        spaceAfter=5 * mm,
    )
    source_title_heading = ParagraphStyle(
        "SourceTitleHeading",
        parent=heading_styles[2],
        fontSize=18,
        leading=22,
        alignment=TA_CENTER,
        spaceBefore=8 * mm,
        spaceAfter=5 * mm,
        keepWithNext=False,
    )
    source_title_heading_first = ParagraphStyle(
        "SourceTitleHeadingFirst",
        parent=source_title_heading,
        fontSize=20,
        leading=24,
        spaceBefore=18 * mm,
    )
    source_title_text = ParagraphStyle(
        "SourceTitleText",
        parent=body,
        fontSize=11.5,
        leading=15,
        alignment=TA_CENTER,
        leftIndent=0,
        rightIndent=0,
        firstLineIndent=0,
        spaceBefore=4 * mm,
        spaceAfter=4 * mm,
    )

    note_ids = {
        str(block["marker"]): str(block["id"])
        for document in documents
        for block in (document.get("_layout_blocks") or [])
        if isinstance(block, dict) and block.get("kind") == "note"
    }
    for document in documents:
        changes = document.get("_revision_changes") or []
        applied_revision_ids: set[str] = set()
        for block in document.get("_layout_blocks") or []:
            lines = _layout_text_values(
                block,
                book_root,
                changes,
                applied_revision_ids,
            )
            if block.get("kind") != "note":
                continue
            marker = str(block["marker"])
            note_id = str(block["id"])
            first_line = lines[0]
            content = re.sub(rf"^\s*{re.escape(marker)}\s+", "", first_line)
            note_records[note_id] = {
                "id": note_id,
                "marker": marker,
                "text": normalize_space(" ".join([content, *lines[1:]])),
            }
    missing_note_records = sorted(set(note_ids.values()) - set(note_records))
    if missing_note_records:
        raise RuntimeError(f"PDF note content is missing: {missing_note_records}")

    def footnote_paragraph(record: dict[str, str]) -> object:
        return Paragraph(
            f'<a name="{html.escape(record["id"])}"/>'
            f'<super>{html.escape(record["marker"])}</super> '
            f'{html.escape(record["text"])}',
            note,
        )

    def footnote_height(reference_ids: tuple[str, ...]) -> float:
        height = FOOTNOTE_SEPARATOR_GAP_MM * mm
        for index, note_id in enumerate(reference_ids):
            paragraph = footnote_paragraph(note_records[note_id])
            height += paragraph.wrap(available_width, available_height)[1]
            if index:
                height += FOOTNOTE_SPACE_MM * mm
        return height

    assets_used: list[dict] = []
    image_buffers: list[BytesIO] = []
    story: list[object] = []
    referenced_note_ids: set[str] = set()
    rendered_note_ids: set[str] = set()

    def append_page_break() -> None:
        if story and isinstance(story[-1], PageBreak):
            return
        story.append(PageBreak())

    def append_flowable(
        flowable: object,
        reference_text: str = "",
    ) -> None:
        reference_ids = _referenced_note_ids(reference_text, note_ids)
        if not reference_ids:
            story.append(flowable)
            return
        referenced_note_ids.update(reference_ids)
        _width, flowable_height = flowable.wrap(available_width, available_height)
        required_height = flowable_height + footnote_height(reference_ids)
        if required_height > available_height:
            raise RuntimeError(
                "A PDF block and its footnote cannot fit on one page: "
                f"{reference_ids}"
            )
        story.append(CondPageBreak(required_height))
        flowable.footnote_ids = reference_ids
        story.append(flowable)
        story.append(FootnoteReserve(footnote_height(reference_ids)))

    if visual_profile is not None:
        cover_bytes = cover_image(book, "PDF")
        cover_buffer = BytesIO(cover_bytes)
        image_buffers.append(cover_buffer)
        cover_scale = min(
            (available_width - 12) / 1200,
            (available_height - 12) / 1800,
        )
        cover_width, cover_height = 1200 * cover_scale, 1800 * cover_scale
        story.extend(
            [
                Image(cover_buffer, width=cover_width, height=cover_height),
                PageBreak(),
            ]
        )
    else:
        cover_bytes = None

    story.append(Paragraph(html.escape(book["title"]), title_style))
    if book["subtitle"]:
        story.append(Paragraph(html.escape(book["subtitle"]), subtitle_style))
        story.append(Spacer(1, 10 * mm))
    if book["author"]:
        story.append(Paragraph(html.escape(book["author"]), centered))
    publication = " / ".join(
        value
        for value in (
            book["publication_place"],
            str(book["publication_year"] or "").strip(),
        )
        if value
    )
    if publication:
        story.append(Spacer(1, 30 * mm))
        story.append(Paragraph(html.escape(publication), centered))
    story.append(PageBreak())

    toc_heading = Paragraph("Sumário", toc_heading_style)
    toc_heading.outline_level = 0
    toc_heading.outline_text = "Sumário"
    toc_heading.outline_key = "toc"
    toc_heading.include_in_toc = False
    story.append(toc_heading)
    toc = TableOfContents(dotsMinLevel=0)
    toc.levelStyles = [toc_style]
    story.extend([toc, PageBreak()])

    def append_image(asset: dict) -> None:
        width, height = _fit_image(
            asset["path"],
            available_width,
            available_height * 0.78,
        )
        story.append(Spacer(1, 3 * mm))
        story.append(Image(str(asset["path"]), width=width, height=height))
        story.append(Spacer(1, 4 * mm))
        if not any(record["id"] == asset["id"] for record in assets_used):
            assets_used.append(
                {
                    "id": asset["id"],
                    "sha256": asset["sha256"],
                    "media_type": asset["media_type"],
                }
            )

    for document_index, document in enumerate(documents):
        if (
            text_edition == "fluid-pt-br"
            and is_fluid_supplementary_document(document)
        ):
            continue
        rendered_note_ids.update(
            str(block["id"])
            for block in (document.get("_layout_blocks") or [])
            if isinstance(block, dict) and block.get("kind") == "note"
        )
        if document_index:
            append_page_break()
        assets = selected_assets_by_document[document["id"]]
        if document.get("kind") == "source_cover":
            for asset in assets:
                append_image(asset)
            continue

        blocks = document.get("_layout_blocks")
        if not isinstance(blocks, list):
            text = document["_text_path"].read_text(encoding="utf-8")
            heading, paragraphs = paragraphs_from_text(
                text,
                str(document["title"]),
                allow_leading_chapter_label=document.get("kind") == "chapter",
            )
            heading_flowable = Paragraph(heading_markup(heading), heading_styles[1])
            heading_flowable.outline_level = 0
            heading_flowable.outline_text = str(document["title"])
            heading_flowable.outline_key = f"document-{document_index}"
            story.append(heading_flowable)
            for asset in assets:
                if asset["placement"] != "end":
                    append_image(asset)
            for value in paragraphs:
                normalized_value = normalize_space(value)
                story.append(
                    Paragraph(
                        html.escape(normalized_value),
                        url if _is_url_paragraph(normalized_value) else body,
                    )
                )
            for asset in assets:
                if asset["placement"] == "end":
                    append_image(asset)
            continue

        before_assets = [asset for asset in assets if asset["placement"] != "end"]
        after_assets = [asset for asset in assets if asset["placement"] == "end"]
        is_title_page = document.get("kind") == "cover"
        inserted_before_assets = False
        changes = document.get("_revision_changes") or []
        applied_revision_ids: set[str] = set()
        outline_added = False
        if not any(block.get("kind") == "heading" for block in blocks):
            flowable = Paragraph(html.escape(str(document["title"])), heading_styles[1])
            flowable.outline_level = 0
            flowable.outline_text = str(document["title"])
            flowable.outline_key = f"document-{document_index}"
            story.append(flowable)
            outline_added = True
        for block_index, block_group in semantic_block_groups(blocks):
            block = block_group[0]
            kind = block["kind"]
            lines: list[str] = []
            for grouped_block in block_group:
                lines.extend(
                    _layout_text_values(
                        grouped_block,
                        book_root,
                        changes,
                        applied_revision_ids,
                    )
                )
            if kind == "heading":
                level = int(block["level"])
                value = "<br/>".join(_rich_text(line, note_ids) for line in lines)
                if (
                    text_edition == "fluid-pt-br"
                    and any(is_fluid_supplementary_title(line) for line in lines)
                ):
                    break
                if is_title_page:
                    heading_style = (
                        source_title_heading_first
                        if block_index == 0
                        else source_title_heading
                    )
                else:
                    heading_style = heading_styles[level]
                flowable = Paragraph(value, heading_style)
                if not outline_added:
                    flowable.outline_level = 0
                    flowable.outline_text = str(document["title"])
                    flowable.outline_key = f"document-{document_index}"
                    outline_added = True
                append_flowable(flowable, " ".join(lines))
                if before_assets and not inserted_before_assets and not is_title_page:
                    for asset in before_assets:
                        append_image(asset)
                    inserted_before_assets = True
            elif kind == "paragraph":
                value = join_semantic_values(lines)
                paragraph_style = (
                    source_title_text
                    if is_title_page
                    else (url if _is_url_paragraph(value) else body)
                )
                append_flowable(
                    Paragraph(
                        _rich_text(value, note_ids),
                        paragraph_style,
                    ),
                    value,
                )
            elif kind == "quotation":
                value = join_semantic_values(lines)
                append_flowable(
                    Paragraph(
                        _rich_text(value, note_ids),
                        quotation,
                    ),
                    value,
                )
            elif kind == "dialogue":
                value = " ".join(lines)
                append_flowable(
                    Paragraph(_rich_text(value, note_ids), dialogue),
                    value,
                )
            elif kind == "verse":
                verse_paragraph = Paragraph(
                    "<br/>".join(_rich_text(line, note_ids) for line in lines),
                    verse,
                )
                append_flowable(verse_paragraph, " ".join(lines))
                story.append(Spacer(1, 3 * mm))
            elif kind == "note":
                continue
            else:
                raise RuntimeError(f"Unsupported PDF layout block kind: {kind}")
        if before_assets and not inserted_before_assets:
            if is_title_page:
                append_page_break()
            for asset_index, asset in enumerate(before_assets):
                if is_title_page and asset_index:
                    append_page_break()
                append_image(asset)
        for asset in after_assets:
            append_image(asset)
        expected_revision_ids = {
            str(change.get("id"))
            for change in changes
            if isinstance(change, dict) and isinstance(change.get("id"), str)
        }
        if applied_revision_ids != expected_revision_ids:
            missing = sorted(expected_revision_ids - applied_revision_ids)
            raise RuntimeError(
                "Approved revision changes are not represented by the semantic PDF layout: "
                f"{missing}"
            )
    endnote_ids = [
        note_id
        for note_id in note_records
        if note_id in rendered_note_ids and note_id not in referenced_note_ids
    ]
    if endnote_ids:
        story.append(PageBreak())
        story.append(Paragraph("Notas", heading_styles[1]))
        for note_id in endnote_ids:
            story.append(footnote_paragraph(note_records[note_id]))
    output.parent.mkdir(parents=True, exist_ok=True)
    staged = temporary_output_path(output, ".pdf")
    if staged.exists():
        raise RuntimeError(f"Temporary PDF export already exists: {staged}")
    document = EditorialDocTemplate(str(staged))
    try:
        document.multiBuild(story)
    except Exception as error:
        if staged.exists():
            staged.unlink()
        raise RuntimeError(f"Cannot compose editorial PDF: {error}") from error

    try:
        from pypdf import PdfReader
    except ImportError as error:
        if staged.exists():
            staged.unlink()
        raise RuntimeError(
            "pypdf is required to finalize PDF export. Run this script with the Codex bundled Python."
        ) from error
    try:
        page_count = len(PdfReader(str(staged)).pages)
        os.replace(staged, output)
    except Exception:
        if staged.exists():
            staged.unlink()
        raise
    presentation = None
    if visual_profile is not None and cover_bytes is not None:
        presentation = {
            "name": PROFILE_NAME,
            "cover": {
                "sha256": __import__("hashlib").sha256(cover_bytes).hexdigest(),
                "media_type": "image/jpeg",
                "format_label": "PDF",
            },
            "resources": [
                {
                    "id": resource.identifier,
                    "source_sha256": resource.sha256,
                    "media_type": resource.media_type,
                }
                for resource in presentation_resources
            ],
        }
    return assets_used, presentation, page_count, str(reportlab.Version)


def pdf_sidecar_assets(
    documents: list[dict],
    selected_assets_by_document: dict[str, list[dict]],
) -> list[dict]:
    assets_used: list[dict] = []
    for document in documents:
        for asset in selected_assets_by_document[document["id"]]:
            if any(record["id"] == asset["id"] for record in assets_used):
                continue
            assets_used.append(
                {
                    "id": asset["id"],
                    "sha256": asset["sha256"],
                    "media_type": asset["media_type"],
                }
            )
    return assets_used


def pdf_sidecar_presentation(book: dict, visual_profile: dict | None) -> dict | None:
    if visual_profile is None:
        return None
    cover_bytes = cover_image(book, "PDF")
    return {
        "name": PROFILE_NAME,
        "cover": {
            "sha256": __import__("hashlib").sha256(cover_bytes).hexdigest(),
            "media_type": "image/jpeg",
            "format_label": "PDF",
        },
        "resources": [
            {
                "id": resource.identifier,
                "source_sha256": resource.sha256,
                "media_type": resource.media_type,
            }
            for resource in profile_resources(visual_profile)
        ],
    }


def read_pdf_page_count(path: Path) -> int | None:
    try:
        from pypdf import PdfReader

        return len(PdfReader(str(path)).pages)
    except Exception:
        return None


def pdf_sidecar_data(
    output: Path,
    book_root: Path,
    fingerprint: dict,
    image_edition: str,
    text_edition: str,
    manifest: dict,
    map_path: Path,
    ledger_path: Path,
    assets_manifest_path: Path,
    documents: list[dict],
    selected_assets_by_document: dict[str, list[dict]],
    layout: dict | None,
    renderer: dict,
    presentation: dict | None,
    page_count: int | None = None,
) -> dict:
    sidecar_data = {
        "schema_version": "1.0",
        "pdf_path": relative_to_book(book_root, output),
        "pdf_sha256": sha256_file(output),
        "input_fingerprint": fingerprint,
        "image_edition": image_edition,
        "text_edition": text_edition,
        "language": manifest["language"],
        "book_map_sha256": sha256_file(map_path),
        "text_ledger_sha256": sha256_file(ledger_path),
        "assets_manifest_sha256": sha256_file(assets_manifest_path),
        "renderer": renderer,
        "assets": pdf_sidecar_assets(documents, selected_assets_by_document),
    }
    resolved_page_count = page_count if page_count is not None else read_pdf_page_count(output)
    if resolved_page_count is not None:
        sidecar_data["page_count"] = resolved_page_count
    if text_edition == "translated-pt-br":
        sidecar_data["source_language"] = manifest["source_language"]
        sidecar_data["translation_ledger_sha256"] = manifest[
            "translation_ledger_sha256"
        ]
    elif text_edition == "revised-pt-br":
        sidecar_data["revision_ledger_sha256"] = manifest["revision_ledger_sha256"]
    elif text_edition == "fluid-pt-br":
        for key in (
            "base_edition",
            "base_ledger_sha256",
            "fluid_style_sha256",
            "fluid_edition_ledger_sha256",
            "profile",
        ):
            sidecar_data[key] = manifest[key]
        if manifest["base_edition"] == "translated-pt-br":
            sidecar_data["source_language"] = manifest["source_language"]
            sidecar_data["translation_ledger_sha256"] = manifest[
                "translation_ledger_sha256"
            ]
    if isinstance(layout, dict):
        sidecar_data["layout"] = manifest["layout"]
    if presentation:
        sidecar_data["visual_profile"] = presentation
    return sidecar_data


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Export an editorial PDF from verified Audiobook Codex source artifacts."
    )
    parser.add_argument("--book-root", required=True, type=Path)
    parser.add_argument("--epub-manifest", type=Path)
    parser.add_argument("--assets-manifest", type=Path)
    parser.add_argument(
        "--image-edition",
        choices=sorted(IMAGE_EDITIONS),
        default="original",
    )
    parser.add_argument(
        "--text-edition",
        choices=sorted(TEXT_EDITIONS),
        default="original",
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    try:
        book_root = resolve_book_paths(args.book_root).assembly_root
        epub_manifest_path = (
            args.epub_manifest.expanduser().resolve()
            if args.epub_manifest
            else book_root
            / "metadata"
            / (
                "epub-manifest.fluid.json"
                if args.text_edition == "fluid-pt-br"
                else (
                    "epub-manifest.pt-br.json"
                    if args.text_edition == "translated-pt-br"
                    else (
                        "epub-manifest.revised.json"
                        if args.text_edition == "revised-pt-br"
                        else "epub-manifest.json"
                    )
                )
            )
        )
        assets_manifest_path = (
            args.assets_manifest.expanduser().resolve()
            if args.assets_manifest
            else book_root / "metadata" / "assets-manifest.json"
        )
        (
            book_map,
            ledger,
            assets_manifest,
            manifest,
            map_path,
            ledger_path,
            translation_ledger,
            revision_ledger,
            _fluid_style,
            fluid_ledger,
            layout,
        ) = load_export_context(
            book_root,
            epub_manifest_path,
            assets_manifest_path,
            args.text_edition,
        )
        documents, asset_by_id = validate_documents(
            book_root,
            manifest,
            assets_manifest,
            ledger,
            args.text_edition,
            translation_ledger,
            revision_ledger,
            fluid_ledger,
            layout,
        )
        selected_assets_by_document = {
            document["id"]: [
                selected_asset(asset_by_id[asset_id], book_root, args.image_edition)
                for asset_id in document["asset_ids"]
            ]
            for document in documents
        }
        visual_profile = normalize_visual_profile(manifest.get("visual_profile"))
        book = book_metadata(book_map, manifest)
        label = edition_label(
            args.text_edition,
            args.image_edition,
            visual_profile is not None,
        )
        default_name = (
            f"{safe_segment(book['title'], 'book')}.pdf"
            if uses_unsuffixed_fluid_export_name(book_root, args.text_edition)
            else f"{safe_segment(book['title'], 'book')}-{label}.pdf"
        )
        output = resolve_export_output(
            book_root,
            args.output,
            default_name,
        )
        sidecar = output.with_suffix(".pdf.json")
        renderer = current_renderer()
        fingerprint = export_input_fingerprint(
            export_fingerprint_payload(
                "pdf",
                book_root,
                epub_manifest_path,
                assets_manifest_path,
                map_path,
                ledger_path,
                manifest,
                book,
                str(manifest["language"]),
                args.text_edition,
                args.image_edition,
                documents,
                selected_assets_by_document,
                visual_profile,
                renderer,
            )
        )
        existing_page_count = read_pdf_page_count(output) if output.is_file() else None
        expected_sidecar = (
            pdf_sidecar_data(
                output,
                book_root,
                fingerprint,
                args.image_edition,
                args.text_edition,
                manifest,
                map_path,
                ledger_path,
                assets_manifest_path,
                documents,
                selected_assets_by_document,
                layout,
                renderer,
                pdf_sidecar_presentation(book, visual_profile),
                existing_page_count,
            )
            if existing_page_count is not None
            else None
        )
        if cached_export_is_current(
            output,
            sidecar,
            book_root,
            "pdf_path",
            "pdf_sha256",
            fingerprint,
            expected_sidecar,
        ):
            print(f"Up to date {output}")
            print(f"Up to date {sidecar}")
            return
        _assets, presentation, page_count, renderer_version = write_pdf(
            output,
            book_root,
            book,
            str(manifest["language"]),
            args.text_edition,
            documents,
            selected_assets_by_document,
            visual_profile,
        )
        sidecar_data = pdf_sidecar_data(
            output,
            book_root,
            fingerprint,
            args.image_edition,
            args.text_edition,
            manifest,
            map_path,
            ledger_path,
            assets_manifest_path,
            documents,
            selected_assets_by_document,
            layout,
            {
                "name": "reportlab",
                "version": renderer_version,
            },
            presentation,
            page_count,
        )
        write_json(sidecar, sidecar_data)
    except RuntimeError as error:
        print(f"Cannot export PDF: {error}", file=sys.stderr)
        raise SystemExit(1) from error

    print(f"Created {output}")
    print(f"Created {sidecar}")


if __name__ == "__main__":
    main()
