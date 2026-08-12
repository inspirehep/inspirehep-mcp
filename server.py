from __future__ import annotations

import os
import re

import httpx
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.server import TransportSecuritySettings

# ---------------------------------------------------------------------------
# Server setup
# ---------------------------------------------------------------------------

INSPIRE_URL = os.environ.get("INSPIRE_URL", "https://inspirehep.net").rstrip("/")
BASE_URL = f"{INSPIRE_URL}/api"
DEFAULT_PAGE_SIZE = 10
MAX_PAGE_SIZE = 30

UI_SERIALIZER_HEADERS = {"Accept": "application/vnd+inspire.record.ui+json"}

MAX_AUTHOR_NAMES = 3
YEAR_REGEXP = re.compile(r"\d{4}")

ABSTRACT_CHAR_BUDGET = 5000
MIN_ABSTRACT_CHARS = 200
MAX_ABSTRACT_CHARS = 1500

mcp = FastMCP(
    name="InspireHEP",
    instructions=(
        "Tools for querying the InspireHEP High-Energy Physics literature "
        "database. Use `search_papers` for general searches, "
        "`get_recent_papers` for the latest publications, and "
        "`get_papers_by_publisher` to browse a journal or publisher's output."
    ),
)

# ---------------------------------------------------------------------------
# Shared HTTP helpers
# ---------------------------------------------------------------------------


class InspireError(Exception):
    """A failure worth reporting to the caller in words rather than a traceback."""


_client: httpx.AsyncClient | None = None


def _get_client() -> httpx.AsyncClient:
    """One pooled client for the process: a new one per call re-does TLS."""
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            timeout=15.0,
            limits=httpx.Limits(max_keepalive_connections=10, max_connections=20),
        )
    return _client


async def _get_json(path: str, params: dict | None = None, ui: bool = True) -> dict:
    """GET from the INSPIRE API, turning transport failures into InspireError."""
    try:
        response = await _get_client().get(
            f"{BASE_URL}/{path}",
            params=params,
            headers=UI_SERIALIZER_HEADERS if ui else None,
        )
    except httpx.HTTPError as error:
        raise InspireError(f"Could not reach InspireHEP: {error}") from error

    if response.status_code == 404:
        raise InspireError(f"InspireHEP has no record at /{path}.")
    if response.status_code >= 400:
        raise InspireError(
            f"InspireHEP returned HTTP {response.status_code} for /{path}."
        )
    return response.json()


def _abstract_limit(count: int) -> int:
    """Spend the abstract budget across however many papers were asked for."""
    per_paper = ABSTRACT_CHAR_BUDGET // max(count, 1)
    return max(MIN_ABSTRACT_CHARS, min(per_paper, MAX_ABSTRACT_CHARS))


def _build_author_names(meta: dict) -> list[str]:
    """Name the first few authors, and say honestly how many were left out."""
    authors = meta.get("authors") or []
    names = [a.get("full_name", "") for a in authors[:MAX_AUTHOR_NAMES]]
    total = meta.get("number_of_authors") or len(authors)
    if total > len(names):
        names.append("et al.")
    return names


def _extract_year(meta: dict) -> str:
    """
    Find the year a paper first appeared.

    The UI serializer has no `earliest_date`, so this walks the dates it does
    carry, earliest first, to keep meaning the same thing as before.
    """
    preprint_date = meta.get("preprint_date") or ""
    if preprint_date[:4].isdigit():
        return preprint_date[:4]

    displayed = YEAR_REGEXP.search(meta.get("date") or "")
    if displayed:
        return displayed.group()

    publication_info = meta.get("publication_info") or [{}]
    return str(publication_info[0].get("year") or "")


def _build_paper_summary(hit: dict, abstract_limit: int = MAX_ABSTRACT_CHARS) -> dict:
    """
    Extract the most useful fields from an InspireHEP literature hit.

    Empty fields are dropped rather than sent as blanks: every paper is read by
    a model, and `"doi": ""` costs tokens to say nothing.
    """
    meta = hit.get("metadata", {})

    titles = meta.get("titles") or [{}]
    abstracts = meta.get("abstracts") or [{}]
    arxiv_eprints = meta.get("arxiv_eprints") or [{}]
    dois = meta.get("dois") or [{}]
    publication_info = meta.get("publication_info") or [{}]

    abstract = abstracts[0].get("value", "")
    inspire_id = meta.get("control_number", hit.get("id", ""))

    summary = {
        "inspire_id": inspire_id,
        "title": titles[0].get("title", "N/A"),
        "authors": _build_author_names(meta),
        "collaborations": [
            c.get("value") for c in meta.get("collaborations") or [] if c.get("value")
        ],
        "year": _extract_year(meta),
        "journal": publication_info[0].get("journal_title", ""),
        "abstract": abstract[:abstract_limit]
        + ("…" if len(abstract) > abstract_limit else ""),
        "citation_count": meta.get("citation_count", 0),
        "arxiv_id": arxiv_eprints[0].get("value", ""),
        "doi": dois[0].get("value", ""),
        "inspire_url": f"{INSPIRE_URL}/literature/{inspire_id}",
    }
    return {key: value for key, value in summary.items() if value or value == 0}


async def _search_literature(params: dict) -> dict:
    """Run a literature search and summarise it for the caller."""
    data = await _get_json("literature", params)
    hits = data.get("hits", {})
    abstract_limit = _abstract_limit(params.get("size", DEFAULT_PAGE_SIZE))
    return {
        "total_results": hits.get("total", 0),
        "papers": [
            _build_paper_summary(hit, abstract_limit) for hit in hits.get("hits", [])
        ],
    }


async def _resolve_journal_title(publisher: str) -> str:
    """
    Turn a journal's name into the abbreviation INSPIRE indexes it under.

    `j Physical Review D` matches nothing; `j Phys.Rev.D` matches everything.
    Search ranking alone is not enough to pick with — "JHEP" ranks
    "JHEP Grav.Cosmol." first — so exact matches win before anything else.
    """
    try:
        data = await _get_json("journals", {"q": publisher, "size": 5}, ui=False)
    except InspireError:
        return publisher

    hits = [hit.get("metadata", {}) for hit in data.get("hits", {}).get("hits", [])]
    wanted = publisher.strip().casefold()

    for meta in hits:
        if (meta.get("short_title") or "").casefold() == wanted:
            return meta["short_title"]
    for meta in hits:
        if (meta.get("journal_title", {}).get("title") or "").casefold() == wanted:
            return meta.get("short_title") or publisher

    contains = [
        meta
        for meta in hits
        if wanted in (meta.get("journal_title", {}).get("title") or "").casefold()
        and meta.get("short_title")
    ]
    if contains:
        return min(contains, key=lambda m: len(m["journal_title"]["title"]))[
            "short_title"
        ]
    return publisher


# ---------------------------------------------------------------------------
# MCP Tools
# ---------------------------------------------------------------------------


@mcp.tool()
async def get_recent_papers(
    count: int = DEFAULT_PAGE_SIZE,
    subject: str = "",
) -> dict:
    """
    Return the most recently added papers on InspireHEP.

    Args:
        count:   Number of papers to return (1–25, default 10). Asking for
                 fewer papers returns longer abstracts for each.
        subject: Optional subject / keyword filter (e.g. "dark matter",
                 "string theory"). Leave empty for all subjects.
    """
    count = max(1, min(count, MAX_PAGE_SIZE))
    params: dict = {"sort": "mostrecent", "size": count, "page": 1}
    if subject:
        params["q"] = subject

    try:
        return await _search_literature(params)
    except InspireError as error:
        return {"error": str(error)}


@mcp.tool()
async def get_papers_by_publisher(
    publisher: str,
    count: int = DEFAULT_PAGE_SIZE,
    page: int = 1,
) -> dict:
    """
    Return papers published in a specific journal or by a specific publisher.

    Either the full name or INSPIRE's abbreviation works: the full name is
    resolved to the abbreviation the records are actually indexed under.

    Args:
        publisher: Journal title or publisher name (e.g. "Physical Review D",
                   "Phys.Rev.D", "JHEP", "Nuclear Physics B").
        count:     Number of results per page (1–25, default 10). Asking for
                   fewer papers returns longer abstracts for each.
        page:      Page number for pagination (default 1).
    """
    count = max(1, min(count, MAX_PAGE_SIZE))
    try:
        journal = await _resolve_journal_title(publisher)
        result = await _search_literature(
            {"sort": "mostrecent", "size": count, "page": page, "q": f"j {journal}"}
        )
    except InspireError as error:
        return {"error": str(error)}

    if journal != publisher:
        result["resolved_journal"] = journal
    return result


@mcp.tool()
async def search_papers(
    query: str,
    sort: str = "mostrecent",
    count: int = DEFAULT_PAGE_SIZE,
    page: int = 1,
) -> dict:
    """
    Full-text search across InspireHEP using the INSPIRE/SPIRES search syntax.

    Syntax examples
    ---------------
    • Author search:          "a Witten"  or  "a E.Witten.1"
    • Title keyword:          "t supersymmetry"
    • Abstract keyword:       "abs entanglement"
    • ArXiv ID:               "eprint 2101.12345"
    • Journal:                "j Phys.Rev.D"  (abbreviated, not "Physical Review D")
    • Date range:             "de 2023->2026"
    • Citation count filter:  "topcite 500+"  or  "tc 1000+"
    • Papers citing a paper:  "refersto recid 1124337"
    • Papers a paper cites:   "citedby recid 1124337"
    • Search the full text:   'fulltext "self-coupling"'
    • Combined:               "a Maldacena AND topcite 1000+"

    Searching broadly then narrowing works well: run a wide search to see what
    is out there, then re-query the interesting records with a small `count`,
    which returns a longer abstract for each.

    Two things to know before translating a question into a query:

    • Papers are titled formally. They say "observation", "measurement" or
      "search for", never "discovery" or "breakthrough". Searching
      "t higgs discovery" finds neither Higgs discovery paper, because neither
      title contains that word.
    • `t` searches titles only, and a landmark paper may not have the obvious
      keyword in its title — the CMS Higgs discovery paper is called
      "Observation of a New Boson at a Mass of 125 GeV" and never says "Higgs".
      Drop the `t` to search more widely.

    For "the paper(s) on X" questions, pair the topic with a citation threshold
    and sort by citations: "higgs 125 GeV AND tc 5000+" with sort="mostcited"
    returns both discovery papers first.

    Args:
        query: INSPIRE search query string.
        sort:  Sort order — "mostrecent" (default) or "mostcited".
        count: Results per page (1–25, default 10). Asking for fewer papers
               returns longer abstracts for each.
        page:  Page number for pagination (default 1).
    """
    if sort not in {"mostrecent", "mostcited"}:
        sort = "mostrecent"
    count = max(1, min(count, MAX_PAGE_SIZE))

    try:
        return await _search_literature(
            {"q": query, "sort": sort, "size": count, "page": page}
        )
    except InspireError as error:
        return {"error": str(error)}


@mcp.tool()
async def get_paper_by_id(inspire_id: int) -> dict:
    """
    Fetch the full metadata for a single paper by its InspireHEP record ID.

    Returns the longest abstract of any tool, so this is the one to reach for
    once a search has narrowed things down to a specific paper.

    Args:
        inspire_id: The integer record ID shown in an InspireHEP URL,
                    e.g. 1705857 for inspirehep.net/literature/1705857.
    """
    try:
        data = await _get_json(f"literature/{inspire_id}")
    except InspireError as error:
        return {"error": str(error)}

    return _build_paper_summary(
        {"metadata": data.get("metadata", {}), "id": inspire_id},
        MAX_ABSTRACT_CHARS,
    )


async def _resolve_orcid_to_bai(orcid: str) -> str:
    """
    Look up an author by ORCID on InspireHEP and return their INSPIRE BAI
    (e.g. "Juan.M.Maldacena.1"), which is the reliable key for literature
    searches.  Raises InspireError if no author record is found.
    """
    data = await _get_json("authors", {"q": f"ids.value:{orcid}", "size": 1}, ui=False)

    hits = data.get("hits", {}).get("hits", [])
    if not hits:
        raise InspireError(f"InspireHEP has no author with ORCID {orcid}.")

    meta = hits[0]["metadata"]
    for id_entry in meta.get("ids", []):
        if id_entry.get("schema") == "INSPIRE BAI":
            return id_entry["value"]

    # Fallback: use the canonical name if no BAI is present
    name = meta.get("name", {}).get("value", "")
    if name:
        return name
    raise InspireError(
        f"The author with ORCID {orcid} has no INSPIRE identifier to search by."
    )


@mcp.tool()
async def get_papers_by_author(
    author: str = "",
    orcid: str = "",
    sort: str = "mostcited",
    count: int = DEFAULT_PAGE_SIZE,
    page: int = 1,
) -> dict:
    """
    Return papers by a specific author.  Supply either a name or an ORCID.

    Args:
        author: Author name in INSPIRE format, e.g. "Witten, Edward",
                "E.Witten.1" (exact INSPIRE BAI), or simply "Hawking".
                Ignored when `orcid` is provided.
        orcid:  Author ORCID, e.g. "0000-0002-9127-1687".  When given, the
                server resolves it to an INSPIRE BAI first, then searches.
        sort:   "mostcited" (default) or "mostrecent".
        count:  Results per page (1–25, default 10). Asking for fewer papers
                returns longer abstracts for each.
        page:   Page number for pagination (default 1).
    """
    if not author and not orcid:
        return {"error": "Provide at least one of `author` or `orcid`."}
    if sort not in {"mostrecent", "mostcited"}:
        sort = "mostcited"
    count = max(1, min(count, MAX_PAGE_SIZE))

    try:
        bai = await _resolve_orcid_to_bai(orcid) if orcid else ""
        result = await _search_literature(
            {
                "q": f"a {bai or author}",
                "sort": sort,
                "size": count,
                "page": page,
            }
        )
    except InspireError as error:
        return {"error": str(error)}

    # Surface which author identifier was actually used
    if orcid:
        result["resolved_inspire_bai"] = bai
        result["queried_orcid"] = orcid

    return result


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    transport = "stdio"
    port = 8000

    args = sys.argv[1:]
    if "--transport" in args:
        idx = args.index("--transport")
        transport = args[idx + 1]
    if "--port" in args:
        idx = args.index("--port")
        port = int(args[idx + 1])

    if transport == "http":
        mcp.settings.host = "0.0.0.0"
        mcp.settings.port = port
        allowed_host = os.environ.get("ALLOWED_HOST")
        if allowed_host:
            mcp.settings.transport_security = TransportSecuritySettings(
                enable_dns_rebinding_protection=True,
                allowed_hosts=[allowed_host, "127.0.0.1:*", "localhost:*"],
                allowed_origins=[
                    f"https://{allowed_host}",
                    "http://127.0.0.1:*",
                    "http://localhost:*",
                ],
            )
        mcp.run(transport="streamable-http")
    else:
        mcp.run(transport="stdio")
