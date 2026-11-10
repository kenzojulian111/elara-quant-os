from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping


@dataclass(frozen=True)
class ArchitectureHotspot:
    file_path: str
    score: int
    lines: int
    imports: int
    config_mentions: int
    trace_mentions: int
    event_mentions: int
    reasons: tuple[str, ...]

    def to_dict(self) -> Mapping[str, object]:
        return {
            "file_path": self.file_path,
            "score": int(self.score),
            "lines": int(self.lines),
            "imports": int(self.imports),
            "config_mentions": int(self.config_mentions),
            "trace_mentions": int(self.trace_mentions),
            "event_mentions": int(self.event_mentions),
            "reasons": tuple(self.reasons),
        }


@dataclass(frozen=True)
class ArchitectureHeatmap:
    scanned_files: int
    hotspots: tuple[ArchitectureHotspot, ...]

    def to_dict(self) -> Mapping[str, object]:
        return {
            "scanned_files": int(self.scanned_files),
            "hotspots": tuple(hotspot.to_dict() for hotspot in self.hotspots),
        }

    def to_markdown(self, *, limit: int = 12) -> str:
        rows = [
            "# Architecture Heatmap",
            "",
            f"Scanned Python files: {self.scanned_files}",
            "",
            "| File | Score | Lines | Imports | Config | Trace | Events | Reasons |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
        ]
        for hotspot in self.hotspots[: max(1, int(limit))]:
            rows.append(
                "| "
                f"`{hotspot.file_path}` | "
                f"{hotspot.score} | "
                f"{hotspot.lines} | "
                f"{hotspot.imports} | "
                f"{hotspot.config_mentions} | "
                f"{hotspot.trace_mentions} | "
                f"{hotspot.event_mentions} | "
                f"{', '.join(hotspot.reasons) or 'watch'} |"
            )
        return "\n".join(rows) + "\n"


class ArchitectureHeatmapBuilder:
    DEFAULT_ROOTS = ("config", "runtime", "trading_core", "utils")

    def __init__(self, root: str | Path = ".", roots: Iterable[str] | None = None):
        self.root = Path(root)
        self.roots = tuple(roots or self.DEFAULT_ROOTS)

    def build(self, *, limit: int = 12) -> ArchitectureHeatmap:
        hotspots: list[ArchitectureHotspot] = []
        scanned = 0
        for path in self._python_files():
            scanned += 1
            hotspot = self._hotspot(path)
            if hotspot.score > 0:
                hotspots.append(hotspot)
        ranked = tuple(sorted(hotspots, key=lambda item: (-item.score, item.file_path))[: max(1, int(limit))])
        return ArchitectureHeatmap(scanned_files=scanned, hotspots=ranked)

    def _python_files(self) -> Iterable[Path]:
        for root_name in self.roots:
            root_path = self.root / root_name
            if not root_path.exists():
                continue
            for path in root_path.rglob("*.py"):
                if any(part in {"__pycache__", ".venv"} for part in path.parts):
                    continue
                yield path

    def _hotspot(self, path: Path) -> ArchitectureHotspot:
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            text = ""
        lines = sum(1 for line in text.splitlines() if line.strip())
        imports = sum(1 for line in text.splitlines() if line.startswith(("import ", "from ")))
        config_mentions = len(re.findall(r"\b(config|settings|env|Config|Settings)\b", text))
        trace_mentions = len(re.findall(r"\b(trace_id|pipeline_trace|reasoned_decision|DecisionTraceStep)\b", text))
        event_mentions = len(re.findall(r"\b(EventLedger|EventStream|event_type|append_dataclass)\b", text))
        score = _score(lines, imports, config_mentions, trace_mentions, event_mentions)
        reasons = _reasons(lines, imports, config_mentions, trace_mentions, event_mentions)
        return ArchitectureHotspot(
            file_path=_relative(path, self.root),
            score=score,
            lines=lines,
            imports=imports,
            config_mentions=config_mentions,
            trace_mentions=trace_mentions,
            event_mentions=event_mentions,
            reasons=reasons,
        )


def _score(lines: int, imports: int, config_mentions: int, trace_mentions: int, event_mentions: int) -> int:
    raw = (lines // 35) + imports + config_mentions * 2 + trace_mentions * 3 + event_mentions * 2
    return min(100, int(raw))


def _reasons(
    lines: int,
    imports: int,
    config_mentions: int,
    trace_mentions: int,
    event_mentions: int,
) -> tuple[str, ...]:
    reasons: list[str] = []
    if lines >= 300:
        reasons.append("large_module")
    if imports >= 20:
        reasons.append("dependency_dense")
    if config_mentions >= 8:
        reasons.append("config_touchpoint")
    if trace_mentions >= 8:
        reasons.append("decision_trace_touchpoint")
    if event_mentions >= 5:
        reasons.append("event_touchpoint")
    return tuple(reasons)


def _relative(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()
