# Probabilistic Cognition

Elara's decision intelligence now separates three related but different concepts:

- posterior probability
- propagated confidence
- calibrated uncertainty

This is intentionally not a claim of mature probabilistic cognition. It is the foundation needed before Bayesian reasoning, causal inference, replay evaluation, and distributed validation can safely converge.

## Components

`BayesianSignalModel`

- converts reasoning components into evidence
- produces posterior probability, variance, conflict, and effective sample size

`ConfidencePropagationEngine`

- propagates confidence through support, neutral, degrade, and block components
- estimates confidence spread and conflict
- prevents final confidence from being treated as a flat strategy score

`AdaptiveUncertaintyCalibrator`

- raises uncertainty when calibration observations are poor
- raises uncertainty under degraded or safe runtime mode
- accounts for confidence propagation conflict

`ProbabilisticDecisionEngine`

- combines posterior probability, confidence propagation, and uncertainty calibration
- writes explainability fields into `ReasonedDecision.component_scores`

## Rule

A future model may be smarter, but it must still expose:

- probability
- raw uncertainty
- calibrated uncertainty
- propagated confidence
- evidence conflict
- reasons

Opaque confidence is not enough for governed trading cognition.
