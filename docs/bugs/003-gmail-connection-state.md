# BUG-003: Gmail connection state is lost or absent from agent context

Discovered: 2026-10-21. Status: implemented; regression tests pass.

## Symptoms and reproduction

Connect Gmail, then restart the backend or continue a conversation that previously said Gmail was unavailable. Settings can show a connected account while chat insists Gmail is disconnected.

## Cause and fix

The active Gmail user was held only in process memory. Browser localStorage retained connection metadata across restarts, producing conflicting state. Neither agent's system prompt included current integration state, allowing older conversation claims to dominate.

`server/services/gmail/client.py` now persists the user in `server/data/gmail_user.txt` after a successful status check and reads it when resolving the active account. Starting OAuth alone no longer marks Gmail connected. A known inactive account or disconnect clears the preference; a transient status lookup failure does not erase an existing verified preference.

Both agent prompt builders include current integration context and distinguish Gmail capability from the absent calendar integration.

## Verification and limits

`server/tests/test_gmail_connection_state.py` covers persistence, changes visible to existing callers, current state in both prompts, pending OAuth, active/inactive accounts, and transient lookup failures.

After loading this change, use Settings → Refresh status once to migrate an already connected account into persistent state. Persisted verification does not guarantee a token remains valid indefinitely; actual tool errors remain authoritative. Prompt instructions reduce stale claims but cannot guarantee that every model response is grounded.
