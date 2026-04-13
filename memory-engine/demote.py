#!/usr/bin/env python3
"""Move stale warm memory files into cold archived storage."""

from __future__ import annotations

import argparse
import logging
import re
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePath


LOGGER = logging.getLogger("memory_demoter")
DEFAULT_DAYS = 90
DEMOTED_COMMENT_RE = re.compile(
    r"^<!-- demoted: .*?, reason: stale \(\d+ days\) -->\n(?:\n)?", re.DOTALL
)
PROTECTED_RELATIVE_PATHS = {
    "memory/warm/current-priorities.md",
    "memory/warm/active-projects.md",
    "memory/warm/user.md",
}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def format_utc_timestamp(moment: datetime | None = None) -> str:
    value = moment or utc_now()
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class DemotionCandidate:
    path: Path
    stale_days: int


class MemoryDemoter:
    def __init__(self, base_dir: Path) -> None:
        self.base_dir = base_dir
        self.warm_dir = base_dir / "memory" / "warm"
        self.archived_dir = base_dir / "memory" / "cold" / "archived"

    def run(self, *, days: int = DEFAULT_DAYS, dry_run: bool = False) -> int:
        candidates = self.find_candidates(days)
        if not candidates:
            print("No warm memory files eligible for demotion.")
            return 0

        for candidate in candidates:
            message = self.demote_file(candidate, dry_run=dry_run)
            LOGGER.info(message)
            print(message)
        return 0

    def restore(self, restore_path: str) -> int:
        candidate = Path(restore_path)
        if not candidate.is_absolute():
            candidate = (self.base_dir / restore_path).resolve()

        archived_root = self.archived_dir.resolve()
        resolved_candidate = candidate.resolve()
        try:
            resolved_candidate.relative_to(archived_root)
        except ValueError as exc:
            raise ValueError(
                "Restore path must point inside memory/cold/archived/."
            ) from exc

        if not resolved_candidate.exists():
            raise FileNotFoundError(f"Archived file not found: {restore_path}")

        destination = self.warm_dir / resolved_candidate.name
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            raise FileExistsError(
                f"Warm memory already contains {destination.name}; refusing to overwrite it."
            )
        text = resolved_candidate.read_text(encoding="utf-8")
        cleaned = DEMOTED_COMMENT_RE.sub("", text, count=1)
        destination.write_text(cleaned, encoding="utf-8")
        resolved_candidate.unlink()
        message = f"Restored {destination.name} to warm memory"
        LOGGER.info(message)
        print(message)
        return 0

    def find_candidates(self, days: int) -> list[DemotionCandidate]:
        if days < 0:
            raise ValueError("--days must be zero or greater")

        self.warm_dir.mkdir(parents=True, exist_ok=True)
        now = utc_now().timestamp()
        candidates: list[DemotionCandidate] = []
        for path in sorted(self.warm_dir.glob("*.md")):
            if self.is_protected(path):
                continue
            stale_days = int((now - path.stat().st_mtime) / 86400)
            if stale_days > days:
                candidates.append(DemotionCandidate(path=path, stale_days=stale_days))
        return candidates

    def is_protected(self, path: Path) -> bool:
        try:
            relative = self.normalize_relative_path(
                path.resolve().relative_to(self.base_dir.resolve())
            )
        except ValueError:
            relative = self.normalize_relative_path(path)
        if relative in PROTECTED_RELATIVE_PATHS:
            return True

        first_lines = path.read_text(encoding="utf-8").splitlines()[:5]
        return any(line.strip() == "<!-- protected -->" for line in first_lines)

    @staticmethod
    def normalize_relative_path(path: PurePath) -> str:
        return path.as_posix()

    def demote_file(self, candidate: DemotionCandidate, *, dry_run: bool) -> str:
        destination = self.archived_dir / candidate.path.name
        if dry_run:
            return (
                f"Would demote {candidate.path.name} to {destination} "
                f"(stale {candidate.stale_days} days)"
            )

        self.archived_dir.mkdir(parents=True, exist_ok=True)
        original = candidate.path.read_text(encoding="utf-8")
        annotated = (
            f"<!-- demoted: {format_utc_timestamp()}, reason: stale ({candidate.stale_days} days) -->\n"
            f"{original}"
        )
        destination.write_text(annotated, encoding="utf-8")
        candidate.path.unlink()
        return f"Demoted {candidate.path.name} to cold storage (stale {candidate.stale_days} days)"


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Demote stale warm memory files.")
    parser.add_argument(
        "--days",
        type=int,
        default=DEFAULT_DAYS,
        help="Demote files older than this many days (default: 90).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be demoted without moving files.",
    )
    parser.add_argument(
        "--restore",
        help="Restore a file from memory/cold/archived/ back to memory/warm/.",
    )
    return parser


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    args = build_argument_parser().parse_args()
    demoter = MemoryDemoter(Path(__file__).resolve().parents[1])

    try:
        if args.restore:
            return demoter.restore(args.restore)
        return demoter.run(days=int(args.days), dry_run=bool(args.dry_run))
    except Exception as exc:
        LOGGER.error("Demotion failed: %s", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
