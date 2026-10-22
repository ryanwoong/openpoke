# BUG-002: Model logs report the startup default

Discovered: 2026-10-19. Status: implemented; regression tests pass.

## Symptoms and reproduction

Select another model in Settings, save, and send a request that creates an execution agent. The agent logs `anthropic/claude-sonnet-4` even though the shared OpenRouter client overrides that default with the saved selection.

## Cause and fix

Agent runtimes cache their default model at initialization. Logging happened before the shared client's model override, so the log did not describe the outgoing payload.

`server/openrouter_client/client.py` now logs the requested model from the actual payload and the model returned in OpenRouter's response. Agent-level logs no longer claim the cached default is the model being sent.

## Verification and limits

`server/tests/test_model_settings.py` verifies that saving a selection changes the next outgoing request even when a caller keeps passing its startup default, and that logs match the request and response.

`openrouter/free` is a router selection; its returned model can differ from the requested identifier. A missing response model is logged as `unknown`, not guessed. These tests use mocked HTTP responses and do not verify the provider used by a historical live request.
