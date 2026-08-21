"""The record page's markdown pipeline (GAPS U41).

Everything here is pure: text in, text out. The claims are about what a note's
own content can and cannot do to the page that renders it.
"""

from basic_memory.web.render import link_wikilinks, render_body, split_frontmatter


def test_frontmatter_splits_into_a_mapping_and_a_body() -> None:
    parsed = split_frontmatter("---\ntitle: A record\nstatus: open\n---\n\n# Heading\n")

    assert parsed.metadata == {"title": "A record", "status": "open"}
    assert parsed.body.strip() == "# Heading"


def test_a_file_with_no_frontmatter_is_all_body() -> None:
    parsed = split_frontmatter("# Just a heading\n\nand a paragraph.\n")

    assert parsed.metadata == {}
    assert parsed.body.startswith("# Just a heading")


def test_a_thematic_break_in_the_body_is_not_a_delimiter() -> None:
    """`---` only opens a header at line 1; anywhere else it is a horizontal rule."""
    parsed = split_frontmatter("Some prose.\n\n---\n\nMore prose.\n")

    assert parsed.metadata == {}
    assert "More prose." in parsed.body


def test_an_unterminated_header_leaves_the_file_as_body() -> None:
    parsed = split_frontmatter("---\ntitle: A record\n\nbody with no closing delimiter\n")

    assert parsed.metadata == {}
    assert "no closing delimiter" in parsed.body


def test_malformed_yaml_costs_the_metadata_not_the_body() -> None:
    """`bm doctor` reports a broken record; this page still shows what is in it."""
    parsed = split_frontmatter("---\ntitle: [unclosed\n---\n\nthe body survives\n")

    assert parsed.metadata == {}
    assert "the body survives" in parsed.body


def test_a_header_that_is_not_a_mapping_yields_no_metadata() -> None:
    parsed = split_frontmatter("---\n- one\n- two\n---\n\nbody\n")

    assert parsed.metadata == {}
    assert "body" in parsed.body


def test_a_wikilink_becomes_a_link_to_the_record_route() -> None:
    assert link_wikilinks("See [[tnd-open0001]].") == "See [tnd-open0001](/r/tnd-open0001)."


def test_a_labelled_wikilink_keeps_its_label() -> None:
    rewritten = link_wikilinks("See [[tnd-open0001|the backup task]].")

    assert rewritten == "See [the backup task](/r/tnd-open0001)."


def test_a_wikilink_inside_a_code_span_stays_literal() -> None:
    """A note documenting the syntax must not have its example turned into a link."""
    source = "Write `[[record-id]]` to point at a record."

    assert link_wikilinks(source) == source


def test_a_wikilink_inside_a_fenced_block_stays_literal() -> None:
    source = "before\n\n```\n[[record-id]]\n```\n\nafter"

    assert link_wikilinks(source) == source


def test_a_wikilink_after_a_fence_closes_is_rewritten_again() -> None:
    """Positive control: the fence state must reset, or every later link is lost."""
    rewritten = link_wikilinks("```\n[[inside]]\n```\n\n[[outside]]")

    assert "[[inside]]" in rewritten
    assert "[outside](/r/outside)" in rewritten


def test_raw_html_in_a_body_is_escaped_not_executed() -> None:
    """`html: False`: a note body is content an agent wrote, and the browser is ours."""
    rendered = render_body("<script>alert(1)</script>")

    assert "<script>" not in rendered
    assert "&lt;script&gt;" in rendered


def test_markdown_emphasis_renders_as_html() -> None:
    assert "<strong>bold</strong>" in render_body("**bold**")


def test_render_body_resolves_wikilinks_on_the_way_through() -> None:
    rendered = render_body("See [[tnd-open0001]] for the rest.")

    assert '<a href="/r/tnd-open0001">tnd-open0001</a>' in rendered
