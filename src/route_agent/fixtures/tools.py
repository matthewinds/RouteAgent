"""Budgeted, allow-listed tool calls with request-local deduplication."""
import json
import time
from .models import ToolResult, TaskState


class ToolFailure(RuntimeError):
    pass


class ToolRegistry:
    def __init__(self, state: TaskState, max_calls=24, max_seconds=120):
        self.state = state
        self.max_calls, self.max_seconds = max_calls, max_seconds
        self.started = time.monotonic()
        self._functions, self._cache = {}, {}

    def register(self, name, function):
        self._functions[name] = function

    def call(self, tool_name, **arguments):
        name = tool_name
        if name not in self._functions:
            raise ToolFailure("未注册的地图工具。")
        if time.monotonic() - self.started > self.max_seconds:
            raise ToolFailure("本次规划已达到时间上限，请缩小需求后重试。")
        key = json.dumps([name, arguments], sort_keys=True, default=str)
        cached = key in self._cache
        if not cached and len([x for x in self.state.tools if not x.cached]) >= self.max_calls:
            raise ToolFailure("本次规划已达到工具调用上限。")
        start = time.monotonic()
        try:
            value = self._cache[key] if cached else self._functions[name](**arguments)
            self._cache[key] = value
            self.state.tools.append(ToolResult(tool=name, arguments=arguments, status="ok",
                source="adapter", cached=cached, duration_ms=(time.monotonic()-start)*1000,
                summary=f"{len(value)} results" if isinstance(value, (list, dict)) else type(value).__name__))
            return value
        except Exception:
            self.state.tools.append(ToolResult(tool=name, arguments=arguments, status="error",
                source="adapter", duration_ms=(time.monotonic()-start)*1000, summary="工具未能完成请求"))
            raise
