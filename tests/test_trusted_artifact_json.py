import os
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
from sklearn.ensemble import RandomForestClassifier

from config.settings import Config
from utils.trusted_pickle import dump_pickle_artifact, load_pickle_artifact


class TrustedArtifactJsonTests(unittest.TestCase):
    def test_roundtrip_json_payload(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "artifact.json")
            payload = {"name": "elara", "n": 7, "active": True, "scores": [1, 2, 3]}
            with patch.object(Config, "ML_ARTIFACT_SIGNING_KEY", "unit-sign-key"), patch.object(
                Config, "ML_REQUIRE_SIGNED_ARTIFACTS", True
            ):
                self.assertTrue(dump_pickle_artifact(payload, path, purpose="unit_payload"))
                loaded = load_pickle_artifact(path, purpose="unit_payload")
            self.assertEqual(payload, loaded)
            self.assertTrue(os.path.exists(path + ".sig"))

    def test_random_forest_model_roundtrip_without_pickle(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "model.json")
            x = np.random.RandomState(42).rand(120, 4)
            y = np.random.RandomState(7).randint(0, 2, size=120)
            model = RandomForestClassifier(n_estimators=4, random_state=11)
            model.fit(x, y)
            expected = model.predict(x[:10]).tolist()

            with patch.object(Config, "ML_ARTIFACT_ALLOWED_CLASS_PREFIXES", "sklearn."), patch.object(
                Config, "ML_ARTIFACT_SIGNING_KEY", "unit-sign-key"
            ):
                self.assertTrue(dump_pickle_artifact(model, path, purpose="ml_model"))
                loaded = load_pickle_artifact(path, purpose="ml_model")

            self.assertEqual(expected, loaded.predict(x[:10]).tolist())

    def test_legacy_pickle_disabled_by_default(self):
        import pickle

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "legacy.pkl")
            with open(path, "wb") as f:
                pickle.dump({"legacy": True}, f, protocol=pickle.HIGHEST_PROTOCOL)

            with patch.object(Config, "ML_ARTIFACT_ALLOW_LEGACY_PICKLE", False):
                with self.assertRaises(ValueError):
                    load_pickle_artifact(path, purpose="legacy_artifact")


if __name__ == "__main__":
    unittest.main()

