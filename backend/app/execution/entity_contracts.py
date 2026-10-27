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


def entity_detail_instruction(entity_type: str, simulation: object) -> str:
    if not isinstance(simulation, dict):
        return ""
    granularity = str(simulation.get("detail_granularity", "concise")).strip().lower()
    if granularity != "detailed":
        return ""
    instructions = {
        "individual": (
            "When useful, show the person's specific words and actions rather than only a summary. "
            "Private experience belongs only in private fields, never public action narration. "
            "Select particulars that matter to this response; silence or a brief action can be sufficient."
        ),
        "group": (
            "Do not write the group as one person. When relevant, show differences in opinion, representative remarks, "
            "or observable collective responses without requiring a catalogue of every subgroup."
        ),
        "organization": (
            "Show the relevant institutional decision, procedure, or resource consequence concretely when needed. "
            "Do not invent internal activity just to make the response longer."
        ),
        "environment": (
            "Describe environmental particulars only where they affect perception, constraints, or available action. "
            "Do not repeat scenery that has not meaningfully changed."
        ),
        "event": (
            "Show what the event actually changes and its relevant immediate consequences. "
            "Do not invent extra stages or aftershocks to fill space, or turn the event into a person."
        ),
        "artifact": (
            "Show the artifact's relevant content, condition, or use when these affect the current interaction. "
            "Do not repeat its full description or assign it a human inner life."
        ),
    }
    return instructions.get(entity_type, "Show the concrete contribution at the detail level needed; do not pad the response.") + (
        " These are optional detail choices, not a checklist or minimum length. The current Scene Contract takes precedence."
    )
