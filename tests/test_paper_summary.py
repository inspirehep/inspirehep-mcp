"""Tests for the summaries the tools hand back to a model."""

from server import _build_paper_summary


def _hit(**metadata) -> dict:
    return {"metadata": {"control_number": 1124337, **metadata}}


def test_names_the_first_authors_and_marks_the_rest():
    summary = _build_paper_summary(
        _hit(
            authors=[{"full_name": f"Author, {i}"} for i in range(10)],
            number_of_authors=2932,
        )
    )
    assert summary["authors"] == [
        "Author, 0",
        "Author, 1",
        "Author, 2",
        "et al.",
    ]


def test_does_not_say_et_al_when_every_author_is_named():
    summary = _build_paper_summary(
        _hit(authors=[{"full_name": "Witten, Edward"}], number_of_authors=1)
    )
    assert summary["authors"] == ["Witten, Edward"]


def test_counts_authors_the_serializer_left_out():
    """`authors` arrives truncated, so its length is not the author count."""
    summary = _build_paper_summary(
        _hit(
            authors=[{"full_name": f"Author, {i}"} for i in range(10)],
            number_of_authors=10,
        )
    )
    assert summary["authors"][-1] == "et al."


def test_reports_the_collaboration_behind_a_paper():
    summary = _build_paper_summary(_hit(collaborations=[{"value": "ATLAS"}]))
    assert summary["collaborations"] == ["ATLAS"]


def test_prefers_the_preprint_year():
    summary = _build_paper_summary(
        _hit(
            preprint_date="1997-11",
            date="Nov, 1997",
            publication_info=[{"year": 1999}],
        )
    )
    assert summary["year"] == "1997"


def test_falls_back_through_the_dates_it_has():
    assert _build_paper_summary(_hit(date="Nov, 1997"))["year"] == "1997"
    assert (
        _build_paper_summary(_hit(publication_info=[{"year": 1999}]))["year"] == "1999"
    )


def test_truncates_a_long_abstract():
    summary = _build_paper_summary(_hit(abstracts=[{"value": "x" * 800}]))
    assert summary["abstract"] == "x" * 500 + "…"


def test_keeps_a_short_abstract_whole():
    summary = _build_paper_summary(_hit(abstracts=[{"value": "Short one."}]))
    assert summary["abstract"] == "Short one."


def test_drops_fields_it_has_nothing_to_say_about():
    summary = _build_paper_summary(_hit(titles=[{"title": "A paper"}]))
    assert "doi" not in summary
    assert "journal" not in summary
    assert "arxiv_id" not in summary
    assert "collaborations" not in summary


def test_keeps_a_citation_count_of_zero():
    """Zero citations is a fact about a paper, not a missing field."""
    summary = _build_paper_summary(_hit(citation_count=0))
    assert summary["citation_count"] == 0


def test_always_identifies_the_record():
    summary = _build_paper_summary(_hit(titles=[{"title": "A paper"}]))
    assert summary["inspire_id"] == 1124337
    assert summary["inspire_url"].endswith("/literature/1124337")
