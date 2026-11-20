from openai import OpenAI
from dotenv import load_dotenv
import os

from app.agent.loop import AgentLoop, new_run_id
from app.agent.tools import AddTool, ToolRegistry
from app.agent.model_adapter import ModelAdapter
from app.storage.sqlite_store import SQLiteStore
load_dotenv()

api_key = os.getenv("API_KEY")
base_url = os.getenv("BASE_URL")
model = os.getenv("MODEL")

client = OpenAI(api_key=api_key,base_url=base_url)
model_adapter = ModelAdapter(client=client,model_name=model)
tool_registry = ToolRegistry()
tool_registry.register(AddTool())
store = SQLiteStore()

agent_loop = AgentLoop(model=model_adapter,tool_registry=tool_registry,store=store)
run_id = new_run_id()
res = agent_loop.run("请使用add工具告诉我899+799等于多少呢？",verbose=True,run_id=run_id)
print(res)