import json
import uuid

from app.agent.tools import ToolRegistry
from app.agent.protocols import Message, Model, ModelRequest, ToolResult
from app.storage.sqlite_store import SQLiteStore


def new_run_id() -> str:
    return f"run_{uuid.uuid4().hex}"


class AgentLoopError(RuntimeError):
    pass


class AgentLoop:
    def __init__(
        self,
        model: Model,
        tool_registry: ToolRegistry,
        store: SQLiteStore,
        max_steps: int = 5,
    ) -> None:
        self.model = model
        self.tool_registry = tool_registry
        self.store = store
        self.max_steps = max_steps

    def _serialize_result(self, result: ToolResult) -> str:
        if result.success:
            payload = {"success": True, "output": result.output}
        else:
            payload = {"success": False, "error": result.error}
        return json.dumps(payload, ensure_ascii=False)

    def run(
        self, user_input: str, verbose: bool = False, run_id: str | None = None
    ) -> str:
        if run_id is None:
            run_id = new_run_id()
        self.store.create_run(run_id, user_input)
        messages = [Message(role="user", content=user_input)]
        try:
            for step in range(self.max_steps):
                model_request = ModelRequest(
                    messages=messages,
                    tools=self.tool_registry.list_specs(),
                    model_name=None,
                )
                response = self.model.complete(model_request)
                self.store.append_event(
                    run_id,
                    "model_response",
                    {
                        "content": response.content,
                        "tool_calls": [
                            {
                                "call_id": tool_call.call_id,
                                "name": tool_call.name,
                                "arguments": tool_call.arguments,
                            }
                            for tool_call in response.tool_calls
                        ],
                        "finish_reason": response.finish_reason,
                    },
                )
                if verbose:
                    print(f"\n--- 第 {step + 1} 轮模型响应 ---")
                    print(f"content: {response.content}")
                    print(f"tool_calls: {response.tool_calls}")
                    print(f"finish_reason: {response.finish_reason}")

                messages.append(
                    Message(
                        role="assistant",
                        content=response.content,
                        tool_calls=response.tool_calls,
                    )
                )
                if response.tool_calls:
                    for tool_call in response.tool_calls:
                        result = self.tool_registry.execute(
                            name=tool_call.name,
                            arguments=tool_call.arguments,
                            call_id=tool_call.call_id,
                        )
                        self.store.append_event(
                            run_id,
                            "tool_result",
                            {
                                "output": result.output,
                                "call_id": result.call_id,
                                "name": result.name,
                                "success": result.success,
                                "error": result.error,
                            },
                        )
                        if verbose:
                            print(
                                f"工具结果: success={result.success}, "
                                f"output={result.output}, "
                                f"error={result.error}"
                            )

                        messages.append(
                            Message(
                                role="tool",
                                content=self._serialize_result(result),
                                tool_call_id=result.call_id,
                            )
                        )
                    continue
                if response.content is None:
                    raise AgentLoopError("模型返回空响应")
                break
            else:
                raise AgentLoopError(f"Agent 超过最大步骤数：{self.max_steps}")
        except Exception as exc:
            try:
                self.store.finish_run(
                    run_id=run_id,
                    error_message=str(exc) if isinstance(exc, AgentLoopError)
                                else f"{type(exc).__name__}: {exc}",
                )
            except Exception:
                pass
            raise
        self.store.finish_run(run_id=run_id, final_output=response.content)
        return response.content