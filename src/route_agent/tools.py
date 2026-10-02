"""Allow-listed execution, typed failures, and a single end-to-end budget."""
import time
class ToolFailure(RuntimeError):
    def __init__(self, message, code="tool_error"):
        super().__init__(message)
        self.code = code
class Budget:
    def __init__(self, settings):
        self.settings = settings
        self.started = time.monotonic()
        self.http_calls = 0
        self.tool_calls = 0
    def remaining(self):
        return self.settings.max_seconds - (time.monotonic() - self.started)
    def check(self):
        if self.remaining() <= 0:
            raise ToolFailure("本次规划已达到总时间预算。", "budget_exhausted")
    def http(self):
        self.check()
        if self.http_calls >= self.settings.max_http_calls:
            raise ToolFailure("本次规划已达到外部请求上限。", "budget_exhausted")
        self.http_calls += 1
    def tool(self):
        self.check()
        if self.tool_calls >= self.settings.max_tool_calls:
            raise ToolFailure("本次规划已达到工具执行上限。", "budget_exhausted")
        self.tool_calls += 1
