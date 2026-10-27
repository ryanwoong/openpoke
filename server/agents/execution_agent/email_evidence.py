"""Track actual body reads separately from discovery and exploration limits."""

from dataclasses import dataclass, field

EMAIL_READ_TOOLS = {"task_email_search", "task_email_read"}


def _scope(arguments):
    if arguments.get("thread_id"):
        return "thread:" + str(arguments["thread_id"])
    return "message:" + str(arguments.get("message_id", "unknown"))


@dataclass
class EmailEvidence:
    headers: dict = field(default_factory=dict)
    scopes: dict = field(default_factory=dict)
    queries: dict = field(default_factory=dict)
    legacy_records: dict = field(default_factory=dict)
    legacy_gaps: list = field(default_factory=list)

    def observe(self, tool, arguments, success, result):
        if tool not in EMAIL_READ_TOOLS:
            return
        if isinstance(result, list) and success:
            for record in result:
                if isinstance(record, dict) and isinstance(record.get("id"), str):
                    self.legacy_records[record["id"]] = record
            return
        if not isinstance(result, dict):
            return
        if tool == "task_email_search":
            if not success and arguments.get("mode") == "all":
                self.queries.setdefault(arguments.get("search_query", "").strip(), {"pages": {}, "ids": set()})
            if result.get("body_scope") != "headers":
                if result.get("search_complete") is False:
                    self.legacy_gaps.append(result.get("warning", "A previous read was incomplete"))
                    for record in result.get("messages", []):
                        if isinstance(record, dict) and isinstance(record.get("id"), str):
                            self.legacy_records[record["id"]] = record
                return
            for record in result.get("messages", []):
                self.headers[record["id"]] = record
            if result.get("coverage_required") or arguments.get("mode") == "all":
                query = arguments.get("search_query", "").strip()
                token = result.get("page_token")
                if token is None or query not in self.queries:
                    self.queries[query] = {"pages": {}, "ids": set()}
                state = self.queries[query]
                state["pages"][token] = result.get("next_page_token")
                state["ids"].update(record["id"] for record in result.get("messages", []))
            return
        if not any(isinstance(arguments.get(key), str) and arguments[key].strip() for key in ("thread_id", "message_id")):
            return
        scope = _scope(arguments)
        if not success:
            self.scopes.setdefault(scope, {})["error"] = "The selected conversation could not be read"
            return
        snapshot = result.get("snapshot_id")
        count = result.get("message_count")
        if result.get("scope_id") != scope or not isinstance(snapshot, str) or type(count) is not int or not 0 < count <= 10000:
            self.scopes.setdefault(scope, {})["error"] = "The selected read returned invalid coverage information"
            return
        if self.scopes.get(scope, {}).get("snapshot") != snapshot:
            self.scopes[scope] = {"snapshot": snapshot, "count": count, "messages": {}, "ended": False}
        state = self.scopes[scope]
        state.pop("error", None)
        state["ended"] = state["ended"] or not result.get("next_cursor")
        for record in result.get("messages", []):
            index, offset, total = record.get("message_index"), record.get("body_offset"), record.get("body_total_chars")
            text = record.get("clean_text")
            if any(type(value) is not int for value in (index, offset, total)) or not 0 <= index < count or not isinstance(text, str) or not 0 <= offset <= offset + len(text) <= total:
                state["error"] = "The selected read returned an invalid body fragment"
                continue
            entry = state["messages"].setdefault(index, {"record": record, "pieces": {}})
            if entry["record"]["id"] != record["id"] or entry["record"]["body_total_chars"] != total:
                state["error"] = "The selected conversation changed during retrieval"
                continue
            entry["pieces"][offset] = text

    @staticmethod
    def _assembled(entry):
        record = entry["record"]
        if not record.get("body_available"):
            return None
        text = ""
        for offset, part in sorted(entry["pieces"].items()):
            if offset > len(text):
                return None
            overlap = min(len(text) - offset, len(part))
            if text[offset : offset + overlap] != part[:overlap]:
                return None
            text += part[overlap:]
        if len(text) != record["body_total_chars"]:
            return None
        return {**record, "clean_text": text}

    @property
    def records(self):
        records = dict(self.legacy_records)
        for state in self.scopes.values():
            for entry in state.get("messages", {}).values():
                record = self._assembled(entry)
                if record is not None:
                    records[record["id"]] = record
        return records

    @property
    def gaps(self):
        gaps = list(self.legacy_gaps)
        records = self.records
        for scope, state in self.scopes.items():
            if state.get("error"):
                gaps.append(state["error"])
                continue
            complete = sum(self._assembled(entry) is not None or entry["record"]["id"] in records and (not entry["record"].get("body_available") or len(records[entry["record"]["id"]]["clean_text"]) == entry["record"]["body_total_chars"]) for entry in state.get("messages", {}).values())
            if not state.get("ended") or complete != state.get("count"):
                subject = next((entry["record"].get("subject", "Selected conversation") for entry in state.get("messages", {}).values()), "Selected conversation")
                gaps.append(f"{subject}: {max(0, state.get('count', 0) - complete)} selected message bodies remain unread or unfinished")
        records = self.records
        for query, state in self.queries.items():
            token = None
            seen = set()
            while token in state["pages"] and token not in seen:
                seen.add(token)
                token = state["pages"][token]
                if token is None:
                    break
            else:
                gaps.append("An exhaustive search still has more header pages to review")
            missing = state["ids"] - records.keys()
            if missing:
                gaps.append(f"An exhaustive search has {len(missing)} matching message bodies that have not been read")
        return gaps
