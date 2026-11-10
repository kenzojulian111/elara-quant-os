import time
import unittest

from core.life import LifeManager


class LifeManagerTests(unittest.TestCase):
    def test_trading_denied_when_sleep_below_threshold(self):
        life = LifeManager(min_sleep_hours=6.0, max_stress=7, min_focus_minutes=120)
        life.update(sleep_hours=5.5, energy=80, focus_minutes=180, stress=3)
        decision = life.evaluate_trading()
        self.assertFalse(decision.allowed)
        self.assertIn("Tidur kurang", decision.reason)

    def test_trading_denied_when_context_stale(self):
        life = LifeManager(context_max_age_sec=1)
        life.update(sleep_hours=8, energy=70, focus_minutes=150, stress=2)
        ctx = life.get_context()
        ctx.last_update = time.time() - 10
        decision = life.evaluate_trading()
        self.assertFalse(decision.allowed)
        self.assertIn("stale", decision.reason.lower())

    def test_entertainment_denied_when_focus_below_threshold(self):
        life = LifeManager(min_focus_minutes=180)
        life.update(sleep_hours=8, energy=70, focus_minutes=120, stress=2)
        decision = life.evaluate_entertainment()
        self.assertFalse(decision.allowed)
        self.assertIn("Fokus belum", decision.reason)

    def test_signal_callback_only_on_state_change(self):
        events = []

        def _collector(payload):
            events.append(payload)

        life = LifeManager(min_sleep_hours=7, signal_callback=_collector)

        life.update(sleep_hours=8, energy=80, focus_minutes=200, stress=1)
        life.update(sleep_hours=8, energy=75, focus_minutes=220, stress=1)
        life.update(sleep_hours=4, energy=70, focus_minutes=200, stress=2)
        life.update(sleep_hours=4, energy=65, focus_minutes=210, stress=2)

        self.assertEqual(len(events), 2)
        self.assertTrue(events[0]["allowed"])
        self.assertFalse(events[1]["allowed"])

    def test_update_accepts_numeric_string_inputs(self):
        life = LifeManager(min_sleep_hours=6.0, max_stress=7, min_focus_minutes=120)
        ctx = life.update(
            sleep_hours="7.5",
            energy="88",
            focus_minutes="150",
            stress="3",
        )
        self.assertEqual(ctx.sleep_hours, 7.5)
        self.assertEqual(ctx.energy, 88)
        self.assertEqual(ctx.focus_minutes, 150)
        self.assertEqual(ctx.stress, 3)
        self.assertTrue(life.evaluate_trading().allowed)


if __name__ == "__main__":
    unittest.main()

