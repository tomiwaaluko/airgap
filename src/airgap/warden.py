"""LLM triage whose output is a proposal, never a final verdict."""

from __future__ import annotations

import json
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol, cast

from airgap.models import WardenAssessment
from airgap.policy import PolicyRule, matches_tool, resolve
from airgap.vocab import PolicyAction

# No check_injection_signature: DESIGN.md T2 withdrew LLM-side injection
# screening as a control. The bound is policy narrowing after this proposal.
READONLY_TOOLS: tuple[str, ...] = (
    "classify_risk",
    "check_policy",
    "search_decision_history",
    "read_autonomy_dial",
)

DEFAULT_MODEL = "claude-sonnet-4-20250514"
MAX_TOOL_ROUNDS = 8
_VALID_RISK = frozenset({"low", "medium", "high", "blocked"})
_FALLBACK_REASONING = "warden output unusable; escalating"

_SYSTEM_PROMPT = (
    "You are Airgap's Warden. You triage an irreversible action an actor "
    "wants to take. You propose auto_approve, escalate, or block — you never "
    "execute the action, never close a relay, and never produce a final "
    "verdict. Deterministic policy will run after you and can only narrow "
    "this proposal. Use the read-only tools if they help. Reply with one "
    "JSON object whose keys are action, risk_class, reversible, blast_radius, "
    "injection_suspected, and reasoning. injection_suspected is an "
    "observation for the audit row, not a control."
)

_TOOL_SPECS: dict[str, dict[str, object]] = {
    "classify_risk": {
        "description": "Classify typical risk for a tool name without executing it.",
        "input_schema": {
            "type": "object",
            "properties": {"tool_name": {"type": "string"}},
        },
    },
    "check_policy": {
        "description": "Look up the deterministic policy row for a tool name.",
        "input_schema": {
            "type": "object",
            "properties": {"tool_name": {"type": "string"}},
        },
    },
    "search_decision_history": {
        "description": "Search prior decisions for a tool name.",
        "input_schema": {
            "type": "object",
            "properties": {"tool_name": {"type": "string"}},
        },
    },
    "read_autonomy_dial": {
        "description": (
            "Read the current physical autonomy dial. Software cannot set it."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
}


class MessagesPort(Protocol):
    def create(self, **kwargs: Any) -> Any: ...


class AnthropicPort(Protocol):
    messages: MessagesPort


class AssessmentSession(Protocol):
    def add(self, instance: object) -> None: ...


@dataclass(frozen=True, slots=True, kw_only=True)
class TriageRequest:
    """The actor's ask; the Warden never executes it."""

    request_id: str
    actor: str
    tool_name: str
    tool_args: Mapping[str, object]
    justification: str


@dataclass(frozen=True, slots=True, kw_only=True)
class DecisionHistoryEntry:
    """Enough of a past decision for the history tool to be honest."""

    tool_name: str
    verdict: str
    decided_by: str


@dataclass(frozen=True, slots=True, kw_only=True)
class TriageResult:
    """Keep the LLM proposal distinct from the policy-narrowed action."""

    proposal: PolicyAction
    resolved: PolicyAction
    assessment: WardenAssessment


class Warden:
    """Talk to the model, then bound whatever it said with policy.resolve."""

    def __init__(
        self,
        client: AnthropicPort,
        session: AssessmentSession,
        *,
        model: str = DEFAULT_MODEL,
    ) -> None:
        self._client = client
        self._session = session
        self._model = model

    def triage(
        self,
        request: TriageRequest,
        *,
        dial: int,
        policies: Sequence[PolicyRule] = (),
        history: Sequence[DecisionHistoryEntry] = (),
    ) -> TriageResult:
        """Policy runs after the proposal so a compromised model cannot widen."""
        started = time.monotonic()
        tool_calls: list[dict[str, object]] = []
        try:
            proposal, payload, model_id = self._consult(
                request,
                dial=dial,
                policies=policies,
                history=history,
                tool_calls=tool_calls,
            )
        except Exception as exc:
            if isinstance(exc, AssertionError):
                raise
            return self._finish(
                request,
                PolicyAction.ESCALATE,
                {},
                model_id=self._model,
                tool_calls=tool_calls,
                latency_ms=_elapsed_ms(started),
                dial=dial,
                policies=policies,
                fallback_reason="warden unavailable",
            )

        return self._finish(
            request,
            proposal,
            payload,
            model_id=model_id,
            tool_calls=tool_calls,
            latency_ms=_elapsed_ms(started),
            dial=dial,
            policies=policies,
        )

    def _consult(
        self,
        request: TriageRequest,
        *,
        dial: int,
        policies: Sequence[PolicyRule],
        history: Sequence[DecisionHistoryEntry],
        tool_calls: list[dict[str, object]],
    ) -> tuple[PolicyAction, dict[str, object], str]:
        messages: list[dict[str, object]] = [
            {"role": "user", "content": _user_prompt(request)}
        ]
        model_id = self._model
        for _ in range(MAX_TOOL_ROUNDS):
            response = self._client.messages.create(
                model=self._model,
                max_tokens=1024,
                system=_SYSTEM_PROMPT,
                tools=_tool_definitions(),
                messages=messages,
            )
            model_id = str(getattr(response, "model", self._model) or self._model)
            content = getattr(response, "content", [])
            tool_blocks = [
                block
                for block in _iter_blocks(content)
                if _block_field(block, "type") == "tool_use"
            ]
            if tool_blocks:
                results: list[dict[str, object]] = []
                for block in tool_blocks:
                    name = str(_block_field(block, "name", ""))
                    raw_input = _block_field(block, "input", {})
                    tool_input = _as_mapping(raw_input)
                    output = self._run_tool(
                        name,
                        tool_input,
                        request,
                        policies=policies,
                        dial=dial,
                        history=history,
                    )
                    tool_calls.append(
                        {"name": name, "input": tool_input, "output": output}
                    )
                    results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": str(_block_field(block, "id", "")),
                            "content": json.dumps(output, default=str),
                        }
                    )
                messages.append(
                    {"role": "assistant", "content": _content_as_dicts(content)}
                )
                messages.append({"role": "user", "content": results})
                continue

            action, payload = _parse_proposal(_collect_text(content))
            if action is None:
                return PolicyAction.ESCALATE, payload, model_id
            return action, payload, model_id
        return PolicyAction.ESCALATE, {}, model_id

    def _run_tool(
        self,
        name: str,
        tool_input: dict[str, object],
        request: TriageRequest,
        *,
        policies: Sequence[PolicyRule],
        dial: int,
        history: Sequence[DecisionHistoryEntry],
    ) -> object:
        if name == "classify_risk":
            tool_name = str(tool_input.get("tool_name") or request.tool_name)
            return _classify_risk(tool_name)
        if name == "check_policy":
            tool_name = str(tool_input.get("tool_name") or request.tool_name)
            return _check_policy(tool_name, policies)
        if name == "search_decision_history":
            tool_name = str(tool_input.get("tool_name") or request.tool_name)
            return _search_history(tool_name, history)
        if name == "read_autonomy_dial":
            return {"dial": dial}
        return {"error": "unknown tool", "name": name}

    def _finish(
        self,
        request: TriageRequest,
        proposal: PolicyAction,
        payload: dict[str, object],
        *,
        model_id: str,
        tool_calls: list[dict[str, object]],
        latency_ms: int,
        dial: int,
        policies: Sequence[PolicyRule],
        fallback_reason: str | None = None,
    ) -> TriageResult:
        reasoning = fallback_reason or _as_str(
            payload.get("reasoning"), _FALLBACK_REASONING
        )
        assessment = WardenAssessment(
            request_id=request.request_id,
            model=model_id,
            risk_class=_risk_class(payload.get("risk_class")),
            reversible=_as_bool(payload.get("reversible"), False),
            blast_radius=_as_str(payload.get("blast_radius"), "unknown"),
            injection_suspected=_as_bool(payload.get("injection_suspected"), False),
            reasoning=reasoning,
            tool_calls=tool_calls,
            latency_ms=latency_ms,
            created_at=datetime.now(UTC),
        )
        self._session.add(assessment)
        resolved = resolve(proposal, _find_rule(request.tool_name, policies), dial)
        return TriageResult(
            proposal=proposal,
            resolved=resolved,
            assessment=assessment,
        )


def _tool_definitions() -> list[dict[str, object]]:
    return [{"name": name, **_TOOL_SPECS[name]} for name in READONLY_TOOLS]


def _user_prompt(request: TriageRequest) -> str:
    arguments = json.dumps(dict(request.tool_args), default=str, ensure_ascii=False)
    return (
        f"request_id: {request.request_id}\n"
        f"actor: {request.actor}\n"
        f"tool_name: {request.tool_name}\n"
        f"tool_args: {arguments}\n"
        f"justification:\n{request.justification}"
    )


def _find_rule(tool_name: str, policies: Sequence[PolicyRule]) -> PolicyRule | None:
    for rule in policies:
        if matches_tool(rule.tool_pattern, tool_name):
            return rule
    return None


def _classify_risk(tool_name: str) -> dict[str, object]:
    lowered = tool_name.lower()
    if any(
        token in lowered
        for token in ("drop", "delete", "destroy", "send_money", "wire")
    ):
        return {"risk_class": "high", "reversible": False}
    if any(token in lowered for token in ("read", "list", "get", "search")):
        return {"risk_class": "low", "reversible": True}
    return {"risk_class": "medium", "reversible": False}


def _check_policy(tool_name: str, policies: Sequence[PolicyRule]) -> dict[str, object]:
    rule = _find_rule(tool_name, policies)
    if rule is None:
        return {"matched": False, "default_action": PolicyAction.ESCALATE.value}
    return {
        "matched": True,
        "tool_pattern": rule.tool_pattern,
        "action": rule.action.value,
        "min_dial": rule.min_dial,
        "relay_gated": rule.relay_gated,
    }


def _search_history(
    tool_name: str, history: Sequence[DecisionHistoryEntry]
) -> list[dict[str, str]]:
    return [
        {
            "tool_name": entry.tool_name,
            "verdict": entry.verdict,
            "decided_by": entry.decided_by,
        }
        for entry in history
        if entry.tool_name == tool_name
    ]


def _parse_proposal(text: str) -> tuple[PolicyAction | None, dict[str, object]]:
    """Parse only assistant text so justification JSON cannot become the verdict.

    More than one action-bearing object is malformed: taking the first would let
    an echoed injection payload beat a later honest escalate.
    """
    action_objects = [obj for obj in _json_objects(text) if "action" in obj]
    if len(action_objects) != 1:
        return None, {}
    obj = action_objects[0]
    raw = obj["action"]
    try:
        return PolicyAction(str(raw)), obj
    except TypeError, ValueError:
        return None, obj


def _json_objects(text: str) -> list[dict[str, object]]:
    decoder = json.JSONDecoder()
    objects: list[dict[str, object]] = []
    index = 0
    while index < len(text):
        if text[index] != "{":
            index += 1
            continue
        try:
            parsed, consumed = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            index += 1
            continue
        if isinstance(parsed, dict):
            objects.append(cast(dict[str, object], parsed))
        index += max(consumed, 1)
    return objects


def _iter_blocks(content: object) -> list[object]:
    if content is None:
        return []
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    if isinstance(content, Sequence) and not isinstance(content, (str, bytes)):
        return list(content)
    return [content]


def _block_field(block: object, name: str, default: object = None) -> object:
    if isinstance(block, Mapping):
        return block.get(name, default)
    return getattr(block, name, default)


def _content_as_dicts(content: object) -> list[dict[str, object]]:
    blocks: list[dict[str, object]] = []
    for block in _iter_blocks(content):
        block_type = str(_block_field(block, "type", "text"))
        if block_type == "tool_use":
            blocks.append(
                {
                    "type": "tool_use",
                    "id": str(_block_field(block, "id", "")),
                    "name": str(_block_field(block, "name", "")),
                    "input": _as_mapping(_block_field(block, "input", {})),
                }
            )
            continue
        blocks.append(
            {
                "type": "text",
                "text": str(_block_field(block, "text", "")),
            }
        )
    return blocks


def _collect_text(content: object) -> str:
    parts: list[str] = []
    for block in _iter_blocks(content):
        if _block_field(block, "type", "text") == "text":
            parts.append(str(_block_field(block, "text", "")))
    return "\n".join(parts)


def _as_mapping(value: object) -> dict[str, object]:
    if isinstance(value, Mapping):
        return {str(key): item for key, item in value.items()}
    return {}


def _as_bool(value: object, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    return default


def _as_str(value: object, default: str) -> str:
    if isinstance(value, str) and value:
        return value
    return default


def _risk_class(value: object) -> str:
    if isinstance(value, str) and value in _VALID_RISK:
        return value
    return "medium"


def _elapsed_ms(started: float) -> int:
    return max(0, int((time.monotonic() - started) * 1000))
