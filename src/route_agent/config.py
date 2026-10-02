"""Real online configuration. Credentials are read only inside provider clients."""
from dataclasses import dataclass, field
from pathlib import Path
import os
from dotenv import load_dotenv
ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env", override=False)
def env(name, default=""):
    return field(default_factory=lambda: os.getenv(name, default))
@dataclass(frozen=True)
class Settings:
    root: Path = ROOT
    mode: str = env("FYP_MODE", "live")
    model: str = env("DEEPSEEK_MODEL", "deepseek-flash")
    deepseek_base: str = env("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
    thinking: str = env("DEEPSEEK_THINKING", "enabled")
    osrm_driving_base: str = env("OSRM_DRIVING_BASE_URL", "https://routing.openstreetmap.de/routed-car")
    osrm_walking_base: str = env("OSRM_WALKING_BASE_URL", "https://routing.openstreetmap.de/routed-foot")
    osrm_driving_profile: str = env("OSRM_DRIVING_PROFILE", "driving")
    osrm_walking_profile: str = env("OSRM_WALKING_PROFILE", "foot")
    geocode_base: str = env("ORS_GEOCODE_BASE_URL", "https://api.heigit.org/pelias/v1")
    overpass_url: str = env("OVERPASS_URL", "https://overpass-api.de/api/interpreter")
    lta_base: str = env("LTA_API_BASE", "https://datamall2.mytransport.sg/ltaodataservice")
    weather_base: str = env("WEATHER_API_BASE", "https://api-open.data.gov.sg/v2/real-time/api")
    open_meteo_base: str = env("OPEN_METEO_BASE_URL", "https://api.open-meteo.com/v1")
    max_rounds: int = 16
    max_tool_calls: int = 32
    max_http_calls: int = 96
    max_seconds: float = 240
    max_replans: int = 2
    first_pois: int = 10
    expanded_pois: int = 30
    paths_per_leg: int = 3
    traffic_max_age_s: int = 600
    def credential(self, name):
        return os.getenv(name, "").strip()
    def readiness(self):
        return {name: bool(self.credential(name)) for name in
            ("DEEPSEEK_API_KEY", "ORS_API_KEY", "LTA_API_KEY", "DATAGOVSG_API_KEY")}
    def routing_configuration(self):
        return {"engine":"OSRM","driving_configured":bool(self.osrm_driving_base.strip()),
                "walking_configured":bool(self.osrm_walking_base.strip()),"key_required":False,
                "public_driving_service":self.osrm_driving_base.rstrip("/")=="https://routing.openstreetmap.de/routed-car"}
    def missing_required(self):
        return [name for name in ("DEEPSEEK_API_KEY", "ORS_API_KEY") if not self.credential(name)]
