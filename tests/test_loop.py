
from app.agent.tools import ToolRegistry,AddTool


registry = ToolRegistry()
registry.register(AddTool())
print(registry.list_specs())
registry.register(AddTool())