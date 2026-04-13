#!/usr/bin/env python3
"""Extract YouTube transcripts and optionally summarize them."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import aiohttp

try:
    from pytube import YouTube  # type: ignore
except ImportError:  # pragma: no cover - optional dependency
    YouTube = None

try:
    from youtube_transcript_api import YouTubeTranscriptApi  # type: ignore
except ImportError:  # pragma: no cover - optional dependency
    YouTubeTranscriptApi = None


DEFAULT_SAVE_DIR = "memory/cold/reference/youtube"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def format_utc_timestamp(moment: datetime | None = None) -> str:
    value = moment or utc_now()
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")


def slugify(value: str, *, default: str = "video") -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return normalized or default


def parse_simple_yaml(text: str) -> dict[str, Any]:
    root: dict[str, Any] = {}
    stack: list[tuple[int, dict[str, Any]]] = [(-1, root)]
    for raw_line in text.splitlines():
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue
        indent = len(raw_line) - len(raw_line.lstrip(" "))
        key, sep, value = raw_line.strip().partition(":")
        if not sep:
            continue
        while len(stack) > 1 and indent <= stack[-1][0]:
            stack.pop()
        parent = stack[-1][1]
        value = value.strip()
        if not value:
            node: dict[str, Any] = {}
            parent[key] = node
            stack.append((indent, node))
            continue
        cleaned = value.split("#", 1)[0].strip().strip('"').strip("'")
        parent[key] = cleaned
    return root


def load_config(config_path: Path) -> dict[str, Any]:
    try:
        import yaml  # type: ignore
    except ImportError:
        yaml = None
    raw_text = config_path.read_text(encoding="utf-8")
    if yaml is not None:
        loaded = yaml.safe_load(raw_text) or {}
        if isinstance(loaded, dict):
            return loaded
    return parse_simple_yaml(raw_text)


def get_config(config: dict[str, Any], dotted_key: str, default: Any = None) -> Any:
    current: Any = config
    for part in dotted_key.split("."):
        if not isinstance(current, dict) or part not in current:
            return default
        current = current[part]
    return current


def extract_video_id(url: str) -> str:
    parsed = urlparse(url)
    if parsed.hostname in {"youtu.be"}:
        return parsed.path.lstrip("/")
    if parsed.path == "/watch":
        return parse_qs(parsed.query).get("v", [""])[0]
    if parsed.path.startswith("/shorts/"):
        return parsed.path.split("/", 2)[2]
    return ""


@dataclass
class TranscriptResult:
    url: str
    title: str
    channel: str
    transcript_text: str
    transcript_path: Path | None
    summary_text: str = ""
    summary_path: Path | None = None

    @property
    def preview(self) -> str:
        source = self.summary_text or self.transcript_text
        compact = re.sub(r"\s+", " ", source).strip()
        return compact[:500]


class YouTubeTranscriptTool:
    def __init__(self, base_dir: Path) -> None:
        self.base_dir = base_dir
        self.default_save_dir = base_dir / DEFAULT_SAVE_DIR
        self.config = load_config(base_dir / "config.yaml")
        self.api_base = str(
            get_config(self.config, "model.api_base", "https://openrouter.ai/api/v1")
        ).rstrip("/")
        self.model_id = str(
            get_config(self.config, "model.model_id", "deepseek/deepseek-chat")
        )
        self.api_key = os.environ.get("AGENT_LOOP_API_KEY", "")

    async def fetch_and_save(
        self, url: str, *, summarize: bool = False, save_to: str | Path | None = None
    ) -> TranscriptResult:
        video_id = extract_video_id(url)
        if not video_id:
            raise ValueError("Could not determine a YouTube video ID from the URL.")
        title, channel = self.fetch_video_metadata(url)
        transcript_segments = self.fetch_transcript_segments(video_id)
        transcript_text = clean_transcript_segments(transcript_segments)
        if not transcript_text.strip():
            raise RuntimeError("No transcript text was extracted from the video.")

        save_dir = self.resolve_save_dir(save_to)
        save_dir.mkdir(parents=True, exist_ok=True)
        filename_stem = f"{slugify(channel, default='youtube')}-{slugify(title)}-{utc_now().strftime('%Y-%m-%d')}"
        transcript_path = save_dir / f"{filename_stem}.md"
        transcript_path.write_text(
            self.render_transcript_markdown(
                url=url,
                title=title,
                channel=channel,
                transcript_text=transcript_text,
            ),
            encoding="utf-8",
        )

        summary_text = ""
        summary_path: Path | None = None
        if summarize:
            summary_text = await self.summarize_transcript(
                title=title, channel=channel, transcript_text=transcript_text
            )
            summary_path = save_dir / f"{filename_stem}-summary.md"
            summary_path.write_text(summary_text.rstrip() + "\n", encoding="utf-8")

        return TranscriptResult(
            url=url,
            title=title,
            channel=channel,
            transcript_text=transcript_text,
            transcript_path=transcript_path,
            summary_text=summary_text,
            summary_path=summary_path,
        )

    def resolve_save_dir(self, save_to: str | Path | None) -> Path:
        if save_to is None:
            return self.default_save_dir
        path = Path(save_to)
        if not path.is_absolute():
            path = self.base_dir / path
        return path

    def fetch_video_metadata(self, url: str) -> tuple[str, str]:
        if YouTube is None:
            return ("YouTube Video", "unknown-channel")
        yt = YouTube(url)
        title = (yt.title or "YouTube Video").strip()
        channel = (yt.author or "unknown-channel").strip()
        return title, channel

    def fetch_transcript_segments(self, video_id: str) -> list[dict[str, Any]]:
        if YouTubeTranscriptApi is None:
            raise RuntimeError(
                "youtube-transcript-api is not installed. Add it to requirements.txt."
            )

        try:
            listed = YouTubeTranscriptApi.list_transcripts(video_id)
        except Exception as exc:
            raise RuntimeError(f"Could not list transcripts for {video_id}: {exc}") from exc

        for chooser in [
            lambda pool: pool.find_transcript(["en"]),
            lambda pool: pool.find_generated_transcript(["en"]),
        ]:
            try:
                transcript = chooser(listed)
                fetched = transcript.fetch()
                return [dict(item) for item in fetched]
            except Exception:
                continue

        try:
            transcript = next(iter(listed))
            fetched = transcript.fetch()
            return [dict(item) for item in fetched]
        except Exception as exc:
            raise RuntimeError(f"No usable transcript found for {video_id}: {exc}") from exc

    def render_transcript_markdown(
        self, *, url: str, title: str, channel: str, transcript_text: str
    ) -> str:
        header = "\n".join(
            [
                f"<!-- source: {url} -->",
                f"<!-- title: {title} -->",
                f"<!-- channel: {channel} -->",
                f"<!-- fetched: {format_utc_timestamp()} -->",
                "",
                f"# {title}",
                "",
                f"- Channel: {channel}",
                f"- Source: {url}",
                "",
                "## Transcript",
                "",
            ]
        )
        return header + transcript_text.rstrip() + "\n"

    async def summarize_transcript(
        self, *, title: str, channel: str, transcript_text: str
    ) -> str:
        if not self.api_key:
            raise RuntimeError("AGENT_LOOP_API_KEY is required for --summarize.")
        prompt = (
            f"Video title: {title}\n"
            f"Channel: {channel}\n\n"
            "Summarize this YouTube transcript using the exact markdown structure below.\n"
            "If relevance to current work is unclear, say so.\n\n"
            f"## Video: {title}\n"
            "### Key Points\n"
            "- ...\n\n"
            "### Notable Quotes or Claims\n"
            "- ...\n\n"
            "### Relevance (if context available)\n"
            "- ...\n\n"
            "Transcript:\n"
            f"{transcript_text[:50000]}"
        )
        payload = {
            "model": self.model_id,
            "messages": [
                {
                    "role": "system",
                    "content": "You summarize transcripts tersely and faithfully.",
                },
                {"role": "user", "content": prompt},
            ],
            "max_tokens": 1400,
        }
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=180)
        ) as session:
            async with session.post(
                f"{self.api_base}/chat/completions",
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {self.api_key}",
                },
                json=payload,
            ) as response:
                raw = await response.text()
                if response.status >= 400:
                    raise RuntimeError(f"Transcript summary failed: HTTP {response.status}")
        data = json.loads(raw)
        choices = data.get("choices") or []
        if not choices or not isinstance(choices[0], dict):
            raise RuntimeError("Transcript summary returned no choices.")
        message = choices[0].get("message") or {}
        content = message.get("content", "")
        if not isinstance(content, str) or not content.strip():
            raise RuntimeError("Transcript summary returned empty content.")
        return content.strip() + "\n"


def clean_transcript_segments(segments: list[dict[str, Any]]) -> str:
    paragraphs: list[str] = []
    buffer: list[str] = []
    char_budget = 0

    for segment in segments:
        text = str(segment.get("text", "")).strip()
        if not text:
            continue
        if re.fullmatch(r"\[(music|applause|laughter)\]", text, flags=re.I):
            continue
        text = re.sub(r"\s+", " ", text).strip()
        buffer.append(text)
        char_budget += len(text)
        if char_budget >= 220 or text.endswith((".", "!", "?", ":")):
            paragraphs.append(" ".join(buffer).strip())
            buffer = []
            char_budget = 0

    if buffer:
        paragraphs.append(" ".join(buffer).strip())
    return "\n\n".join(item for item in paragraphs if item)


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Extract a YouTube transcript into memory.")
    parser.add_argument("url", help="The YouTube URL to process.")
    parser.add_argument(
        "--summarize",
        action="store_true",
        help="Also summarize the transcript with the configured LLM.",
    )
    parser.add_argument(
        "--save-to",
        default=DEFAULT_SAVE_DIR,
        help=f"Destination directory (default: {DEFAULT_SAVE_DIR}).",
    )
    return parser


async def async_main() -> int:
    args = build_argument_parser().parse_args()
    tool = YouTubeTranscriptTool(Path(__file__).resolve().parents[1])
    result = await tool.fetch_and_save(
        args.url, summarize=bool(args.summarize), save_to=args.save_to
    )
    if result.transcript_path is not None:
        print(f"Saved transcript to {result.transcript_path}")
    if result.summary_path is not None:
        print(f"Saved summary to {result.summary_path}")
    print(result.preview)
    return 0


def main() -> int:
    return asyncio.run(async_main())


if __name__ == "__main__":
    raise SystemExit(main())
