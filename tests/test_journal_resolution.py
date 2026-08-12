"""Tests for turning a journal's name into the abbreviation INSPIRE indexes."""

import asyncio

import pytest

import server


def _journals(*entries) -> dict:
    return {
        "hits": {
            "hits": [
                {
                    "metadata": {
                        "short_title": short,
                        "journal_title": {"title": title},
                    }
                }
                for short, title in entries
            ]
        }
    }


@pytest.fixture
def resolve(monkeypatch):
    """Resolve a name against a fixed set of journal hits, without INSPIRE."""

    def _resolve(name, payload):
        async def fake_get_json(path, params=None, ui=True):
            assert path == "journals"
            return payload

        monkeypatch.setattr(server, "_get_json", fake_get_json)
        return asyncio.run(server._resolve_journal_title(name))

    return _resolve


def test_resolves_a_full_name_to_the_abbreviation(resolve):
    hits = _journals(("Phys.Rev.D", "Physical Review D"))
    assert resolve("Physical Review D", hits) == "Phys.Rev.D"


def test_prefers_an_exact_abbreviation_over_a_better_ranked_one(resolve):
    """Searching "JHEP" ranks "JHEP Grav.Cosmol." first; the exact one wins."""
    hits = _journals(
        (
            "JHEP Grav.Cosmol.",
            "Journal of High Energy Physics, Gravitation and Cosmology",
        ),
        ("JHEP", "The Journal of High Energy Physics (JHEP)"),
    )
    assert resolve("JHEP", hits) == "JHEP"


def test_prefers_an_exact_title_over_a_better_ranked_supplement(resolve):
    hits = _journals(
        ("Nucl.Phys.B Proc.Suppl.", "Nuclear Physics B Proceedings Supplements"),
        ("Nucl.Phys.B", "Nuclear Physics B"),
    )
    assert resolve("Nuclear Physics B", hits) == "Nucl.Phys.B"


def test_falls_back_to_the_base_journal_when_nothing_matches_exactly(resolve):
    hits = _journals(
        ("Int.J.High Energy Phys.", "International Journal of High Energy Physics"),
        ("JHEP", "The Journal of High Energy Physics (JHEP)"),
        (
            "JHEP Grav.Cosmol.",
            "Journal of High Energy Physics, Gravitation and Cosmology",
        ),
    )
    assert resolve("Journal of High Energy Physics", hits) == "JHEP"


def test_keeps_the_name_when_no_journal_matches(resolve):
    assert resolve("zzzz not a journal", _journals()) == "zzzz not a journal"


def test_keeps_the_name_when_the_lookup_fails(monkeypatch):
    """A journal lookup that fails should not fail the whole search."""

    async def failing(path, params=None, ui=True):
        raise server.InspireError("INSPIRE is down")

    monkeypatch.setattr(server, "_get_json", failing)
    resolved = asyncio.run(server._resolve_journal_title("Physical Review D"))
    assert resolved == "Physical Review D"
