"""Build the LTA TrafficSpeedBands LinkID -> page index (geometry only, no speeds).

Usage: .venv/bin/python scripts/build_lta_link_index.py
"""
import time
from route_agent.config import Settings
from route_agent.lta_index import build_index, index_path

if __name__ == "__main__":
    settings = Settings()
    started = time.monotonic()
    summary = build_index(settings)
    print(f"已建立 {summary['pages']} 页、{summary['links']} 条链接的索引：{index_path(settings)}"
          f"（{time.monotonic()-started:.1f} 秒）")
