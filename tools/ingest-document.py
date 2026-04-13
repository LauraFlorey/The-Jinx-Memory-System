#!/usr/bin/env python3
"""OCR scanned documents into cold memory."""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import tempfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import fitz  # type: ignore
except ImportError:  # pragma: no cover - optional dependency
    fitz = None

try:
    import httpx  # type: ignore
except ImportError:  # pragma: no cover - optional dependency
    httpx = None

try:
    from PIL import Image  # type: ignore
except ImportError:  # pragma: no cover - optional dependency
    Image = None


DEFAULT_MODEL = "qwen/qwen3.5-9b"
DEFAULT_LOCAL_ENDPOINT = "http://localhost:1234/v1/chat/completions"
DEFAULT_SAVE_DIR = "memory/cold/family/documents"
DEFAULT_DPI = 150
DEFAULT_OPENROUTER_WORKERS = 8
DEFAULT_LOCAL_WORKERS = 1
OPENROUTER_COST_PER_PAGE = 0.00012
SUPPORTED_INPUT_SUFFIXES = {".pdf", ".png", ".jpg", ".jpeg", ".webp"}
OCR_PROMPT = (
    "OCR this document page. Return all the text exactly as it appears, preserving "
    "layout where possible. Use markdown formatting for tables and lists. "
    "Do not add any commentary."
)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def format_utc_timestamp(moment: datetime | None = None) -> str:
    value = moment or utc_now()
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")


def slugify(value: str, *, default: str = "document") -> str:
    cleaned = "".join(char.lower() if char.isalnum() else "-" for char in value)
    cleaned = "-".join(part for part in cleaned.split("-") if part)
    return cleaned or default


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
        parent[key] = value.split("#", 1)[0].strip().strip('"').strip("'")
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


@dataclass
class OCRResult:
    source_path: Path
    pages: int
    text: str
    saved_path: Path | None
    estimated_cost: float


class DocumentIngester:
    def __init__(self, base_dir: Path) -> None:
        self.base_dir = base_dir
        self.config = load_config(base_dir / "config.yaml")
        self.api_base = str(
            get_config(self.config, "model.api_base", "https://openrouter.ai/api/v1")
        ).rstrip("/")
        self.api_key = os.environ.get("AGENT_LOOP_API_KEY", "")

    async def ingest_path(
        self,
        path_input: str | Path,
        *,
        save_to: str | Path | None = None,
        use_local: bool = False,
        dry_run: bool = False,
    ) -> list[OCRResult]:
        targets = self.collect_targets(path_input)
        if not targets:
            raise FileNotFoundError(f"No supported documents found in {path_input}")

        results: list[OCRResult] = []
        total = len(targets)
        for index, target in enumerate(targets, start=1):
            print(f"Processing {index}/{total}: {target.name}...")
            result = await self.ingest_single_file(
                target,
                save_to=save_to,
                use_local=use_local,
                dry_run=dry_run,
            )
            results.append(result)
        return results

    def collect_targets(self, path_input: str | Path) -> list[Path]:
        source = Path(path_input)
        if not source.is_absolute():
            source = (Path.cwd() / source).resolve()
        if source.is_file():
            if source.suffix.lower() not in SUPPORTED_INPUT_SUFFIXES:
                raise ValueError(f"Unsupported input type: {source.suffix}")
            return [source]
        if not source.is_dir():
            raise FileNotFoundError(f"Input path not found: {source}")
        return sorted(
            [
                path
                for path in source.rglob("*")
                if path.is_file() and path.suffix.lower() in SUPPORTED_INPUT_SUFFIXES
            ]
        )

    async def ingest_single_file(
        self,
        source_path: Path,
        *,
        save_to: str | Path | None = None,
        use_local: bool = False,
        dry_run: bool = False,
    ) -> OCRResult:
        save_dir = self.resolve_save_dir(save_to)
        page_count = self.estimate_page_count(source_path)
        estimated_cost = 0.0 if use_local else page_count * OPENROUTER_COST_PER_PAGE
        if dry_run:
            return OCRResult(
                source_path=source_path,
                pages=page_count,
                text="",
                saved_path=None,
                estimated_cost=estimated_cost,
            )

        image_paths = await asyncio.to_thread(self.prepare_page_images, source_path)
        try:
            page_texts = await self.ocr_images(image_paths, use_local=use_local)
        finally:
            for image_path in image_paths:
                if image_path.exists() and image_path.parent.name.startswith("agent-ocr-"):
                    image_path.unlink(missing_ok=True)
            for parent in {image.parent for image in image_paths}:
                if parent.exists() and parent.name.startswith("agent-ocr-"):
                    parent.rmdir()

        combined = self.combine_page_texts(page_texts)
        save_dir.mkdir(parents=True, exist_ok=True)
        filename = (
            f"{slugify(source_path.stem)}-ocr-{utc_now().strftime('%Y-%m-%d')}.md"
        )
        saved_path = save_dir / filename
        saved_path.write_text(
            self.render_document_markdown(
                source_name=source_path.name,
                pages=len(page_texts),
                text=combined,
                use_local=use_local,
                estimated_cost=estimated_cost,
            ),
            encoding="utf-8",
        )
        return OCRResult(
            source_path=source_path,
            pages=len(page_texts),
            text=combined,
            saved_path=saved_path,
            estimated_cost=estimated_cost,
        )

    def resolve_save_dir(self, save_to: str | Path | None) -> Path:
        configured = str(get_config(self.config, "ocr.default_save_dir", DEFAULT_SAVE_DIR))
        if save_to is None:
            save_to = configured
        path = Path(save_to)
        if not path.is_absolute():
            path = self.base_dir / path
        return path

    def estimate_page_count(self, source_path: Path) -> int:
        if source_path.suffix.lower() == ".pdf" and fitz is not None:
            document = fitz.open(source_path)
            try:
                return max(1, len(document))
            finally:
                document.close()
        return 1

    def prepare_page_images(self, source_path: Path) -> list[Path]:
        if source_path.suffix.lower() == ".pdf":
            if fitz is None:
                raise RuntimeError("PyMuPDF is required to OCR PDF files.")
            temp_dir = Path(tempfile.mkdtemp(prefix="agent-ocr-"))
            document = fitz.open(source_path)
            dpi = int(get_config(self.config, "ocr.dpi", DEFAULT_DPI))
            scale = max(dpi, 72) / 72.0
            try:
                image_paths: list[Path] = []
                for page_index in range(len(document)):
                    page = document.load_page(page_index)
                    pixmap = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
                    image_path = temp_dir / f"{source_path.stem}-page-{page_index + 1}.png"
                    pixmap.save(image_path)
                    image_paths.append(image_path)
                return image_paths
            finally:
                document.close()

        if Image is None:
            raise RuntimeError("Pillow is required to OCR image files.")
        Image.open(source_path).close()
        return [source_path]

    async def ocr_images(self, image_paths: list[Path], *, use_local: bool) -> list[str]:
        if httpx is None:
            raise RuntimeError("httpx is required for OCR requests.")
        max_workers = self.resolve_worker_count(use_local=use_local)
        loop = asyncio.get_running_loop()
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            tasks = [
                loop.run_in_executor(
                    executor, self.ocr_image_sync, image_path, use_local, page_number
                )
                for page_number, image_path in enumerate(image_paths, start=1)
            ]
            return await asyncio.gather(*tasks)

    def resolve_worker_count(self, *, use_local: bool) -> int:
        configured = get_config(
            self.config,
            "ocr.max_workers",
            DEFAULT_LOCAL_WORKERS if use_local else DEFAULT_OPENROUTER_WORKERS,
        )
        try:
            count = int(configured)
        except (TypeError, ValueError):
            count = DEFAULT_LOCAL_WORKERS if use_local else DEFAULT_OPENROUTER_WORKERS
        return max(1, count)

    def ocr_image_sync(self, image_path: Path, use_local: bool, page_number: int) -> str:
        if httpx is None:
            raise RuntimeError("httpx is required for OCR requests.")
        endpoint = (
            str(get_config(self.config, "ocr.local_endpoint", DEFAULT_LOCAL_ENDPOINT))
            if use_local
            else f"{self.api_base}/chat/completions"
        )
        model = str(get_config(self.config, "ocr.model", DEFAULT_MODEL))
        if not use_local and not self.api_key:
            raise RuntimeError("AGENT_LOOP_API_KEY is required for OpenRouter OCR.")

        encoded = base64.b64encode(image_path.read_bytes()).decode("ascii")
        media_type = "image/png" if image_path.suffix.lower() == ".png" else "image/jpeg"
        payload = {
            "model": model,
            "temperature": 0,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": OCR_PROMPT},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:{media_type};base64,{encoded}"
                            },
                        },
                    ],
                }
            ],
            "max_tokens": 2000,
        }
        headers = {"Content-Type": "application/json"}
        if not use_local:
            headers["Authorization"] = f"Bearer {self.api_key}"

        with httpx.Client(timeout=180.0) as client:
            response = client.post(endpoint, headers=headers, json=payload)
            response.raise_for_status()
            data = response.json()

        try:
            choice = data["choices"][0]
            message = choice["message"]
            content = message["content"]
        except Exception as exc:
            raise RuntimeError(f"OCR response for page {page_number} was malformed.") from exc

        if isinstance(content, list):
            parts = [item.get("text", "") for item in content if isinstance(item, dict)]
            text = "\n".join(part for part in parts if part)
        else:
            text = str(content)
        return text.strip()

    def combine_page_texts(self, page_texts: list[str]) -> str:
        blocks: list[str] = []
        for index, text in enumerate(page_texts, start=1):
            blocks.append(f"--- Page {index} ---\n{text.strip()}")
        return "\n\n".join(blocks).rstrip() + "\n"

    def render_document_markdown(
        self,
        *,
        source_name: str,
        pages: int,
        text: str,
        use_local: bool,
        estimated_cost: float,
    ) -> str:
        model = "local" if use_local else str(get_config(self.config, "ocr.model", DEFAULT_MODEL))
        header = "\n".join(
            [
                f"<!-- source: {source_name} -->",
                f"<!-- pages: {pages} -->",
                f"<!-- ocr_model: {model} -->",
                f"<!-- processed: {format_utc_timestamp()} -->",
                f"<!-- estimated_cost: ${estimated_cost:.6f} -->",
                "",
            ]
        )
        return header + text


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="OCR documents into memory.")
    parser.add_argument("path", help="A PDF/image file or a folder of supported files.")
    parser.add_argument(
        "--local",
        action="store_true",
        help="Use the local LM Studio OCR endpoint instead of OpenRouter.",
    )
    parser.add_argument(
        "--save-to",
        default=DEFAULT_SAVE_DIR,
        help=f"Destination directory (default: {DEFAULT_SAVE_DIR}).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be processed without making OCR API calls.",
    )
    return parser


async def async_main() -> int:
    args = build_argument_parser().parse_args()
    ingester = DocumentIngester(Path(__file__).resolve().parents[1])
    results = await ingester.ingest_path(
        args.path,
        save_to=args.save_to,
        use_local=bool(args.local),
        dry_run=bool(args.dry_run),
    )
    total_pages = sum(result.pages for result in results)
    total_cost = sum(result.estimated_cost for result in results)
    if args.dry_run:
        print(
            f"{len(results)} documents would be processed, {total_pages} pages total, "
            f"estimated cost: ${total_cost:.3f}"
        )
        return 0

    for result in results:
        if result.saved_path is not None:
            print(f"Saved {result.source_path.name} -> {result.saved_path}")
    print(
        f"{len(results)} documents processed, {total_pages} pages total, "
        f"estimated cost: ${total_cost:.3f}"
    )
    return 0


def main() -> int:
    return asyncio.run(async_main())


if __name__ == "__main__":
    raise SystemExit(main())
