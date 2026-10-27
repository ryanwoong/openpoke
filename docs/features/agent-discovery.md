# On-demand execution agent discovery

**Status: implemented in the working tree.** Replaces the full execution-agent roster in every Interaction Agent prompt with a read-only `find_agents` tool. Older agents remain discoverable without making prompt size grow with the directory.

## Previous behavior

`server/data/execution_agents/roster.json` stores a plain list of agent names:

```json
["Email to Alice", "Tokyo Restaurant Search", "Q3 Budget Analysis"]
```

`AgentRoster` in `server/services/execution/roster.py` reads the file. During prompt construction, `_render_active_agents()` in `server/agents/interaction_agent/agent.py` injects every entry into the Interaction Agent's input:

```xml
<active_agents>
  <agent name="Email to Alice" />
  <agent name="Tokyo Restaurant Search" />
  <agent name="Q3 Budget Analysis" />
</active_agents>
```

The model calls `send_message_to_agent(agent_name, instructions)`. An exact existing name reuses that agent; an unknown name creates one and saves it to the roster. Execution logs are stored separately in `server/data/execution_agents/*.log`. Previously, every request carried the full roster, so irrelevant names consumed increasing prompt space.

## Implemented behavior

The directory stays on disk; tool-based lookup replaces full-roster prompt injection. The JSON file now uses a versioned structure:

```json
{
  "version": 1,
  "agents": [
    {
      "agent_name": "Tokyo Restaurant Search",
      "purpose": "Find vegetarian restaurants for the Tokyo trip",
      "last_used_at": "2026-10-26T18:30:00Z"
    }
  ],
  "recent_dispatches": []
}
```

`find_agents({ "query": "Tokyo restaurants" })` reads the directory, ranks keyword matches across names and purposes, and returns at most five matching records. The Interaction Agent selects a candidate and delegates through the existing `send_message_to_agent` tool. Recent dispatch associations provide a bounded set of request-to-agent references for direct follow-ups.

The JSON directory can grow on disk while directory context in the prompt stays bounded. No vector database or embedding service is required.

## Request flow

1. For an unambiguous follow-up, reuse the agent associated with the referenced exchange. Persist request-to-agent associations; never assume the globally most recent agent is correct. Include at most five associated agent references in the prompt; search when the reference is missing or ambiguous.
2. Otherwise, call `find_agents({ "query": "Tokyo trip restaurants" })` before creating an agent.
3. The tool returns up to five candidates containing `agent_name`, `purpose`, and `last_used_at`. It does not dispatch work or return execution logs.
4. The Interaction Agent selects a suitable candidate and calls the existing `send_message_to_agent`. If no candidate fits, it creates a new agent through that same tool. Material ambiguity requires clarification.

Discovery identifies an agent; it does not guarantee complete recall. Preserve the existing execution-history policy and supply relevant conversation context in delegation instructions.

## Task ownership and reuse

Manual testing exposed an overly broad reuse policy: Chipotle order analysis, September 1 inbox lookup, Jai correspondence, and a Subaru parts-order search all reused `Email Search Agent`. The directory correctly saved four dispatches to one name, but its purpose still described only Chipotle orders. A generic name and the original instruction to prefer reuse encouraged routing by shared capability.

The Interaction Agent prompt and tool descriptions now define an agent as a specific ongoing task. Search using the subject, person, project, order, or thread; inspect each candidate's purpose before reusing it. Shared email-search tools or the same mailbox are insufficient. New independent tasks get distinct names and purposes; direct refinements reuse the task owner.

| Request                                              | Intended task owner     |
| ---------------------------------------------------- | ----------------------- |
| Count Chipotle orders in the last year               | `Chipotle Orders`       |
| Find emails received on September 1                  | `September 1 Emails`    |
| Find the email from Jai                              | `Emails from Jai`       |
| Find the Subaru dealership parts-order email         | `Subaru Parts Order`    |
| Limit those Chipotle orders to the last three months | Reuse `Chipotle Orders` |

These are naming examples, not hardcoded mappings. Generic capability names such as `Email Search Agent` are discouraged for new agents. Existing generic agents remain unchanged and may be reused when their purpose or referenced exchange establishes ownership of the same task. The implementation does not rename agents, split mixed execution logs, or overwrite purposes to absorb unrelated work.

This is a model routing policy, not a deterministic task classifier. Keyword search can return an irrelevant candidate; the Interaction Agent must reject it. Five chat messages do not necessarily create five agents, so manual discovery tests must check distinct names in the Execution view or `roster.json`.

## Directory and search

Extend `roster.json` from names to versioned records with an exact agent name, a short purpose, and a UTC last-used timestamp. The Interaction Agent supplies the purpose when creating an agent; cap it at 256 characters. Update activity on successful dispatch. Keep the latest 20 dispatch associations and include at most five distinct agent references in prompts, capped at 8 KiB. Migrate legacy names without renaming agents or changing their log references; use the name as the initial purpose and leave unknown timestamps unset. Use a stable lock file around read-modify-write operations and atomic replacement to avoid lost concurrent updates. Before migrating, preserve the original file as `roster.legacy.json`; rollback to the old application requires restoring that backup. Clear Data deletes the backup too.

Search names and purposes using case-insensitive word matching. Rank case-folded exact name matches first, then unique query-token matches weighted twice for names and once for purposes, then recency, with names breaking ties deterministically. Matching does not stem words or infer synonyms. Exclude entries with no textual match. Cap queries at 256 characters and serialized results at 8 KiB, returning only complete records. Report whether additional matches were omitted; allow a more specific query.

An empty result means no lexical match, not proof that no suitable agent exists. A storage failure returns an explicit error, never an empty success or the full roster. Validate reused names against the directory. Clear Data removes the directory and exchange associations.

## Integration and tradeoffs

The integration updates directory storage, Interaction Agent tool schemas and registry, prompt construction and instructions, request-to-agent associations, and context compaction framing. Discovery remains available during normal interaction; preserve the runtime's existing result-delivery restrictions.

Keyword search adds no embedding service or model call, but misses paraphrases. Discovery can add a model round trip, and instructions alone cannot guarantee search before creation. Start with this inspectable approach; defer embeddings, clustering, and automatic archiving until routing evaluations justify them.

## Validation

`server/tests/test_agent_discovery.py` covers migration and backups, ranking, Unicode matching, result and prompt bounds with a thousand dormant agents, concurrent writes, corruption, reset and cancellation, search-to-dispatch routing, direct follow-ups, creation, lookup failures, trace redaction, and result-delivery restrictions. Lookup telemetry records latency, result counts, truncation, and creation/reuse without query text. Model routing remains probabilistic: instructions encourage search before creation but do not enforce it at runtime.

The opt-in live evaluation passed against the configured `openrouter/free` router: four independent email tasks selected four distinct owners, the Subaru follow-up reused its owner, and the Chipotle follow-up returned to its original owner rather than the most recent agent. The existing generic Chipotle agent retained its purpose. This is one synthetic run, not a guarantee across models or real conversations. Prettier formats Markdown; the available Prettier Python plugin cannot run with this environment, so changed Python files are formatted with Ruff at the requested 500-column width.

To evaluate the task-ownership policy against the configured model with synthetic requests and intercepted tools, run:

```sh
OPENPOKE_EVALUATE_AGENT_DISCOVERY=1 .venv/bin/python -m unittest server.tests.test_agent_discovery.LiveTaskOwnershipTests -v
```

This opt-in check seeds a legacy generic Chipotle agent, runs the four independent email tasks, and checks direct follow-up reuse. It sends only synthetic prompts to the configured model and writes to a temporary directory; it never searches Gmail, dispatches workers, or sends user messages. Normal regression runs skip this network-dependent check.
