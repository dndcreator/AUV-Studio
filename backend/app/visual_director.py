from __future__ import annotations

import json
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from .schemas import WorkflowDefinition


class VisualObject(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Region(VisualObject):
    id: str = Field(min_length=1, max_length=100)
    label: str = Field(max_length=100)
    x: float = Field(ge=0, le=10000)
    y: float = Field(ge=0, le=10000)
    width: float = Field(ge=200, le=2000)
    height: float = Field(ge=180, le=2000)


class Placement(VisualObject):
    node_id: str
    region_id: str
    x: float = Field(ge=0, le=2000)
    y: float = Field(ge=0, le=2000)


class Primitive(VisualObject):
    id: str = Field(min_length=1, max_length=100)
    shape: Literal["rect", "circle", "line"]
    x: float = Field(ge=0, le=10000)
    y: float = Field(ge=0, le=10000)
    width: float = Field(default=20, ge=0, le=2000)
    height: float = Field(default=20, ge=0, le=2000)
    color: str = Field(pattern=r"^#[0-9a-fA-F]{6}$")
    label: str = Field(default="", max_length=100)

    @model_validator(mode="after")
    def nondegenerate(self):
        if (self.shape == "line" and self.width == self.height == 0) or (self.shape != "line" and (self.width == 0 or self.height == 0)):
            raise ValueError("degenerate primitive")
        return self


class Binding(VisualObject):
    kind: Literal["location", "color"]
    concept_id: str = Field(min_length=1, max_length=100)
    target_id: str = Field(min_length=1, max_length=100)
    values: dict[str, str] = Field(max_length=16)


class Palette(VisualObject):
    background: str = Field(pattern=r"^#[0-9a-fA-F]{6}$")
    surface: str = Field(pattern=r"^#[0-9a-fA-F]{6}$")
    ink: str = Field(pattern=r"^#[0-9a-fA-F]{6}$")
    accent: str = Field(pattern=r"^#[0-9a-fA-F]{6}$")


class VisualPlan(VisualObject):
    version: Literal[1]
    renderer: Literal["spatial"]
    theme: Palette
    regions: list[Region] = Field(min_length=1, max_length=12)
    placements: list[Placement] = Field(max_length=120)
    primitives: list[Primitive] = Field(default_factory=list, max_length=80)
    bindings: list[Binding] = Field(default_factory=list, max_length=32)


class VisualRequest(BaseModel):
    workflow: WorkflowDefinition
    run_id: str | None = None


class VisualGenerationError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def parse_visual(content: str, workflow: WorkflowDefinition, concepts: dict[str, Any]) -> VisualPlan:
    content = content.strip()
    if content.startswith("```json\n") and content.endswith("```"):
        content = content[8:-3].strip()
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate visual field")
            result[key] = value
        return result

    plan = VisualPlan.model_validate(json.loads(content, object_pairs_hook=unique))
    nodes = {node.id for node in workflow.nodes}
    regions = {r.id for r in plan.regions}
    primitives = {p.id for p in plan.primitives}
    if len(regions) != len(plan.regions) or len(primitives) != len(plan.primitives):
        raise ValueError("duplicate visual object")
    placed = set()
    for placement in plan.placements:
        if placement.node_id not in nodes or placement.region_id not in regions or placement.node_id in placed:
            raise ValueError("invalid visual placement")
        placed.add(placement.node_id)
    for binding in plan.bindings:
        concept = concepts.get(binding.concept_id, {})
        if not concept or concept.get("visibility") != "global":
            raise ValueError("visual binding requires a public concept")
        if binding.kind == "location":
            if binding.target_id not in nodes or concept.get("subject_id") != binding.target_id or any(v not in regions for v in binding.values.values()):
                raise ValueError("invalid location binding")
        elif binding.target_id not in primitives or any(not re.fullmatch(r"#[0-9a-fA-F]{6}", v) for v in binding.values.values()):
            raise ValueError("invalid color binding")
    return plan


async def generate_visual(provider: Any, workflow: WorkflowDefinition, public_state: dict[str, Any]) -> VisualPlan:
    actors = [{"id": n.id, "name": n.config.get("entity_name", n.id), "type": n.config.get("entity_type", n.type)}
              for n in workflow.nodes if n.type in {"agent", "external_agent"} and not n.id.startswith(("summary_", "report_"))]
    prompt = json.dumps({"scenario": workflow.environment.scenario, "space": workflow.environment.spatial_context,
                         "actors": actors, "public_state": public_state}, ensure_ascii=False)
    if len(prompt) > 24000:
        raise ValueError("visual context exceeds limit")
    system = (
        "You are a Visual Director, not a world-state writer. Convert the public brief into a compact schematic SVG scene. "
        "This is a small preview: use 2-3 regions and at most 8 decorative primitives, short labels and compact JSON. "
        "Do not fill schema maxima, list unused objects or emit design explanations. "
        "Use visually distinct colors and geometry appropriate to the brief, not a generic diagram. "
        "Rectangles/circles/lines can depict masonry, warm lights, pipes, consoles or other decorative motifs. "
        "Decorations are illustrative, not proof an object exists. Never invent actor actions, deaths or successful outcomes. "
        "Place actors only when their starting location is explicit; omit unknown placements. "
        "Bindings may reference ONLY supplied public concept IDs. Location binding requires subject_id=target actor. "
        "Placement x/y are RELATIVE to the region, not absolute scene coordinates; leave room for a 248x180 actor card. "
        "Theme has exactly background, surface, ink, accent hex colors, never material names as keys. "
        "In binding.values, KEYS are actual state values and VALUES are region IDs (not labels) or hex colors. "
        'For example values={"engineering":"room2"} for location, or {"on":"#FF0000","off":"#444444"} for color. '
        'Never return {"value":"on","color":"#FF0000"}. '
        "Do not create binding concepts. Empty bindings are valid. Return JSON only using this schema: "
        + json.dumps(VisualPlan.model_json_schema())
    )
    scoped = provider.with_output_limit(2400) if hasattr(provider, "with_output_limit") else provider
    if hasattr(scoped, "with_visual_mode"):
        scoped = scoped.with_visual_mode()
    response = await scoped.chat(model="", system_prompt=system, user_prompt=prompt)
    raw = response.get("raw", {})
    choices = raw.get("choices", [])
    if raw.get("mock") or (choices and choices[0].get("finish_reason") in {"length", "max_tokens"}):
        raise VisualGenerationError("visual_output_truncated", "visual response missing or truncated")
    try:
        return parse_visual(str(response.get("content", "")), workflow, public_state.get("concepts", {}))
    except (ValueError, ValidationError) as exc:
        error = VisualGenerationError("visual_invalid_schema", "Visual Director returned an invalid scene; no layout applied")
        error.diagnostics = [{"type": type(exc).__name__, "issues": [
            {"path": list(row["loc"]), "type": row["type"]} for row in exc.errors(include_input=False)][:8]}
            if isinstance(exc, ValidationError) else {"type": type(exc).__name__, "reason": str(exc)[:160]},
            {"response_excerpt": str(response.get("content", ""))[:2400]}]
        raise error from exc
