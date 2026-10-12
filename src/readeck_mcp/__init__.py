"""MCP server exposing a Readeck instance's bookmarks, labels, highlights and collections."""

import argparse
import os
import sys
from typing import Annotated, Any, Literal

import anyio
import anyio.to_thread
import httpx
from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field

from readeck_mcp import auth

READECK_URL = os.environ.get("READECK_URL", "https://readeck.example.com").rstrip("/")
READECK_TOKEN = os.environ.get("READECK_TOKEN", "")

READ_ONLY = ToolAnnotations(read_only_hint=True, open_world_hint=False)
WRITE = ToolAnnotations(read_only_hint=False, destructive_hint=False, open_world_hint=False)
DESTRUCTIVE = ToolAnnotations(read_only_hint=False, destructive_hint=True, open_world_hint=False)

# Readeck rejects limit > 100 or a negative offset with a bare 404, so validate up front.
Limit = Annotated[int, Field(ge=1, le=100)]
Offset = Annotated[int, Field(ge=0)]

mcp = MCPServer(
    "Readeck",
    instructions=(
        "Tools for a personal Readeck read-it-later library. Use search_bookmarks to find "
        "bookmarks, then read_bookmark to get an article's full text as Markdown."
    ),
)


_login_lock = anyio.Lock()


async def _token() -> str:
    if READECK_TOKEN:
        return READECK_TOKEN
    async with _login_lock:
        if token := auth.cached_token(READECK_URL):
            return token
        try:
            return await anyio.to_thread.run_sync(auth.login, READECK_URL, abandon_on_cancel=True)
        except auth.LoginError as exc:
            raise ToolError(str(exc)) from exc


async def _send(method: str, path: str, token: str, **kwargs: Any) -> httpx.Response:
    async with httpx.AsyncClient(
        base_url=f"{READECK_URL}/api",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        timeout=30,
    ) as client:
        try:
            return await client.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise ToolError(f"Could not reach Readeck at {READECK_URL}: {exc}") from exc


async def _request(method: str, path: str, **kwargs: Any) -> httpx.Response:
    token = await _token()
    response = await _send(method, path, token, **kwargs)
    if response.status_code == 401 and not READECK_TOKEN:
        # The saved token was revoked; drop it and log in again.
        auth.forget_token(READECK_URL)
        response = await _send(method, path, await _token(), **kwargs)
    if response.is_error:
        raise ToolError(f"Readeck returned HTTP {response.status_code} for {method} {path}: {response.text[:500]}")
    return response


def _drop_none(params: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in params.items() if v is not None}


def _summarize(bookmark: dict[str, Any]) -> dict[str, Any]:
    """Keep the fields useful to a model and drop links to resources/images."""
    keys = (
        "id",
        "title",
        "url",
        "site_name",
        "authors",
        "description",
        "type",
        "lang",
        "created",
        "published",
        "labels",
        "is_marked",
        "is_archived",
        "read_progress",
        "reading_time",
        "word_count",
        "has_article",
        "state",
    )
    summary = {k: bookmark[k] for k in keys if k in bookmark}
    summary["readeck_url"] = f"{READECK_URL}/bookmarks/{bookmark['id']}"
    return summary


@mcp.tool(title="Search bookmarks", annotations=READ_ONLY)
async def search_bookmarks(
    search: str | None = None,
    labels: str | None = None,
    site: str | None = None,
    author: str | None = None,
    type: list[Literal["article", "photo", "video"]] | None = None,
    read_status: list[Literal["unread", "reading", "read"]] | None = None,
    is_archived: bool | None = None,
    is_marked: bool | None = None,
    collection: str | None = None,
    sort: list[str] | None = None,
    limit: Limit = 20,
    offset: Offset = 0,
) -> dict[str, Any]:
    """Search and filter saved bookmarks. All filters are optional and combined with AND.

    Args:
        search: Full text search string.
        labels: Label name to filter by.
        site: Site name or domain.
        author: Author name.
        type: Bookmark types to include.
        read_status: Reading states to include.
        is_archived: True for archived only, False for non-archived only.
        is_marked: True for favorites only.
        collection: Collection ID (see list_collections).
        sort: Sort keys such as "-created" (newest first), "created", "title", "-published",
            "duration", "site", "domain", or "random".
        limit: Page size, 1 to 100.
        offset: Number of results to skip, for pagination.
    """
    params = _drop_none(
        {
            "search": search,
            "labels": labels,
            "site": site,
            "author": author,
            "type": type,
            "read_status": read_status,
            "is_archived": is_archived,
            "is_marked": is_marked,
            "collection": collection,
            "sort": sort,
            "limit": limit,
            "offset": offset,
        }
    )
    response = await _request("GET", "/bookmarks", params=params)
    return {
        "total": int(response.headers.get("Total-Count", 0)),
        "offset": offset,
        "bookmarks": [_summarize(b) for b in response.json()],
    }


@mcp.tool(title="Get bookmark", annotations=READ_ONLY)
async def get_bookmark(bookmark_id: str) -> dict[str, Any]:
    """Get a bookmark's metadata (title, URL, labels, read state, etc.) by its ID."""
    response = await _request("GET", f"/bookmarks/{bookmark_id}")
    return _summarize(response.json())


@mcp.tool(title="Read bookmark", annotations=READ_ONLY)
async def read_bookmark(bookmark_id: str) -> str:
    """Get the full saved content of a bookmark as Markdown, including its metadata header."""
    response = await _request("GET", f"/bookmarks/{bookmark_id}/article.md", headers={"Accept": "text/markdown"})
    return response.text


@mcp.tool(title="Add bookmark", annotations=WRITE)
async def add_bookmark(url: str, title: str | None = None, labels: list[str] | None = None) -> dict[str, Any]:
    """Save a new URL to Readeck. The page is fetched and processed in the background.

    Args:
        url: URL to save.
        title: Optional title overriding the page's own.
        labels: Optional labels to attach.
    """
    response = await _request("POST", "/bookmarks", json=_drop_none({"url": url, "title": title, "labels": labels}))
    bookmark_id = response.headers.get("Bookmark-Id")
    return {
        "id": bookmark_id,
        "readeck_url": f"{READECK_URL}/bookmarks/{bookmark_id}" if bookmark_id else None,
        "status": "queued; content will be available once Readeck finishes fetching the page",
    }


@mcp.tool(title="Update bookmark", annotations=WRITE)
async def update_bookmark(
    bookmark_id: str,
    title: str | None = None,
    note: str | None = None,
    is_marked: bool | None = None,
    is_archived: bool | None = None,
    read_progress: int | None = None,
    add_labels: list[str] | None = None,
    remove_labels: list[str] | None = None,
) -> dict[str, Any]:
    """Update a bookmark. Only the fields you pass are changed.

    Args:
        bookmark_id: Bookmark ID.
        title: New title.
        note: User note (plain text).
        is_marked: Favorite state.
        is_archived: Archive state.
        read_progress: Reading progress percentage, 0 to 100 (100 marks it as read).
        add_labels: Labels to add.
        remove_labels: Labels to remove.
    """
    body = _drop_none(
        {
            "title": title,
            "note": note,
            "is_marked": is_marked,
            "is_archived": is_archived,
            "read_progress": read_progress,
            "add_labels": add_labels,
            "remove_labels": remove_labels,
        }
    )
    if not body:
        raise ToolError("Nothing to update: pass at least one field.")
    response = await _request("PATCH", f"/bookmarks/{bookmark_id}", json=body)
    return response.json()


@mcp.tool(title="Delete bookmark", annotations=DESTRUCTIVE)
async def delete_bookmark(bookmark_id: str) -> str:
    """Permanently delete a bookmark."""
    await _request("DELETE", f"/bookmarks/{bookmark_id}")
    return f"Bookmark {bookmark_id} deleted."


@mcp.tool(title="List labels", annotations=READ_ONLY)
async def list_labels() -> list[dict[str, Any]]:
    """List all labels with their bookmark counts."""
    response = await _request("GET", "/bookmarks/labels")
    return [{"name": label["name"], "count": label["count"]} for label in response.json()]


@mcp.tool(title="List highlights", annotations=READ_ONLY)
async def list_highlights(
    bookmark_id: str | None = None, limit: Limit = 50, offset: Offset = 0
) -> list[dict[str, Any]]:
    """List highlighted passages, either across all bookmarks or for a single bookmark.

    Args:
        bookmark_id: If given, only this bookmark's highlights are returned (limit/offset are ignored).
        limit: Page size when listing all highlights, 1 to 100.
        offset: Pagination offset when listing all highlights.
    """
    if bookmark_id:
        response = await _request("GET", f"/bookmarks/{bookmark_id}/annotations")
        return [{k: h.get(k) for k in ("id", "text", "created")} for h in response.json()]
    response = await _request("GET", "/bookmarks/annotations", params={"limit": limit, "offset": offset})
    keys = ("id", "text", "created", "bookmark_id", "bookmark_title", "bookmark_url", "bookmark_site_name")
    return [{k: h.get(k) for k in keys} for h in response.json()]


@mcp.tool(title="List collections", annotations=READ_ONLY)
async def list_collections() -> list[dict[str, Any]]:
    """List saved collections (saved searches). Pass a collection ID to search_bookmarks to list its bookmarks."""
    response = await _request("GET", "/bookmarks/collections")
    return [{k: v for k, v in c.items() if k != "href" and v not in (None, "", [])} for c in response.json()]


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="readeck-mcp",
        description=f"MCP server for Readeck at {READECK_URL} (set READECK_URL to change).",
    )
    parser.add_argument(
        "command",
        nargs="?",
        default="serve",
        choices=["serve", "login", "logout"],
        help="serve: run the MCP server over stdio (default). login: authorize in the browser "
        "and save a token. logout: revoke and delete the saved token.",
    )
    command = parser.parse_args().command
    if command == "serve":
        mcp.run()
    elif command == "login":
        try:
            auth.login(READECK_URL)
        except auth.LoginError as exc:
            sys.exit(f"Login failed: {exc}")
    elif auth.logout(READECK_URL):
        print(f"Logged out of {READECK_URL}.", file=sys.stderr)
    else:
        print(f"No saved token for {READECK_URL}.", file=sys.stderr)


if __name__ == "__main__":
    main()
