# readeck-mcp

An MCP server for [Readeck](https://readeck.org), set by default to `https://readeck.example.com`.

## Tools

| Tool | What it does |
| --- | --- |
| `search_bookmarks` | Full text search and filters (labels, site, author, type, read status, archived, favorite, collection), with sorting and pagination |
| `get_bookmark` | Bookmark metadata |
| `read_bookmark` | Full article content as Markdown |
| `add_bookmark` | Save a URL, optionally with a title and labels |
| `update_bookmark` | Change title, note, favorite/archive state, read progress, labels |
| `delete_bookmark` | Delete a bookmark |
| `list_labels` | All labels with counts |
| `list_highlights` | Highlights, either across the library or for one bookmark |
| `list_collections` | Saved collections |

## Setup

Register the server with Claude Code:

```sh
claude mcp add readeck --scope user \
  -- uv run --directory /path/to/readeck-mcp readeck-mcp
```

The first time a tool needs Readeck, the server opens the Readeck authorization page in your browser. Once you approve, it saves the token to `~/.config/readeck-mcp/tokens.json` and reuses it. Readeck tokens don't expire. If one gets revoked, the server opens the authorization page again on the next call.

You can also manage the login from a terminal:

```sh
uv run --directory /path/to/readeck-mcp readeck-mcp login   # authorize now
uv run --directory /path/to/readeck-mcp readeck-mcp logout  # revoke and delete the saved token
```

To skip the browser flow, set `READECK_TOKEN` to an API token created under **Settings > API Tokens**. To use a different Readeck instance, set `READECK_URL`. Other MCP clients can run the same `uv run ... readeck-mcp` command over stdio.
