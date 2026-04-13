#!/usr/bin/env python3
"""Discord interface for the agent loop framework."""

from __future__ import annotations

import asyncio
import base64
import importlib.util
import json
import logging
import mimetypes
import os
import re
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiohttp
import discord

try:
    import yaml  # type: ignore
except ImportError:  # pragma: no cover - optional dependency fallback
    yaml = None


ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config.yaml"
SYSTEM_PROMPT_PATH = ROOT / "prompts" / "system.md"
SUMMARIZER_MODULE_PATH = ROOT / "memory-engine" / "summarizer.py"
SEARCH_MODULE_PATH = ROOT / "tools" / "search-memory.py"
FETCH_MODULE_PATH = ROOT / "tools" / "fetch-content.py"
YOUTUBE_MODULE_PATH = ROOT / "tools" / "youtube-transcript.py"
OCR_MODULE_PATH = ROOT / "tools" / "ingest-document.py"
STATE_DIR = ROOT / "state"
HOT_MEMORY_DIR = ROOT / "memory" / "hot"
LOG_PATH = ROOT / "logs" / "discord-bot.log"

DEFAULT_MANIFEST = "manifests/agent-full.md"
DEFAULT_HISTORY_LIMIT = 20
RECENT_HISTORY_KEEP = 8
RESERVED_RESPONSE_TOKENS = 4096
MAX_DISCORD_MESSAGE_LEN = 2000
OPENROUTER_REPLY_MAX_TOKENS = 4096
MAX_INLINE_IMAGE_ATTACHMENTS = 4
BOT_SYSTEM_APPENDIX = (
    "You are in a live conversation on Discord. Respond naturally. "
    "Do not output SESSION_COMPLETE unless explicitly asked to end the session."
)


def load_conversation_summarizer_class():
    spec = importlib.util.spec_from_file_location(
        "conversation_summarizer_module", SUMMARIZER_MODULE_PATH
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("Could not load memory-engine/summarizer.py.")

    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module.ConversationSummarizer


ConversationSummarizer = load_conversation_summarizer_class()


def load_search_provider_class():
    spec = importlib.util.spec_from_file_location(
        "memory_search_module", SEARCH_MODULE_PATH
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("Could not load tools/search-memory.py.")

    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module.KeywordSearchProvider


KeywordSearchProvider = load_search_provider_class()


def load_fetcher_class():
    spec = importlib.util.spec_from_file_location(
        "content_fetch_module", FETCH_MODULE_PATH
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("Could not load tools/fetch-content.py.")

    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module.ContentFetcher


def load_youtube_tool_class():
    spec = importlib.util.spec_from_file_location(
        "youtube_transcript_module", YOUTUBE_MODULE_PATH
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("Could not load tools/youtube-transcript.py.")

    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module.YouTubeTranscriptTool


def load_document_ingester_class():
    spec = importlib.util.spec_from_file_location(
        "ocr_ingest_module", OCR_MODULE_PATH
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("Could not load tools/ingest-document.py.")

    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module.DocumentIngester


ContentFetcher = load_fetcher_class()
YouTubeTranscriptTool = load_youtube_tool_class()
DocumentIngester = load_document_ingester_class()


def setup_logging() -> logging.Logger:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("discord_bot")
    logger.setLevel(logging.INFO)

    if not logger.handlers:
        handler = logging.FileHandler(LOG_PATH, encoding="utf-8")
        formatter = logging.Formatter(
            "%(asctime)s %(levelname)s %(name)s: %(message)s"
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)

    return logger


LOGGER = setup_logging()


def strip_inline_comment(value: str) -> str:
    in_single = False
    in_double = False
    result: list[str] = []

    for char in value:
        if char == "'" and not in_double:
            in_single = not in_single
        elif char == '"' and not in_single:
            in_double = not in_double
        elif char == "#" and not in_single and not in_double:
            break
        result.append(char)

    return "".join(result).strip()


def parse_simple_yaml(text: str) -> dict[str, Any]:
    """Small fallback parser for the project's simple nested config.yaml."""
    root: dict[str, Any] = {}
    stack: list[tuple[int, dict[str, Any]]] = [(-1, root)]

    for raw_line in text.splitlines():
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue

        indent = len(raw_line) - len(raw_line.lstrip(" "))
        line = raw_line.strip()
        if ":" not in line:
            continue

        key, value = line.split(":", 1)
        key = key.strip()
        value = strip_inline_comment(value.strip())

        while len(stack) > 1 and indent <= stack[-1][0]:
            stack.pop()

        parent = stack[-1][1]
        if not value:
            node: dict[str, Any] = {}
            parent[key] = node
            stack.append((indent, node))
            continue

        cleaned = value.strip()
        if (
            len(cleaned) >= 2
            and cleaned[0] in {"'", '"'}
            and cleaned[-1] == cleaned[0]
        ):
            cleaned = cleaned[1:-1]
        elif cleaned.lower() in {"true", "false"}:
            parent[key] = cleaned.lower() == "true"
            continue
        else:
            try:
                if "." in cleaned:
                    parent[key] = float(cleaned)
                else:
                    parent[key] = int(cleaned)
                continue
            except ValueError:
                pass

        parent[key] = cleaned

    return root


def load_config(config_path: Path) -> dict[str, Any]:
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


def estimate_tokens(text: str) -> int:
    if not text:
        return 0
    return (len(text) + 3) // 4


def compress_context(text: str) -> str:
    lines: list[str] = []
    pending_blank = False
    seen_text = False

    for raw_line in text.splitlines():
        line = raw_line

        if re.match(r"^[\s]*([*-][\s]*){3,}$", line):
            continue

        line = re.sub(r"^[\s]*#{1,6}[\s]*", "", line)
        line = re.sub(r"^[\s]*[-+*][\s]+", "", line)
        line = line.replace("**", "").replace("__", "").replace("*", "")
        line = re.sub(r"\s+", " ", line).strip()

        if not line:
            if seen_text and not pending_blank:
                pending_blank = True
            continue

        if pending_blank:
            lines.append("")
            pending_blank = False

        lines.append(line)
        seen_text = True

    return "\n".join(lines)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def format_utc_timestamp(moment: datetime | None = None) -> str:
    value = moment or utc_now()
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")


def format_current_datetime_context(moment: datetime | None = None) -> str:
    value = moment.astimezone() if moment is not None else datetime.now().astimezone()
    local_value = value.strftime("%Y-%m-%d %H:%M:%S %Z")
    utc_value = value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return (
        "Current date/time reference:\n"
        f"- Local: {local_value}\n"
        f"- UTC: {utc_value}"
    )


def relative_to_root(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def has_standalone_line(content: str, line: str) -> bool:
    return any(item == line for item in content.splitlines())


def format_size_bytes(total_bytes: int) -> str:
    if total_bytes < 1024:
        return f"~{total_bytes}B"
    return f"~{total_bytes / 1024:.1f}KB"


def format_month_day(value: datetime) -> str:
    return f"{value.strftime('%b')} {value.day}"


def format_date_range(start_date: str | None, end_date: str | None) -> str:
    if not start_date or not end_date:
        return "unknown date range"

    try:
        start = datetime.strptime(start_date, "%Y-%m-%d")
        end = datetime.strptime(end_date, "%Y-%m-%d")
    except ValueError:
        return f"{start_date}-{end_date}"

    if start.date() == end.date():
        return format_month_day(start)
    if start.year == end.year and start.month == end.month:
        return f"{format_month_day(start)}-{end.day}"
    return f"{format_month_day(start)}-{format_month_day(end)}"


def chunk_message(content: str, limit: int = MAX_DISCORD_MESSAGE_LEN) -> list[str]:
    if len(content) <= limit:
        return [content]

    chunks: list[str] = []
    remaining = content
    while remaining:
        if len(remaining) <= limit:
            if remaining:
                chunks.append(remaining)
            break

        newline_split_at = remaining.rfind("\n", 0, limit)
        space_split_at = remaining.rfind(" ", 0, limit)

        if newline_split_at > 0:
            split_at = newline_split_at
        elif space_split_at > 0:
            split_at = space_split_at
        elif newline_split_at == 0 or space_split_at == 0:
            # A delimiter at index 0 would produce an empty chunk and stall the loop.
            split_at = limit
        else:
            split_at = limit

        chunk = remaining[:split_at]
        if chunk:
            chunks.append(chunk)
        remaining = remaining[split_at:]

    return chunks


def coerce_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def resolve_manifest_path(manifest_name: str) -> Path:
    candidate = manifest_name.strip()
    if not candidate:
        candidate = DEFAULT_MANIFEST

    manifest_path = Path(candidate)
    if manifest_path.is_absolute():
        return manifest_path

    if "/" not in candidate and not candidate.endswith(".md"):
        manifest_path = Path("manifests") / f"{candidate}.md"
    elif "/" not in candidate and candidate.endswith(".md"):
        manifest_path = Path("manifests") / candidate

    return (ROOT / manifest_path).resolve()


@dataclass
class BudgetState:
    max_tokens: int
    reserved_for_response: int = RESERVED_RESPONSE_TOKENS
    used: int = 0
    hot: int = 0
    manifest: int = 0
    state: int = 0
    files_loaded: list[tuple[str, str, int]] = field(default_factory=list)
    files_skipped: list[str] = field(default_factory=list)

    @property
    def usable_tokens(self) -> int:
        return max(self.max_tokens - self.reserved_for_response, 0)

    @property
    def percent_used(self) -> float:
        usable = self.usable_tokens
        if usable <= 0:
            return 100.0 if self.used else 0.0
        return min(max((self.used / usable) * 100.0, 0.0), 100.0)

    def check_budget(self, additional_tokens: int) -> bool:
        return self.used + additional_tokens <= self.usable_tokens

    def add(self, category: str, name: str, tokens: int) -> None:
        self.used += tokens
        if category == "hot":
            self.hot += tokens
        elif category == "manifest":
            self.manifest += tokens
        elif category == "state":
            self.state += tokens
        self.files_loaded.append((category, name, tokens))


@dataclass
class ContextBuildResult:
    text: str
    budget: BudgetState
    warnings: list[str]


class ConversationManager:
    def __init__(self, max_history: int = DEFAULT_HISTORY_LIMIT, recent_keep: int = RECENT_HISTORY_KEEP) -> None:
        self.max_history = max_history
        self.recent_keep = recent_keep
        self.history: list[dict[str, str]] = []
        self.archived_summary = ""
        self.last_summarized_messages: list[dict[str, str]] = []

    def add_message(self, role: str, content: str) -> None:
        self.history.append({"role": role, "content": content})

    def get_messages(self) -> list[dict[str, str]]:
        return list(self.history)

    def clear(self) -> None:
        self.history = []
        self.archived_summary = ""
        self.last_summarized_messages = []

    def summarize_old(self) -> bool:
        if len(self.history) <= self.max_history:
            self.last_summarized_messages = []
            return False

        older_messages = self.history[:-self.recent_keep]
        self.history = self.history[-self.recent_keep :]
        self.last_summarized_messages = [dict(entry) for entry in older_messages]

        summary_lines: list[str] = []
        if self.archived_summary:
            summary_lines.append(self.archived_summary.strip())
            summary_lines.append("")
            summary_lines.append("Additional summarized exchanges:")

        for entry in older_messages:
            role = entry["role"].capitalize()
            condensed = re.sub(r"\s+", " ", entry["content"]).strip()
            if len(condensed) > 240:
                condensed = f"{condensed[:237].rstrip()}..."
            summary_lines.append(f"- {role}: {condensed}")

        self.archived_summary = "\n".join(summary_lines).strip()
        return True

    def pop_last_summarized_messages(self) -> list[dict[str, str]]:
        messages = list(self.last_summarized_messages)
        self.last_summarized_messages = []
        return messages

    def get_token_estimate(self) -> int:
        total = estimate_tokens(self.archived_summary) if self.archived_summary else 0
        for message in self.history:
            total += estimate_tokens(message["content"])
        return total


class ContextAssembler:
    def __init__(
        self,
        *,
        root: Path,
        max_context_tokens: int,
        hot_memory_cap: int,
        logger: logging.Logger | None = None,
    ) -> None:
        self.root = Path(root)
        self.max_context_tokens = max_context_tokens
        self.hot_memory_cap = hot_memory_cap
        self.hot_memory_dir = self.root / "memory" / "hot"
        self.state_dir = self.root / "state"
        self.logger = logger

    def compress(self, text: str) -> str:
        return compress_context(text)

    def assemble(self, manifest_path: Path) -> ContextBuildResult:
        warnings: list[str] = []
        budget = BudgetState(max_tokens=self.max_context_tokens)
        sections: list[str] = []
        resolved_manifest = manifest_path if manifest_path.is_absolute() else (self.root / manifest_path)

        if self.hot_memory_dir.exists():
            for hot_file in sorted(self.hot_memory_dir.glob("*.md")):
                self.load_context_file(
                    path=hot_file,
                    category="hot",
                    section_label=f"HOT MEMORY: {hot_file.name}",
                    budget=budget,
                    sections=sections,
                    warnings=warnings,
                    hot_cap=self.hot_memory_cap,
                )
        else:
            warnings.append(
                f"Missing hot memory directory: {relative_to_root(self.hot_memory_dir)}"
            )

        if not resolved_manifest.exists():
            warnings.append(f"Missing manifest file: {relative_to_root(resolved_manifest)}")
        else:
            for raw_line in resolved_manifest.read_text(encoding="utf-8").splitlines():
                line = raw_line.strip()
                if not line or line.startswith("#"):
                    continue

                manifest_file = (self.root / line).resolve()
                self.load_context_file(
                    path=manifest_file,
                    category="manifest",
                    section_label=f"MANIFEST: {manifest_file.name}",
                    budget=budget,
                    sections=sections,
                    warnings=warnings,
                )

        state_files = [
            ("PREVIOUS SESSION SUMMARY", self.state_dir / "session-summary.md"),
            ("BELIEFS (earned perspectives and lessons)", self.state_dir / "beliefs.md"),
            ("PROGRESS", self.state_dir / "progress.md"),
        ]
        for label, state_file in state_files:
            self.load_context_file(
                path=state_file,
                category="state",
                section_label=label,
                budget=budget,
                sections=sections,
                warnings=warnings,
            )

        context_text = "\n".join(sections).strip()
        if not context_text:
            context_text = "No context files were loaded."

        if self.logger is not None:
            for warning in warnings:
                self.logger.warning(warning)

        return ContextBuildResult(text=context_text, budget=budget, warnings=warnings)

    def load_context_file(
        self,
        *,
        path: Path,
        category: str,
        section_label: str,
        budget: BudgetState,
        sections: list[str],
        warnings: list[str],
        hot_cap: int | None = None,
    ) -> None:
        if not path.exists():
            warnings.append(f"Missing file: {relative_to_root(path)}")
            return

        raw_content = path.read_text(encoding="utf-8")
        compressed = self.compress(raw_content)
        tokens = estimate_tokens(compressed)

        if category == "hot" and hot_cap is not None and (budget.hot + tokens) > hot_cap:
            budget.files_skipped.append(f"hot:{path.name}")
            warnings.append(
                f"Skipped hot memory over cap: {path.name} ({tokens} tokens, cap {hot_cap})"
            )
            return

        if category != "hot" and not budget.check_budget(tokens):
            budget.files_skipped.append(f"{category}:{path.name}")
            warnings.append(
                f"Skipped due to context budget: {relative_to_root(path)} ({tokens} tokens)"
            )
            return

        sections.append(f"--- {section_label} ---\n{compressed}\n")
        budget.add(category, path.name, tokens)


class DiscordAgentClient(discord.Client):
    def __init__(self, config: dict[str, Any], **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.config = config
        self.model_id = str(
            get_config(config, "model.model_id", "deepseek/deepseek-chat")
        )
        self.api_base = str(
            get_config(config, "model.api_base", "https://openrouter.ai/api/v1")
        ).rstrip("/")
        self.max_context_tokens = coerce_int(
            get_config(config, "context.max_tokens", 128000), 128000
        )
        self.hot_memory_cap = coerce_int(
            get_config(config, "context.hot_memory_cap", 4000), 4000
        )
        self.history_limit = coerce_int(
            get_config(config, "discord.history_limit", DEFAULT_HISTORY_LIMIT),
            DEFAULT_HISTORY_LIMIT,
        )
        self.channel_id = int(os.environ["DISCORD_CHANNEL_ID"])
        self.api_key = os.environ["AGENT_LOOP_API_KEY"]
        self.summarizer = ConversationSummarizer(
            base_dir=ROOT,
            api_base=self.api_base,
            api_key=self.api_key,
            model_id=self.model_id,
        )
        search_provider_name = str(get_config(config, "search_provider", "keyword"))
        self.search_provider = self.build_search_provider(search_provider_name)
        self.content_fetcher = ContentFetcher(ROOT)
        self.youtube_tool = YouTubeTranscriptTool(ROOT)
        self.document_ingester = DocumentIngester(ROOT)
        self.active_manifest = resolve_manifest_path(DEFAULT_MANIFEST)
        self.context_assembler = ContextAssembler(
            root=ROOT,
            max_context_tokens=self.max_context_tokens,
            hot_memory_cap=self.hot_memory_cap,
            logger=LOGGER,
        )
        self.http_session: aiohttp.ClientSession | None = None
        self.lock = asyncio.Lock()
        self.reset_session_state()

    def reset_session_state(self) -> None:
        self.session_id = utc_now().strftime("%Y%m%d-%H%M%S")
        self.session_started_at = utc_now()
        self.conversation = ConversationManager(
            max_history=self.history_limit, recent_keep=RECENT_HISTORY_KEEP
        )
        self.pending_summary_save = False
        self.last_staged_archived_summary = ""
        self.total_prompt_tokens = 0
        self.total_completion_tokens = 0
        self.total_cost = 0.0
        self.last_budget = BudgetState(max_tokens=self.max_context_tokens)
        self.last_context_warnings: list[str] = []

    def build_search_provider(self, provider_name: str) -> Any:
        normalized = provider_name.strip().lower()
        if normalized in {"", "keyword"}:
            return KeywordSearchProvider(ROOT)

        LOGGER.warning(
            "Unknown search provider %r requested; falling back to keyword search.",
            provider_name,
        )
        return KeywordSearchProvider(ROOT)

    @property
    def archived_summary(self) -> str:
        return self.conversation.archived_summary

    async def setup_hook(self) -> None:
        self.http_session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=180)
        )

    async def close(self) -> None:
        if self.pending_summary_save and len(self.conversation.get_messages()) > 2:
            LOGGER.info("Auto-saving conversation on shutdown")
            try:
                await self.save_conversation_summary_to_staging()
            except Exception as exc:  # pragma: no cover - shutdown safety path
                LOGGER.exception("Failed to auto-save conversation on shutdown: %s", exc)
        if self.http_session is not None and not self.http_session.closed:
            await self.http_session.close()
        await super().close()

    async def on_ready(self) -> None:
        LOGGER.info(
            "Discord bot connected as %s; listening in channel %s using manifest %s",
            self.user,
            self.channel_id,
            relative_to_root(self.active_manifest),
        )

    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot:
            return
        if message.channel.id != self.channel_id:
            return

        content = (message.content or "").strip()
        image_attachments = self.get_image_attachments(message)
        if not content and not image_attachments:
            return

        if content.startswith("!"):
            await self.handle_command(message, content)
            return

        context_warnings: list[str] = []
        response_text = ""
        completion_notice = ""

        async with self.lock:
            augmented_content = await self.maybe_augment_message_with_url_content(content)
            history_content = self.build_user_history_content(
                augmented_content,
                image_attachments,
            )
            self.conversation.add_message("user", history_content)
            self.pending_summary_save = True
            await self.summarize_history_if_needed()

            async with message.channel.typing():
                try:
                    context_result = self.assemble_context()
                    self.last_budget = context_result.budget
                    self.last_context_warnings = context_result.warnings
                    context_warnings = list(context_result.warnings)

                    latest_user_content = await self.build_latest_user_content(
                        augmented_content,
                        image_attachments,
                    )
                    messages = self.build_openrouter_messages(
                        context_result.text,
                        latest_user_content=latest_user_content,
                    )
                    response_text, usage = await self.call_openrouter(messages)

                    self.conversation.add_message("assistant", response_text)
                    self.pending_summary_save = True
                    await self.summarize_history_if_needed()
                    self.record_usage(usage)
                    if has_standalone_line(response_text, "SESSION_COMPLETE"):
                        self.write_session_summary()
                        staged_path = await self.save_conversation_summary_to_staging()
                        if staged_path is not None:
                            completion_notice = "Session saved. Summary written to staging."
                        else:
                            completion_notice = (
                                "Session finished, but the summary could not be written to staging."
                            )
                except Exception as exc:  # pragma: no cover - network/runtime path
                    LOGGER.exception("Failed to process Discord message: %s", exc)
                    warning_message = (
                        "Sorry, I hit an error while talking to the agent loop API. "
                        "Please try again in a moment."
                    )
                    await self.send_chunked_message(message.channel, warning_message)
                    return

        if context_warnings:
            warning_prefix = "Context warnings while loading files:"
            warning_lines = "\n".join(f"- {item}" for item in context_warnings[:8])
            if len(context_warnings) > 8:
                warning_lines += (
                    f"\n- ...and {len(context_warnings) - 8} more warning(s)"
                )
            await self.send_chunked_message(
                message.channel, f"{warning_prefix}\n{warning_lines}"
            )

        await self.send_chunked_message(message.channel, response_text)
        if completion_notice:
            await self.send_chunked_message(message.channel, completion_notice)

    async def handle_command(self, message: discord.Message, content: str) -> None:
        command, _, argument = content.partition(" ")
        command = command.lower()
        argument = argument.strip()

        if command == "!reset":
            async with self.lock:
                self.reset_session_state()
                self.last_context_warnings = self.assemble_context().warnings
            await self.send_chunked_message(
                message.channel,
                f"Session reset. Active manifest: `{relative_to_root(self.active_manifest)}`.",
            )
            return

        if command == "!status":
            async with self.lock:
                context_result = self.assemble_context()
                status_text = self.build_status_message(context_result)
            await self.send_chunked_message(message.channel, status_text)
            return

        if command == "!manifest":
            if not argument:
                await self.send_chunked_message(
                    message.channel,
                    "Usage: `!manifest <name>` (example: `!manifest agent-brief`).",
                )
                return

            manifest_path = resolve_manifest_path(argument)
            if not manifest_path.exists():
                await self.send_chunked_message(
                    message.channel,
                    f"Manifest not found: `{relative_to_root(manifest_path)}`.",
                )
                return

            async with self.lock:
                self.active_manifest = manifest_path
                self.reset_session_state()
                self.last_context_warnings = self.assemble_context().warnings

            await self.send_chunked_message(
                message.channel,
                (
                    f"Switched to `{relative_to_root(self.active_manifest)}` "
                    "and reset the current Discord session."
                ),
            )
            return

        if command == "!save":
            async with self.lock:
                self.write_session_summary()
                staged_path = await self.save_conversation_summary_to_staging()
            if staged_path is not None:
                await self.send_chunked_message(
                    message.channel, "Session saved. Summary written to staging."
                )
            else:
                await self.send_chunked_message(
                    message.channel,
                    "Session saved, but the summary could not be written to staging.",
                )
            return

        if command == "!memory":
            stats = self.summarizer.get_staging_stats()
            await self.send_chunked_message(
                message.channel, self.format_memory_status(stats)
            )
            return

        if command == "!search":
            if not argument:
                await self.send_chunked_message(
                    message.channel,
                    "Usage: `!search <keywords>` (searches warm and cold memory).",
                )
                return

            try:
                results = self.search_provider.search(
                    argument,
                    limit=5,
                    include_warm=True,
                )
            except Exception as exc:
                LOGGER.exception("Memory search failed for query %r: %s", argument, exc)
                await self.send_chunked_message(
                    message.channel,
                    "Memory search failed. Please try again in a moment.",
                )
                return

            for result_message in self.build_search_messages(argument, results):
                await message.channel.send(result_message)
            return

        if command == "!fetch":
            if not argument:
                await self.send_chunked_message(
                    message.channel,
                    "Usage: `!fetch <url>`.",
                )
                return
            try:
                result = await self.content_fetcher.fetch(argument, save=True)
            except Exception as exc:
                LOGGER.exception("URL fetch failed for %r: %s", argument, exc)
                await self.send_chunked_message(
                    message.channel,
                    "Fetching that URL failed. Please try again in a moment.",
                )
                return

            saved_path = relative_to_root(result.saved_path) if result.saved_path else "unsaved"
            await self.send_chunked_message(
                message.channel,
                (
                    f"Fetched `{result.title}` and saved it to `{saved_path}`.\n"
                    f"{result.preview}"
                ),
            )
            return

        if command == "!youtube":
            if not argument:
                await self.send_chunked_message(
                    message.channel,
                    "Usage: `!youtube <url> [--summarize]`.",
                )
                return
            tokens = argument.split()
            summarize = "--summarize" in tokens
            url = next((token for token in tokens if token.startswith("http")), "")
            if not url:
                await self.send_chunked_message(
                    message.channel,
                    "Usage: `!youtube <url> [--summarize]`.",
                )
                return
            try:
                result = await self.youtube_tool.fetch_and_save(
                    url,
                    summarize=summarize,
                )
            except Exception as exc:
                LOGGER.exception("YouTube transcript extraction failed for %r: %s", url, exc)
                await self.send_chunked_message(
                    message.channel,
                    "YouTube transcript extraction failed. Please try again in a moment.",
                )
                return

            transcript_path = (
                relative_to_root(result.transcript_path)
                if result.transcript_path is not None
                else "unsaved"
            )
            extra = ""
            if result.summary_path is not None:
                extra = f"\nSummary saved to `{relative_to_root(result.summary_path)}`."
            await self.send_chunked_message(
                message.channel,
                (
                    f"Saved transcript for `{result.title}` to `{transcript_path}`.{extra}\n"
                    f"{result.preview}"
                ),
            )
            return

        if command == "!ocr":
            try:
                result_message = await self.handle_ocr_command(message, argument)
            except Exception as exc:
                LOGGER.exception("OCR command failed: %s", exc)
                await self.send_chunked_message(
                    message.channel,
                    "OCR failed. Please check the file/path and try again.",
                )
                return
            await self.send_chunked_message(message.channel, result_message)
            return

        await self.send_chunked_message(
            message.channel,
            "Unknown command. Supported commands: `!reset`, `!status`, `!manifest <name>`, `!save`, `!memory`, `!search <keywords>`, `!fetch <url>`, `!youtube <url> [--summarize]`, `!ocr`.",
        )

    def assemble_context(self) -> ContextBuildResult:
        return self.context_assembler.assemble(self.active_manifest)

    def build_system_prompt(self) -> str:
        if SYSTEM_PROMPT_PATH.exists():
            base_prompt = SYSTEM_PROMPT_PATH.read_text(encoding="utf-8").strip()
        else:
            base_prompt = (
                "You are an AI agent. Complete the task described in your context. "
                "When finished, output SESSION_COMPLETE on its own line."
            )
        return (
            f"{base_prompt}\n\n"
            f"{format_current_datetime_context()}\n\n"
            f"{BOT_SYSTEM_APPENDIX}"
        )

    def build_openrouter_messages(
        self,
        context_text: str,
        *,
        latest_user_content: str | list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": self.build_system_prompt()},
            {"role": "user", "content": context_text},
        ]

        if self.archived_summary:
            messages.append(
                {
                    "role": "assistant",
                    "content": f"Conversation summary so far:\n{self.archived_summary}",
                }
            )

        messages.extend(self.conversation.get_messages())
        if latest_user_content is not None:
            for index in range(len(messages) - 1, -1, -1):
                if messages[index].get("role") == "user":
                    messages[index] = {
                        "role": "user",
                        "content": latest_user_content,
                    }
                    break
        return messages

    async def call_openrouter(
        self, messages: list[dict[str, str]]
    ) -> tuple[str, dict[str, Any]]:
        if self.http_session is None:
            raise RuntimeError("HTTP session is not initialized.")

        payload = {
            "model": self.model_id,
            "messages": messages,
            "max_tokens": OPENROUTER_REPLY_MAX_TOKENS,
        }
        url = f"{self.api_base}/chat/completions"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }

        async with self.http_session.post(url, headers=headers, json=payload) as response:
            raw_body = await response.text()
            try:
                data = json.loads(raw_body)
            except json.JSONDecodeError as exc:
                raise RuntimeError(
                    f"OpenRouter returned non-JSON response ({response.status})."
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
            if response.status >= 400 or error_message:
                raise RuntimeError(error_message or f"HTTP {response.status}")

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
            content = content.strip()
            if not content:
                raise RuntimeError("OpenRouter returned an empty assistant response.")

            usage = data.get("usage", {}) if isinstance(data.get("usage"), dict) else {}
            return content, usage

    async def summarize_history_if_needed(self) -> None:
        if not self.conversation.summarize_old():
            return

        breadcrumb_messages = self.conversation.pop_last_summarized_messages()
        if not breadcrumb_messages:
            return

        breadcrumb_session_id = (
            f"{self.session_id}-mid-{utc_now().strftime('%H%M%S%f')}"
        )
        staged_path = await self.stage_messages_to_staging(
            breadcrumb_messages, breadcrumb_session_id
        )
        if staged_path is not None:
            self.last_staged_archived_summary = self.archived_summary
            LOGGER.info(
                "Wrote breadcrumb conversation summary to staging: %s",
                relative_to_root(staged_path),
            )

    def record_usage(self, usage: dict[str, Any]) -> None:
        prompt_tokens = coerce_int(usage.get("prompt_tokens"), 0)
        completion_tokens = coerce_int(usage.get("completion_tokens"), 0)
        self.total_prompt_tokens += prompt_tokens
        self.total_completion_tokens += completion_tokens
        self.total_cost += (prompt_tokens * 0.0000005) + (completion_tokens * 0.000001)

    def build_status_message(self, context_result: ContextBuildResult) -> str:
        assembled_messages = self.build_openrouter_messages(context_result.text)
        estimated_tokens = sum(
            estimate_tokens(message["content"]) for message in assembled_messages
        )
        warning_count = len(context_result.warnings)

        lines = [
            "Current session status:",
            f"- Active manifest: `{relative_to_root(self.active_manifest)}`",
            f"- History messages kept: {len(self.conversation.get_messages())}",
            f"- Has summarized older history: {'yes' if bool(self.archived_summary) else 'no'}",
            f"- Estimated prompt tokens: {estimated_tokens}",
            (
                f"- Context budget used: {context_result.budget.used} / "
                f"{context_result.budget.usable_tokens} ({context_result.budget.percent_used:.1f}%)"
            ),
            f"- Loaded files: {len(context_result.budget.files_loaded)}",
            f"- Skipped files: {len(context_result.budget.files_skipped)}",
            f"- Context warnings: {warning_count}",
            f"- Accumulated usage: prompt={self.total_prompt_tokens}, completion={self.total_completion_tokens}",
            f"- Estimated cost: ${self.total_cost:.6f}",
        ]
        return "\n".join(lines)

    def build_manual_summary_body(self) -> str:
        lines: list[str] = []
        if self.archived_summary:
            lines.append(self.archived_summary.strip())

        history = self.conversation.get_messages()
        if history:
            if lines:
                lines.append("")
                lines.append("Recent exchanges:")
            for entry in history:
                role = entry["role"].capitalize()
                lines.append(f"- {role}: {entry['content'].strip()}")

        if not lines:
            lines.append("No Discord conversation has been recorded yet.")

        return "\n".join(lines).strip()

    def build_messages_for_summary(self) -> list[dict[str, str]]:
        messages: list[dict[str, str]] = []
        if (
            self.archived_summary
            and self.archived_summary != self.last_staged_archived_summary
        ):
            messages.append(
                {
                    "role": "system",
                    "content": (
                        "Earlier conversation summary before truncation:\n"
                        f"{self.archived_summary}"
                    ),
                }
            )
        messages.extend(self.conversation.get_messages())
        return messages

    async def stage_messages_to_staging(
        self, messages: list[dict[str, str]], session_id: str
    ) -> Path | None:
        summary = await self.summarizer.summarize_conversation(messages, session_id)
        if not summary:
            return None
        return self.summarizer.write_summary(summary, session_id)

    async def save_conversation_summary_to_staging(self) -> Path | None:
        staged_path = await self.stage_messages_to_staging(
            self.build_messages_for_summary(), self.session_id
        )
        if staged_path is not None:
            self.pending_summary_save = False
            self.last_staged_archived_summary = self.archived_summary
            LOGGER.info(
                "Wrote full conversation summary to staging: %s",
                relative_to_root(staged_path),
            )
        return staged_path

    def format_memory_status(self, stats: dict[str, Any]) -> str:
        count = int(stats.get("count", 0))
        if count <= 0:
            return "No conversation summaries pending promotion."

        date_range = format_date_range(
            stats.get("oldest_date"), stats.get("newest_date")
        )
        return (
            f"{count} conversation summaries pending promotion "
            f"({date_range}, {format_size_bytes(int(stats.get('total_bytes', 0)))})"
        )

    def build_search_messages(
        self, query: str, results: list[Any]
    ) -> list[str]:
        if not results:
            return [f"No memory matches found for `{query}`."]

        messages = [f"Top memory matches for `{query}`:"]
        for result in results[:5]:
            relative_path = relative_to_root(Path(result.path))
            excerpt = self.format_search_excerpt(result)
            message = (
                f"`{relative_path}` ({int(result.match_count)} matches)\n"
                f"```text\n{excerpt}\n```"
            )
            if len(message) > MAX_DISCORD_MESSAGE_LEN:
                allowed = max(200, MAX_DISCORD_MESSAGE_LEN - len(message) + len(excerpt) - 20)
                trimmed_excerpt = excerpt[:allowed].rstrip()
                if len(trimmed_excerpt) < len(excerpt):
                    trimmed_excerpt += "\n..."
                message = (
                    f"`{relative_path}` ({int(result.match_count)} matches)\n"
                    f"```text\n{trimmed_excerpt}\n```"
                )
            messages.append(message)
        return messages

    def format_search_excerpt(self, result: Any) -> str:
        lines: list[str] = []
        line_budget = 12
        for snippet_index, snippet in enumerate(result.snippets):
            if snippet_index > 0 and lines and len(lines) < line_budget:
                lines.append("...")

            for line_number, content in snippet.lines:
                prefix = ">" if int(line_number) in snippet.matched_lines else " "
                compact = re.sub(r"\s+", " ", str(content)).strip()
                if len(compact) > 160:
                    compact = compact[:157].rstrip() + "..."
                lines.append(f"{prefix} {int(line_number)}: {compact}")
                if len(lines) >= line_budget:
                    break
            if len(lines) >= line_budget:
                break

        if not lines:
            return "(no excerpt available)"
        return "\n".join(lines)

    def should_auto_fetch_url_context(self, content: str) -> bool:
        lowered = content.lower()
        if "http://" not in lowered and "https://" not in lowered:
            return False
        cues = [
            "look at this",
            "look at the link",
            "look at this link",
            "read this",
            "check this",
            "summarize this",
            "can you read",
            "can you check",
            "can you look",
            "what is in",
            "what's in",
            "whats in",
        ]
        return "?" in lowered or any(cue in lowered for cue in cues)

    async def maybe_augment_message_with_url_content(self, content: str) -> str:
        if not self.should_auto_fetch_url_context(content):
            return content

        urls = re.findall(r"https?://[^\s<>()]+", content)
        if not urls:
            return content

        url = urls[0].rstrip(".,)")
        try:
            result = await self.content_fetcher.fetch(url, save=False)
        except Exception as exc:
            LOGGER.warning("Auto-fetch for conversation URL failed: %s", exc)
            return content

        excerpt = result.preview
        if not excerpt:
            return content
        return (
            f"{content}\n\n"
            f"[Fetched link context: {result.title}]\n"
            f"{excerpt}"
        )

    def get_image_attachments(self, message: discord.Message) -> list[Any]:
        attachments = getattr(message, "attachments", None) or []
        return [
            attachment
            for attachment in attachments
            if self.is_image_attachment(attachment)
        ]

    def is_image_attachment(self, attachment: Any) -> bool:
        content_type = str(getattr(attachment, "content_type", "") or "").lower()
        if content_type.startswith("image/"):
            return True
        filename = str(getattr(attachment, "filename", "") or "")
        return Path(filename).suffix.lower() in {".png", ".jpg", ".jpeg", ".gif", ".webp"}

    def build_user_history_content(
        self, content: str, image_attachments: list[Any]
    ) -> str:
        attachment_note = self.describe_image_attachments(image_attachments)
        if content and attachment_note:
            return f"{content}\n\n{attachment_note}"
        if attachment_note:
            return attachment_note
        return content

    def describe_image_attachments(self, image_attachments: list[Any]) -> str:
        if not image_attachments:
            return ""
        names = [
            str(getattr(attachment, "filename", "image"))
            for attachment in image_attachments[:MAX_INLINE_IMAGE_ATTACHMENTS]
        ]
        label = "image attachment" if len(names) == 1 else "image attachments"
        note = f"[User included {label}: {', '.join(names)}]"
        if len(image_attachments) > MAX_INLINE_IMAGE_ATTACHMENTS:
            remaining = len(image_attachments) - MAX_INLINE_IMAGE_ATTACHMENTS
            note += f" (+{remaining} more)"
        return note

    async def build_latest_user_content(
        self, content: str, image_attachments: list[Any]
    ) -> str | list[dict[str, Any]]:
        if not image_attachments:
            return content

        parts: list[dict[str, Any]] = [
            {
                "type": "text",
                "text": content.strip() or "Please analyze the attached image(s).",
            }
        ]

        for attachment in image_attachments[:MAX_INLINE_IMAGE_ATTACHMENTS]:
            data_url = await self.fetch_attachment_data_url(attachment)
            parts.append(
                {
                    "type": "image_url",
                    "image_url": {"url": data_url},
                }
            )
        return parts

    async def fetch_attachment_data_url(self, attachment: Any) -> str:
        data: bytes | None = None
        if hasattr(attachment, "read"):
            data = await attachment.read()
        elif hasattr(attachment, "save"):
            with tempfile.NamedTemporaryFile(delete=False) as temp_file:
                temp_path = Path(temp_file.name)
            try:
                await attachment.save(temp_path)
                data = temp_path.read_bytes()
            finally:
                temp_path.unlink(missing_ok=True)
        else:
            if self.http_session is None:
                raise RuntimeError("HTTP session is not initialized.")
            async with self.http_session.get(attachment.url) as response:
                response.raise_for_status()
                data = await response.read()

        media_type = self.resolve_attachment_media_type(attachment)
        encoded = base64.b64encode(data or b"").decode("ascii")
        return f"data:{media_type};base64,{encoded}"

    def resolve_attachment_media_type(self, attachment: Any) -> str:
        content_type = str(getattr(attachment, "content_type", "") or "").lower()
        if content_type.startswith("image/"):
            return content_type
        filename = str(getattr(attachment, "filename", "") or "")
        guessed, _ = mimetypes.guess_type(filename)
        if guessed and guessed.startswith("image/"):
            return guessed
        return "image/png"

    async def handle_ocr_command(
        self, message: discord.Message, argument: str
    ) -> str:
        temp_path: Path | None = None
        source_path: Path | None = None

        if getattr(message, "attachments", None):
            attachment = message.attachments[0]
            suffix = Path(getattr(attachment, "filename", "attachment.bin")).suffix
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as temp_file:
                temp_path = Path(temp_file.name)
            if hasattr(attachment, "save"):
                await attachment.save(temp_path)
            else:
                if self.http_session is None:
                    raise RuntimeError("HTTP session is not initialized.")
                async with self.http_session.get(attachment.url) as response:
                    response.raise_for_status()
                    temp_path.write_bytes(await response.read())
            source_path = temp_path
        elif argument:
            source_path = Path(argument).expanduser()
            if not source_path.is_absolute():
                source_path = (ROOT / source_path).resolve()
        else:
            raise ValueError("Usage: !ocr <file-path> or attach a file to the message.")

        try:
            results = await self.document_ingester.ingest_path(
                source_path,
                use_local=False,
                dry_run=False,
            )
        finally:
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)

        total_pages = sum(result.pages for result in results)
        if not results:
            return "OCR found nothing to process."
        first_path = (
            relative_to_root(results[0].saved_path)
            if results[0].saved_path is not None
            else "unsaved"
        )
        return (
            f"OCR complete: {len(results)} file(s), {total_pages} page(s). "
            f"First result saved to `{first_path}`."
        )

    def write_session_summary(self) -> Path:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        context_result = self.assemble_context()
        self.last_budget = context_result.budget
        self.last_context_warnings = context_result.warnings
        assistant_message_count = len(
            [
                message
                for message in self.conversation.get_messages()
                if message["role"] == "assistant"
            ]
        )

        last_output = ""
        for entry in reversed(self.conversation.get_messages()):
            if entry["role"] == "assistant":
                last_output = entry["content"].strip()
                break
        if not last_output:
            last_output = "No assistant response has been recorded yet."

        conversation_summary = self.build_manual_summary_body()
        summary_path = STATE_DIR / "session-summary.md"
        summary_path.write_text(
            "\n".join(
                [
                    "# Session Summary",
                    f"- **Session ID**: {self.session_id}",
                    f"- **Completed**: {format_utc_timestamp()}",
                    f"- **Iterations**: {assistant_message_count}",
                    "- **Rotations**: 0",
                    "- **Exit reason**: manual_save",
                    f"- **Estimated cost**: ${self.total_cost:.6f}",
                    "",
                    "## Last Output",
                    last_output[:2000],
                    "",
                    "## CONTINUE FROM HERE",
                    conversation_summary,
                    "",
                    "## Context Budget",
                    (
                        f"- **Tokens used**: {context_result.budget.used} / "
                        f"{context_result.budget.max_tokens} ({context_result.budget.percent_used:.1f}%)"
                    ),
                    f"- **Hot memory**: {context_result.budget.hot} tokens",
                    f"- **Manifest files**: {context_result.budget.manifest} tokens",
                    f"- **State**: {context_result.budget.state} tokens",
                    "- **Files loaded**:",
                    *(
                        [
                            f"- [{category}] {name}: {tokens} tokens"
                            for category, name, tokens in context_result.budget.files_loaded
                        ]
                        or ["- none"]
                    ),
                    "- **Files skipped**:",
                    *(
                        [f"- {item}" for item in context_result.budget.files_skipped]
                        or ["- none"]
                    ),
                    "",
                    "## State",
                    "- **Project**: default",
                    f"- **State directory**: {STATE_DIR}",
                    f"- **Progress**: {STATE_DIR / 'progress.md'}",
                    f"- **Beliefs**: {STATE_DIR / 'beliefs.md'}",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        return summary_path

    async def send_chunked_message(
        self, channel: discord.abc.Messageable, content: str
    ) -> None:
        for chunk in chunk_message(content):
            await channel.send(chunk)


def validate_environment() -> None:
    required_vars = ["AGENT_LOOP_API_KEY", "DISCORD_BOT_TOKEN", "DISCORD_CHANNEL_ID"]
    missing = [name for name in required_vars if not os.environ.get(name)]
    if missing:
        raise RuntimeError(
            f"Missing required environment variables: {', '.join(sorted(missing))}"
        )

    try:
        int(os.environ["DISCORD_CHANNEL_ID"])
    except ValueError as exc:
        raise RuntimeError("DISCORD_CHANNEL_ID must be a numeric Discord channel ID.") from exc


def main() -> None:
    validate_environment()
    config = load_config(CONFIG_PATH)

    intents = discord.Intents.default()
    intents.message_content = True

    client = DiscordAgentClient(config=config, intents=intents)
    client.run(os.environ["DISCORD_BOT_TOKEN"], log_handler=None)


if __name__ == "__main__":
    main()
