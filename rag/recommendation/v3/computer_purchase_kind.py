"""Validate an LLM-observed computer purchase form without selecting one.

``ComputerPurchaseKindValidator.validate`` checks original-text evidence,
action/form consistency, and a centralized explicit-build whitelist. It may
return a clarification but never rewrites a user request into a PC build; this
protects the boundary between “buy a computer” and “assemble a host”.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, replace

from .config import CLARIFICATION_TTL_SECONDS, PC_BUILD_EXPLICIT_SIGNALS
from .types import ClarificationPlan, ComputerPurchaseKind, SemanticObservation, V3Action


@dataclass(frozen=True)
class ComputerPurchaseKindValidation:
    clarification: ClarificationPlan | None = None
    reason_code: str = ""
    observation: SemanticObservation | None = None


class ComputerPurchaseKindValidator:
    """Fail closed on an action/form contradiction; never repairs it by guessing."""

    def validate(self, *, text: str, observation: SemanticObservation) -> ComputerPurchaseKindValidation:
        kind = observation.computer_purchase_kind
        if kind is None:
            if observation.action is V3Action.PC_BUILD:
                return _clarify("computer_purchase_kind_required", observation)
            return ComputerPurchaseKindValidation(observation=observation)
        if kind is ComputerPurchaseKind.UNKNOWN:
            if not _evidence_matches(text, observation.computer_purchase_evidence):
                return ComputerPurchaseKindValidation(
                    reason_code="computer_purchase_context_ignored",
                    observation=replace(observation, computer_purchase_kind=None, computer_purchase_evidence=None),
                )
            return ComputerPurchaseKindValidation(observation=observation)
        if not _evidence_matches(text, observation.computer_purchase_evidence):
            return _clarify("computer_purchase_evidence_unverifiable", observation)
        if kind is ComputerPurchaseKind.DESKTOP_BUILD:
            # The evidence records the computer phrase used by the model (such
            # as "游戏主机").  The explicit build instruction can be another
            # exact phrase in the same turn (such as "配一台").
            if not _has_explicit_desktop_build_signal(text):
                return _clarify("computer_purchase_kind_unresolved", observation)
            return (
                ComputerPurchaseKindValidation(observation=observation)
                if observation.action is V3Action.PC_BUILD
                else _clarify("computer_purchase_action_mismatch", observation)
            )
        if kind in {ComputerPurchaseKind.LAPTOP, ComputerPurchaseKind.PREBUILT_DESKTOP}:
            return (
                ComputerPurchaseKindValidation(observation=observation)
                if observation.action is V3Action.RECOMMEND
                else _clarify("computer_purchase_action_mismatch", observation)
            )
        return _clarify("computer_purchase_kind_invalid", observation)


def _evidence_matches(text: str, evidence) -> bool:
    if evidence is None or evidence.evidence_text != evidence.surface:
        return False
    if 0 <= evidence.evidence_start < evidence.evidence_end <= len(text):
        if text[evidence.evidence_start:evidence.evidence_end] == evidence.evidence_text:
            return True
    return bool(evidence.evidence_text) and text.count(evidence.evidence_text) == 1


def _has_explicit_desktop_build_signal(surface: str) -> bool:
    normalized = surface.lower()
    return any(signal.lower() in normalized for signal in PC_BUILD_EXPLICIT_SIGNALS)


def _clarify(reason_code: str, observation: SemanticObservation) -> ComputerPurchaseKindValidation:
    return ComputerPurchaseKindValidation(
        clarification=ClarificationPlan(
            question="请明确要买笔记本、成品台式机，还是让我按预算配一台台式主机？",
            missing_fields=("computer_purchase_kind",),
            expires_at=time.time() + CLARIFICATION_TTL_SECONDS,
            reason_code=reason_code,
        ),
        reason_code=reason_code,
        observation=observation,
    )
