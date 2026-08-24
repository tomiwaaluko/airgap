"""Pure deterministic policy boundary."""

from dataclasses import dataclass
from fnmatch import fnmatchcase

from airgap.vocab import PolicyAction


@dataclass(frozen=True, slots=True, kw_only=True)
class PolicyRule:
    """Keep the pure resolver independent from persistence-layer models."""

    tool_pattern: str
    min_dial: int
    action: PolicyAction
    relay_gated: bool

    def __post_init__(self) -> None:
        if type(self.relay_gated) is not bool:
            raise TypeError("relay_gated must be bool")


type PolicyInput = PolicyRule | None


def matches_tool(tool_pattern: str, tool_name: str) -> bool:
    """Keep identifier matching case-sensitive on every host platform."""
    return fnmatchcase(tool_name, tool_pattern)


def resolve(
    warden_verdict: PolicyAction,
    policy_action: PolicyInput,
    dial: int,
) -> PolicyAction:
    """Order both inputs by restrictiveness so no branch can widen the Warden."""
    effective_warden = PolicyAction(warden_verdict)
    if policy_action is None:
        effective_policy = PolicyAction.ESCALATE
    else:
        if not isinstance(policy_action, PolicyRule):
            raise TypeError("policy_action must be a complete PolicyRule or None")
        effective_policy = PolicyAction(policy_action.action)
        must_escalate = policy_action.relay_gated or dial >= policy_action.min_dial
        if effective_policy is PolicyAction.AUTO_APPROVE and must_escalate:
            effective_policy = PolicyAction.ESCALATE

    if effective_warden is PolicyAction.BLOCK or effective_policy is PolicyAction.BLOCK:
        return PolicyAction.BLOCK
    if (
        effective_warden is PolicyAction.ESCALATE
        or effective_policy is PolicyAction.ESCALATE
    ):
        return PolicyAction.ESCALATE
    return PolicyAction.AUTO_APPROVE
