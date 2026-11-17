from typing import Any, Literal, Protocol
from dataclasses import dataclass,field

MessageRole = Literal["system","user","assistant","tool"]


@dataclass
class ToolCall:
    call_id:str
    name:str
    arguments:dict[str,Any]

@dataclass
class ToolSpec:
    name:str
    description:str
    input_schema:dict[str,Any]

@dataclass
class ToolResult:
    call_id: str
    name: str
    success:bool
    output: Any | None = None
    error:str | None = None

@dataclass
class Message:
    role:MessageRole
    content:str | None = None
    tool_calls:list[ToolCall] = field(default_factory=list)
    tool_call_id:str | None = None


@dataclass
class ModelRequest:
    messages: list[Message]
    tools: list[ToolSpec]
    model_name: str | None

@dataclass
class ModelResponse:
    content: str| None = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    finish_reason: str | None = None

class Model(Protocol):
    def complete(self, request: ModelRequest) -> ModelResponse:
        return ModelResponse()

class Tool(Protocol):
    """具体工具需要实现的接口。"""

    @property
    def spec(self) -> ToolSpec:
        ...

    def execute(self, arguments: dict[str, Any]) -> Any:
        ...