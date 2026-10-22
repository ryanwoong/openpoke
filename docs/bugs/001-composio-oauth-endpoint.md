# BUG-001: Retired Composio OAuth endpoint

Discovered: 2026-10-19. Status: implemented; live OAuth verification pending.

## Symptoms and reproduction

Connect Gmail using a Composio-managed OAuth auth configuration. Authorization fails with `ComposioLegacyConnectedAccountsEndpointRetiredError`, instructing the caller to use `POST /api/v3/connected_accounts/link`.

## Cause and fix

`server/services/gmail/client.py` called `connected_accounts.initiate()`, which uses the retired endpoint for this configuration. It now calls `connected_accounts.link()` and preserves the redirect URL, connection ID, and user ID returned to the frontend.

Reference: [Composio migration guide](https://docs.composio.dev/docs/auth-configuration/migrating-initiate-to-link).

## Verification and limits

The installed SDK exposes `link()`. A mocked connection check confirmed the response contract. `test_starting_oauth_does_not_mark_gmail_as_connected` also exercises the new call. A real OAuth flow must still be verified; passing SDK mocks does not prove authorization succeeds.
