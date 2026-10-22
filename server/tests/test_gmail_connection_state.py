import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from server.agents.execution_agent.agent import ExecutionAgent
from server.agents.interaction_agent.agent import build_system_prompt
from server.config import Settings
from server.models import GmailConnectPayload, GmailStatusPayload
from server.services.gmail import client


class GmailConnectionStateTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "gmail_user.txt"
        patcher = patch.object(client, "_ACTIVE_USER_PATH", self.path)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_active_user_is_persisted_and_external_changes_are_observed(self):
        client._set_active_gmail_user_id("user_one")
        self.assertEqual(self.path.read_text(), "user_one")
        self.path.write_text("user_two")
        self.assertEqual(client.get_active_gmail_user_id(), "user_two")
        client._set_active_gmail_user_id(None)
        self.assertIsNone(client.get_active_gmail_user_id())
        self.assertFalse(self.path.exists())

    def test_both_agent_prompts_follow_current_connection_state(self):
        agent = ExecutionAgent.__new__(ExecutionAgent)
        agent.name = "Email helper"
        for make_prompt in (build_system_prompt, agent.build_system_prompt):
            self.assertIn("No verified Gmail connection", make_prompt())
            client._set_active_gmail_user_id("user_test")
            prompt = make_prompt()
            self.assertIn("A verified Gmail connection is configured", prompt)
            self.assertIn("supersedes connection claims in conversation history", prompt)
            self.assertIn("no calendar integration", prompt)
            client._set_active_gmail_user_id(None)

    def test_only_active_accounts_are_persisted(self):
        accounts = Mock()
        for status in ("INITIALIZING", "ACTIVE", "EXPIRED"):
            accounts.wait_for_connection.return_value = SimpleNamespace(status=status, user_id="user_test")
            with patch.object(client, "_get_composio_client", return_value=SimpleNamespace(connected_accounts=accounts)), patch.object(client, "_fetch_profile_from_composio", return_value=None):
                response = client.fetch_status(GmailStatusPayload(user_id="user_test", connection_request_id="ca_test"))
            self.assertEqual(json.loads(response.body)["connected"], status == "ACTIVE")
            self.assertEqual(client.get_active_gmail_user_id(), "user_test" if status == "ACTIVE" else None)

    def test_starting_oauth_does_not_mark_gmail_as_connected(self):
        accounts = Mock()
        accounts.link.return_value = SimpleNamespace(id="ca_test", redirect_url="https://example.com/oauth")
        with patch.object(client, "_get_composio_client", return_value=SimpleNamespace(connected_accounts=accounts)):
            response = client.initiate_connect(GmailConnectPayload(user_id="user_test", auth_config_id="ac_test"), Settings())
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(client.get_active_gmail_user_id())

    def test_transient_status_failure_preserves_verified_connection(self):
        client._set_active_gmail_user_id("user_test")
        accounts = Mock()
        accounts.wait_for_connection.side_effect = RuntimeError("temporary failure")
        accounts.get.side_effect = RuntimeError("temporary failure")
        accounts.list.side_effect = RuntimeError("temporary failure")
        with patch.object(client, "_get_composio_client", return_value=SimpleNamespace(connected_accounts=accounts)):
            client.fetch_status(GmailStatusPayload(user_id="user_test", connection_request_id="ca_test"))
        self.assertEqual(client.get_active_gmail_user_id(), "user_test")


if __name__ == "__main__":
    unittest.main()
