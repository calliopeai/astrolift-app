"""Small, realistic Langflow / Flowise export fixtures for importer tests.

Each is the minimal shape an adapter must handle: a chained flow and a flow
with a fan-out or human-gate plus an unmappable construct (so the gap report
has something to report).
"""

from __future__ import annotations


def langflow_chained() -> dict:
    """ChatInput → Agent(implementer, +search tool) → Agent(reviewer) → ChatOutput."""
    return {
        "name": "Feature Dev Flow",
        "description": "Two agents in sequence",
        "data": {
            "nodes": [
                {"id": "ChatInput-aa", "data": {"type": "ChatInput", "node": {"display_name": "Chat Input"}}},
                {
                    "id": "SearchTool-bb",
                    "data": {"type": "TavilySearchTool", "node": {"display_name": "Web Search"}},
                },
                {"id": "Agent-cc", "data": {"type": "Agent", "node": {"display_name": "Implementer"}}},
                {"id": "Agent-dd", "data": {"type": "CrewAIAgent", "node": {"display_name": "Reviewer"}}},
                {
                    "id": "ChatOutput-ee",
                    "data": {"type": "ChatOutput", "node": {"display_name": "Chat Output"}},
                },
            ],
            "edges": [
                {"source": "ChatInput-aa", "target": "Agent-cc"},
                {"source": "SearchTool-bb", "target": "Agent-cc"},
                {"source": "Agent-cc", "target": "Agent-dd"},
                {"source": "Agent-dd", "target": "ChatOutput-ee"},
            ],
        },
    }


def langflow_fan_out() -> dict:
    """Agent(planner) → 2 parallel workers → CombineText(aggregate);
    a Conditional node hangs off the planner as an unmappable construct."""
    return {
        "name": "Fan Out Flow",
        "data": {
            "nodes": [
                {"id": "Agent-p", "data": {"type": "Agent", "node": {"display_name": "Planner"}}},
                {"id": "Agent-w1", "data": {"type": "Agent", "node": {"display_name": "Worker A"}}},
                {"id": "Agent-w2", "data": {"type": "Agent", "node": {"display_name": "Worker B"}}},
                {"id": "Combine-agg", "data": {"type": "CombineText", "node": {"display_name": "Aggregate"}}},
                {"id": "Cond-x", "data": {"type": "ConditionalRouter", "node": {"display_name": "Branch"}}},
            ],
            "edges": [
                {"source": "Agent-p", "target": "Agent-w1"},
                {"source": "Agent-p", "target": "Agent-w2"},
                {"source": "Agent-p", "target": "Cond-x"},
                {"source": "Agent-w1", "target": "Combine-agg"},
                {"source": "Agent-w2", "target": "Combine-agg"},
            ],
        },
    }


def flowise_chained() -> dict:
    """startAgentflow → agent(researcher, +tool) → agent(writer) → directReply."""
    return {
        "name": "Research Flow",
        "description": "AgentFlow v2 chain",
        "nodes": [
            {
                "id": "start_0",
                "data": {"name": "startAgentflow", "category": "Agent Flows", "label": "Start"},
            },
            {
                "id": "tool_0",
                "data": {"name": "toolAgentflow", "category": "Agent Flows", "label": "Search Tool"},
            },
            {
                "id": "agent_0",
                "data": {"name": "agentAgentflow", "category": "Agent Flows", "label": "Researcher"},
            },
            {
                "id": "agent_1",
                "data": {"name": "agentAgentflow", "category": "Agent Flows", "label": "Writer"},
            },
            {
                "id": "reply_0",
                "data": {"name": "directReplyAgentflow", "category": "Agent Flows", "label": "Reply"},
            },
        ],
        "edges": [
            {"source": "start_0", "target": "agent_0"},
            {"source": "tool_0", "target": "agent_0"},
            {"source": "agent_0", "target": "agent_1"},
            {"source": "agent_1", "target": "reply_0"},
        ],
    }


def flowise_gate() -> dict:
    """agent(drafter) → humanInput(approval gate) → agent(publisher);
    a Memory node hangs off the drafter as an unmappable construct."""
    return {
        "name": "Approval Flow",
        "nodes": [
            {
                "id": "agent_d",
                "data": {"name": "agentAgentflow", "category": "Agent Flows", "label": "Drafter"},
            },
            {
                "id": "human_0",
                "data": {"name": "humanInputAgentflow", "category": "Agent Flows", "label": "Approve Draft"},
            },
            {
                "id": "agent_p",
                "data": {"name": "agentAgentflow", "category": "Agent Flows", "label": "Publisher"},
            },
            {
                "id": "mem_0",
                "data": {"name": "bufferMemory", "category": "Memory", "label": "Conversation Memory"},
            },
        ],
        "edges": [
            {"source": "agent_d", "target": "human_0"},
            {"source": "human_0", "target": "agent_p"},
            {"source": "mem_0", "target": "agent_d"},
        ],
    }
