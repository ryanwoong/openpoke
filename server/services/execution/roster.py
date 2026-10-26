"""Persistent execution-agent discovery and bounded dispatch references."""

import asyncio
import fcntl
import json
import os
import re
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter

from ...logging_config import logger


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"\w+", text.casefold()))


class AgentRoster:
    def __init__(self, roster_path: Path):
        self._roster_path = roster_path
        self._lock_path = roster_path.with_suffix(".lock")
        self._backup_path = roster_path.with_suffix(".legacy.json")

    @contextmanager
    def _locked(self):
        self._roster_path.parent.mkdir(parents=True, exist_ok=True)
        # Lock a stable inode: atomic replacement changes the roster's inode.
        with self._lock_path.open("a") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)

    def _write(self, data: dict) -> None:
        descriptor, temporary = tempfile.mkstemp(dir=self._roster_path.parent, prefix=".roster-")
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(data, handle, ensure_ascii=False, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self._roster_path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def _read(self) -> dict:
        if not self._roster_path.exists():
            return {"version": 1, "agents": [], "recent_dispatches": []}
        raw = self._roster_path.read_bytes()
        data = json.loads(raw)
        if isinstance(data, list):
            if not all(isinstance(name, str) and name.strip() for name in data):
                raise ValueError("Invalid legacy agent directory")
            data = {"version": 1, "agents": [{"agent_name": name, "purpose": name[:256], "last_used_at": None} for name in dict.fromkeys(data)], "recent_dispatches": []}
            if not self._backup_path.exists():
                with self._backup_path.open("xb") as handle:
                    handle.write(raw)
                    handle.flush()
                    os.fsync(handle.fileno())
            self._write(data)
        self._validate(data)
        return data

    @staticmethod
    def _validate(data: dict) -> None:
        if not isinstance(data, dict) or data.get("version") != 1 or not isinstance(data.get("agents"), list) or not isinstance(data.get("recent_dispatches"), list):
            raise ValueError("Invalid agent directory format")
        names = set()
        for agent in data["agents"]:
            if not isinstance(agent, dict) or not isinstance(agent.get("agent_name"), str) or not agent["agent_name"].strip() or not isinstance(agent.get("purpose"), str) or len(agent["purpose"]) > 256 or "last_used_at" not in agent:
                raise ValueError("Invalid agent record")
            if agent["agent_name"] in names:
                raise ValueError("Duplicate agent name")
            names.add(agent["agent_name"])
            stamp = agent.get("last_used_at")
            if stamp is not None:
                if not isinstance(stamp, str) or datetime.fromisoformat(stamp).utcoffset() != timezone.utc.utcoffset(None):
                    raise ValueError("Invalid agent timestamp")
        for reference in data["recent_dispatches"]:
            if not isinstance(reference, dict) or not isinstance(reference.get("agent_name"), str) or reference["agent_name"] not in names or not isinstance(reference.get("request_id"), str) or not isinstance(reference.get("dispatched_at"), str):
                raise ValueError("Invalid dispatch reference")

    def load(self) -> None:
        with self._locked():
            self._read()

    def get_agents(self) -> list[str]:
        with self._locked():
            return [agent["agent_name"] for agent in self._read()["agents"]]

    def record_dispatch(self, agent_name: str, request_id: str | None = None, purpose: str | None = None) -> bool:
        if not isinstance(agent_name, str) or not agent_name.strip():
            raise ValueError("agent_name must be a nonempty string")
        if purpose is not None and (not isinstance(purpose, str) or len(purpose) > 256):
            raise ValueError("purpose must be a string of at most 256 characters")
        if request_id is not None and (not isinstance(request_id, str) or not request_id):
            raise ValueError("request_id must be a nonempty string")
        with self._locked():
            from ..conversation.request_context import current_request

            context = current_request.get()
            if context and context.stopped_reason:
                raise asyncio.CancelledError()
            data = self._read()
            agent = next((entry for entry in data["agents"] if entry["agent_name"] == agent_name), None)
            is_new = agent is None
            if is_new:
                agent = {"agent_name": agent_name, "purpose": purpose or agent_name[:256], "last_used_at": None}
                data["agents"].append(agent)
            stamp = datetime.now(timezone.utc).isoformat()
            agent["last_used_at"] = stamp
            if request_id:
                data["recent_dispatches"].append({"request_id": request_id, "agent_name": agent_name, "dispatched_at": stamp})
                data["recent_dispatches"] = data["recent_dispatches"][-20:]
            self._write(data)
        logger.info("Agent dispatch | created=%s", is_new)
        return is_new

    def find_agents(self, query: str) -> dict:
        if not isinstance(query, str) or not query.strip() or len(query.strip()) > 256:
            raise ValueError("query must contain 1 to 256 characters")
        query = query.strip().casefold()
        words = _tokens(query)
        started = perf_counter()
        with self._locked():
            agents = self._read()["agents"]
        matches = []
        for agent in agents:
            exact = agent["agent_name"].casefold() == query
            score = 2 * len(words & _tokens(agent["agent_name"])) + len(words & _tokens(agent["purpose"]))
            if exact or score:
                stamp = agent["last_used_at"]
                recent = datetime.fromisoformat(stamp).timestamp() if stamp else float("-inf")
                record = {key: agent[key] for key in ("agent_name", "purpose", "last_used_at")}
                matches.append((not exact, -score, -recent, agent["agent_name"], record))
        matches.sort(key=lambda entry: entry[:4])
        result = {"agents": [], "truncated": False}
        for *_, agent in matches:
            if len(result["agents"]) == 5:
                break
            candidate = {"agents": result["agents"] + [agent], "truncated": False}
            if len(json.dumps(candidate).encode("utf-8")) <= 8192:
                result["agents"].append(agent)
        result["truncated"] = len(result["agents"]) < len(matches)
        logger.info("Agent discovery | latency_ms=%.2f count=%s truncated=%s", (perf_counter() - started) * 1000, len(result["agents"]), result["truncated"])
        return result

    def recent_dispatches(self) -> list[dict]:
        with self._locked():
            references = [{key: reference[key] for key in ("request_id", "agent_name", "dispatched_at")} for reference in self._read()["recent_dispatches"][-20:]]
        result = []
        seen = set()
        size = 0
        for reference in reversed(references):
            name = reference["agent_name"]
            encoded_size = len(json.dumps(reference).encode("utf-8"))
            if name not in seen and size + encoded_size <= 8192:
                result.append(reference)
                seen.add(name)
                size += encoded_size
            if len(result) == 5:
                break
        return result

    def clear(self) -> None:
        with self._locked():
            self._write({"version": 1, "agents": [], "recent_dispatches": []})
            self._backup_path.unlink(missing_ok=True)


_DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
_ROSTER_PATH = _DATA_DIR / "execution_agents" / "roster.json"
_agent_roster = AgentRoster(_ROSTER_PATH)


def get_agent_roster() -> AgentRoster:
    return _agent_roster
