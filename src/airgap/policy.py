"""Pure deterministic policy boundary."""

from airgap import vocab


def not_implemented() -> None:
    """Reserve the public implementation boundary for the policy ticket."""
    _ = vocab
    raise NotImplementedError
