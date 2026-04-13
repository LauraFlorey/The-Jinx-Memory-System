#!/usr/bin/env python3
#!/usr/bin/env python3
"""Promote staged conversation summaries into warm memory files."""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import shlex
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiohttp  # pyright: ignore[reportMissingImports]


LOGGER = logging.getLogger("memory_promoter")
MAX_UPDATES_PER_RUN = 10
NOTEBOOK_TOKEN_LIMIT = 3000
NOTEBOOK_SECTION_TITLES = {
    "interesting": "Things I Find Interesting",
    "question": "Questions I'm Sitting With",
    "pattern": "Patterns I've Noticed",
    "belief": "Beliefs Taking Shape",
}
STOPWORDS = {
    "a", "an", "about", "across", "after", "again", "all", "also", "am", "and",
    "are", "as", "at", "be", "been", "being", "by", "came", "conversation",
    "conversations", "could", "does", "for", "found", "from", "have", "idea",
    "ideas", "if", "in", "is", "into", "just", "looking", "more", "most", "not",
    "notice", "noticed", "of", "on", "or", "recent", "reviewing", "something",
    "that", "the", "their", "them", "there", "these", "thing", "this",
    "through", "what", "when", "where", "which", "with", "work", "your",
}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def format_utc_timestamp(moment: datetime | None = None) -> str:
    value = moment or utc_now()
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")


def estimate_tokens(text: str) -> int:
    if not text:
        return 0
    return (len(text) + 3) // 4


def parse_simple_yaml(text: str) -> dict[str, Any]:
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
        value = value.strip()

        while len(stack) > 1 and indent <= stack[-1][0]:
            stack.pop()

        parent = stack[-1][1]
        if not value:
            node: dict[str, Any] = {}
            parent[key] = node
            stack.append((indent, node))
            continue

        cleaned = value.split("#", 1)[0].strip().strip('"').strip("'")
        if cleaned.lower() in {"true", "false"}:
            parent[key] = cleaned.lower() == "true"
            continue

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


def load_env_value(base_dir: Path, name: str) -> str:
    existing = os.environ.get(name, "")
    if existing:
        return existing

    env_path = base_dir / ".env"
    if not env_path.exists():
        return ""

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        normalized_key = key.strip()
        if normalized_key.startswith("export "):
            normalized_key = normalized_key[len("export ") :].strip()
        if normalized_key != name:
            continue
        cleaned = value.strip().strip('"').strip("'")
        if cleaned:
            os.environ.setdefault(name, cleaned)
            return cleaned
    return ""


def get_config(config: dict[str, Any], dotted_key: str, default: Any = None) -> Any:
    current: Any = config
    for part in dotted_key.split("."):
        if not isinstance(current, dict) or part not in current:
            return default
        current = current[part]
    return current


@dataclass
class UpdateInstruction:
    path: str
    action: str
    section: str | None = None
    content: str = ""
    old: str = ""
    new: str = ""


@dataclass
class NewFileInstruction:
    path: str
    content: str


@dataclass
class NotebookObservation:
    category: str
    observation: str


class PromotionPlan:
    def __init__(
        self,
        updates: list[UpdateInstruction] | None = None,
        new_files: list[NewFileInstruction] | None = None,
        no_updates_needed: bool = False,
    ) -> None:
        self.updates = updates or []
        self.new_files = new_files or []
        self.no_updates_needed = no_updates_needed

    @property
    def total_actions(self) -> int:
        return len(self.updates) + len(self.new_files)


class MemoryPromoter:
    def __init__(self, base_dir: Path) -> None:
        self.base_dir = base_dir
        self.memory_engine_dir = base_dir / "memory-engine"
        self.staging_dir = self.memory_engine_dir / "staging"
        self.processed_dir = self.memory_engine_dir / "processed"
        self.backups_dir = self.memory_engine_dir / "backups"
        self.reports_dir = self.memory_engine_dir / "reports"
        self.config = load_config(base_dir / "config.yaml")
        self.api_base = str(
            os.environ.get("AGENT_LOOP_API_BASE")
            or get_config(self.config, "model.api_base", "https://openrouter.ai/api/v1")
        ).rstrip("/")
        self.model_id = str(
            os.environ.get("AGENT_LOOP_MODEL_ID")
            or get_config(self.config, "model.model_id", "deepseek/deepseek-chat")
        )
        self.api_key = load_env_value(base_dir, "AGENT_LOOP_API_KEY")
        self.backed_up_paths: set[Path] = set()
        self.notebook_path = base_dir / "memory" / "warm" / "agent-notebook.md"
        self.beliefs_path = base_dir / "state" / "beliefs.md"
        self.beliefs_tools_path = base_dir / "beliefs-tools.sh"

    async def run(
        self, *, dry_run: bool = False, summary_file: str | None = None
    ) -> int:
        summary_paths = self.resolve_summary_paths(summary_file)
        if not summary_paths:
            print("No staged summaries pending promotion.")
            return 0

        plan = await self.build_promotion_plan(summary_paths)
        report_lines = self.render_report_header(summary_paths, dry_run)
        action_messages: list[str] = []

        if not plan.no_updates_needed and plan.total_actions > 0:
            limited_updates = plan.updates[:MAX_UPDATES_PER_RUN]
            limited_new_files = max(0, MAX_UPDATES_PER_RUN - len(limited_updates))
            limited_new_file_actions = plan.new_files[:limited_new_files]
            skipped_count = plan.total_actions - (
                len(limited_updates) + len(limited_new_file_actions)
            )

            if dry_run:
                preview_lines = self.render_plan_preview(
                    limited_updates, limited_new_file_actions
                )
                if preview_lines:
                    print("Proposed memory updates:")
                    report_lines.append("## Proposed Memory Updates")
                    report_lines.extend(preview_lines)
            else:
                for update in limited_updates:
                    message = self.apply_update(update)
                    action_messages.append(message)
                    LOGGER.info(message)

                for new_file in limited_new_file_actions:
                    message = self.apply_new_file(new_file)
                    action_messages.append(message)
                    LOGGER.info(message)

            if skipped_count > 0:
                skipped_line = (
                    f"Skipped {skipped_count} additional proposed updates beyond the "
                    f"{MAX_UPDATES_PER_RUN} per-run limit."
                )
                if dry_run:
                    print(skipped_line)
                    report_lines.append(skipped_line)
                else:
                    action_messages.append(
                        f"Flagged {skipped_count} additional proposed updates for manual review after hitting the per-run limit."
                    )

        elif dry_run:
            report_lines.append("No warm-memory updates were needed.")

        try:
            observations = await self.build_self_observations(summary_paths)
        except Exception as exc:
            warning_message = f"Skipped self-observation step after error: {exc}"
            LOGGER.warning(warning_message)
            observations = []
            report_lines.append(warning_message)

        if dry_run:
            observation_preview = self.render_observation_preview(observations)
            if observation_preview:
                print("Proposed notebook observations:")
                report_lines.append("## Proposed Notebook Observations")
                report_lines.extend(observation_preview)
        else:
            notebook_messages = await self.apply_notebook_observations(observations)
            for message in notebook_messages:
                action_messages.append(message)
                LOGGER.info(message)

        if not dry_run and not action_messages:
            action_messages.append("No updates were needed.")
            print("No updates needed.")

        report_lines.extend(action_messages)
        self.write_report(report_lines)
        if not dry_run:
            self.archive_processed_summaries(summary_paths)
            for line in action_messages:
                print(line)
        return 0

    def resolve_summary_paths(self, summary_file: str | None) -> list[Path]:
        self.staging_dir.mkdir(parents=True, exist_ok=True)
        if summary_file:
            candidate = Path(summary_file)
            if not candidate.is_absolute():
                candidate = (self.base_dir / summary_file).resolve()
            if candidate.exists():
                return [candidate]
            raise FileNotFoundError(f"Summary file not found: {summary_file}")
        return sorted(self.staging_dir.glob("summary-*.md"))

    async def build_promotion_plan(self, summary_paths: list[Path]) -> PromotionPlan:
        prompt = self.build_promotion_prompt(summary_paths)
        response_text = await self.call_openrouter(
            prompt,
            system_prompt="You generate structured memory update plans.",
            max_tokens=1800,
        )
        return self.parse_promotion_plan(response_text)

    def build_promotion_prompt(self, summary_paths: list[Path]) -> str:
        tracked_files = [
            "memory/warm/current-priorities.md",
            "memory/warm/active-projects.md",
            "memory/warm/user.md",
        ]
        file_blocks: list[str] = []
        for rel_path in tracked_files:
            path = self.base_dir / rel_path
            contents = path.read_text(encoding="utf-8") if path.exists() else "[missing]"
            file_blocks.append(f"FILE: {rel_path}\n{contents}")

        summary_blocks = [
            f"SUMMARY FILE: {path.name}\n{path.read_text(encoding='utf-8')}"
            for path in summary_paths
        ]

        system_prompt = (
            "You are updating warm memory files for an AI memory system.\n\n"
            "Read the conversation summaries and the current warm memory files.\n"
            "Only promote facts that were explicitly stated, not inferred.\n"
            "Do not remove information, only add or update.\n"
            "Preserve uncertainty markers.\n"
            "Keep entries concise. This is memory, not narrative.\n"
            "If nothing needs updating, output: NO_UPDATES_NEEDED\n\n"
            "Output update plans in exactly this format:\n"
            "UPDATE: memory/warm/active-projects.md\n"
            "SECTION: ProjectName\n"
            "ACTION: append\n"
            "CONTENT: Brief factual update about the project.\n\n"
            "UPDATE: memory/warm/current-priorities.md\n"
            "ACTION: replace_line\n"
            "OLD: 2. ProjectName - short description, current status\n"
            "NEW: 2. ProjectName - short description, updated status\n\n"
            "NEW_FILE: memory/warm/topic-notes.md\n"
            "CONTENT: Notes from recent research or discussion on a topic..."
        )

        joined_files = "\n\n".join(file_blocks)
        joined_summaries = "\n\n".join(summary_blocks)
        return (
            f"{system_prompt}\n\n"
            f"CURRENT WARM MEMORY FILES:\n\n{joined_files}\n\n"
            f"RECENT CONVERSATION SUMMARIES:\n\n{joined_summaries}"
        )

    def build_self_observation_prompt(self, summary_paths: list[Path]) -> str:
        summary_blocks = [
            f"SUMMARY FILE: {path.name}\n{path.read_text(encoding='utf-8')}"
            for path in summary_paths
        ]
        return "RECENT CONVERSATION SUMMARIES:\n\n" + "\n\n".join(summary_blocks)

    async def build_self_observations(
        self, summary_paths: list[Path]
    ) -> list[NotebookObservation]:
        prompt = self.build_self_observation_prompt(summary_paths)
        system_prompt = (
            "You are the agent, reviewing your recent conversations. You're looking for "
            "things that YOU found interesting, patterns YOU noticed, or questions "
            "that came up for YOU - not just facts about the user.\n\n"
            "Read these conversation summaries. Then consider:\n"
            "- Did anything surprise you or challenge your assumptions?\n"
            "- Did you notice a pattern across multiple conversations?\n"
            "- Is there something you're curious about that you want to explore?\n"
            "- Did you form an opinion about an approach, a technology, or an idea?\n"
            "- Did you learn something about how you work best?\n\n"
            "If you have observations, output them in this format:\n"
            "NOTEBOOK: category | observation\n"
            "Where category is one of: interesting, question, pattern, belief\n\n"
            "If nothing stands out, output: NO_OBSERVATIONS\n\n"
            "Be genuine. Don't manufacture observations for the sake of having them."
        )
        response_text = await self.call_openrouter(
            prompt, system_prompt=system_prompt, max_tokens=1000
        )
        return self.parse_notebook_observations(response_text)

    async def call_openrouter(
        self, prompt: str, *, system_prompt: str, max_tokens: int
    ) -> str:
        if not self.api_key:
            raise RuntimeError("AGENT_LOOP_API_KEY is required to run the promoter.")

        payload = {
            "model": self.model_id,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt},
            ],
            "max_tokens": max_tokens,
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
                raw_body = await response.text()
                data = self.parse_response(response.status, raw_body)
        return self.extract_response_content(data)

    def parse_response(self, status_code: int, raw_body: str) -> dict[str, Any]:
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

    def extract_response_content(self, data: dict[str, Any]) -> str:
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

    def parse_promotion_plan(self, text: str) -> PromotionPlan:
        stripped = text.strip()
        if not stripped or stripped == "NO_UPDATES_NEEDED":
            return PromotionPlan(no_updates_needed=True)

        updates: list[UpdateInstruction] = []
        new_files: list[NewFileInstruction] = []
        blocks = re.split(r"\n(?=UPDATE:|NEW_FILE:)", stripped)
        for raw_block in blocks:
            block = raw_block.strip()
            if not block:
                continue
            if block.startswith("UPDATE:"):
                parsed = self.parse_update_block(block)
                if parsed is not None:
                    updates.append(parsed)
            elif block.startswith("NEW_FILE:"):
                parsed_new = self.parse_new_file_block(block)
                if parsed_new is not None:
                    new_files.append(parsed_new)
        return PromotionPlan(updates=updates, new_files=new_files)

    def parse_notebook_observations(self, text: str) -> list[NotebookObservation]:
        stripped = text.strip()
        if not stripped or stripped == "NO_OBSERVATIONS":
            return []

        observations: list[NotebookObservation] = []
        for raw_line in stripped.splitlines():
            line = raw_line.strip()
            if not line.startswith("NOTEBOOK:"):
                continue
            payload = line[len("NOTEBOOK:") :].strip()
            category, separator, observation = payload.partition("|")
            if separator != "|":
                continue
            normalized_category = category.strip().lower()
            normalized_observation = observation.strip()
            if (
                normalized_category in NOTEBOOK_SECTION_TITLES
                and normalized_observation
            ):
                observations.append(
                    NotebookObservation(
                        category=normalized_category,
                        observation=normalized_observation,
                    )
                )
        return observations

    def parse_update_block(self, block: str) -> UpdateInstruction | None:
        path = self.extract_block_value(block, "UPDATE")
        action = self.extract_block_value(block, "ACTION")
        if not path or not action:
            return None
        return UpdateInstruction(
            path=path,
            section=self.extract_block_value(block, "SECTION"),
            action=action,
            content=self.extract_block_value(block, "CONTENT") or "",
            old=self.extract_block_value(block, "OLD") or "",
            new=self.extract_block_value(block, "NEW") or "",
        )

    def parse_new_file_block(self, block: str) -> NewFileInstruction | None:
        path = self.extract_block_value(block, "NEW_FILE")
        content = self.extract_block_value(block, "CONTENT")
        if not path or not content:
            return None
        return NewFileInstruction(path=path, content=content)

    def extract_block_value(self, block: str, key: str) -> str | None:
        pattern = rf"^{re.escape(key)}:\s*(.*?)(?=^\w+?:|\Z)"
        match = re.search(pattern, block, flags=re.MULTILINE | re.DOTALL)
        if not match:
            return None
        return match.group(1).strip()

    def apply_update(self, update: UpdateInstruction) -> str:
        target_path = self.resolve_warm_memory_path(update.path)
        if target_path is None:
            return f"Skipped unsafe update target: {update.path}"
        if not target_path.exists():
            return f"Skipped missing file for update: {update.path}"

        self.backup_file(target_path)
        original = target_path.read_text(encoding="utf-8")
        updated = original

        if update.action == "append":
            updated = self.append_to_section(original, update.section, update.content)
        elif update.action == "replace_line":
            updated = self.replace_line(original, update.old, update.new)
            if updated == original:
                return f"Skipped replace_line because the target line was not found in {target_path.name}"
        else:
            return f"Skipped unsupported action '{update.action}' for {update.path}"

        target_path.write_text(updated, encoding="utf-8")
        return self.describe_update(update, target_path)

    def apply_new_file(self, new_file: NewFileInstruction) -> str:
        target_path = self.resolve_warm_memory_path(new_file.path)
        if target_path is None:
            return f"Skipped unsafe new file target: {new_file.path}"
        if target_path.exists():
            return f"Skipped NEW_FILE because the file already exists: {new_file.path}"

        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_text(new_file.content.strip() + "\n", encoding="utf-8")
        return f"Created {target_path.name}: added new warm memory file"

    async def apply_notebook_observations(
        self, observations: list[NotebookObservation]
    ) -> list[str]:
        if not observations:
            return []

        notebook_text = (
            self.notebook_path.read_text(encoding="utf-8")
            if self.notebook_path.exists()
            else "# Agent's Notebook\n"
        )
        original_notebook = notebook_text
        messages: list[str] = []
        notebook_changed = False

        for observation in observations:
            if not self.is_valid_notebook_observation(observation.observation):
                messages.append(
                    "Skipped invalid notebook observation: no meaningful content"
                )
                continue

            if self.has_similar_notebook_observation(notebook_text, observation.observation):
                messages.append(
                    f"Skipped duplicate notebook observation: {self.describe_topic(observation.observation)}"
                )
                continue

            section_title = NOTEBOOK_SECTION_TITLES[observation.category]
            notebook_text = self.append_to_section(
                notebook_text,
                section_title,
                f"- {observation.observation}",
            )
            notebook_changed = True
            topic = self.describe_topic(observation.observation)
            messages.append(
                f"Agent notebook updated: added observation about {topic}"
            )

            if observation.category == "belief":
                belief_message = self.add_belief_entry(observation.observation)
                messages.append(belief_message)

        if notebook_changed:
            self.notebook_path.parent.mkdir(parents=True, exist_ok=True)
            if self.notebook_path.exists():
                self.backup_file(self.notebook_path)
            if estimate_tokens(notebook_text) > NOTEBOOK_TOKEN_LIMIT:
                notebook_text, consolidation_message = await self.consolidate_notebook(
                    notebook_text
                )
                messages.append(consolidation_message)
            self.notebook_path.write_text(notebook_text.rstrip() + "\n", encoding="utf-8")

        return messages

    def has_similar_notebook_observation(
        self, notebook_text: str, candidate: str
    ) -> bool:
        candidate_norm = self.normalize_text(candidate)
        if not candidate_norm:
            return False

        candidate_keywords = self.extract_keywords(candidate)
        for line in notebook_text.splitlines():
            stripped = line.strip()
            if not stripped.startswith("- "):
                continue
            existing = stripped[2:].strip()
            existing_norm = self.normalize_text(existing)
            if not existing_norm:
                continue
            if candidate_norm == existing_norm:
                return True
            if candidate_norm in existing_norm or existing_norm in candidate_norm:
                return True

            existing_keywords = self.extract_keywords(existing)
            if candidate_keywords and existing_keywords:
                overlap = candidate_keywords & existing_keywords
                union = candidate_keywords | existing_keywords
                if union and (len(overlap) / len(union)) >= 0.6:
                    return True
        return False

    def normalize_text(self, value: str) -> str:
        return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9\s]", " ", value.lower())).strip()

    def is_valid_notebook_observation(self, observation: str) -> bool:
        normalized = self.normalize_text(observation)
        if not normalized:
            return False

        tokens = normalized.split()
        return any(token not in STOPWORDS for token in tokens)

    def extract_keywords(self, value: str) -> set[str]:
        normalized = self.normalize_text(value)
        return {
            token
            for token in normalized.split()
            if len(token) >= 4 and token not in STOPWORDS
        }

    def describe_topic(self, observation: str) -> str:
        keywords = list(self.extract_keywords(observation))
        if keywords:
            return " ".join(keywords[:3])
        words = observation.split()
        if words:
            return " ".join(words[:3])
        return "recent conversations"

    def add_belief_entry(self, observation: str) -> str:
        title = self.derive_belief_title(observation)
        what_happened = f"While reviewing recent conversations, I noticed: {observation}"
        why = "It stood out as a recurring or meaningful perspective in my own reflections."
        what_i_learned = observation

        if self.beliefs_path.exists():
            self.backup_file(self.beliefs_path)

        command = " && ".join(
            [
                f"source {shlex.quote(str(self.beliefs_tools_path))}",
                f"BELIEFS={shlex.quote(str(self.beliefs_path))}",
                "add_belief "
                + " ".join(
                    shlex.quote(part)
                    for part in [title, what_happened, why, what_i_learned]
                ),
            ]
        )
        subprocess.run(
            ["bash", "-lc", command],
            check=True,
            cwd=str(self.base_dir),
        )
        return f"Belief added from notebook observation: {title}"

    def derive_belief_title(self, observation: str) -> str:
        words = re.findall(r"[A-Za-z0-9']+", observation)
        if not words:
            return "Notebook observation"
        return " ".join(words[:8]).strip().capitalize()

    async def consolidate_notebook(self, notebook_text: str) -> tuple[str, str]:
        prompt = (
            "Your notebook is getting long. Review your entries and consolidate - "
            "merge related observations, remove anything that feels stale, and keep "
            "what still resonates. Preserve your genuine voice.\n\n"
            f"{notebook_text}"
        )
        try:
            consolidated = await self.call_openrouter(
                prompt,
                system_prompt=(
                    "You are the agent, refining your own notebook while preserving your voice."
                ),
                max_tokens=1600,
            )
        except Exception as exc:
            LOGGER.warning("Notebook consolidation failed: %s", exc)
            return (
                notebook_text,
                "Agent notebook consolidation failed: kept existing notebook",
            )

        cleaned = consolidated.strip()
        if not cleaned:
            return (
                notebook_text,
                "Agent notebook consolidation skipped: model returned empty content",
            )
        if not cleaned.startswith("# Agent's Notebook"):
            cleaned = "# Agent's Notebook\n\n" + cleaned
        cleaned = cleaned.rstrip() + "\n"
        if cleaned == notebook_text.rstrip() + "\n":
            return (
                notebook_text,
                "Agent notebook consolidation skipped: no changes suggested",
            )
        return (
            cleaned,
            "Agent notebook updated: consolidated notebook after reaching size threshold",
        )

    def resolve_warm_memory_path(self, relative_path: str) -> Path | None:
        rel = Path(relative_path)
        if rel.is_absolute():
            return None
        candidate = (self.base_dir / rel).resolve()
        warm_root = (self.base_dir / "memory" / "warm").resolve()
        hot_root = (self.base_dir / "memory" / "hot").resolve()
        try:
            candidate.relative_to(warm_root)
        except ValueError:
            return None
        try:
            candidate.relative_to(hot_root)
            return None
        except ValueError:
            pass
        return candidate

    def backup_file(self, path: Path) -> None:
        resolved = path.resolve()
        if resolved in self.backed_up_paths:
            return
        dated_dir = self.backups_dir / utc_now().strftime("%Y-%m-%d")
        dated_dir.mkdir(parents=True, exist_ok=True)
        backup_path = dated_dir / path.name
        shutil.copy2(path, backup_path)
        self.backed_up_paths.add(resolved)

    def append_to_section(
        self, original: str, section: str | None, content: str
    ) -> str:
        entry = content.strip()
        if not entry:
            return original
        if section is None:
            return original.rstrip() + f"\n{entry}\n"

        lines = original.splitlines()
        section_header = f"## {section.strip()}"
        start_index = next(
            (index for index, line in enumerate(lines) if line.strip() == section_header),
            None,
        )
        if start_index is None:
            suffix = f"\n\n{section_header}\n{entry}\n"
            return original.rstrip() + suffix

        insert_index = len(lines)
        for index in range(start_index + 1, len(lines)):
            if lines[index].startswith("## "):
                insert_index = index
                break

        updated_lines = list(lines)
        insertion: list[str] = []
        if insert_index > 0 and updated_lines[insert_index - 1].strip():
            insertion.append("")
        insertion.append(entry)
        if insert_index < len(updated_lines) and updated_lines[insert_index].strip():
            insertion.append("")
        updated_lines[insert_index:insert_index] = insertion
        return "\n".join(updated_lines).rstrip() + "\n"

    def replace_line(self, original: str, old: str, new: str) -> str:
        old_line = old.strip()
        new_line = new.strip()
        if not old_line or not new_line:
            return original
        lines = original.splitlines()
        for index, line in enumerate(lines):
            if line.strip() == old_line:
                lines[index] = new_line
                return "\n".join(lines).rstrip() + "\n"
        return original

    def describe_update(self, update: UpdateInstruction, target_path: Path) -> str:
        if update.action == "append":
            topic = update.section or target_path.stem
            return f"Updated {target_path.name}: added {topic} note"
        if update.action == "replace_line":
            return f"Updated {target_path.name}: refreshed existing line"
        return f"Updated {target_path.name}"

    def archive_processed_summaries(self, summary_paths: list[Path]) -> None:
        self.processed_dir.mkdir(parents=True, exist_ok=True)
        for path in summary_paths:
            if path.parent.resolve() != self.staging_dir.resolve():
                continue
            shutil.move(str(path), self.processed_dir / path.name)

    def render_plan_preview(
        self,
        updates: list[UpdateInstruction],
        new_files: list[NewFileInstruction],
    ) -> list[str]:
        lines: list[str] = []
        for update in updates:
            lines.append(
                f"- UPDATE {update.path} [{update.action}]"
                + (f" section={update.section}" if update.section else "")
            )
            if update.action == "replace_line":
                lines.append(f"  OLD: {update.old}")
                lines.append(f"  NEW: {update.new}")
            else:
                lines.append(f"  CONTENT: {update.content}")
        for new_file in new_files:
            lines.append(f"- NEW_FILE {new_file.path}")
            lines.append(f"  CONTENT: {new_file.content}")
        return lines

    def render_observation_preview(
        self, observations: list[NotebookObservation]
    ) -> list[str]:
        if not observations:
            return ["- No notebook observations proposed."]
        return [
            f"- NOTEBOOK {observation.category}: {observation.observation}"
            for observation in observations
        ]

    def render_report_header(self, summary_paths: list[Path], dry_run: bool) -> list[str]:
        timestamp = format_utc_timestamp()
        lines = [
            "# Promotion Report",
            f"- **Generated**: {timestamp}",
            f"- **Dry run**: {'yes' if dry_run else 'no'}",
            "- **Summary files processed**:",
        ]
        lines.extend(f"  - {path.name}" for path in summary_paths)
        lines.append("")
        return lines

    def write_report(self, lines: list[str]) -> Path:
        self.reports_dir.mkdir(parents=True, exist_ok=True)
        report_name = f"promotion-{utc_now().strftime('%Y-%m-%d')}.md"
        report_path = self.reports_dir / report_name
        report_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
        return report_path


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Promote staged summaries into warm memory.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show proposed updates without applying them.",
    )
    parser.add_argument(
        "--summary-file",
        help="Process a specific summary file.",
    )
    return parser


async def async_main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    args = build_argument_parser().parse_args()
    base_dir = Path(__file__).resolve().parents[1]
    promoter = MemoryPromoter(base_dir)
    try:
        return await promoter.run(
            dry_run=bool(args.dry_run), summary_file=args.summary_file
        )
    except Exception as exc:
        LOGGER.error("Promotion failed: %s", exc)
        return 1


def main() -> int:
    import asyncio

    return asyncio.run(async_main())


if __name__ == "__main__":
    raise SystemExit(main())