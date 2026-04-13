#!/usr/bin/env python3
"""Keyword search across warm and cold memory files."""

from __future__ import annotations

import argparse
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence


SUPPORTED_SUFFIXES = {".md", ".txt"}
DEFAULT_CONTEXT_LINES = 2
DEFAULT_LIMIT = 10


@dataclass
class SearchSnippet:
    start_line: int
    end_line: int
    lines: list[tuple[int, str]]
    matched_lines: set[int]


@dataclass
class SearchResult:
    path: Path
    match_count: int
    snippets: list[SearchSnippet]


class SearchProvider(ABC):
    @abstractmethod
    def search(
        self,
        query: str,
        limit: int = DEFAULT_LIMIT,
        *,
        directory: Path | None = None,
        include_warm: bool = False,
    ) -> list[SearchResult]:
        raise NotImplementedError


class KeywordSearchProvider(SearchProvider):
    def __init__(self, base_dir: Path) -> None:
        self.base_dir = base_dir
        self.cold_dir = base_dir / "memory" / "cold"
        self.warm_dir = base_dir / "memory" / "warm"

    def search(
        self,
        query: str,
        limit: int = DEFAULT_LIMIT,
        *,
        directory: Path | None = None,
        include_warm: bool = False,
    ) -> list[SearchResult]:
        terms = compile_terms(query)
        if not terms or limit <= 0:
            return []

        roots = self.resolve_roots(directory=directory, include_warm=include_warm)
        results: list[SearchResult] = []
        for path in self.iter_candidate_files(roots):
            result = self.search_file(path, terms)
            if result is not None:
                results.append(result)

        results.sort(
            key=lambda item: (
                -item.match_count,
                self.relative_to_base(item.path),
            )
        )
        return results[:limit]

    def resolve_roots(
        self, *, directory: Path | None, include_warm: bool
    ) -> list[Path]:
        if directory is not None:
            resolved = directory
            if not resolved.is_absolute():
                resolved = (self.base_dir / resolved).resolve()
            else:
                resolved = resolved.resolve()
            try:
                resolved.relative_to(self.base_dir.resolve())
            except ValueError as exc:
                raise ValueError("Search directory must be inside the repository.") from exc
            return [resolved]

        roots = [self.cold_dir]
        if include_warm:
            roots.append(self.warm_dir)
        return roots

    def iter_candidate_files(self, roots: Sequence[Path]) -> Iterable[Path]:
        seen: set[Path] = set()
        for root in roots:
            if not root.exists():
                continue
            for path in root.rglob("*"):
                if (
                    path.is_file()
                    and path.suffix.lower() in SUPPORTED_SUFFIXES
                    and path not in seen
                ):
                    seen.add(path)
                    yield path

    def search_file(
        self, path: Path, terms: Sequence[re.Pattern[str]]
    ) -> SearchResult | None:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None

        lines = text.splitlines()
        if not lines:
            return None

        matched_lines: list[int] = []
        match_count = 0
        for index, line in enumerate(lines, start=1):
            line_hits = sum(len(pattern.findall(line)) for pattern in terms)
            if line_hits > 0:
                match_count += line_hits
                matched_lines.append(index)

        if match_count <= 0:
            return None

        snippets = build_snippets(lines, matched_lines, context_lines=DEFAULT_CONTEXT_LINES)
        return SearchResult(path=path, match_count=match_count, snippets=snippets)

    def relative_to_base(self, path: Path) -> str:
        try:
            return path.resolve().relative_to(self.base_dir.resolve()).as_posix()
        except ValueError:
            return path.as_posix()


def compile_terms(query: str) -> list[re.Pattern[str]]:
    terms: list[re.Pattern[str]] = []
    seen: set[str] = set()
    for raw_term in query.split():
        term = raw_term.strip()
        normalized = term.lower()
        if not term or normalized in seen:
            continue
        seen.add(normalized)
        terms.append(re.compile(re.escape(term), re.IGNORECASE))
    return terms


def build_snippets(
    lines: Sequence[str], matched_lines: Sequence[int], *, context_lines: int
) -> list[SearchSnippet]:
    windows: list[tuple[int, int]] = []
    total_lines = len(lines)

    for line_number in matched_lines:
        start = max(1, line_number - context_lines)
        end = min(total_lines, line_number + context_lines)
        if windows and start <= windows[-1][1] + 1:
            windows[-1] = (windows[-1][0], max(windows[-1][1], end))
        else:
            windows.append((start, end))

    matched_set = set(matched_lines)
    snippets: list[SearchSnippet] = []
    for start, end in windows:
        snippet_lines = [(line_no, lines[line_no - 1]) for line_no in range(start, end + 1)]
        snippets.append(
            SearchSnippet(
                start_line=start,
                end_line=end,
                lines=snippet_lines,
                matched_lines={line_no for line_no in range(start, end + 1) if line_no in matched_set},
            )
        )
    return snippets


def format_search_results(
    results: Sequence[SearchResult], *, base_dir: Path, show_context: bool = True
) -> str:
    if not results:
        return "No matches found."

    blocks: list[str] = []
    for result in results:
        try:
            relative_path = result.path.resolve().relative_to(base_dir.resolve()).as_posix()
        except ValueError:
            relative_path = result.path.as_posix()

        lines: list[str] = [f"=== {relative_path} ({result.match_count} matches) ==="]
        if show_context:
            for snippet in result.snippets:
                for line_number, content in snippet.lines:
                    prefix = ">" if line_number in snippet.matched_lines else " "
                    lines.append(f"{prefix} {line_number}: {content}")
                lines.append("")
        else:
            for snippet in result.snippets:
                for line_number, content in snippet.lines:
                    if line_number in snippet.matched_lines:
                        lines.append(f"Line {line_number}: {content}")
        blocks.append("\n".join(lines).rstrip())
    return "\n\n".join(blocks)


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Search cold and warm memory files.")
    parser.add_argument("query", help="Keywords to search for.")
    parser.add_argument(
        "--limit",
        type=int,
        default=DEFAULT_LIMIT,
        help=f"Maximum number of matching files to return (default: {DEFAULT_LIMIT}).",
    )
    parser.add_argument(
        "--dir",
        dest="directory",
        help="Search only within this directory, relative to the repo root or absolute.",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Search both cold and warm memory. Default is cold memory only.",
    )
    return parser


def main() -> int:
    args = build_argument_parser().parse_args()
    base_dir = Path(__file__).resolve().parents[1]
    provider = KeywordSearchProvider(base_dir)

    try:
        directory = Path(args.directory) if args.directory else None
        results = provider.search(
            args.query,
            limit=int(args.limit),
            directory=directory,
            include_warm=bool(args.all),
        )
    except Exception as exc:
        print(f"Search failed: {exc}")
        return 1

    print(format_search_results(results, base_dir=base_dir))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
