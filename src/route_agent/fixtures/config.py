from pathlib import Path
from dataclasses import dataclass
import os
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[3]
load_dotenv(ROOT / ".env", override=False)


@dataclass(frozen=True)
class Settings:
    root: Path = ROOT
    max_replans: int = 2
    first_pois: int = 10
    expanded_pois: int = 30
    paths_per_leg: int = 3
    max_tool_calls: int = 24
    max_seconds: int = 120
    traffic_max_age_s: int = 600
    max_snap_m: int = 150
    parser: str = os.getenv("FYP_PARSER", "rules")
    model: str = os.getenv("FYP_MODEL", os.getenv("MAPAGENT_MODEL", "openai/gpt-3.5-turbo"))
    lta_base: str = os.getenv("LTA_API_BASE", "https://datamall2.mytransport.sg/ltaodataservice")

    @property
    def graph_path(self):
        return self.root / "data/osm/singapore.graphml"

    @property
    def snapshots(self):
        return self.root / "data/snapshots"
