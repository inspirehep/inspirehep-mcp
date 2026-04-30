# InspireHEP MCP Server

An [MCP (Model Context Protocol)](https://modelcontextprotocol.io) server that
connects Claude and other MCP-compatible AI clients to the
[InspireHEP](https://inspirehep.net) High-Energy Physics literature database.

Ask Claude to find papers, look up authors by ORCID, browse journals, or fetch
full record metadata — all without leaving your conversation.

---

## Tools

| Tool | Description |
|------|-------------|
| `search_papers` | Full INSPIRE/SPIRES search syntax — author, title, ArXiv ID, citation filters, and more |
| `get_recent_papers` | Most recently added papers, with optional keyword filter |
| `get_papers_by_author` | Papers by a specific author — accepts a name **or an ORCID** |
| `get_papers_by_publisher` | Browse a journal or publisher's output |
| `get_paper_by_id` | Fetch the full metadata for a single record by InspireHEP ID |

### Example prompts

```
Find Maldacena's most-cited papers
Papers by ORCID 0000-0002-9127-1687
Recent papers on dark matter from the last week
Witten papers in JHEP with more than 500 citations
Fetch InspireHEP record 451647
```

---

## Requirements

- Python 3.12 (managed via [pyenv](https://github.com/pyenv/pyenv))
- [Poetry](https://python-poetry.org/)

---

## Installation

```bash
git clone https://github.com/inspirehep/inspirehep-mcp.git
cd inspirehep-mcp
pyenv install 3.12
poetry install
```

---

## Claude Desktop setup

### Remote (hosted on inspirehep.net)

1. Open (or create) `~/Library/Application Support/Claude/claude_desktop_config.json`

2. Add the `inspirehep` entry:

```json
{
  "mcpServers": {
    "inspirehep": {
      "type": "http",
      "url": "https://mcp.inspirehep.net/mcp"
    }
  }
}
```

3. Quit and reopen Claude Desktop.

### Local (running from source)

1. Open (or create) `~/Library/Application Support/Claude/claude_desktop_config.json`

2. Add the `inspirehep` entry — adjust the path to match where you cloned the repo:

```json
{
  "mcpServers": {
    "inspirehep": {
      "command": "poetry",
      "args": [
        "run",
        "python",
        "server.py"
      ],
      "cwd": "/absolute/path/to/inspirehep-mcp"
    }
  }
}
```

3. Quit and reopen Claude Desktop.

A filled-in template lives in [`claude_desktop_config.json.example`](claude_desktop_config.json.example).

---

## Claude Code setup

### Remote
```bash
claude mcp add --transport http inspirehep https://mcp.inspirehep.net/mcp
```

### Local
```bash
claude mcp add inspirehep -- poetry run python /absolute/path/to/inspirehep-mcp/server.py
```

---

## Running the server manually

**stdio** (default — used by Claude Desktop / Claude Code local setup):
```bash
poetry run python server.py
```

**HTTP** (remote / multi-client):
```bash
poetry run python server.py --transport http --port 8000
# Server available at http://localhost:8000/mcp
```

**Docker:**
```bash
docker run -p 8000:8000 registry.cern.ch/cern-sis/inspire/inspirehep-mcp:latest
# Server available at http://localhost:8000/mcp
```

---

## Search syntax

The `search_papers` tool accepts the full [INSPIRE search syntax](https://github.com/inspirehep/rest-api-doc):

| Query | Matches |
|-------|---------|
| `a Witten` | Papers by any author named Witten |
| `a E.Witten.1` | Papers by Edward Witten (exact INSPIRE BAI) |
| `t dark matter` | Title contains "dark matter" |
| `eprint 2101.12345` | Specific ArXiv preprint |
| `topcite 1000+` | Papers cited more than 1 000 times |
| `j "Physical Review D"` | Published in Physical Review D |
| `a Maldacena AND topcite 500+` | Maldacena papers with 500+ citations |

---

## ORCID author lookup

`get_papers_by_author` accepts either a name or an ORCID. When an ORCID is
supplied the server performs a two-step resolution:

1. `GET /api/authors?q=ids.value:{orcid}` — retrieve the author record
2. Extract the author's **INSPIRE BAI** (e.g. `Juan.M.Maldacena.1`)
3. Search `/api/literature?q=a {BAI}` — unambiguous, no false positives

The response includes a `resolved_inspire_bai` field so you can see which
identifier was used.

---

## Development

```bash
poetry install
poetry run pre-commit install           # install git hooks
poetry run pre-commit run --all-files   # run all hooks manually
poetry run ruff check server.py         # lint
poetry run ruff format server.py        # format
```
