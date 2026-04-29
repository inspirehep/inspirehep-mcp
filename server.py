from __future__ import annotations

import httpx
from mcp.server.fastmcp import FastMCP

# ---------------------------------------------------------------------------
# Server setup
# ---------------------------------------------------------------------------

BASE_URL = "https://inspirehep.net/api"
DEFAULT_PAGE_SIZE = 10
MAX_PAGE_SIZE = 25

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


def _build_paper_summary(hit: dict) -> dict:
    """Extract the most useful fields from a raw InspireHEP literature hit."""
    meta = hit.get("metadata", {})

    titles = meta.get("titles", [])
    title = titles[0].get("title", "N/A") if titles else "N/A"

    authors = meta.get("authors", [])
    author_names = [a.get("full_name", "") for a in authors[:5]]
    if len(authors) > 5:
        author_names.append("et al.")

    abstracts = meta.get("abstracts", [])
    abstract = abstracts[0].get("value", "") if abstracts else ""

    arxiv_eprints = meta.get("arxiv_eprints", [])
    arxiv_id = arxiv_eprints[0].get("value", "") if arxiv_eprints else ""

    dois = meta.get("dois", [])
    doi = dois[0].get("value", "") if dois else ""

    publication_info = meta.get("publication_info", [])
    journal = ""
    if publication_info:
        info = publication_info[0]
        journal = info.get("journal_title", "")

    inspire_id = meta.get("control_number", hit.get("id", ""))

    return {
        "inspire_id": inspire_id,
        "title": title,
        "authors": author_names,
        "abstract": abstract[:500] + ("…" if len(abstract) > 500 else ""),
        "year": meta.get("earliest_date", "")[:4],
        "journal": journal,
        "arxiv_id": arxiv_id,
        "doi": doi,
        "citation_count": meta.get("citation_count", 0),
        "inspire_url": f"https://inspirehep.net/literature/{inspire_id}",
    }


async def _fetch_literature(params: dict) -> dict:
    """
    Low-level async GET against /api/literature.
    Returns the parsed JSON response.
    Raises httpx.HTTPStatusError on non-2xx responses.
    """
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.get(f"{BASE_URL}/literature", params=params)
        response.raise_for_status()
        return response.json()


def _format_results(data: dict) -> dict:
    """
    Turn a raw InspireHEP search response into a clean summary dict
    suitable for returning from an MCP tool.
    """
    hits = data.get("hits", {})
    total = hits.get("total", 0)
    papers = [_build_paper_summary(h) for h in hits.get("hits", [])]
    return {"total_results": total, "papers": papers}


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
        count:   Number of papers to return (1–25, default 10).
        subject: Optional subject / keyword filter (e.g. "dark matter",
                 "string theory"). Leave empty for all subjects.
    """
    count = max(1, min(count, MAX_PAGE_SIZE))
    params: dict = {"sort": "mostrecent", "size": count, "page": 1}
    if subject:
        params["q"] = subject

    data = await _fetch_literature(params)
    return _format_results(data)


@mcp.tool()
async def get_papers_by_publisher(
    publisher: str,
    count: int = DEFAULT_PAGE_SIZE,
    page: int = 1,
) -> dict:
    """
    Return papers published in a specific journal or by a specific publisher.

    Args:
        publisher: Journal title or publisher name (e.g. "Physical Review D",
                   "JHEP", "Nuclear Physics B").
        count:     Number of results per page (1–25, default 10).
        page:      Page number for pagination (default 1).
    """
    count = max(1, min(count, MAX_PAGE_SIZE))
    params = {
        "sort": "mostrecent",
        "size": count,
        "page": page,
        "q": f"j {publisher}",
    }
    data = await _fetch_literature(params)
    return _format_results(data)


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
    • ArXiv ID:               "eprint 2101.12345"
    • Citation count filter:  "topcite 500+"
    • Combined:               "a Maldacena AND topcite 1000+"

    Args:
        query: INSPIRE search query string.
        sort:  Sort order — "mostrecent" (default) or "mostcited".
        count: Results per page (1–25, default 10).
        page:  Page number for pagination (default 1).
    """
    if sort not in {"mostrecent", "mostcited"}:
        sort = "mostrecent"
    count = max(1, min(count, MAX_PAGE_SIZE))

    params = {"q": query, "sort": sort, "size": count, "page": page}
    data = await _fetch_literature(params)
    return _format_results(data)


@mcp.tool()
async def get_paper_by_id(inspire_id: int) -> dict:
    """
    Fetch the full metadata for a single paper by its InspireHEP record ID.

    Args:
        inspire_id: The integer record ID shown in an InspireHEP URL,
                    e.g. 1705857 for inspirehep.net/literature/1705857.
    """
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.get(f"{BASE_URL}/literature/{inspire_id}")
        response.raise_for_status()
        data = response.json()

    return _build_paper_summary(
        {"metadata": data.get("metadata", {}), "id": inspire_id}
    )


async def _resolve_orcid_to_bai(orcid: str) -> str:
    """
    Look up an author by ORCID on InspireHEP and return their INSPIRE BAI
    (e.g. "Juan.M.Maldacena.1"), which is the reliable key for literature
    searches.  Raises ValueError if no author record is found.
    """
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.get(
            f"{BASE_URL}/authors",
            params={"q": f"ids.value:{orcid}", "size": 1},
        )
        response.raise_for_status()
        data = response.json()

    hits = data.get("hits", {}).get("hits", [])
    if not hits:
        raise ValueError(f"No InspireHEP author found for ORCID {orcid!r}.")

    meta = hits[0]["metadata"]
    for id_entry in meta.get("ids", []):
        if id_entry.get("schema") == "INSPIRE BAI":
            return id_entry["value"]

    # Fallback: use the canonical name if no BAI is present
    name = meta.get("name", {}).get("value", "")
    if name:
        return name
    raise ValueError(f"Author found for ORCID {orcid!r} but has no usable INSPIRE BAI.")


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
        count:  Results per page (1–25, default 10).
        page:   Page number for pagination (default 1).
    """
    if not author and not orcid:
        raise ValueError("Provide at least one of `author` or `orcid`.")
    if sort not in {"mostrecent", "mostcited"}:
        sort = "mostcited"
    count = max(1, min(count, MAX_PAGE_SIZE))

    if orcid:
        bai = await _resolve_orcid_to_bai(orcid)
        query = f"a {bai}"
    else:
        query = f"a {author}"

    params = {"q": query, "sort": sort, "size": count, "page": page}
    data = await _fetch_literature(params)
    result = _format_results(data)

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
        mcp.run(transport="streamable-http")
    else:
        mcp.run(transport="stdio")
