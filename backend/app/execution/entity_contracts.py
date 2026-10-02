from __future__ import annotations


_ENTITY_ACTION_CONTRACTS = {
    "group": (
        "Represent a collective, not one person. Produce an in-world aggregate response: show the distribution of "
        "majority, minority, and uncertain positions when relevant, plus observable collective behavior, subgroup movement, "
        "social pressure, rumors, silence, or typical anonymous remarks. Do not invent one private inner life, speak as a "
        "single named individual, form a one-to-one intimate relationship, or merely restate the instruction. If the group "
        "has no unified response, express fragmentation or uncertainty rather than forcing consensus."
    ),
    "organization": (
        "Act as an institution through policy, procedure, resource allocation, assignments, enforcement, internal factions, "
        "and external strategy. Do not collapse the organization into one person's casual reaction or merely restate the instruction."
    ),
    "environment": (
        "Act through changes in conditions, access, risk, resources, time, place, and constraints. Do not make character "
        "decisions or merely describe the requested task."
    ),
    "event": (
        "Act as a concrete occurrence with onset, affected entities, intensity, duration, direct consequences, and aftereffects. "
        "Do not behave as a persistent person or merely restate the instruction."
    ),
    "artifact": (
        "Act through the artifact's content, state, ownership, visibility, credibility, circulation, and use. Do not give it a "
        "private human personality or merely restate the instruction."
    ),
    "individual": (
        "Act as one situated person through personal perception, speech, choices, hesitation, emotion, and bodily action. "
        "Do not speak for an entire population or merely restate the instruction."
    ),
}


def entity_action_contract(entity_type: str) -> str:
    normalized = str(entity_type).strip().lower()
    return _ENTITY_ACTION_CONTRACTS.get(
        normalized,
        "Act as the defined entity inside the world and produce a concrete contribution rather than restating the instruction.",
    )
