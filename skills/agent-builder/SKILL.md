---
name: agent-builder
description: Use when the user wants to decide what to build, automate, or change based on how their organization currently works — for example, asks what a team should automate, explicitly asks for the agent builder, or asks to build a skill, agent, or automation from organizational processes or Within workspace data. Do NOT invoke for general coding, MCP servers or tools unrelated to Within workspace data, local scripts or automation, generic skill or agent development, or debugging. For questions that only ask how an existing organizational process works, use within-process-context-graph instead.
---

# Agent Builder

This is the entry point for all Within agent-building work. The methodology is served from the Within MCP — do not improvise it. Fetch it and follow it.

1. Call `get_agent_builder_instructions` and follow what it returns before doing any other agent-building work. It self-guides from there.
2. The returned instructions may reference supporting resources. When the current work needs one, call `get_agent_builder_resource` to fetch it and use the returned content.
3. Write all durable outputs to the local project directory as the instructions specify. The tools are read-only and serve methodology only; the client owns project state.

See `tools.md` for the two tools.
