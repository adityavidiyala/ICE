from enum import Enum
from dataclasses import dataclass, field
from typing import Any

class AgentEventType(str, Enum):
    AGENT_START      = "agent_start"
    AGENT_END        = "agent_end"
    AGENT_ERROR      = "agent_error"
    TEXT_COMPLETE    = "text_complete"
    TEXT_DELTA       = "text_delta"
    TOOL_CALL_START  = "tool_call_start"
    TOOL_CALL_COMPLETE = "tool_call_complete"
    TOKEN_USAGE      = "token_usage"       # emitted after each LLM turn
    CONTEXT_PRUNED   = "context_pruned"    # emitted when old tool outputs are pruned

@dataclass
class AgentEvent:
    type: AgentEventType
    data: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def agent_start(cls, message: str):
        return cls(type=AgentEventType.AGENT_START, data={"message": message})

    @classmethod
    def agent_end(cls, response: str | None = None):
        return cls(type=AgentEventType.AGENT_END, data={"response": response})

    @classmethod
    def agent_error(cls, error: str):
        return cls(type=AgentEventType.AGENT_ERROR, data={"error": error})

    @classmethod
    def text_complete(cls, content: str):
        return cls(type=AgentEventType.TEXT_COMPLETE, data={"content": content})

    @classmethod
    def text_delta(cls, content: str):
        return cls(type=AgentEventType.TEXT_DELTA, data={"content": content})

    @classmethod
    def tool_call_start(cls, name: str, arguments: dict[str, Any]):
        return cls(type=AgentEventType.TOOL_CALL_START, data={"name": name, "arguments": arguments})

    @classmethod
    def tool_call_complete(cls, name: str, result: str):
        return cls(type=AgentEventType.TOOL_CALL_COMPLETE, data={"name": name, "output": result})

    @classmethod
    def token_usage(cls, total_tokens: int, turn: int):
        return cls(type=AgentEventType.TOKEN_USAGE, data={"total_tokens": total_tokens, "turn": turn})

    @classmethod
    def context_pruned(cls, n_pruned: int, tokens_before: int):
        return cls(type=AgentEventType.CONTEXT_PRUNED, data={"n_pruned": n_pruned, "tokens_before": tokens_before})
