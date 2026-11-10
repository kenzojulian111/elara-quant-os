import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from trading_core.coordination.event_ledger import EventLedger
from trading_core.protocols import build_core_contract_registry, validate_core_contracts
from trading_core.roadmap import SubsystemConstitutionChecker, default_subsystem_constitutions


class ArchitecturePreservationTests(unittest.TestCase):
    def test_core_contracts_are_frozen_platform_protocol(self):
        report = validate_core_contracts()
        contracts = {contract.contract_id: contract for contract in build_core_contract_registry()}

        self.assertTrue(report.ok, report.to_dict())
        self.assertIn("decision_envelope", contracts)
        self.assertIn("reasoned_decision", contracts)
        self.assertEqual("frozen", contracts["decision_envelope"].stability)
        self.assertIn("reasoned_decision", contracts["decision_envelope"].field_names)

    def test_subsystem_constitution_has_authority_and_forbidden_actions(self):
        report = SubsystemConstitutionChecker(ROOT).build_report()
        constitutions = {item.subsystem: item for item in default_subsystem_constitutions()}

        self.assertTrue(report.ok, report.to_dict())
        self.assertIn("decision_reasoner", constitutions)
        self.assertIn("approve_entry_without_execution_plan", constitutions["decision_reasoner"].forbidden_actions)
        self.assertIn("REASONING_SYNTHESIZED", constitutions["decision_reasoner"].produced_events)

    def test_event_ledger_records_are_schema_versioned_and_hash_chained(self):
        with tempfile.TemporaryDirectory(prefix="elara_event_ledger_contract_") as tmpdir:
            ledger = EventLedger(os.path.join(tmpdir, "ledger.jsonl"))
            ledger.append("decision", {"trace_id": "trace-1", "action_id": "action-1", "status": "ready"})
            ledger.append("execution", {"trace_id": "trace-1", "action_id": "action-1", "status": "shadowed"})

            records = list(ledger.read())
            ok, reason = ledger.verify_hash_chain()

        self.assertTrue(ok, reason)
        self.assertEqual([1, 2], [record["sequence"] for record in records])
        self.assertEqual("trace-1", records[0]["trace_id"])
        self.assertEqual("action-1", records[0]["correlation_id"])
        self.assertEqual(records[0]["event_hash"], records[1]["previous_hash"])

    def test_event_ledger_hash_chain_detects_tampering(self):
        with tempfile.TemporaryDirectory(prefix="elara_event_ledger_tamper_") as tmpdir:
            path = os.path.join(tmpdir, "ledger.jsonl")
            ledger = EventLedger(path)
            ledger.append("decision", {"trace_id": "trace-1", "status": "ready"})

            with open(path, "r", encoding="utf-8") as handle:
                row = json.loads(handle.readline())
            row["payload"]["status"] = "changed"
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(json.dumps(row, sort_keys=True) + "\n")

            ok, reason = EventLedger(path).verify_hash_chain()

        self.assertFalse(ok)
        self.assertIn("event_hash_mismatch", reason)


if __name__ == "__main__":
    unittest.main()
