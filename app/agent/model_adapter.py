

import json

from app.agent.protocols import Message, ModelRequest, ModelResponse, ToolCall, ToolSpec

class ModelAdapterError(RuntimeError):
    pass

class ModelAdapter:
    def __init__(self,client,model_name:str) -> None:
        self.client = client
        self.model_name = model_name

    def complete(self, request:ModelRequest) -> ModelResponse:
        payload = {
            "model": request.model_name or self.model_name,
            "messages": self._build_messages(request.messages),
            }
        tools = self._build_tools(request.tools)
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        response = self.client.chat.completions.create(**payload)
        return self._parse_response(response)
    
    def _build_messages(self,messages: list[Message]) -> list[dict]:
        result = []
        for message in messages:
            item = {
                "role": message.role,
                "content": message.content, 
            }
            if message.role == "assistant" and message.tool_calls:
                item["tool_calls"] = [
                    {
                        "id": call.call_id,
                        "type": "function",
                        "function": {
                            "name": call.name,
                            "arguments": json.dumps(call.arguments)
                        }
                    }
                    for call in message.tool_calls
                ]
            if message.role == "tool":
                item["tool_call_id"] = message.tool_call_id
            result.append(item)
        return result

    def _build_tools(self,tools: list[ToolSpec]) -> list[dict]:
        return [
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.input_schema
                }
            }
            for tool in tools
        ] 

    def _parse_response(self,response) -> ModelResponse:
        choices = getattr(response, "choices", None)
        if not choices:
            raise ModelAdapterError("模型响应中无choices")
        choice = choices[0]
        message = getattr(choice, "message", None)
        if message is None:
            raise ModelAdapterError("模型响应中没有 message")

        parsed_tool_calls = []
        for raw_call in getattr(message, "tool_calls", None) or []:
            function = getattr(raw_call,"function", None)
            if function is None:
                raise ModelAdapterError("工具调用中缺少 function")

            raw_arguments = function.arguments
            try:
                arguments = json.loads(raw_arguments)
            except (TypeError, json.JSONDecodeError) as exc:
                raise ModelAdapterError(
                    f"工具 {function.name!r} 的 arguments 不是合法 JSON"
                ) from exc
            if not isinstance(arguments, dict):
                raise ModelAdapterError(
                    f"工具 {function.name!r} 的 arguments 必须是 JSON object"
                )

            call_id = getattr(raw_call, "id", None)
            if not call_id:
                raise ModelAdapterError("工具调用中没有 call id")

            parsed_tool_calls.append(
                ToolCall(
                    call_id=call_id,
                    name=function.name,
                    arguments=arguments,
                )
            )

        return ModelResponse(
            content=message.content,
            tool_calls=parsed_tool_calls,
            finish_reason=getattr(choice, "finish_reason", None),
        )