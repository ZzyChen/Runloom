
from typing import Any

from Runloom.app.agent.protocols import ToolSpec


class AddTool:
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
        return arguments["a"]+arguments["b"]

class ToolRegistry:
    def registry(tool):
        pass

