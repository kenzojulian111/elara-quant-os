import json
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class ObservabilityDeployTests(unittest.TestCase):
    def test_observability_configs_are_present(self):
        required = [
            ROOT / "deploy" / "observability" / "prometheus" / "prometheus.yml",
            ROOT / "deploy" / "observability" / "prometheus" / "alerts.yml",
            ROOT / "deploy" / "observability" / "grafana" / "provisioning" / "datasources" / "datasources.yml",
            ROOT / "deploy" / "observability" / "grafana" / "dashboards" / "elara-runtime.json",
            ROOT / "deploy" / "observability" / "loki" / "loki.yml",
            ROOT / "deploy" / "observability" / "promtail" / "promtail.yml",
            ROOT / "deploy" / "observability" / "tempo" / "tempo.yml",
        ]

        for path in required:
            self.assertTrue(path.exists(), f"missing observability config: {path}")

    def test_compose_declares_observability_profile_services(self):
        compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")

        for service in ("prometheus:", "grafana:", "loki:", "promtail:", "tempo:"):
            self.assertIn(service, compose)
        self.assertIn("profiles:", compose)
        self.assertIn("observability", compose)
        self.assertIn("/metrics", (ROOT / "deploy" / "observability" / "prometheus" / "prometheus.yml").read_text(encoding="utf-8"))

    def test_grafana_dashboard_json_is_valid(self):
        dashboard_path = ROOT / "deploy" / "observability" / "grafana" / "dashboards" / "elara-runtime.json"
        dashboard = json.loads(dashboard_path.read_text(encoding="utf-8"))

        self.assertEqual("Elara Runtime", dashboard["title"])
        self.assertGreaterEqual(len(dashboard.get("panels") or []), 4)
        self.assertEqual("elara-runtime", dashboard["uid"])


if __name__ == "__main__":
    unittest.main()
