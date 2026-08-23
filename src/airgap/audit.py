"""Tamper-evident audit-chain operations."""

import json
import re
from datetime import UTC, datetime
from decimal import Decimal
from hashlib import sha256
from typing import cast as type_cast

from sqlalchemy import Text, cast, select, text

from airgap.models import AuditLog, session_factory
from airgap.vocab import AuditEvent

_GENESIS_HASH = "0" * 64
_REQUEST_ID = re.compile(r"[0-9a-f]{8}")


def _canonical_json(payload: object) -> str:
    return json.dumps(
        payload,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _canonical_stored_json(payload_text: str) -> str:
    decoded: object = json.loads(
        payload_text,
        parse_float=Decimal,
        parse_int=Decimal,
    )
    return _canonical_json_value(decoded)


def _canonical_json_value(value: object) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("JSONB numerics must be finite")
        return format(value, "f")
    if isinstance(value, list):
        sequence = type_cast(list[object], value)
        return "[" + ",".join(_canonical_json_value(item) for item in sequence) + "]"
    if isinstance(value, dict):
        mapping = type_cast(dict[str, object], value)
        return (
            "{"
            + ",".join(
                json.dumps(key, ensure_ascii=False)
                + ":"
                + _canonical_json_value(mapping[key])
                for key in sorted(mapping)
            )
            + "}"
        )
    raise TypeError(f"unsupported JSONB value: {type(value).__name__}")


def _at_iso(at: datetime) -> str:
    return at.astimezone(UTC).isoformat()


def _row_hash(
    previous_hash: str,
    seq: int,
    at: datetime,
    event: str,
    request_id: str | None,
    canonical_payload: str,
) -> str:
    material = (
        previous_hash
        + str(seq)
        + _at_iso(at)
        + event
        + (request_id or "")
        + canonical_payload
    )
    return sha256(material.encode("utf-8")).hexdigest()


def append(
    event: AuditEvent,
    request_id: str | None,
    payload: object,
) -> None:
    """Commit before returning so callers cannot act ahead of their audit row."""
    if request_id is not None and _REQUEST_ID.fullmatch(request_id) is None:
        raise ValueError("request_id must be eight lowercase hexadecimal characters")

    with session_factory()() as session, session.begin():
        session.execute(text("LOCK TABLE audit_log IN SHARE ROW EXCLUSIVE MODE"))
        stored_payload_text = session.execute(
            text("SELECT CAST(CAST(:payload AS jsonb) AS text)"),
            {"payload": _canonical_json(payload)},
        ).scalar_one()
        if not isinstance(stored_payload_text, str):
            raise TypeError("PostgreSQL did not return JSONB text")
        canonical_payload = _canonical_stored_json(stored_payload_text)
        previous_hash = session.scalar(
            select(AuditLog.row_hash).order_by(AuditLog.seq.desc()).limit(1)
        )
        if previous_hash is None:
            previous_hash = _GENESIS_HASH

        seq = int(
            session.execute(
                text("SELECT nextval(pg_get_serial_sequence('audit_log', 'seq'))")
            ).scalar_one()
        )
        at = datetime.now(UTC)
        session.execute(
            text(
                "INSERT INTO audit_log "
                "(seq, at, event, request_id, payload, prev_hash, row_hash) "
                "VALUES "
                "(:seq, :at, :event, :request_id, CAST(:payload AS jsonb), "
                ":prev_hash, :row_hash)"
            ),
            {
                "seq": seq,
                "at": at,
                "event": event.value,
                "request_id": request_id,
                "payload": stored_payload_text,
                "prev_hash": previous_hash,
                "row_hash": _row_hash(
                    previous_hash,
                    seq,
                    at,
                    event.value,
                    request_id,
                    canonical_payload,
                ),
            },
        )


def verify_chain(from_seq: int = 0) -> tuple[bool, int | None]:
    """Check row material first so sequence tampering localizes to that row."""
    statement = select(
        AuditLog.seq,
        AuditLog.at,
        AuditLog.event,
        AuditLog.request_id,
        cast(AuditLog.payload, Text).label("payload_text"),
        AuditLog.prev_hash,
        AuditLog.row_hash,
    ).order_by(AuditLog.seq)
    if from_seq != 0:
        statement = statement.where(AuditLog.seq >= from_seq)

    with session_factory()() as session:
        rows = list(session.execute(statement))

        for row in rows:
            if not isinstance(row.at, datetime) or not isinstance(
                row.payload_text, str
            ):
                return False, row.seq
            expected_hash = _row_hash(
                row.prev_hash,
                row.seq,
                row.at,
                row.event,
                row.request_id,
                _canonical_stored_json(row.payload_text),
            )
            if row.row_hash != expected_hash:
                return False, row.seq

        previous_hash = _GENESIS_HASH
        if from_seq > 0:
            stored_previous_hash = session.scalar(
                select(AuditLog.row_hash)
                .where(AuditLog.seq < from_seq)
                .order_by(AuditLog.seq.desc())
                .limit(1)
            )
            if stored_previous_hash is not None:
                previous_hash = stored_previous_hash

        for row in rows:
            if row.prev_hash != previous_hash:
                return False, row.seq
            previous_hash = row.row_hash

    return True, None
