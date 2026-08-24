"""Deterministic policy resolution tests."""

from typing import cast

import pytest
from hypothesis import given
from hypothesis import strategies as st

from airgap.policy import PolicyRule, matches_tool, resolve
from airgap.vocab import PolicyAction


def _policy(action: PolicyAction) -> PolicyRule:
    return PolicyRule(
        tool_pattern="*",
        min_dial=10,
        action=action,
        relay_gated=False,
    )


def test_auto_approve_with_auto_approve_stays_auto_approve() -> None:
    assert (
        resolve(PolicyAction.AUTO_APPROVE, _policy(PolicyAction.AUTO_APPROVE), dial=0)
        is PolicyAction.AUTO_APPROVE
    )


def test_auto_approve_with_escalate_escalates() -> None:
    assert (
        resolve(PolicyAction.AUTO_APPROVE, _policy(PolicyAction.ESCALATE), dial=0)
        is PolicyAction.ESCALATE
    )


def test_auto_approve_with_block_blocks() -> None:
    assert (
        resolve(PolicyAction.AUTO_APPROVE, _policy(PolicyAction.BLOCK), dial=0)
        is PolicyAction.BLOCK
    )


def test_escalate_with_auto_approve_stays_escalated() -> None:
    assert (
        resolve(PolicyAction.ESCALATE, _policy(PolicyAction.AUTO_APPROVE), dial=0)
        is PolicyAction.ESCALATE
    )


def test_escalate_with_escalate_stays_escalated() -> None:
    assert (
        resolve(PolicyAction.ESCALATE, _policy(PolicyAction.ESCALATE), dial=0)
        is PolicyAction.ESCALATE
    )


def test_escalate_with_block_blocks() -> None:
    assert (
        resolve(PolicyAction.ESCALATE, _policy(PolicyAction.BLOCK), dial=0)
        is PolicyAction.BLOCK
    )


def test_block_with_auto_approve_stays_blocked() -> None:
    assert (
        resolve(PolicyAction.BLOCK, _policy(PolicyAction.AUTO_APPROVE), dial=0)
        is PolicyAction.BLOCK
    )


def test_block_with_escalate_stays_blocked() -> None:
    assert (
        resolve(PolicyAction.BLOCK, _policy(PolicyAction.ESCALATE), dial=0)
        is PolicyAction.BLOCK
    )


def test_block_with_block_stays_blocked() -> None:
    assert (
        resolve(PolicyAction.BLOCK, _policy(PolicyAction.BLOCK), dial=0)
        is PolicyAction.BLOCK
    )


def test_unmatched_tool_defaults_policy_action_to_escalate() -> None:
    assert resolve(PolicyAction.AUTO_APPROVE, None, dial=0) is PolicyAction.ESCALATE


def test_unmatched_tool_does_not_widen_warden_block() -> None:
    assert resolve(PolicyAction.BLOCK, None, dial=0) is PolicyAction.BLOCK


def test_dial_below_minimum_preserves_auto_approve() -> None:
    policy = PolicyRule(
        tool_pattern="file.read",
        min_dial=7,
        action=PolicyAction.AUTO_APPROVE,
        relay_gated=False,
    )

    assert (
        resolve(PolicyAction.AUTO_APPROVE, policy, dial=6) is PolicyAction.AUTO_APPROVE
    )


def test_dial_at_minimum_escalates_auto_approve() -> None:
    policy = PolicyRule(
        tool_pattern="file.read",
        min_dial=7,
        action=PolicyAction.AUTO_APPROVE,
        relay_gated=False,
    )

    assert resolve(PolicyAction.AUTO_APPROVE, policy, dial=7) is PolicyAction.ESCALATE


def test_dial_does_not_widen_block() -> None:
    policy = PolicyRule(
        tool_pattern="db.drop_*",
        min_dial=7,
        action=PolicyAction.BLOCK,
        relay_gated=False,
    )

    assert resolve(PolicyAction.AUTO_APPROVE, policy, dial=7) is PolicyAction.BLOCK


def test_relay_gated_auto_approve_is_forced_to_escalate() -> None:
    policy = PolicyRule(
        tool_pattern="bench.energize",
        min_dial=10,
        action=PolicyAction.AUTO_APPROVE,
        relay_gated=True,
    )

    assert resolve(PolicyAction.AUTO_APPROVE, policy, dial=0) is PolicyAction.ESCALATE


def test_invalid_warden_action_cannot_fall_through_to_auto_approve() -> None:
    invalid_action = cast(PolicyAction, "allow")

    with pytest.raises(ValueError, match="allow"):
        resolve(invalid_action, _policy(PolicyAction.AUTO_APPROVE), dial=0)


def test_invalid_policy_action_cannot_fall_through_to_auto_approve() -> None:
    policy = PolicyRule(
        tool_pattern="*",
        min_dial=10,
        action=cast(PolicyAction, "allow"),
        relay_gated=False,
    )

    with pytest.raises(ValueError, match="allow"):
        resolve(PolicyAction.AUTO_APPROVE, policy, dial=0)


def test_policy_action_without_row_metadata_is_rejected() -> None:
    incomplete = cast(PolicyRule, PolicyAction.AUTO_APPROVE)

    with pytest.raises(TypeError, match="complete PolicyRule"):
        resolve(PolicyAction.AUTO_APPROVE, incomplete, dial=0)


def test_policy_rule_requires_relay_gated_metadata() -> None:
    with pytest.raises(TypeError, match="relay_gated"):
        PolicyRule(  # type: ignore[call-arg]
            tool_pattern="bench.energize",
            min_dial=10,
            action=PolicyAction.AUTO_APPROVE,
        )


@pytest.mark.parametrize(
    "relay_gated",
    [None, 0, 0.0, "", (), [], {}],
    ids=("none", "zero-int", "zero-float", "empty-str", "tuple", "list", "dict"),
)
def test_policy_rule_rejects_falsey_non_bool_relay_gated(
    relay_gated: object,
) -> None:
    with pytest.raises(TypeError, match="relay_gated must be bool"):
        PolicyRule(
            tool_pattern="bench.energize",
            min_dial=10,
            action=PolicyAction.AUTO_APPROVE,
            relay_gated=cast(bool, relay_gated),
        )


@pytest.mark.parametrize(
    "relay_gated",
    [1, 1.0, "false", (False,), [False], {"relay": False}],
    ids=("one-int", "one-float", "str", "tuple", "list", "dict"),
)
def test_policy_rule_rejects_truthy_non_bool_relay_gated(
    relay_gated: object,
) -> None:
    with pytest.raises(TypeError, match="relay_gated must be bool"):
        PolicyRule(
            tool_pattern="bench.energize",
            min_dial=10,
            action=PolicyAction.AUTO_APPROVE,
            relay_gated=cast(bool, relay_gated),
        )


def test_tool_pattern_uses_case_sensitive_glob_matching() -> None:
    assert matches_tool("db.drop_*", "db.drop_users")
    assert not matches_tool("db.drop_*", "DB.drop_users")
    assert not matches_tool("db.drop_*", "db.create_users")


ACTION_RANK = {
    PolicyAction.BLOCK: 0,
    PolicyAction.ESCALATE: 1,
    PolicyAction.AUTO_APPROVE: 2,
}
POLICY_ACTIONS = st.sampled_from(tuple(PolicyAction))
POLICY_RULES = st.builds(
    PolicyRule,
    tool_pattern=st.text(),
    min_dial=st.integers(min_value=-32_768, max_value=32_767),
    action=POLICY_ACTIONS,
    relay_gated=st.booleans(),
)


@given(
    warden_verdict=POLICY_ACTIONS,
    policy=st.one_of(st.none(), POLICY_RULES),
    dial=st.integers(min_value=-32_768, max_value=32_767),
)
def test_resolution_never_widens_warden_verdict(
    warden_verdict: PolicyAction,
    policy: PolicyRule | None,
    dial: int,
) -> None:
    result = resolve(warden_verdict, policy, dial)

    assert ACTION_RANK[result] <= ACTION_RANK[warden_verdict]


@given(
    warden_verdict=POLICY_ACTIONS,
    policy=POLICY_RULES,
    first_dial=st.integers(min_value=-32_768, max_value=32_767),
    second_dial=st.integers(min_value=-32_768, max_value=32_767),
)
def test_turning_dial_up_never_makes_outcome_more_permissive(
    warden_verdict: PolicyAction,
    policy: PolicyRule,
    first_dial: int,
    second_dial: int,
) -> None:
    lower_dial, higher_dial = sorted((first_dial, second_dial))

    lower_result = resolve(warden_verdict, policy, lower_dial)
    higher_result = resolve(warden_verdict, policy, higher_dial)

    assert ACTION_RANK[higher_result] <= ACTION_RANK[lower_result]
