# Cognition Mesh Beginnings

Elara now has minimal contracts for distributed cognition, not a full distributed reasoning mesh.

The current pieces are intentionally small:

- `ReasoningNode`: describes a reasoning worker and its capabilities
- `ReasoningNodeHeartbeat`: exposes health, latency, queue depth, and capabilities
- `MemoryNode`: bounded context memory for recent cognitive artifacts
- `AdaptiveComputeRouter`: chooses local, remote, minimal, standard, deep, or defer routing

## Routing Rule

Runtime safety outranks cognitive depth.

If runtime mode is `safe`, routing falls back to local minimal reasoning. If uncertainty is high and a healthy remote reasoning node exists, routing may select deeper validation.

## Not Yet Mature

This is not yet:

- distributed consensus
- multi-agent validation
- autonomous recursive evaluation
- cross-node replay
- Rust runtime mesh

It is the contract layer needed before those become safe to build.
