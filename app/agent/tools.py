
from typing import Any

from app.agent.protocols import Tool, ToolResult, ToolSpec


class AddTool:
    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="add",
            description="加法",
            input_schema={
                "type":"object",
                "properties":{
                    "a":{"type":"number"},
                    "b":{"type":"number"},
                },
                "required":["a","b"],
            },
        )

    def execute(self, arguments:dict[str,Any]):
        return float(arguments["a"])+float(arguments["b"])

class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self,tool:Tool) -> None:
        spec = tool.spec
        if spec.name in self._tools:
            raise ValueError(f"工具已注册：{spec.name}")
        self._tools[spec.name] = tool

    def get_tool(self,name: str) -> Tool:
        try:
            return self._tools[name]
        except KeyError as exc:
            raise KeyError(f"工具不存在：{name}") from exc

    def list_specs(self) -> list[ToolSpec]:
        return [tool.spec for tool in self._tools.values()]

    def execute(self,name:str,arguments:dict[str,Any],call_id:str) -> ToolResult:
        if not isinstance(arguments, dict):
            return ToolResult(
                call_id=call_id,
                name=name,
                success=False,
                error="工具参数必须是字典",
            )
        try:
            tool = self.get_tool(name)
            output = tool.execute(arguments)

            return ToolResult(
                call_id=call_id,
                name=name,
                success=True,
                output=output,
            )

        except Exception as exc:
            return ToolResult(
                call_id=call_id,
                name=name,
                success=False,
                error=f"{type(exc).__name__}: {exc}",
            )