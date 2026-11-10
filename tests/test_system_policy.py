import os
import sys
import unittest
from unittest.mock import patch

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from config.settings import Config
from utils.system_policy import (
    effective_required_components,
    enabled_experimental_subsystems,
    resolve_dependency_impact,
    subsystem_runtime_enabled,
)


class SystemPolicyTests(unittest.TestCase):
    def test_experimental_subsystem_requires_runtime_allowlist(self):
        with patch.object(Config, "ENABLE_VOICE", True), \
             patch.object(Config, "EXPERIMENTAL_RUNTIME_ALLOWLIST", ""):
            self.assertFalse(subsystem_runtime_enabled("voice"))

    def test_experimental_subsystem_requires_flag_and_allowlist(self):
        with patch.object(Config, "ENABLE_VOICE", True), \
             patch.object(Config, "EXPERIMENTAL_RUNTIME_ALLOWLIST", "voice"):
            self.assertTrue(subsystem_runtime_enabled("voice"))

    def test_experimental_components_never_become_required(self):
        required = effective_required_components(["web", "voice", "scheduler", "mqtt"])
        self.assertEqual(("web", "scheduler"), required)

    def test_enabled_experimental_subsystems_are_explicit(self):
        with patch.object(Config, "ENABLE_VOICE", True), \
             patch.object(Config, "ENABLE_STREAM", True), \
             patch.object(Config, "EXPERIMENTAL_RUNTIME_ALLOWLIST", "voice"):
            self.assertEqual(("voice",), enabled_experimental_subsystems())

    def test_support_dependency_impact_is_explicit(self):
        impact = resolve_dependency_impact("redis", "down")
        self.assertEqual("support", impact.tier)
        self.assertEqual("event_bus", impact.scope)
        self.assertEqual("degraded", impact.effect)
        self.assertEqual("support_dependency_down=redis:down:event_bus", impact.reason)


if __name__ == "__main__":
    unittest.main()

