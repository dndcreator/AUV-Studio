"""Experimental joint admission shared by the engine and isolated trials."""
from __future__ import annotations

import copy
import json

from .action_consistency import CHECK_SYSTEM_PROMPT, parse_assessment
from .action_evidence import build_consistency_evidence
from .action_output import _unique_object, _invalid_constant

JOINT = (CHECK_SYSTEM_PROMPT + "\nBATCH ADMISSION OVERRIDE: CANDIDATES is an ordered array of distinct actors, "
    "not one composite actor. Apply all ownership, privacy and six-category rules separately to each. "
    "Then jointly check that the accepted subset can coexist, including competing resources and incompatible "
    "effects. All proposals use the same round-start knowledge; do not grant knowledge of peers' proposals. "
    "Never invent a winner or rewrite an action. Defer uncertain outcomes. Return JSON only: "
    '{"assessments":{"actor_id":{"decision":"accept|conflict|uncertain","reason":"brief",'
    '"scope":{"self_owned":true,"public_only":true},"findings":[]}},'
    '"joint_findings":[{"candidate_ids":["a","b"],"reason":"brief","evidence_ids":["exact ID"]}]}. '
    "Each finding in an assessment uses the existing category/reason/evidence_ids contract. "
    "Every actor must have exactly one assessment. A mutually incompatible group cannot all be accepted. "
    "Joint findings must cite known evidence and at least two candidates.")


def parse_joint(response, candidates, evidence):
    # Reuse strict JSON parsing, including duplicate/nonfinite/truncation protection.
    raw = response.get("raw", {})
    if raw.get("mock") or any(choice.get("finish_reason") in {"length", "max_tokens"} for choice in raw.get("choices", [])):
        raise ValueError("unusable joint assessment")
    text = response["content"].strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    data = json.loads(text, object_pairs_hook=_unique_object, parse_constant=_invalid_constant)
    if not isinstance(data, dict):
        raise ValueError("joint assessment must be an object")
    ids = {item["actor_id"] for item in candidates}
    assessments = data.get("assessments")
    if not isinstance(assessments, dict) or set(assessments) != ids or not isinstance(data.get("joint_findings"), list):
        raise ValueError("joint checker must assess every candidate exactly once")
    verdicts = {actor: parse_assessment(json.dumps(value), evidence) for actor, value in assessments.items()}
    known = {row["id"] for row in evidence}
    for finding in data["joint_findings"]:
        group, refs = finding.get("candidate_ids"), finding.get("evidence_ids")
        if (not isinstance(group, list) or len(set(group)) < 2 or not set(group) <= ids
                or not isinstance(refs, list) or not refs or not set(refs) <= known or not finding.get("reason")):
            raise ValueError("invalid joint conflict reference")
        if all(verdicts[actor]["decision"] == "accept" for actor in group):
            raise ValueError("incompatible proposals cannot all be accepted")
    return verdicts


async def check_joint(provider, wf, state, ledger, inputs, candidates):
    evidence = []
    for node, runtime in zip(wf.nodes, inputs):
        for row in build_consistency_evidence(wf, state, ledger, runtime):
            item = copy.deepcopy(row)
            if item["id"] == "actor.input": item["id"] += "." + node.id
            if item not in evidence: evidence.append(item)
    prompt = json.dumps({"EVIDENCE": evidence, "CANDIDATES": candidates}, ensure_ascii=False)
    for attempt in range(2):
        response = await provider.chat("", JOINT, prompt)
        try: return parse_joint(response, candidates, evidence)
        except (ValueError, TypeError, KeyError):
            if attempt: raise
            prompt += "\nRepair ONLY the assessment protocol for the SAME candidates and evidence."
