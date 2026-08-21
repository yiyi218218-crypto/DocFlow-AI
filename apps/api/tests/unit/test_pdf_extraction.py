"""PDF parsing and the rotation fix, verified against rendered pixels.

Asserting that a rectangle "looks right" numerically is weak — the numbers can be
self-consistently wrong. So these tests render the page to an image and check
that the ink actually falls inside the computed highlight. If the rotation
transform is dropped, the ink lands outside and the test fails.

This is the check that would have caught the single most likely silent bug in the
whole product: highlights offset by 90 degrees on scanned landscape pages.
"""

from __future__ import annotations

import pymupdf
import pytest

from docflow.ai.alignment.span_matcher import CitationStatus, align_quote
from docflow.core.errors import EncryptedDocument, TooManyPages
from docflow.documents.pdf import (
    MIN_CHARS_FOR_NATIVE,
    parse_pdf,
    render_page,
    text_to_pdf,
)

SENTENCE = "Applications must be submitted by 15 September 2026."


def make_pdf(text: str = SENTENCE, *, rotation: int = 0, pages: int = 1) -> bytes:
    doc = pymupdf.open()
    for _ in range(pages):
        page = doc.new_page(width=595, height=842)  # A4 portrait
        page.insert_text((72, 120), text, fontsize=14)
        if rotation:
            page.set_rotation(rotation)
    data = doc.tobytes()
    doc.close()
    return data


def ink_fraction_inside(pdf: bytes, page_number: int, rects: list, *, dpi: int = 100) -> float:
    """Fraction of dark pixels on the page that fall inside the given rectangles.

    The page contains only our sentence, so a correct highlight should contain
    nearly all of the ink.
    """
    doc = pymupdf.open(stream=pdf, filetype="pdf")
    try:
        pix = doc[page_number - 1].get_pixmap(dpi=dpi, alpha=False)
        width, height, n = pix.width, pix.height, pix.n
        samples = pix.samples

        boxes = [
            (
                int(r.x0 * width),
                int(r.y0 * height),
                int(r.x1 * width),
                int(r.y1 * height),
            )
            for r in rects
        ]

        total = inside = 0
        for y in range(height):
            row = y * pix.stride
            for x in range(width):
                if samples[row + x * n] >= 128:  # not dark
                    continue
                total += 1
                if any(bx0 <= x <= bx1 and by0 <= y <= by1 for bx0, by0, bx1, by1 in boxes):
                    inside += 1
        return inside / total if total else 0.0
    finally:
        doc.close()


# --------------------------------------------------------------------------
# parsing
# --------------------------------------------------------------------------


def test_parses_text_and_word_boxes() -> None:
    parsed = parse_pdf(make_pdf())
    assert parsed.page_count == 1
    page = parsed.pages[0]
    assert "15 September 2026" in page.text
    assert page.text_map.words
    assert all(0.0 <= w.x0 <= 1.0 and 0.0 <= w.y0 <= 1.0 for w in page.text_map.words)


def make_wrapped_pdf(text: str) -> bytes:
    """insert_text draws one unwrapped line that runs off the page; use a text
    box so a multi-line fixture really contains the characters it claims to."""
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    page.insert_textbox(pymupdf.Rect(50, 60, 545, 780), text, fontsize=11)
    data = doc.tobytes()
    doc.close()
    return data


def test_native_text_layer_is_detected_as_usable() -> None:
    parsed = parse_pdf(make_wrapped_pdf("\n".join([SENTENCE] * 6)))
    page = parsed.pages[0]
    assert page.char_count >= 120
    assert page.has_usable_text_layer
    assert not parsed.is_scanned


def test_sparse_but_real_page_is_not_mistaken_for_a_scan() -> None:
    """A short notice covers ~2% of the page. The coverage gate must not send
    real pages to OCR — see the measured values in documents/pdf.py."""
    parsed = parse_pdf(make_wrapped_pdf("\n".join([SENTENCE] * 4)))
    page = parsed.pages[0]
    assert 0.005 < page.coverage < 0.10
    assert page.has_usable_text_layer


def test_blank_page_is_flagged_for_ocr() -> None:
    """An empty text layer is what a scan looks like — it must route to OCR."""
    doc = pymupdf.open()
    doc.new_page(width=595, height=842)
    blank = doc.tobytes()
    doc.close()

    parsed = parse_pdf(blank)
    assert not parsed.pages[0].has_usable_text_layer
    assert parsed.is_scanned
    assert parsed.scanned_page_numbers == [1]


def test_full_text_carries_page_markers() -> None:
    """The extraction prompt relies on these to attribute a quote to a page."""
    parsed = parse_pdf(make_pdf(pages=3))
    for n in (1, 2, 3):
        assert f"[page {n}]" in parsed.full_text


# --------------------------------------------------------------------------
# rotation — the bug this file exists for
# --------------------------------------------------------------------------


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_highlight_lands_on_the_ink_at_every_rotation(rotation: int) -> None:
    pdf = make_pdf(rotation=rotation)
    parsed = parse_pdf(pdf)

    result = align_quote(SENTENCE, parsed.text_maps(), claimed_page=1)
    assert result.status is CitationStatus.VERIFIED, f"rotation {rotation}: quote not verified"
    assert result.rects, f"rotation {rotation}: no highlight produced"

    covered = ink_fraction_inside(pdf, 1, result.rects)
    assert covered > 0.85, (
        f"rotation {rotation}: only {covered:.0%} of the page's ink falls inside the "
        "highlight — the rotation transform is probably missing"
    )


@pytest.mark.parametrize("rotation", [90, 270])
def test_display_box_swaps_orientation_when_rotated(rotation: int) -> None:
    """A portrait page rotated 90 degrees displays as landscape."""
    parsed = parse_pdf(make_pdf(rotation=rotation))
    page = parsed.pages[0]
    assert page.rotation == rotation
    assert page.width > page.height


def test_unrotated_page_stays_portrait() -> None:
    page = parse_pdf(make_pdf()).pages[0]
    assert page.width < page.height


# --------------------------------------------------------------------------
# the full chain, on a real PDF
# --------------------------------------------------------------------------


def test_end_to_end_quote_to_rectangle_on_a_real_pdf() -> None:
    """document -> page -> char span -> word boxes -> rectangle.

    The provenance chain from docs/ARCHITECTURE.md §2, exercised on a real file
    rather than a synthetic text map.
    """
    body = (
        "University of Dhaka\n"
        "Admission Circular 2026\n"
        "Applications must be submitted by 15 September 2026.\n"
        "Required documents: academic transcript, National ID copy."
    )
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_textbox(pymupdf.Rect(60, 80, 520, 300), body, fontsize=12)
    pdf = doc.tobytes()
    doc.close()

    parsed = parse_pdf(pdf)
    result = align_quote(
        "Applications must be submitted by 15 September 2026.",
        parsed.text_maps(),
        claimed_page=1,
    )

    assert result.status is CitationStatus.VERIFIED
    assert result.page == 1
    assert len(result.rects) == 1, "a single-line quote should give a single rectangle"

    rect = result.rects[0]
    assert 0.0 < rect.x0 < rect.x1 < 1.0
    assert 0.0 < rect.y0 < rect.y1 < 1.0
    # The quote is the third line of four, so it sits in the upper half.
    assert rect.y0 < 0.5


def test_fabricated_quote_finds_nothing_in_a_real_pdf() -> None:
    parsed = parse_pdf(make_pdf())
    result = align_quote(
        "Successful applicants receive a full tuition waiver and monthly stipend.",
        parsed.text_maps(),
        claimed_page=1,
    )
    assert result.status is CitationStatus.UNVERIFIED


# --------------------------------------------------------------------------
# rendering and conversion
# --------------------------------------------------------------------------


def test_render_page_produces_webp() -> None:
    image = render_page(make_pdf(), 1, dpi=72)
    assert image[:4] == b"RIFF" and image[8:12] == b"WEBP"


def test_text_to_pdf_round_trips() -> None:
    """Part of normalising every input to one canonical PDF representation."""
    pdf = text_to_pdf("Deadline: 15 September 2026\nSubmit the form.", title="Notice")
    parsed = parse_pdf(pdf)
    assert parsed.page_count >= 1
    assert "15 September 2026" in parsed.full_text


# --------------------------------------------------------------------------
# refusals
# --------------------------------------------------------------------------


def test_encrypted_pdf_fails_with_an_actionable_message() -> None:
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 100), "secret")
    encrypted = doc.tobytes(encryption=pymupdf.PDF_ENCRYPT_AES_256, owner_pw="o", user_pw="u")
    doc.close()

    with pytest.raises(EncryptedDocument) as exc:
        parse_pdf(encrypted)
    assert "password" in str(exc.value).lower()


def test_page_limit_is_enforced() -> None:
    with pytest.raises(TooManyPages):
        parse_pdf(make_pdf(pages=5), max_pages=3)


# --------------------------------------------------------------------------
# paragraph structure
# --------------------------------------------------------------------------


def test_paragraph_breaks_are_preserved() -> None:
    """A blank line means something; a line wrap does not.

    Without this distinction the page text is one unbroken run of single
    newlines, and anything reasoning about sentences merges a document's header
    block into the sentence that follows it — which is how a publication date
    gets extracted as the submission deadline.

    Measured on a real A4 page at 10.5pt: gaps inside a paragraph are ~0.8
    units, paragraph breaks ~16. The threshold is derived from those numbers per
    page rather than from a multiple of font size, which does not track them.
    """
    body = (
        "University of Dhaka\n"
        "Office of the Registrar\n"
        "\n"
        "Published on 1 August 2026\n"
        "\n"
        "Applications must be submitted by\n"
        "15 September 2026 at 5:00 PM."
    )
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    page.insert_textbox(pymupdf.Rect(50, 55, 545, 790), body, fontsize=10.5, lineheight=1.35)
    pdf = doc.tobytes()
    doc.close()

    text = parse_pdf(pdf).pages[0].text
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]

    assert len(paragraphs) == 3, f"expected 3 paragraphs, got {len(paragraphs)}: {paragraphs}"
    # The wrapped sentence stays whole rather than being split at the line break.
    assert "submitted by" in paragraphs[2]
    assert "15 September 2026" in paragraphs[2]
    # The header does not bleed into the deadline sentence.
    assert "1 August 2026" not in paragraphs[2]


def test_single_line_page_has_no_spurious_paragraph_breaks() -> None:
    """Too little structure to infer paragraphs must mean none, not all."""
    text = parse_pdf(make_pdf()).pages[0].text
    assert "\n\n" not in text


# --------------------------------------------------------------------------
# Bengali: the text layer can be worse than OCR
# --------------------------------------------------------------------------

BENGALI_NOTICE = (
    "ঢাকা বিশ্ববিদ্যালয়\n"
    "আবেদনের শেষ তারিখ ১৫ সেপ্টেম্বর ২০২৬।\n"
    "শিক্ষার্থীদের অবশ্যই একাডেমিক ট্রান্সক্রিপ্ট জমা দিতে হবে।\n"
    "জাতীয় পরিচয়পত্রের ফটোকপি প্রয়োজন। অতিরিক্ত তথ্যের জন্য যোগাযোগ করুন।"
)


def test_bengali_text_renders_rather_than_becoming_question_marks() -> None:
    """Without an embedded Bengali face the text layer is literally `?????`.

    The font is bundled rather than assumed, because this must behave the same
    on macOS, the Debian worker image and CI.
    """
    text = parse_pdf(text_to_pdf(BENGALI_NOTICE)).pages[0].text
    assert "?????" not in text
    bengali = sum(1 for ch in text if "\u0980" <= ch <= "\u09ff")
    assert bengali > 40, f"almost no Bengali survived: {text[:80]!r}"


def test_corrupt_bengali_text_layer_is_routed_to_ocr() -> None:
    """The dangerous failure: plenty of text, passes every length and coverage
    check, and silently unusable for citations.

    Bengali conjuncts shape into ligature glyphs; when the ToUnicode CMap does
    not map them back, extraction emits Latin-1/Greek substitutes inside
    otherwise-Bengali words. Measured: 63-70 per-line alignment (every citation
    UNVERIFIED) versus 100 for OCR of the same rendered page.
    """
    page = parse_pdf(text_to_pdf(BENGALI_NOTICE)).pages[0]

    assert page.char_count >= MIN_CHARS_FOR_NATIVE, "not a length problem"
    assert page.coverage >= 0.005, "not a coverage problem"
    assert page.script_corruption > 0.05, "corruption not detected"
    assert not page.has_usable_text_layer, "corrupt Bengali must route to OCR"


def test_english_text_layer_is_not_sent_to_ocr() -> None:
    """The corruption detector must not fire on documents it knows nothing about."""
    page = parse_pdf(make_wrapped_pdf("\n".join([SENTENCE] * 6))).pages[0]
    assert page.script_corruption == 0.0
    assert page.has_usable_text_layer


def test_code_switched_text_is_not_flagged_as_corrupt() -> None:
    """Bangla-English mixing is normal in this corpus, not corruption."""
    mixed = (
        "Admission Circular 2026\n"
        "আবেদনের শেষ তারিখ 15 September 2026\n"
        "Applicants must submit একাডেমিক ট্রান্সক্রিপ্ট with the form.\n"
    ) * 2
    from docflow.ai.normalization.text import script_corruption_ratio

    assert script_corruption_ratio(mixed) == 0.0
