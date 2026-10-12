# readeck-mcp

An MCP server for a [Readeck](https://readeck.org) instance.

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

## Install in Claude Code

The repo is a Claude Code plugin marketplace with one plugin, `readeck`. You need [uv](https://docs.astral.sh/uv/) on your `PATH`.

In a Claude Code session:

```
/plugin marketplace add overflowy/readeck-mcp
/plugin install readeck@readeck-mcp
```

Or from a shell:

```sh
claude plugin marketplace add overflowy/readeck-mcp
claude plugin install readeck@readeck-mcp --config READECK_URL=https://readeck.example.com
```

When you enable the plugin, Claude Code asks for your Readeck URL. To change it later, open `/plugin`, select the plugin under **Installed** and choose **Configure options**.

The repo is private, so Claude Code clones it with the git credentials already on your machine. Over HTTPS, run `gh auth login` and `gh auth setup-git` once. Set `CLAUDE_CODE_PLUGIN_PREFER_HTTPS=1` if you don't use an SSH key with GitHub.

### Without the plugin

You can also register the server directly from a local checkout:

```sh
claude mcp add readeck --scope user \
  -e READECK_URL=https://readeck.example.com \
  -- uv run --directory /path/to/readeck-mcp readeck-mcp
```

## Login

The first time a tool needs Readeck, the server opens the Readeck authorization page in your browser. Once you approve, it saves the token to `~/.config/readeck-mcp/tokens.json` and reuses it. Readeck tokens don't expire. If one gets revoked, the server opens the authorization page again on the next call.

You can also manage the login from a terminal:

```sh
export READECK_URL=https://readeck.example.com
uv run --directory /path/to/readeck-mcp readeck-mcp login   # authorize now
uv run --directory /path/to/readeck-mcp readeck-mcp logout  # revoke and delete the saved token
```

`READECK_URL` is required; the server exits with an error if it's unset. To skip the browser flow, set `READECK_TOKEN` to an API token created under **Settings > API Tokens**. Other MCP clients can run the same `uv run ... readeck-mcp` command over stdio with `READECK_URL` in its environment.
