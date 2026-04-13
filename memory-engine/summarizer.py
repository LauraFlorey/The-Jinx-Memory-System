#!/usr/bin/env python3
"""Conversation summary writer for the memory promotion pipeline."""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiohttp


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def format_utc_timestamp(moment: datetime | None = None) -> str:
    value = moment or utc_now()
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")


class ConversationSummarizer:
    def __init__(self, base_dir, api_base, api_key, model_id) -> None:
        self.base_dir = Path(base_dir)
        self.api_base = str(api_base).rstrip("/")
        self.api_key = str(api_key)
        self.model_id = str(model_id)
        self.memory_engine_dir = self.base_dir / "memory-engine"
        self.staging_dir = self.memory_engine_dir / "staging"
        self.logger = logging.getLogger("conversation_summarizer")

    async def summarize_conversation(
        self, messages: list[dict[str, str]], session_id: str | None = None
    ) -> dict[str, Any]:
        if not messages:
            return {}

        resolved_session_id = session_id or utc_now().strftime("%Y%m%d-%H%M%S")
        resolved_timestamp = format_utc_timestamp()
        transcript = self._render_messages(messages)
        system_prompt = (
            "You are writing a compact conversation summary for a memory promotion "
            "pipeline.\n\n"
            "Be terse and factual.\n"
            "Do not invent information that was not in the conversation.\n"
            "Preserve uncertainty exactly as expressed.\n"
            "Focus on extractable facts and decisions, not feelings.\n"
            "If there were no decisions, write \"None.\" in that section.\n"
            "If there was no new information, write \"None.\" in that section.\n"
            "Use this exact markdown structure:\n"
            "## Session: {session_id}\n"
            "Date: {ISO timestamp}\n\n"
            "### What Was Discussed\n"
            "<brief overview>\n\n"
            "### Decisions Made\n"
            "- ...\n\n"
            "### New Information Learned\n"
            "TOPIC: fact\n\n"
            "### Unresolved Questions\n"
            "- ...\n\n"
            "### Mood/Context\n"
            "<brief note>"
        )
        user_prompt = (
            f"Session ID: {resolved_session_id}\n"
            f"Timestamp: {resolved_timestamp}\n\n"
            "Conversation transcript:\n"
            f"{transcript}"
        )
        payload = {
            "model": self.model_id,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "max_tokens": 1200,
        }

        try:
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
                    raw_body = await response.text()
                    data = self._parse_response(response.status, raw_body)

            content = self._extract_response_content(data)
            if not content:
                self.logger.warning(
                    "Skipping conversation summarization because the model returned no content."
                )
                return {}

            markdown = self._normalize_summary_markdown(
                content=content,
                session_id=resolved_session_id,
                timestamp=resolved_timestamp,
            )
            parsed = self._parse_summary_markdown(markdown)
            parsed["session_id"] = resolved_session_id
            parsed["date"] = parsed.get("date") or resolved_timestamp
            parsed["markdown"] = markdown
            return parsed
        except Exception as exc:
            self.logger.warning(
                "Skipping conversation summarization after API or response error: %s",
                exc,
            )
            return {}

    def write_summary(self, summary: dict[str, Any], session_id: str) -> Path | None:
        markdown = str(summary.get("markdown", "")).strip()
        if not markdown:
            return None

        self.staging_dir.mkdir(parents=True, exist_ok=True)
        iso_date = str(summary.get("date") or format_utc_timestamp())
        date_part = iso_date[:10] if re.match(r"^\d{4}-\d{2}-\d{2}", iso_date) else utc_now().strftime("%Y-%m-%d")
        target_path = self.staging_dir / f"summary-{date_part}-{session_id}.md"
        target_path.write_text(f"{markdown}\n", encoding="utf-8")
        return target_path

    def get_staging_stats(self) -> dict[str, Any]:
        self.staging_dir.mkdir(parents=True, exist_ok=True)
        files = sorted(self.staging_dir.glob("summary-*.md"))
        total_bytes = sum(path.stat().st_size for path in files)
        dates: list[str] = []

        for path in files:
            match = re.match(r"summary-(\d{4}-\d{2}-\d{2})-", path.name)
            if match:
                dates.append(match.group(1))

        return {
            "count": len(files),
            "oldest_date": min(dates) if dates else None,
            "newest_date": max(dates) if dates else None,
            "total_bytes": total_bytes,
        }

    def _parse_response(self, status_code: int, raw_body: str) -> dict[str, Any]:
        try:
            data = json.loads(raw_body)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"OpenRouter returned non-JSON response ({status_code})."
            ) from exc

        if not isinstance(data, dict):
            raise RuntimeError(
                f"OpenRouter returned unexpected JSON type: {type(data).__name__}."
            )

        error_payload = data.get("error")
        error_message = ""
        if isinstance(error_payload, dict):
            raw_error_message = error_payload.get("message")
            if isinstance(raw_error_message, str):
                error_message = raw_error_message
        elif isinstance(error_payload, str):
            error_message = error_payload

        if status_code >= 400 or error_message:
            raise RuntimeError(error_message or f"HTTP {status_code}")

        return data

    def _extract_response_content(self, data: dict[str, Any]) -> str:
        choices = data.get("choices")
        if not isinstance(choices, list) or not choices:
            raise RuntimeError("OpenRouter response did not include any choices.")

        first_choice = choices[0]
        if not isinstance(first_choice, dict):
            raise RuntimeError("OpenRouter returned an invalid choice payload.")

        message = first_choice.get("message")
        if not isinstance(message, dict):
            raise RuntimeError("OpenRouter response is missing a message object.")

        content = message.get("content", "")
        if not isinstance(content, str):
            raise RuntimeError("OpenRouter response content must be a string.")

        return content.strip()

    def _render_messages(self, messages: list[dict[str, str]]) -> str:
        rendered: list[str] = []
        for entry in messages:
            role = str(entry.get("role", "unknown")).strip() or "unknown"
            content = str(entry.get("content", "")).strip()
            if not content:
                continue
            rendered.append(f"{role.upper()}:\n{content}")
        return "\n\n".join(rendered)

    def _normalize_summary_markdown(
        self, *, content: str, session_id: str, timestamp: str
    ) -> str:
        body = content.strip()
        if not body.startswith("## Session:"):
            body = f"## Session: {session_id}\nDate: {timestamp}\n\n{body}"

        lines = body.splitlines()
        if len(lines) < 2 or not lines[1].startswith("Date:"):
            lines.insert(1, f"Date: {timestamp}")
            body = "\n".join(lines)

        return body.strip()

    def _parse_summary_markdown(self, markdown: str) -> dict[str, Any]:
        session_match = re.search(r"^## Session:\s*(.+)$", markdown, flags=re.MULTILINE)
        date_match = re.search(r"^Date:\s*(.+)$", markdown, flags=re.MULTILINE)
        return {
            "session_heading": session_match.group(1).strip() if session_match else "",
            "date": date_match.group(1).strip() if date_match else "",
            "what_was_discussed": self._extract_section(markdown, "What Was Discussed"),
            "decisions_made": self._extract_section(markdown, "Decisions Made"),
            "new_information_learned": self._extract_section(markdown, "New Information Learned"),
            "unresolved_questions": self._extract_section(markdown, "Unresolved Questions"),
            "mood_context": self._extract_section(markdown, "Mood/Context"),
        }

    def _extract_section(self, markdown: str, title: str) -> str:
        pattern = rf"^### {re.escape(title)}\n(.*?)(?=^### |\Z)"
        match = re.search(pattern, markdown, flags=re.MULTILINE | re.DOTALL)
        return match.group(1).strip() if match else ""
