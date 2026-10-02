from __future__ import annotations

from .schemas import ModeContract

MODE_CONTRACTS: dict[str, ModeContract] = {
    "roleplay": ModeContract(
        mode="roleplay",
        description="Character-driven narrative evolution and entertainment-first simulation.",
        objectives=["maintain character consistency", "advance plot with conflict and resolution"],
        required_outputs=["timeline", "turning_points", "character_state"],
        submodes={},
    ),
    "research": ModeContract(
        mode="research",
        description="Methodology-driven exploration for early-stage elimination and decision support.",
        objectives=["rule out weak options", "surface assumptions and evidence gaps", "save validation cost"],
        required_outputs=["keep", "drop", "to_validate", "confidence", "limitations"],
        submodes={
            "simulation": {
                "description": "Simulate real-world evolution under given background and constraints.",
                "required_outputs": ["evolution_timeline", "scenario_outcomes", "risk_map"],
            },
            "research": {
                "description": "Simulate scientific/structured research process with methods and bias control.",
                "required_outputs": ["research_plan", "evidence_summary", "bias_and_limitations"],
            },
            "consulting": {
                "description": "Consulting-style logic pushdown across role-based functional workstreams.",
                "required_outputs": ["issue_tree", "option_screening", "action_recommendations"],
            },
        },
    ),
    "custom": ModeContract(
        mode="custom",
        description="User-defined objective, role setup, and output format.",
        objectives=["respect user-defined contracts", "maximize configurability with traceability"],
        required_outputs=["custom_output"],
        submodes={},
    ),
}
