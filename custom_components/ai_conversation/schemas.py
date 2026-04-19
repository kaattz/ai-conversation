import json
from homeassistant.helpers import llm
from homeassistant.components import conversation
from voluptuous_openapi import convert
from .const import LOGGER

class Dict(dict):
    def __getattr__(self, item):
        return self.get(item)

    def __setattr__(self, key, value):
        self[key] = Dict(value) if isinstance(value, dict) else value

class ChatCompletions(Dict):
    @property
    def messages(self):
        return self.setdefault("messages", [])

    @property
    def tools(self):
        return self.setdefault("tools", [])
    
    def set_thinking_if_needed(self, model, thinking_type=None):
        """根据模型类型决定是否添加thinking参数"""
        # glm-4.5和glm-4.6v系列模型需要thinking参数
        if model and (model.startswith("glm-4.5") or model.startswith("glm-4.6v")) and thinking_type:
            # 根据官方文档，GLM-4.5和GLM-4.6v的thinking参数应该是字典格式
            if thinking_type == "enabled":
                self["thinking"] = {"type": "enabled"}
            elif isinstance(thinking_type, dict) and "type" in thinking_type:
                self["thinking"] = thinking_type
            else:
                # 如果是其他值，不添加thinking参数
                pass

class ChatMessage(Dict):
    def __init__(self, content, role="user", **kwargs):
        if isinstance(content, str):
            content = content.lstrip()
        super().__init__(role=role, content=content, **kwargs)

    @staticmethod
    def from_conversation_content(content: conversation.Content):
        if isinstance(content, conversation.ToolResultContent):
            return ChatMessage(
                role="tool",
                content=json.dumps(content.tool_result),
                tool_call_id=content.tool_call_id,
            )

        role = content.role
        if role == "system" and content.content:
            return ChatMessage(role=role, content=content.content)
        if role == "user" and content.content:
            return ChatMessage(role=role, content=content.content)
        if role == "assistant":
            param = ChatMessage(role=role, content=content.content)
            if isinstance(content, conversation.AssistantContent) and content.tool_calls:
                param.tool_calls = [
                    Dict(
                        type="function",
                        id=tool_call.id,
                        function=Dict(arguments=json.dumps(tool_call.tool_args), name=tool_call.tool_name),
                    )
                    for tool_call in content.tool_calls
                ]
            return param
        return None

    async def to_conversation_content_delta(self):
        data = {
            "role": self.role,
            "content": self.content,
        }
        if self.tool_calls:
            data["tool_calls"] = [
                llm.ToolInput(
                    id=tool_call["id"],
                    tool_name=tool_call["function"]["name"],
                    tool_args=json.loads(tool_call["function"]["arguments"]),
                )
                for tool_call in self.tool_calls
            ]
        yield data


class ChatMessageContent(Dict):
    def __init__(self, text=None, image_url=None, video_url=None, file_url=None):
        if text is not None:
            super().__init__(type="text", text=text)
        elif image_url is not None:
            super().__init__(type="image_url", image_url=Dict(url=image_url))
        elif video_url is not None:
            super().__init__(type="video_url", video_url=Dict(url=video_url))
        elif file_url is not None:
            super().__init__(type="file_url", file_url=Dict(url=file_url))

class ChatTool(Dict):
    @staticmethod
    def from_hass_llm_tool(tool: llm.Tool, custom_serializer=None):
        func = Dict(
            name=tool.name,
            parameters=convert(tool.parameters, custom_serializer=custom_serializer),
        )
        if tool.description:
            func.description = tool.description
        return ChatTool(type="function", function=func)

class ResponseJsonSchema(Dict):
    def __init__(self, name, schema, llm_api=None):
        super().__init__(name=name, strict=True)
        self.schema = convert(
            schema,
            custom_serializer=llm_api.custom_serializer if llm_api else llm.selector_serializer,
        )
        self._adjust_schema(self.schema)

    def _adjust_schema(self, schema: dict):
        if schema["type"] == "object":
            if "properties" not in schema:
                return
            if "required" not in schema:
                schema["required"] = []
            for prop, prop_info in schema["properties"].items():
                self._adjust_schema(prop_info)
                if prop not in schema["required"]:
                    prop_info["type"] = [prop_info["type"], "null"]
                    schema["required"].append(prop)
        elif schema["type"] == "array":
            if "items" not in schema:
                return
            self._adjust_schema(schema["items"])

class ChatCompletionsResult(Dict):
    response = None

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # 记录响应的关键信息
        if "choices" in self and self["choices"]:
            for i, choice in enumerate(self["choices"]):
                if "message" in choice:
                    message = choice["message"]
                    LOGGER.debug('Response Choice %d - Role: %s', i, message.get("role", "unknown"))
                    LOGGER.debug('Response Choice %d - Content: %s', i, message.get("content", ""))
                    if "reasoning_content" in message:
                        LOGGER.debug('Response Choice %d - Reasoning: %s', i, message.get("reasoning_content"))
        
        # 记录使用量信息
        if "usage" in self:
            usage = self["usage"]
            LOGGER.debug('Token Usage - Prompt: %s, Completion: %s, Total: %s',
                        usage.get("prompt_tokens", 0),
                        usage.get("completion_tokens", 0),
                        usage.get("total_tokens", 0))
        
        # 记录错误信息
        if "error" in self:
            error = self["error"]
            LOGGER.error('API Error - Code: %s, Message: %s',
                        error.get("code", "unknown"),
                        error.get("message", ""))

    def to_dict(self):
        data = self.copy()
        data.pop("response", None)
        return data

    @property
    def choices(self):
        choices = self.get("choices", [])
        for choice in choices:
            if "message" in choice:
                choice["message"] = ChatMessage(**choice["message"])
        return choices

    @property
    def message(self):
        for choice in self.choices:
            if "message" in choice:
                return choice["message"]
        return None
