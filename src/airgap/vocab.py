"""Shared closed vocabularies for the Airgap contracts."""

from enum import StrEnum


class CommandName(StrEnum):
    PING = "ping"
    LED = "led"
    TONE = "tone"
    FLAG = "flag"
    RELAY = "relay"
    RELAY_RENEW = "relay_renew"
    LCD = "lcd"
    ARM = "arm"
    DISARM = "disarm"


class AckErrorCode(StrEnum):
    UNKNOWN_CMD = "unknown_cmd"
    BAD_FIELD = "bad_field"
    OUT_OF_RANGE = "out_of_range"
    NOT_ARMED = "not_armed"
    NOT_CLOSED = "not_closed"
    BUSY = "busy"


class EventName(StrEnum):
    BUTTON = "btn"
    BOOT = "boot"
    LEASE_EXPIRED = "lease_expired"
    TICK = "tick"


class LedState(StrEnum):
    OFF = "off"
    GREEN = "green"
    AMBER = "amber"
    RED = "red"


class TonePattern(StrEnum):
    OK = "ok"
    DENY = "deny"
    ALERT = "alert"


class Verdict(StrEnum):
    APPROVED = "approved"
    DENIED = "denied"
    EXPIRED = "expired"
    LINK_LOST = "link_lost"


class DecidedBy(StrEnum):
    HUMAN = "human"
    POLICY = "policy"
    WARDEN_AUTO = "warden_auto"
    SYSTEM = "system"


class AuditEvent(StrEnum):
    REQUEST_CREATED = "request_created"
    WARDEN_VERDICT = "warden_verdict"
    POLICY_OVERRIDE = "policy_override"
    ARMED = "armed"
    BUTTON = "button"
    RELAY_CLOSED = "relay_closed"
    RELAY_OPENED = "relay_opened"
    LEASE_EXPIRED = "lease_expired"
    RESOLVED = "resolved"
    SAFE_STATE = "safe_state"


class PolicyAction(StrEnum):
    AUTO_APPROVE = "auto_approve"
    ESCALATE = "escalate"
    BLOCK = "block"

