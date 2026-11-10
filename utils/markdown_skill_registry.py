import logging
import os
import re
import time
from fnmatch import fnmatch
from dataclasses import dataclass
from typing import Dict, List, Optional


log = logging.getLogger("elara.markdown_skills")


_FRONTMATTER_PATTERN = re.compile(
    r"^\ufeff?---\s*\n(.*?)\n---\s*\n?",
    flags=re.DOTALL,
)
_SKILL_REF_PATTERN = re.compile(r"\$([a-z0-9][a-z0-9\-_]{1,63})")
_SKILL_CMD_PATTERN = re.compile(
    r"(?:pakai|gunakan|use|aktifkan)\s+(?:skill\s+)?\$?([a-z0-9][a-z0-9\-_]{1,63})",
    flags=re.IGNORECASE,
)
_SKILL_SLASH_PATTERN = re.compile(
    r"^/(?:sk|skill)\s+([a-z0-9][a-z0-9\-_]{1,63})(?:\s+.+)?$",
    flags=re.IGNORECASE,
)
_SEARCH_TOKEN_PATTERN = re.compile(r"[a-z0-9][a-z0-9\-_]{1,63}")
_TRUST_LEVELS = {"blocked", "restricted", "standard", "trusted"}
_SKILL_NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9\-]{1,63}$")


@dataclass
class MarkdownSkillDoc:
    name: str
    description: str
    body: str
    path: str
    trust_level: str = "standard"
    tags: List[str] | None = None

    def __post_init__(self):
        raw_trust = str(self.trust_level or "").strip().lower()
        self.trust_level = raw_trust if raw_trust in _TRUST_LEVELS else "standard"
        cleaned_tags = []
        for item in self.tags or []:
            tag = str(item or "").strip().lower()
            if tag and tag not in cleaned_tags:
                cleaned_tags.append(tag)
        self.tags = cleaned_tags


@dataclass
class MarkdownSkillMatch:
    doc: MarkdownSkillDoc
    score: int
    matched_by: str


def _strip_quotes(value: str) -> str:
    raw = str(value or "").strip()
    if len(raw) >= 2 and ((raw[0] == raw[-1]) and raw[0] in {"'", '"'}):
        return raw[1:-1].strip()
    return raw


def _parse_frontmatter(raw_frontmatter: str) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for line in str(raw_frontmatter or "").splitlines():
        row = line.strip()
        if not row or row.startswith("#") or ":" not in row:
            continue
        key, value = row.split(":", 1)
        k = str(key or "").strip().lower()
        if not k:
            continue
        out[k] = _strip_quotes(value)
    return out


def _parse_frontmatter_tags(frontmatter: Dict[str, str]) -> List[str]:
    raw = str(
        frontmatter.get("tags")
        or frontmatter.get("tag")
        or frontmatter.get("labels")
        or ""
    ).strip()
    if not raw:
        return []
    out: List[str] = []
    for part in raw.split(","):
        value = str(part or "").strip().lower()
        if not value:
            continue
        if value not in out:
            out.append(value)
    return out


def _normalize_skill_name(value: str) -> str:
    raw = str(value or "").strip().lower().lstrip("$").replace("_", "-")
    if not raw:
        return ""
    return raw if _SKILL_NAME_PATTERN.match(raw) else ""


class MarkdownSkillRegistry:
    def __init__(
        self,
        directories: List[str],
        *,
        root_path: str,
        enabled: bool = True,
        max_skills: int = 1200,
        max_body_chars: int = 6000,
        refresh_sec: int = 30,
        watch_enabled: bool = True,
        watch_interval_sec: float = 2.0,
        include_patterns: Optional[List[str]] = None,
        exclude_patterns: Optional[List[str]] = None,
    ):
        self.enabled = bool(enabled)
        self.watch_enabled = bool(watch_enabled)
        self.root_path = str(root_path or os.getcwd())
        self.max_skills = max(1, int(max_skills or 1))
        self.max_body_chars = max(500, int(max_body_chars or 500))
        self.refresh_sec = max(5, int(refresh_sec or 5))
        self.watch_interval_sec = max(0.2, float(watch_interval_sec or 0.2))
        self.directories = self._normalize_directories(directories)
        self.include_patterns = self._normalize_patterns(include_patterns or [])
        self.exclude_patterns = self._normalize_patterns(exclude_patterns or [])
        self._last_scan_ts: float = 0.0
        self._skills: Dict[str, MarkdownSkillDoc] = {}
        self._source_counts: Dict[str, int] = {}
        self._scan_error_once = False
        self._duplicate_name_warnings: set[str] = set()
        self._source_fingerprint: Dict[str, str] = {}
        self._last_watch_check_ts: float = 0.0
        self._watch_change_count: int = 0
        self._watch_last_change_ts: float = 0.0
        self._refresh_count: int = 0
        self._forced_refresh_count: int = 0
        self._scan_error_count: int = 0
        self._last_refresh_reason: str = "init"
        self._last_refresh_duration_ms: float = 0.0
        self._last_error: str = ""

    def _normalize_directories(self, raw_dirs: List[str]) -> List[str]:
        normalized: List[str] = []
        for raw in raw_dirs or []:
            value = str(raw or "").strip()
            if not value:
                continue
            value = os.path.expandvars(os.path.expanduser(value))
            if not os.path.isabs(value):
                value = os.path.abspath(os.path.join(self.root_path, value))
            else:
                value = os.path.abspath(value)
            if value not in normalized:
                normalized.append(value)
        return normalized

    @staticmethod
    def _normalize_patterns(raw_patterns: List[str]) -> List[str]:
        normalized: List[str] = []
        for raw in raw_patterns:
            value = str(raw or "").strip().lower()
            if not value:
                continue
            if value not in normalized:
                normalized.append(value)
        return normalized

    @staticmethod
    def _pattern_match(value: str, pattern: str) -> bool:
        candidate = str(value or "").strip().lower()
        pat = str(pattern or "").strip().lower()
        if not candidate or not pat:
            return False
        has_glob = ("*" in pat) or ("?" in pat) or ("[" in pat)
        if has_glob:
            return fnmatch(candidate, pat)
        return candidate == pat

    def _is_allowed(self, skill_name: str) -> bool:
        name = str(skill_name or "").strip().lower()
        if not name:
            return False
        if self.include_patterns:
            if not any(self._pattern_match(name, pat) for pat in self.include_patterns):
                return False
        if self.exclude_patterns:
            if any(self._pattern_match(name, pat) for pat in self.exclude_patterns):
                return False
        return True

    def _parse_skill_file(self, skill_md_path: str) -> Optional[MarkdownSkillDoc]:
        try:
            with open(skill_md_path, "r", encoding="utf-8", errors="ignore") as f:
                raw = f.read()
        except Exception as e:
            log.debug(f"Cannot read markdown skill file '{skill_md_path}': {e}")
            return None

        raw = str(raw or "").strip()
        if not raw:
            return None

        frontmatter = {}
        body = raw
        match = _FRONTMATTER_PATTERN.match(raw)
        if match:
            frontmatter = _parse_frontmatter(match.group(1))
            body = raw[match.end() :].strip()

        name = _normalize_skill_name(frontmatter.get("name") or "")
        if not name:
            name = _normalize_skill_name(os.path.basename(os.path.dirname(skill_md_path)))
        if not name:
            return None

        description = str(frontmatter.get("description") or "").strip()
        trust_level = str(
            frontmatter.get("trust_level")
            or frontmatter.get("trust")
            or frontmatter.get("risk")
            or "standard"
        ).strip().lower()
        tags = _parse_frontmatter_tags(frontmatter)
        if len(body) > self.max_body_chars:
            body = body[: self.max_body_chars].rstrip() + "\n\n[...truncated...]"

        return MarkdownSkillDoc(
            name=name,
            description=description,
            body=body,
            path=skill_md_path,
            trust_level=trust_level,
            tags=tags,
        )

    def _scan(self) -> Dict[str, MarkdownSkillDoc]:
        found: Dict[str, MarkdownSkillDoc] = {}
        source_counts: Dict[str, int] = {base: 0 for base in self.directories}
        if not self.enabled:
            self._source_counts = source_counts
            return found

        for base in self.directories:
            if not os.path.isdir(base):
                continue
            try:
                entries = sorted(
                    (item for item in os.scandir(base) if item.is_dir()),
                    key=lambda item: item.name.lower(),
                )
            except Exception as e:
                log.debug(f"Cannot scan markdown skills dir '{base}': {e}")
                continue

            for entry in entries:
                if entry.name.startswith("."):
                    continue
                skill_md_path = os.path.join(entry.path, "SKILL.md")
                if not os.path.isfile(skill_md_path):
                    continue
                doc = self._parse_skill_file(skill_md_path)
                if not doc:
                    continue
                if not self._is_allowed(doc.name):
                    continue
                if doc.name in found:
                    existing = found[doc.name]
                    dup_key = f"{doc.name}|{existing.path}|{skill_md_path}"
                    if dup_key not in self._duplicate_name_warnings:
                        self._duplicate_name_warnings.add(dup_key)
                        log.warning(
                            "Duplicate markdown skill name '%s' ignored from '%s' (already loaded from '%s').",
                            doc.name,
                            skill_md_path,
                            existing.path,
                        )
                    continue
                found[doc.name] = doc
                source_counts[base] = int(source_counts.get(base, 0)) + 1
                if len(found) >= self.max_skills:
                    self._source_counts = source_counts
                    return found
        self._source_counts = source_counts
        return found

    def _collect_source_fingerprint(self) -> Dict[str, str]:
        snapshot: Dict[str, str] = {}
        for base in self.directories:
            exists = bool(os.path.isdir(base))
            snapshot[f"{base}::exists"] = "1" if exists else "0"
            if not exists:
                continue
            try:
                entries = sorted(
                    (item for item in os.scandir(base) if item.is_dir()),
                    key=lambda item: item.name.lower(),
                )
            except Exception:
                continue

            for entry in entries:
                if entry.name.startswith("."):
                    continue
                skill_md_path = os.path.join(entry.path, "SKILL.md")
                if not os.path.isfile(skill_md_path):
                    continue
                try:
                    st = os.stat(skill_md_path)
                    fingerprint = f"{int(st.st_mtime_ns)}:{int(st.st_size)}"
                except Exception:
                    continue
                snapshot[os.path.abspath(skill_md_path)] = fingerprint
        return snapshot

    def _detect_source_changes(self, now: float) -> bool:
        if not self.enabled or not self.watch_enabled:
            return False
        if (now - self._last_watch_check_ts) < self.watch_interval_sec:
            return False
        self._last_watch_check_ts = now

        current = self._collect_source_fingerprint()
        if not self._source_fingerprint:
            self._source_fingerprint = current
            return False
        if current == self._source_fingerprint:
            return False

        self._source_fingerprint = current
        self._watch_change_count += 1
        self._watch_last_change_ts = now
        return True

    def refresh_if_needed(self, force: bool = False, reason: str = ""):
        if not self.enabled:
            return
        now = time.time()
        watch_dirty = self._detect_source_changes(now)
        should_refresh = force or (not self._skills) or watch_dirty or ((now - self._last_scan_ts) >= self.refresh_sec)
        if not should_refresh:
            return
        started = time.perf_counter()
        try:
            self._skills = self._scan()
            self._last_scan_ts = now
            if self.watch_enabled:
                self._source_fingerprint = self._collect_source_fingerprint()
            self._refresh_count += 1
            if force:
                self._forced_refresh_count += 1
            if reason:
                resolved_reason = str(reason).strip().lower()
            elif force:
                resolved_reason = "force"
            elif watch_dirty:
                resolved_reason = "watch"
            elif not self._skills:
                resolved_reason = "empty"
            else:
                resolved_reason = "ttl"
            self._last_refresh_reason = resolved_reason
            self._last_refresh_duration_ms = round((time.perf_counter() - started) * 1000.0, 3)
            self._last_error = ""
        except Exception as e:
            self._scan_error_count += 1
            self._last_error = str(e)
            if not self._scan_error_once:
                log.warning(f"Failed loading markdown skills: {e}")
                self._scan_error_once = True

    def count(self) -> int:
        self.refresh_if_needed()
        return len(self._skills)

    def list_names(self) -> List[str]:
        self.refresh_if_needed()
        return sorted(self._skills.keys())

    def source_directories(self) -> List[str]:
        return list(self.directories)

    def source_breakdown(self) -> List[Dict[str, object]]:
        self.refresh_if_needed()
        rows: List[Dict[str, object]] = []
        for base in self.directories:
            rows.append(
                {
                    "path": str(base),
                    "exists": bool(os.path.isdir(base)),
                    "loaded": int(self._source_counts.get(base, 0) or 0),
                }
            )
        return rows

    def stats(self) -> Dict[str, object]:
        self.refresh_if_needed(reason="stats")
        return {
            "enabled": bool(self.enabled),
            "watch_enabled": bool(self.watch_enabled),
            "watch_interval_sec": float(self.watch_interval_sec),
            "directory_count": int(len(self.directories)),
            "skill_count": int(len(self._skills)),
            "refresh_sec": int(self.refresh_sec),
            "refresh_count": int(self._refresh_count),
            "forced_refresh_count": int(self._forced_refresh_count),
            "last_refresh_reason": str(self._last_refresh_reason or ""),
            "last_refresh_duration_ms": float(self._last_refresh_duration_ms),
            "last_scan_ts": float(self._last_scan_ts or 0.0),
            "last_watch_check_ts": float(self._last_watch_check_ts or 0.0),
            "watch_change_count": int(self._watch_change_count),
            "watch_last_change_ts": float(self._watch_last_change_ts or 0.0),
            "scan_error_count": int(self._scan_error_count),
            "last_error": str(self._last_error or ""),
        }

    def get(self, name: str) -> Optional[MarkdownSkillDoc]:
        self.refresh_if_needed()
        key = _normalize_skill_name(name or "")
        if not key:
            return None
        return self._skills.get(key)

    @staticmethod
    def _tokenize_search_query(query: str) -> List[str]:
        tokens: List[str] = []
        for token in _SEARCH_TOKEN_PATTERN.findall(str(query or "").lower()):
            normalized = _normalize_skill_name(token)
            if normalized and normalized not in tokens:
                tokens.append(normalized)
        return tokens

    @staticmethod
    def _normalize_search_query(query: str) -> str:
        raw = str(query or "").strip().lower().lstrip("$")
        if not raw:
            return ""
        raw = re.sub(r"^/(?:findskill|searchskill|skills)\s+", "", raw, flags=re.IGNORECASE)
        raw = re.sub(r"^(?:cari\s+skill|find\s+skill)\s+", "", raw, flags=re.IGNORECASE)
        return raw.strip().replace("_", "-")

    @staticmethod
    def _extract_explicit_skill_names(text: str) -> List[str]:
        source = str(text or "").strip().lower()
        if not source:
            return []

        names: List[str] = []
        for match in _SKILL_REF_PATTERN.finditer(source):
            value = _normalize_skill_name(match.group(1) or "")
            if value and value not in names:
                names.append(value)
        cmd_match = _SKILL_CMD_PATTERN.search(source)
        if cmd_match:
            value = _normalize_skill_name(cmd_match.group(1) or "")
            if value and value not in names:
                names.append(value)
        slash_match = _SKILL_SLASH_PATTERN.match(source)
        if slash_match:
            value = _normalize_skill_name(slash_match.group(1) or "")
            if value and value not in names:
                names.append(value)
        return names

    @staticmethod
    def _score_doc(query: str, tokens: List[str], doc: MarkdownSkillDoc) -> int:
        name = str(doc.name or "").lower()
        desc = str(doc.description or "").lower()
        tags = " ".join([str(tag or "").strip().lower() for tag in (doc.tags or []) if str(tag or "").strip()])
        score = 0

        if name == query:
            score += 1000
        if name.startswith(query):
            score += 260
        if query in name:
            score += 180
        if query and query in desc:
            score += 75

        for tok in tokens:
            if tok == name:
                score += 180
            elif name.startswith(tok):
                score += 55
            elif tok in name:
                score += 30
            if tok in desc:
                score += 12
            if tags and tok in tags:
                score += 8
        return score

    def search_scored(self, query: str, limit: int = 20) -> List[tuple[MarkdownSkillDoc, int]]:
        self.refresh_if_needed()
        q = self._normalize_search_query(query)
        if not q:
            return []
        max_items = max(1, int(limit or 1))
        tokens = self._tokenize_search_query(q)
        scored = []
        for doc in self._skills.values():
            score = self._score_doc(q, tokens, doc)
            if score > 0:
                scored.append((score, str(doc.name or "").lower(), doc))
        scored.sort(key=lambda item: (-item[0], item[1]))
        return [(row[2], int(row[0])) for row in scored[:max_items]]

    def search(self, query: str, limit: int = 20) -> List[MarkdownSkillDoc]:
        return [doc for doc, _score in self.search_scored(query, limit=limit)]

    def route_from_text(
        self,
        text: str,
        *,
        min_score: int = 120,
        top_k: int = 8,
    ) -> Optional[MarkdownSkillMatch]:
        self.refresh_if_needed()
        source = str(text or "")
        if not source:
            return None

        explicit_names = self._extract_explicit_skill_names(source)
        for skill_name in explicit_names:
            doc = self._skills.get(skill_name)
            if doc:
                return MarkdownSkillMatch(doc=doc, score=3000, matched_by="explicit")

        scored = self.search_scored(source, limit=max(1, int(top_k or 1)))
        if not scored:
            return None
        best_doc, best_score = scored[0]
        if int(best_score) < int(min_score):
            return None
        return MarkdownSkillMatch(doc=best_doc, score=int(best_score), matched_by="search")

    def resolve_from_text(self, text: str) -> Optional[MarkdownSkillDoc]:
        routed = self.route_from_text(text, min_score=1, top_k=8)
        return routed.doc if routed else None

