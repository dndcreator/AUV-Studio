from __future__ import annotations

from .schemas import NodeFieldSpec, NodeSpec


def get_node_specs() -> list[NodeSpec]:
    return [
        NodeSpec(
            type="external_agent",
            title="External Agent Node",
            description="Text-only v1 protocol bridge for user-owned agent services.",
            config_fields=[
                NodeFieldSpec(
                    key="integration_mode",
                    label="Integration Mode",
                    kind="select",
                    required=True,
                    default="mock",
                    options=["mock", "http", "bridge_local"],
                ),
                NodeFieldSpec(
                    key="agent_id",
                    label="Agent ID",
                    kind="text",
                    required=False,
                    default="user_agent",
                ),
                NodeFieldSpec(
                    key="endpoint_url",
                    label="Endpoint URL",
                    kind="text",
                    required=False,
                    default="",
                    placeholder="Required when mode=http, optional for bridge_local (defaults to http://127.0.0.1:8787)",
                ),
                NodeFieldSpec(
                    key="endpoint_path",
                    label="Endpoint Path",
                    kind="text",
                    required=False,
                    default="/agent/tasks",
                    advanced=True,
                ),
                NodeFieldSpec(
                    key="api_key",
                    label="API Key",
                    kind="password",
                    required=False,
                    default="",
                    advanced=True,
                ),
                NodeFieldSpec(
                    key="timeout_ms",
                    label="Timeout (ms)",
                    kind="number",
                    required=False,
                    default=60000,
                ),
                NodeFieldSpec(
                    key="max_tokens",
                    label="Max Tokens",
                    kind="number",
                    required=False,
                    default=1500,
                    advanced=True,
                ),
                NodeFieldSpec(
                    key="include_background_in_prompt",
                    label="Inject Background Context",
                    kind="select",
                    required=False,
                    default="true",
                    options=["true", "false"],
                ),
                NodeFieldSpec(
                    key="prompt_template",
                    label="Prompt Template",
                    kind="textarea",
                    required=False,
                    default="{{prompt}}",
                    placeholder="Use {{prompt}} {{environment}} {{global_guidance}} {{interactions}} {{memory}}",
                    advanced=True,
                ),
            ],
            input_fields=[
                NodeFieldSpec(
                    key="prompt",
                    label="Task Prompt",
                    kind="textarea",
                    required=False,
                    default="{{input.task}}",
                )
            ],
        ),
        NodeSpec(
            type="director",
            title="Director Node",
            description="Top-level orchestrator with GPRO-like candidate optimization and global guidance output.",
            config_fields=[
                NodeFieldSpec(key="model", label="Model Name", kind="text", required=False, default=""),
                NodeFieldSpec(
                    key="objective",
                    label="Global Objective",
                    kind="textarea",
                    required=False,
                    default="Keep collaboration aligned with scenario goals while controlling cost.",
                ),
                NodeFieldSpec(
                    key="style_guardrails",
                    label="Style Guardrails",
                    kind="textarea",
                    required=False,
                    default="Be concise, role-consistent, and avoid unnecessary long responses.",
                ),
                NodeFieldSpec(
                    key="gpro_candidates",
                    label="GPRO Candidates",
                    kind="number",
                    required=False,
                    default=3,
                ),
            ],
            input_fields=[
                NodeFieldSpec(
                    key="situation",
                    label="Current Situation",
                    kind="textarea",
                    required=False,
                    default="{{input.task}}",
                )
            ],
        ),
        NodeSpec(
            type="human_checkpoint",
            title="Human Checkpoint Node",
            description="Pause run for human intervention, then continue from this node after response.",
            config_fields=[
                NodeFieldSpec(
                    key="owner",
                    label="Checkpoint Owner",
                    kind="select",
                    required=False,
                    default="director",
                    options=["director", "node"],
                ),
                NodeFieldSpec(
                    key="question_template",
                    label="Question Template",
                    kind="textarea",
                    required=False,
                    default="Please review current simulation state and provide intervention guidance.",
                ),
                NodeFieldSpec(
                    key="required",
                    label="Response Required",
                    kind="select",
                    required=False,
                    default="true",
                    options=["true", "false"],
                ),
            ],
            input_fields=[
                NodeFieldSpec(
                    key="question",
                    label="Question Override",
                    kind="textarea",
                    required=False,
                    default="",
                ),
                NodeFieldSpec(
                    key="context_hint",
                    label="Context Hint",
                    kind="textarea",
                    required=False,
                    default="{{input.task}}",
                ),
            ],
        ),
        NodeSpec(
            type="agent",
            title="Agent Node",
            description="Role-based LLM execution with simplified model connection options.",
            config_fields=[
                NodeFieldSpec(
                    key="role",
                    label="Role",
                    kind="select",
                    required=True,
                    default="planner",
                    options=["planner", "coder", "reviewer", "custom"],
                ),
                NodeFieldSpec(
                    key="model_connection_mode",
                    label="Model Connection",
                    kind="select",
                    required=True,
                    default="platform_default",
                    options=["platform_default", "custom_endpoint"],
                ),
                NodeFieldSpec(
                    key="model",
                    label="Model Name",
                    kind="text",
                    required=False,
                    default="",
                    placeholder="Leave empty to use platform default model",
                ),
                NodeFieldSpec(
                    key="system_prompt",
                    label="System Prompt",
                    kind="textarea",
                    required=False,
                    default="You are a helpful agent.",
                ),
                NodeFieldSpec(
                    key="model_provider",
                    label="Model Provider",
                    kind="select",
                    required=False,
                    default="openai_compatible",
                    options=[
                        "openai_compatible",
                        "ollama",
                        "llama_cpp",
                        "lm_studio",
                        "vllm",
                        "localai",
                        "tgi",
                        "text_generation_webui",
                    ],
                    advanced=True,
                ),
                NodeFieldSpec(
                    key="model_base_url",
                    label="Custom Base URL",
                    kind="text",
                    required=False,
                    default="",
                    placeholder="Required only for custom endpoint mode",
                    advanced=True,
                ),
                NodeFieldSpec(
                    key="model_api_key",
                    label="Custom API Key",
                    kind="password",
                    required=False,
                    default="",
                    placeholder="Required only for custom endpoint mode",
                    advanced=True,
                ),
            ],
            input_fields=[
                NodeFieldSpec(
                    key="prompt",
                    label="User Prompt",
                    kind="textarea",
                    required=False,
                    default="{{input.task}}",
                )
            ],
        ),
        NodeSpec(
            type="prompt",
            title="Prompt Node",
            description="Template rendering node.",
            config_fields=[
                NodeFieldSpec(
                    key="template",
                    label="Template",
                    kind="textarea",
                    required=True,
                    default="Process input: {{input.task}}",
                )
            ],
            input_fields=[],
        ),
        NodeSpec(
            type="tool",
            title="Tool Node",
            description="Whitelist tool execution node.",
            config_fields=[
                NodeFieldSpec(
                    key="tool_name",
                    label="Tool Name",
                    kind="select",
                    required=True,
                    default="echo",
                    options=["echo", "concat", "select"],
                )
            ],
            input_fields=[
                NodeFieldSpec(key="text", label="Text", kind="text", required=False, default="{{input.task}}"),
                NodeFieldSpec(key="items", label="Items(JSON)", kind="json", required=False, default=[]),
                NodeFieldSpec(key="path", label="Path", kind="text", required=False, default=""),
            ],
        ),
        NodeSpec(
            type="condition",
            title="Condition Node",
            description="Boolean expression branch node.",
            config_fields=[
                NodeFieldSpec(
                    key="expression",
                    label="Expression",
                    kind="text",
                    required=True,
                    default="True",
                    placeholder='Use ctx["node_id"]["field"] style when needed',
                )
            ],
            input_fields=[],
        ),
    ]


def get_interaction_modes() -> list[str]:
    return ["report", "instruction", "feedback", "dialogue", "handoff"]
