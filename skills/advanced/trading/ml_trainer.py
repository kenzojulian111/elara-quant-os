import os
import logging
from sklearn.ensemble import RandomForestClassifier
from utils.trusted_pickle import dump_pickle_artifact, load_pickle_artifact

log = logging.getLogger("elara.ml_trainer")


def train_model(data_path: str, model_path: str, min_samples: int = 100) -> bool:
    if not os.path.exists(data_path):
        return False
    try:
        payload = load_pickle_artifact(data_path, purpose="ml_train_payload")
        features = payload.get("features")
        target = payload.get("target")
        if features is None or target is None:
            return False
        if len(features) < min_samples:
            return False

        model = RandomForestClassifier(
            n_estimators=100,
            max_depth=5,
            min_samples_split=10,
            class_weight='balanced',
            n_jobs=-1,
            random_state=42
        )
        model.fit(features, target.loc[features.index])

        dump_pickle_artifact(model, model_path, purpose="ml_model")
        return True
    except Exception as e:
        log.error(f"ML train error: {e}")
        return False

