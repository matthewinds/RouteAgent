"""Allow-listed execution, typed failures, and a single end-to-end budget."""
import threading
import time
from contextlib import contextmanager
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
        self.search_deadline = None
        self.lock = threading.Lock()
    def remaining(self):
        return self.settings.max_seconds - (time.monotonic() - self.started)
    def check(self):
        if self.remaining() <= 0:
            raise ToolFailure("本次规划已达到总时间预算。", "budget_exhausted")
    def search_remaining(self):
        return min(self.remaining(), self.search_deadline-time.monotonic()) if self.search_deadline is not None else self.remaining()
    def check_search(self):
        self.check()
        if self.search_deadline is not None and (self.search_remaining() <= 0 or
                self.http_calls >= self.settings.max_http_calls-self.settings.completion_reserve_http_calls):
            raise ToolFailure("候选搜索达到本批次或预留边界，保留预算用于核验已有行程和完成推荐；搜索范围有限。", "search_budget")
    @contextmanager
    def search(self):
        previous = self.search_deadline
        self.search_deadline = min(time.monotonic()+self.settings.search_batch_seconds,
            self.started+self.settings.max_seconds-self.settings.completion_reserve_seconds,
            previous if previous is not None else float("inf"))
        try:
            yield
        finally:
            self.search_deadline = previous
    def http(self):
        self.check_search()
        # Speed-band pages are fetched by parallel workers.
        with self.lock:
            if self.http_calls >= self.settings.max_http_calls:
                raise ToolFailure("本次规划已达到外部请求上限。", "budget_exhausted")
            self.http_calls += 1
    def tool(self):
        self.check()
        if self.tool_calls >= self.settings.max_tool_calls:
            raise ToolFailure("本次规划已达到工具执行上限。", "budget_exhausted")
        self.tool_calls += 1
