from __future__ import annotations


def narrative_prompts(
    *,
    source: str,
    style: str,
    language: str,
    audience: str,
    length: str,
) -> tuple[str, str]:
    system = (
        "You adapt a simulation into a finished literary work. Write the work itself, not a report, synopsis, "
        "chronological event inventory, or explanation of your writing. By default write fiction in natural prose "
        "paragraphs: a coherent viewpoint, concrete scenes, character-specific dialogue, and selective interiority "
        "where consistent with the established characters. Let diction, rhythm, imagery, narrative distance, and "
        "dialogue carry the requested style rather than merely adding genre labels. "
        "The user's requested form and style take priority over this default: if a screenplay or another form is "
        "requested, follow its conventions. Preserve established actors, consequential actions, causality, and outcomes; "
        "actual interactions take precedence over a contradictory summary. You may shape narration, transitions, and "
        "non-consequential expressive detail, but do not invent major events, secret knowledge, or a different ending "
        "to make the work more exciting. Do not mention nodes, prompts, tokens, traces, Director instructions, "
        "simulation machinery, evidence labels, or the adaptation process. Detail is selective, not a checklist. "
        "Do not pad the text, repeat beats, or explain emotions already apparent from the scene."
    )
    user = (
        f"Output language: {language}\n"
        f"Audience: {audience}\n"
        f"Requested form and style: {style or 'Literary short fiction in prose; use a natural, coherent voice.'}\n"
        f"Length preference: {length}. Let the available scene support the length; there is no minimum word count.\n"
        "Return only the finished work in Markdown. Do not add an introduction, analytical headings, "
        "character dossiers, retrospective commentary, or a list of unresolved issues unless the requested form needs them.\n\n"
        f"Readable Simulation Material:\n{source}"
    )
    return system, user
