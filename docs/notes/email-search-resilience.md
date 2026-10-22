# Email search resilience

Recorded: 2026-10-21. Classification: development note, not a bug. Error handling improvements are implemented; the cause of the observed live TLS error remains unverified.

## Observed behavior

Ask for today's emails. A search can return `[]` even when the model never calls Gmail or a request fails. The chat can then claim Gmail is disconnected after an SSL error, despite a successful Gmail operation in the same session.

Observed evidence: one Gmail draft lookup succeeded; a later search recorded `SSLV3_ALERT_BAD_RECORD_MAC`; another search returned an empty list. These logs do not establish that Gmail authentication failed or conclusively identify the original TLS failure's service.

## Error handling improvements

`server/agents/execution_agent/tasks/search_email/tool.py` treated model completion without a Gmail query as a successful empty result. Gmail errors could be lost when the model later selected zero messages, and unsuccessful Composio payloads could be parsed as empty messages.

The search now requires at least one successful Gmail query before returning results, propagates unsuccessful Composio responses, rejects incomplete searches reported as no matches, and rejects model termination that silently drops found messages.

`server/openrouter_client/client.py` labels raw SSL exceptions as OpenRouter errors and retries connection/TLS failures once with a fresh client. It does not disable certificate verification. Direct certificate verification failures are not retried. Integration context instructs agents to distinguish model-provider failures from Gmail API failures and successful zero-match searches.

## Verification and limits

`server/tests/test_email_search_failures.py` covers skipped searches, failed queries, unsuccessful Composio payloads, legitimate zero-match searches, bounded TLS retry, and error-source labeling.

The tests simulate transport failures. They do not establish the cause of the original live SSL error or prove that it will recover. Repeated failures require inspection of the new service-specific error logs; reconnecting Gmail is not justified solely by an SSL error.
