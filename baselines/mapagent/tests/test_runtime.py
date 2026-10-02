"""Offline end-to-end test: real SDK and run.py, mocked HTTP and map result."""
import contextlib
import io
import json
import os
from pathlib import Path
import runpy
import socket
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]


class RuntimeTest(unittest.TestCase):
    def test_original_pipeline_and_transport(self):
        # Set test-only keys before importing modules. They never leave this process.
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "offline-test",
                                    "GOOGLE_MAP_API_KEY": "AIza" + "0" * 35,
                                    "MAPAGENT_MAP_MODEL": "openai/gpt-3.5-turbo"}):
            import httpx
            from openai import OpenAI
            import utilities
            import parallel_function_implementation as maps
            requests = []

            def transport(request):
                body = json.loads(request.content)
                requests.append(body)
                self.assertEqual(request.url.host, "openrouter.ai")
                self.assertEqual(body["model"], "openai/gpt-3.5-turbo")
                if "tools" in body:
                    message = {"role": "assistant", "content": None, "tool_calls": [{"id": "call_offline",
                               "type": "function", "function": {"name": "get_place_info",
                               "arguments": '{"location_address":"Offline location"}'}}]}
                elif len(requests) == 1:
                    message = {"role": "assistant", "content": '["google_maps", "sequencer", "solution_generator", "answer_generator"]'}
                else:
                    message = {"role": "assistant", "content": "The answer is A."}
                return httpx.Response(200, json={"id": "offline", "object": "chat.completion", "created": 0,
                    "model": body["model"], "choices": [{"index": 0, "message": message, "finish_reason": "stop"}]})

            client = OpenAI(api_key="offline-test", base_url="https://openrouter.ai/api/v1",
                            http_client=httpx.Client(transport=httpx.MockTransport(transport)))
            with tempfile.TemporaryDirectory() as directory:
                argv = ["run.py", "--data_root", str(ROOT / "datasets_dir/txt_data/trip"),
                        "--output_root", directory, "--test_split", "minitest", "--test_number", "1"]
                with patch.object(utilities, "get_llm_client", return_value=client), \
                     patch.object(maps, "get_llm_client", return_value=client), \
                     patch.object(maps, "get_place_info", return_value="OFFLINE MAP RESULT"), \
                     patch.object(socket.socket, "connect", side_effect=AssertionError("Network forbidden")), \
                     patch.object(sys, "argv", argv), contextlib.redirect_stdout(io.StringIO()):
                    runpy.run_path(str(ROOT / "src/run.py"), run_name="__main__")
                result = json.loads((Path(directory) / "trip/chameleon_chatgpt_minitest.json").read_text())
                self.assertEqual(result["count"], 1)
                cache = json.loads((Path(directory) / "trip/chameleon_chatgpt_minitest_cache.jsonl").read_text())
                self.assertEqual(cache["response"][2]["tool_calls"][0]["function"]["name"], "get_place_info")
                self.assertEqual(cache["prediction"], cache["example"]["choices"][0])
                self.assertEqual(len(requests), 4)  # planner, map agent, sequencer, solution
                self.assertEqual(requests[0]["temperature"], 0)
                self.assertEqual(requests[0]["top_p"], 0.95)
                self.assertEqual(requests[0]["max_tokens"], 128)
                self.assertNotIn("temperature", requests[1])  # retain original tool-agent default
                self.assertIn("OFFLINE MAP RESULT", requests[2]["messages"][0]["content"])
                self.assertEqual(requests[2]["messages"], requests[3]["messages"])
                self.assertEqual(requests[3]["max_tokens"], 512)
            client.close()


if __name__ == "__main__":
    unittest.main()
