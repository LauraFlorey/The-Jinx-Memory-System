#!/usr/bin/env python3
"""Fetch web content and save a cleaned markdown copy into memory."""

from __future__ import annotations

import argparse
import asyncio
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from html import unescape
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import aiohttp

try:
    from bs4 import BeautifulSoup, NavigableString, Tag  # type: ignore
except ImportError:  # pragma: no cover - optional dependency
    BeautifulSoup = None
    NavigableString = Any  # type: ignore[assignment]
    Tag = Any  # type: ignore[assignment]


DEFAULT_SAVE_DIR = "memory/cold/reference"
USER_AGENT = "agentMemoryFetcher/1.0 (+https://github.com/openai)"
URL_RE = re.compile(r"https?://[^\s<>()]+", re.IGNORECASE)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def format_utc_timestamp(moment: datetime | None = None) -> str:
    value = moment or utc_now()
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")


def slugify(value: str, *, default: str = "page") -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return normalized or default


def extract_urls(text: str) -> list[str]:
    return [match.group(0).rstrip(".,)") for match in URL_RE.finditer(text or "")]


@dataclass
class FetchResult:
    url: str
    title: str
    markdown: str
    saved_path: Path | None

    @property
    def preview(self) -> str:
        compact = re.sub(r"\s+", " ", self.markdown).strip()
        return compact[:200]


class ContentFetcher:
    def __init__(self, base_dir: Path) -> None:
        self.base_dir = base_dir
        self.default_save_dir = base_dir / DEFAULT_SAVE_DIR

    async def fetch(
        self,
        url: str,
        *,
        save_to: str | Path | None = None,
        save: bool = True,
    ) -> FetchResult:
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=180),
            headers={"User-Agent": USER_AGENT},
        ) as session:
            async with session.get(url) as response:
                response.raise_for_status()
                html = await response.text(errors="replace")

        title, body_markdown = self.extract_content(html, url=url)
        metadata = self.render_metadata(url=url, title=title)
        markdown = f"{metadata}\n{body_markdown}".rstrip() + "\n"
        saved_path = None
        if save:
            destination_dir = self.resolve_save_dir(save_to)
            destination_dir.mkdir(parents=True, exist_ok=True)
            saved_path = destination_dir / self.build_filename(url, title)
            saved_path.write_text(markdown, encoding="utf-8")
        return FetchResult(url=url, title=title, markdown=markdown, saved_path=saved_path)

    def resolve_save_dir(self, save_to: str | Path | None) -> Path:
        if save_to is None:
            return self.default_save_dir
        path = Path(save_to)
        if not path.is_absolute():
            path = self.base_dir / path
        return path

    def build_filename(self, url: str, title: str) -> str:
        parsed = urlparse(url)
        domain = slugify(parsed.netloc.replace("www.", ""), default="page")
        return f"{domain}-{slugify(title)}-{utc_now().strftime('%Y-%m-%d')}.md"

    def render_metadata(self, *, url: str, title: str) -> str:
        return "\n".join(
            [
                f"<!-- source: {url} -->",
                f"<!-- fetched: {format_utc_timestamp()} -->",
                f"<!-- title: {title} -->",
                "",
            ]
        )

    def extract_content(self, html: str, *, url: str) -> tuple[str, str]:
        if BeautifulSoup is None:
            return self.extract_without_bs4(html, url=url)

        soup = BeautifulSoup(html, "html.parser")
        for selector in [
            "script",
            "style",
            "noscript",
            "nav",
            "footer",
            "aside",
            "header",
            "form",
            "iframe",
            ".sidebar",
            ".advertisement",
            ".ads",
            "[role='navigation']",
            "[role='complementary']",
        ]:
            for node in soup.select(selector):
                node.decompose()

        title = self.extract_title(soup, fallback=url)
        root = soup.find("article") or soup.find("main") or soup.body or soup
        markdown = self.node_to_markdown(root)
        cleaned = self.normalize_markdown(markdown)
        if not cleaned:
            cleaned = self.extract_without_bs4(html, url=url)[1]
        return title, cleaned

    def extract_title(self, soup: Any, *, fallback: str) -> str:
        for selector in ["meta[property='og:title']", "title", "h1"]:
            node = soup.select_one(selector) if hasattr(soup, "select_one") else None
            if node is None:
                continue
            if getattr(node, "name", "") == "meta":
                content = node.get("content", "").strip()
            else:
                content = node.get_text(" ", strip=True)
            if content:
                return content
        return fallback

    def node_to_markdown(self, node: Any, *, depth: int = 0) -> str:
        if node is None:
            return ""
        if isinstance(node, NavigableString):
            return unescape(str(node))

        name = getattr(node, "name", "") or ""
        if name in {"script", "style", "noscript"}:
            return ""
        if name in {"article", "main", "section", "div", "body"}:
            return "\n\n".join(
                chunk
                for child in getattr(node, "children", [])
                for chunk in [self.node_to_markdown(child, depth=depth)]
                if chunk.strip()
            )
        if name in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            level = int(name[1]) if len(name) == 2 and name[1].isdigit() else 2
            return f"{'#' * level} {node.get_text(' ', strip=True)}"
        if name == "p":
            return node.get_text(" ", strip=True)
        if name in {"ul", "ol"}:
            lines: list[str] = []
            for index, item in enumerate(node.find_all("li", recursive=False), start=1):
                marker = f"{index}." if name == "ol" else "-"
                lines.append(f"{marker} {item.get_text(' ', strip=True)}")
            return "\n".join(lines)
        if name == "pre":
            text = node.get_text("\n", strip=False).strip("\n")
            return f"```text\n{text}\n```" if text else ""
        if name == "blockquote":
            text = node.get_text("\n", strip=True)
            return "\n".join(f"> {line}" for line in text.splitlines() if line.strip())
        if name in {"code", "span", "strong", "em", "a"}:
            return node.get_text(" ", strip=True)
        return "\n\n".join(
            chunk
            for child in getattr(node, "children", [])
            for chunk in [self.node_to_markdown(child, depth=depth + 1)]
            if chunk.strip()
        )

    def normalize_markdown(self, text: str) -> str:
        lines: list[str] = []
        blank_pending = False
        for raw_line in text.splitlines():
            line = re.sub(r"[ \t]+", " ", raw_line).strip()
            if not line:
                if lines:
                    blank_pending = True
                continue
            if blank_pending:
                lines.append("")
                blank_pending = False
            lines.append(line)
        return "\n".join(lines).strip()

    def extract_without_bs4(self, html: str, *, url: str) -> tuple[str, str]:
        title_match = re.search(r"<title[^>]*>(.*?)</title>", html, flags=re.I | re.S)
        title = unescape(title_match.group(1)).strip() if title_match else url
        cleaned = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", html)
        cleaned = re.sub(r"(?s)<[^>]+>", " ", cleaned)
        cleaned = unescape(cleaned)
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        return title, cleaned


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Fetch a web page into memory.")
    parser.add_argument("url", help="The URL to fetch.")
    parser.add_argument(
        "--save-to",
        default=DEFAULT_SAVE_DIR,
        help=f"Destination directory (default: {DEFAULT_SAVE_DIR}).",
    )
    parser.add_argument(
        "--stdout",
        action="store_true",
        help="Print the cleaned markdown instead of saving it.",
    )
    return parser


async def async_main() -> int:
    args = build_argument_parser().parse_args()
    fetcher = ContentFetcher(Path(__file__).resolve().parents[1])
    result = await fetcher.fetch(
        args.url,
        save_to=args.save_to,
        save=not bool(args.stdout),
    )
    if args.stdout:
        print(result.markdown, end="")
    elif result.saved_path is not None:
        print(f"Saved to {result.saved_path}")
        print(result.preview)
    return 0


def main() -> int:
    return asyncio.run(async_main())


if __name__ == "__main__":
    raise SystemExit(main())
