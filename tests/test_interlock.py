"""Relay interlock and cycle: Rules 4, 4a, 4b, 4c."""

from __future__ import annotations

import json

import pytest
from test_supervisor import AutoAckTransport, Clock, _frames, _run

from airgap.protocol import (
    Ack,
    ArmCommand,
    ButtonEvent,
    LeaseExpiredEvent,
    RelayCommand,
    RelayRenewCommand,
    TickEvent,
)
from airgap.supervisor import Supervisor, SupervisorRejection
from airgap.vocab import AckErrorCode, AuditEvent, DecidedBy, Verdict

RID = "a91f3c2e"
OTHER = "deadbeef"


class RelayAwareTransport(AutoAckTransport):
    """Acks `relay_renew` with `not_closed` while the contact is open."""

    def __init__(self) -> None:
        super().__init__()
        self.contact_closed = False

    async def write(self, frame: bytes) -> Ack:
        payload = json.loads(frame.decode("ascii"))
        cmd = payload.get("cmd")
        if cmd == "relay_renew":
            self.writes.append(frame)
            if self.timeout_on_relay:
                raise NotImplementedError
            if not self.contact_closed:
                return Ack(int(payload["id"]), False, AckErrorCode.NOT_CLOSED)
            return Ack(int(payload["id"]), True)
        ack = await super().write(frame)
        if cmd == "relay":
            self.contact_closed = bool(payload["closed"])
        return ack


def _tick(
    *,
    t: int,
    btns: int = 0,
    relay: bool = False,
    armed: bool = True,
    lease_ms: int = 10_000,
) -> TickEvent:
    return TickEvent(
        dial=5,
        relay=relay,
        armed=armed,
        lease_ms=lease_ms,
        btns=btns,
        t=t,
    )


def _approve(*, req: str = RID, t: int = 1_000) -> ButtonEvent:
    return ButtonEvent(which="approve", req=req, t=t)


def _closes(transport: AutoAckTransport) -> list[dict[str, object]]:
    return [
        frame
        for frame in _frames(transport)
        if frame.get("cmd") == "relay" and frame.get("closed") is True
    ]


def _opens(transport: AutoAckTransport) -> list[dict[str, object]]:
    return [
        frame
        for frame in _frames(transport)
        if frame.get("cmd") == "relay" and frame.get("closed") is False
    ]


def _cmds(transport: AutoAckTransport) -> list[str]:
    return [str(frame["cmd"]) for frame in _frames(transport)]


def _make(
    *,
    transport: AutoAckTransport | None = None,
) -> tuple[
    Supervisor,
    AutoAckTransport,
    Clock,
    list[tuple[str, str, str]],
    list[tuple[str, str | None, object]],
    list[tuple[str, str]],
    list[str],
]:
    clock = Clock()
    transport = transport or AutoAckTransport()
    resolves: list[tuple[str, str, str]] = []
    audits: list[tuple[str, str | None, object]] = []
    pending: list[tuple[str, str]] = []
    order: list[str] = []

    original_write = transport.write

    async def _logged_write(frame: bytes) -> Ack:
        payload = json.loads(frame.decode("ascii"))
        order.append(f"write:{payload['cmd']}:{payload.get('closed', '')}")
        return await original_write(frame)

    transport.write = _logged_write  # type: ignore[method-assign]

    supervisor = Supervisor(
        transport,
        clock=clock,
        on_resolve_pending=lambda request_id, verdict: pending.append(
            (request_id, verdict)
        ),
        on_resolve=lambda request_id, verdict, decided_by: (
            order.append(f"resolve:{verdict}:{decided_by}"),
            resolves.append((request_id, verdict, decided_by)),
        ),
        on_audit=lambda event, request_id, payload: (
            order.append(f"audit:{event}"),
            audits.append((event, request_id, payload)),
        ),
    )
    return supervisor, transport, clock, resolves, audits, pending, order


def _arm_acked(
    supervisor: Supervisor,
    *,
    request_id: str = RID,
    relay_gated: bool = True,
    dwell_s: int = 60,
    cmd_id: int = 1,
    device_t: int = 100,
) -> None:
    supervisor.arm(request_id, relay_gated=relay_gated, dwell_s=dwell_s)
    _run(supervisor.on_event(_tick(t=device_t, btns=0)))
    _run(supervisor.send(ArmCommand(id=cmd_id, req=request_id)))


def _advance(
    supervisor: Supervisor,
    clock: Clock,
    seconds: float,
    *,
    btns: int = 0,
    relay: bool = False,
    armed: bool = True,
) -> None:
    clock.advance(seconds)
    _run(
        supervisor.on_event(
            _tick(
                t=int(clock.t * 1000),
                btns=btns,
                relay=relay,
                armed=armed,
            )
        )
    )
    _run(supervisor.check_watchdog())


def test_interlock_condition_1_fails_when_not_armed() -> None:
    supervisor, transport, _, _, _, _, _ = _make()

    _run(supervisor.on_event(_approve()))
    with pytest.raises(SupervisorRejection, match="condition 1"):
        _run(supervisor.send(RelayCommand(id=1, closed=True)))
    assert _closes(transport) == []


def test_interlock_condition_2_fails_without_approve_button() -> None:
    supervisor, transport, _, _, _, _, _ = _make()
    _arm_acked(supervisor)

    with pytest.raises(SupervisorRejection, match="condition 2"):
        _run(supervisor.send(RelayCommand(id=2, closed=True)))
    assert _closes(transport) == []


def test_interlock_condition_3_fails_on_req_mismatch() -> None:
    supervisor, transport, _, _, _, _, _ = _make()
    _arm_acked(supervisor)
    _run(supervisor.on_event(_approve(req=OTHER, t=200)))

    with pytest.raises(SupervisorRejection, match="condition 3"):
        _run(supervisor.send(RelayCommand(id=2, closed=True)))
    assert _closes(transport) == []


def test_interlock_condition_4_fails_when_button_precedes_arm_ack() -> None:
    supervisor, transport, _, _, _, _, _ = _make()
    supervisor.arm(RID, relay_gated=True)
    _run(supervisor.on_event(_tick(t=50, btns=0)))
    _run(supervisor.on_event(_approve(t=50)))
    _run(supervisor.send(ArmCommand(id=1, req=RID)))

    with pytest.raises(SupervisorRejection, match="condition 4"):
        _run(supervisor.send(RelayCommand(id=2, closed=True)))
    assert _closes(transport) == []


def test_interlock_condition_5_fails_after_30_seconds() -> None:
    supervisor, transport, clock, _, _, _, _ = _make()
    _arm_acked(supervisor, device_t=100)
    clock.advance(30.0)
    _run(supervisor.on_event(_tick(t=30_200, btns=0)))
    _run(supervisor.on_event(_approve(t=200)))

    with pytest.raises(SupervisorRejection, match="condition 5"):
        _run(supervisor.send(RelayCommand(id=2, closed=True)))
    assert _closes(transport) == []


def test_resolve_then_close_succeeds_because_armed_survives() -> None:
    supervisor, transport, _, resolves, _, _, order = _make()
    _arm_acked(supervisor)
    _run(supervisor.on_event(_approve(t=200)))

    assert resolves == [(RID, Verdict.APPROVED, DecidedBy.HUMAN)]
    assert _closes(transport)
    resolve_at = next(i for i, item in enumerate(order) if item.startswith("resolve:"))
    close_at = next(
        i for i, item in enumerate(order) if item.startswith("write:relay:True")
    )
    assert resolve_at < close_at


def test_mismatched_req_never_closes() -> None:
    supervisor, transport, _, resolves, _, _, _ = _make()
    _arm_acked(supervisor)
    _run(supervisor.on_event(_approve(req=OTHER, t=200)))
    assert _closes(transport) == []
    assert resolves == []


def test_button_while_disarmed_never_closes() -> None:
    supervisor, transport, _, resolves, _, _, _ = _make()
    _run(supervisor.on_event(_approve()))
    assert _closes(transport) == []
    assert resolves == []


def test_replayed_button_closes_at_most_once() -> None:
    supervisor, transport, clock, _, _, _, _ = _make()
    _arm_acked(supervisor)
    button = _approve(t=200)
    _run(supervisor.on_event(button))
    clock.advance(1.0)
    _run(supervisor.on_event(button))
    assert len(_closes(transport)) == 1


def test_button_held_across_arm_cannot_approve() -> None:
    supervisor, transport, _, resolves, _, _, _ = _make()
    _run(supervisor.on_event(_tick(t=40, btns=1)))
    _run(supervisor.on_event(_approve(t=40)))
    supervisor.arm(RID, relay_gated=True)
    _run(supervisor.send(ArmCommand(id=1, req=RID)))
    _run(supervisor.on_event(_approve(t=40)))
    assert _closes(transport) == []
    assert resolves == []


def test_relay_renew_accepted_while_closed_not_closed_while_open_never_closes() -> None:
    transport = RelayAwareTransport()
    supervisor, _, _, _, _, _, _ = _make(transport=transport)

    _run(supervisor.send(RelayRenewCommand(id=1)))
    assert transport.contact_closed is False
    assert _closes(transport) == []
    assert any(frame.get("cmd") == "relay_renew" for frame in _frames(transport))

    _arm_acked(supervisor, cmd_id=2)
    _run(supervisor.on_event(_approve(t=200)))
    assert transport.contact_closed is True
    _run(supervisor.send(RelayRenewCommand(id=3)))
    assert transport.contact_closed is True
    assert len(_closes(transport)) == 1


def test_rule_4b_cycle_in_order() -> None:
    transport = RelayAwareTransport()
    supervisor, _, clock, resolves, audits, _, order = _make(transport=transport)
    _arm_acked(supervisor, dwell_s=6)
    _run(supervisor.on_event(_approve(t=200)))

    assert resolves == [(RID, Verdict.APPROVED, DecidedBy.HUMAN)]
    assert _closes(transport)

    _advance(supervisor, clock, 3.0, relay=True)
    _advance(supervisor, clock, 3.0, relay=True)

    write_cmds = [
        item
        for item in order
        if item.startswith("write:") and not item.startswith("write:arm")
    ]
    assert write_cmds[0].startswith("write:relay:True")
    assert "write:relay_renew:" in write_cmds
    assert write_cmds[-2] == "write:relay:False"
    assert write_cmds[-1] == "write:disarm:"
    audit_names = [event for event, _, _ in audits]
    assert AuditEvent.RESOLVED in audit_names
    assert AuditEvent.RELAY_OPENED in audit_names
    opened_at = order.index(f"audit:{AuditEvent.RELAY_OPENED}")
    disarm_at = order.index("write:disarm:")
    assert opened_at < disarm_at
    assert len(_opens(transport)) >= 1


def test_renewal_continues_after_verdict_never_stops_on_verdict() -> None:
    transport = RelayAwareTransport()
    supervisor, _, clock, resolves, _, _, _ = _make(transport=transport)
    _arm_acked(supervisor, dwell_s=9)
    _run(supervisor.on_event(_approve(t=200)))
    assert resolves
    before = _cmds(transport).count("relay_renew")
    _advance(supervisor, clock, 3.0, relay=True)
    _advance(supervisor, clock, 3.0, relay=True)
    after = _cmds(transport).count("relay_renew")
    assert after - before >= 2


def test_renewal_stops_on_dwell_expiry() -> None:
    transport = RelayAwareTransport()
    supervisor, _, clock, _, _, _, _ = _make(transport=transport)
    _arm_acked(supervisor, dwell_s=6)
    _run(supervisor.on_event(_approve(t=200)))
    _advance(supervisor, clock, 3.0, relay=True)
    _advance(supervisor, clock, 3.0, relay=True)
    renews = _cmds(transport).count("relay_renew")
    _advance(supervisor, clock, 3.0, relay=False, armed=False)
    assert _cmds(transport).count("relay_renew") == renews
    assert _opens(transport)


def test_renewal_stops_on_explicit_open() -> None:
    transport = RelayAwareTransport()
    supervisor, _, clock, _, _, _, _ = _make(transport=transport)
    _arm_acked(supervisor, dwell_s=60)
    _run(supervisor.on_event(_approve(t=200)))
    _run(supervisor.send(RelayCommand(id=40, closed=False)))
    renews = _cmds(transport).count("relay_renew")
    _advance(supervisor, clock, 3.0)
    assert _cmds(transport).count("relay_renew") == renews


def test_renewal_stops_on_safe_state() -> None:
    transport = RelayAwareTransport()
    supervisor, _, clock, _, _, _, _ = _make(transport=transport)
    _arm_acked(supervisor, dwell_s=60)
    _run(supervisor.on_event(_approve(t=200)))
    clock.advance(3.001)
    _run(supervisor.check_watchdog())
    assert supervisor.healthy is False
    renews = _cmds(transport).count("relay_renew")
    clock.advance(3.0)
    _run(supervisor.check_watchdog())
    assert _cmds(transport).count("relay_renew") == renews


def test_renewal_stops_on_disarm() -> None:
    transport = RelayAwareTransport()
    supervisor, _, clock, _, _, _, _ = _make(transport=transport)
    _arm_acked(supervisor, dwell_s=60)
    _run(supervisor.on_event(_approve(t=200)))
    supervisor.disarm()
    renews = _cmds(transport).count("relay_renew")
    _advance(supervisor, clock, 3.0)
    assert _cmds(transport).count("relay_renew") == renews


def test_consent_channel_sends_no_relay_command() -> None:
    supervisor, transport, _, resolves, _, _, _ = _make()
    _arm_acked(supervisor, relay_gated=False)
    _run(supervisor.on_event(_approve(t=200)))
    assert resolves == [(RID, Verdict.APPROVED, DecidedBy.HUMAN)]
    assert [frame for frame in _frames(transport) if frame.get("cmd") == "relay"] == []
    assert "relay_renew" not in _cmds(transport)


def test_auto_approved_never_closes_even_when_relay_gated() -> None:
    supervisor, transport, _, resolves, _, _, _ = _make()
    _arm_acked(supervisor, relay_gated=True)
    with pytest.raises(SupervisorRejection, match="condition 2"):
        _run(supervisor.send(RelayCommand(id=2, closed=True)))
    assert _closes(transport) == []
    assert resolves == []
    assert not any(
        frame.get("cmd") == "relay" and frame.get("closed") is True
        for frame in _frames(transport)
    )


def test_audit_and_resolve_precede_close_frame() -> None:
    supervisor, transport, _, _, audits, _, order = _make()
    _arm_acked(supervisor)
    _run(supervisor.on_event(_approve(t=200)))

    audit_resolved = next(
        i for i, item in enumerate(order) if item == f"audit:{AuditEvent.RESOLVED}"
    )
    resolve_at = next(i for i, item in enumerate(order) if item.startswith("resolve:"))
    close_at = next(
        i for i, item in enumerate(order) if item.startswith("write:relay:True")
    )
    assert audit_resolved < close_at
    assert resolve_at < close_at
    assert AuditEvent.BUTTON in {event for event, _, _ in audits}


def test_lease_expired_mid_dwell_does_not_change_verdict() -> None:
    transport = RelayAwareTransport()
    supervisor, _, _, resolves, audits, pending, _ = _make(transport=transport)
    supervisor.track_pending(RID)
    _arm_acked(supervisor, dwell_s=60)
    _run(supervisor.on_event(_approve(t=200)))
    assert resolves == [(RID, Verdict.APPROVED, DecidedBy.HUMAN)]
    _run(supervisor.on_event(LeaseExpiredEvent(t=5_000)))
    assert resolves == [(RID, Verdict.APPROVED, DecidedBy.HUMAN)]
    assert pending == []
    assert any(event == AuditEvent.LEASE_EXPIRED for event, _, _ in audits)
    fault = next(
        payload for event, _, payload in audits if event == AuditEvent.LEASE_EXPIRED
    )
    assert isinstance(fault, dict)
    assert fault.get("cycle_incomplete") is True


def test_lease_expired_while_unarmed_touches_no_queued_request() -> None:
    supervisor, transport, _, resolves, audits, pending, _ = _make()
    supervisor.track_pending(OTHER)
    _run(supervisor.on_event(LeaseExpiredEvent(t=1)))
    assert pending == []
    assert resolves == []
    assert any(event == AuditEvent.LEASE_EXPIRED for event, _, _ in audits)
    assert _opens(transport)
    _arm_acked(supervisor, request_id=OTHER, cmd_id=2)
    assert supervisor.healthy is True


def test_dead_time_and_buttons_released_required_before_next_arm() -> None:
    supervisor, transport, clock, resolves, _, _, _ = _make()
    _arm_acked(supervisor, relay_gated=False, dwell_s=60)
    _run(supervisor.on_event(_approve(t=200)))
    assert resolves

    _run(supervisor.on_event(_approve(req=OTHER, t=300)))
    assert _closes(transport) == []

    with pytest.raises(SupervisorRejection):
        supervisor.arm(OTHER, relay_gated=True)

    clock.advance(2.0)
    with pytest.raises(SupervisorRejection):
        supervisor.arm(OTHER, relay_gated=True)

    _run(supervisor.on_event(_tick(t=int(clock.t * 1000), btns=0, armed=False)))
    supervisor.arm(OTHER, relay_gated=True)
    _run(supervisor.send(ArmCommand(id=2, req=OTHER)))
    _run(supervisor.on_event(_approve(req=OTHER, t=int(clock.t * 1000) + 1)))
    assert _closes(transport)
