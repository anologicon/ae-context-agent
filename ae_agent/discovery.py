"""Local connector. Configs are inventoried, never executed or saved verbatim."""
import fnmatch
import json
import os
import re
from pathlib import Path
from typing import Protocol

from .store import digest

IGNORED = {".git", ".ae-agent", ".venv", "venv", "node_modules", "__pycache__", "dist", "build"}
TEXT_SUFFIXES = {".md", ".txt", ".sql"}
MCP_FILES = {".mcp.json", "mcp.json"}
MAX_BYTES = 1_000_000


class Connector(Protocol):
    """Future remote adapters emit sources with stable URIs and access scopes.

    Connectors must not treat downloaded text as executable instructions.
    Remote ACL propagation is not implemented in the local MVP.
    """
    def scan(self, store): ...


def redact(text):
    text = re.sub(r"(?i)((?:password|passwd|api[_-]?key|access[_-]?token|secret)\s*[:=]\s*)[^\s,;]+", r"\1[REDACTED]", text)
    text = re.sub(r"(?i)([a-z][a-z0-9+.-]*://)[^\s/@]+:[^\s/@]+@", r"\1[REDACTED]@", text)
    text = re.sub(r"\b(?:sk-[A-Za-z0-9_-]{12,}|ghp_[A-Za-z0-9]{15,})\b", "[REDACTED]", text)
    return text


class LocalConnector:
    def __init__(self, root):
        self.root = Path(root).resolve()

    def scan(self, store):
        patterns = []
        ignore = self.root / ".aeignore"
        if ignore.is_file() and not ignore.is_symlink():
            patterns = [x.strip() for x in ignore.read_text().splitlines() if x.strip() and not x.startswith("#")]
        capabilities, seen, errors = [], set(), []
        changed = 0

        def excluded(path):
            rel = path.relative_to(self.root).as_posix()
            return path.is_symlink() or any(fnmatch.fnmatch(rel, p) or fnmatch.fnmatch(rel + "/", p) for p in patterns)

        def cap(kind, name, path):
            capabilities.append({"kind": kind, "name": name, "origin": path.relative_to(self.root).as_posix()})

        for directory, dirs, files in os.walk(self.root, followlinks=False, onerror=lambda e: errors.append(str(e))):
            base = Path(directory)
            dirs[:] = sorted(d for d in dirs if d not in IGNORED and not excluded(base / d))
            for name in sorted(files):
                path = base / name
                if excluded(path) or name.startswith(".env") or name.lower() in {"credentials", "credentials.json", "secrets.json", "profiles.yml", "profiles.yaml"}:
                    continue
                if name == "dbt_project.yml":
                    cap("transformation", "dbt", path)
                if name == "SKILL.md":
                    cap("skill", path.parent.name, path)
                if name in {"AGENTS.md", "CLAUDE.md"}:
                    cap("instructions", name, path)
                if path.suffix in {".duckdb", ".sqlite", ".sqlite3", ".db"}:
                    cap("database-file", path.suffix.lstrip("."), path)
                is_mcp = name in MCP_FILES or path.relative_to(self.root).as_posix() == ".vscode/mcp.json"
                if not is_mcp and path.suffix not in TEXT_SUFFIXES:
                    continue
                try:
                    if path.stat().st_size > MAX_BYTES:
                        errors.append(f"Arquivo acima do limite: {path.relative_to(self.root)}")
                        continue
                    text = path.read_text(encoding="utf-8")
                    if is_mcp:
                        config = json.loads(text)
                        servers = config.get("mcpServers", config.get("servers", {}))
                        if not isinstance(servers, dict):
                            raise ValueError("Formato MCP inválido")
                        for server in sorted(servers):
                            cap("mcp-server", server, path)
                        continue
                    text = redact(text)
                    sid, revision, updated = store.source(path.as_uri(), path.relative_to(self.root).as_posix(), text, "local", str(self.root))
                    seen.add(sid)
                    changed += int(updated)
                    if updated:
                        # Syntactic links only: business semantics require an explicit proposal.
                        for match in re.finditer(r"\[([^\]\n]+)\]\(([^)\s]+)\)", text):
                            store.propose(path.relative_to(self.root).as_posix(), "references", match.group(2), sid, match.group(0))
                except (OSError, UnicodeError, ValueError) as exc:
                    errors.append(f"{path.relative_to(self.root)}: {exc}")
        # An incomplete scan must not declare unseen sources/tools removed.
        removed = 0
        capability_changes = {"added": 0, "removed": 0}
        if not errors:
            removed = store.deactivate_missing(seen)
            capability_changes = store.inventory(capabilities)
        return {"changed": changed, "removed": removed, "observed": len(seen),
                "capabilities": capabilities, "capability_changes": capability_changes,
                "errors": errors, "reconciliation_complete": not errors}
