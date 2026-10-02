"""Version 2 online facts. Unknown is null, not a fabricated value."""
from datetime import datetime
from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field
class Record(BaseModel):
    model_config = ConfigDict(extra="forbid")
class Evidence(Record):
    id: str
    source: str
    retrieved_at: datetime
    observed_at: datetime | None = None
    valid_until: datetime | None = None
    tool_call_id: str
    response_sha256: str
    summary: str
class Place(Record):
    id: str
    name: str
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    category: str | None = None
    opening_hours: str | None = None
    source: str
    evidence_ids: list[str] = Field(default_factory=list)
    available_lots: int | None = None
    entrance_confirmed: bool = False
    requires_confirmation: bool = False
class TravelRequest(Record):
    original_text: str
    origin: str | None = None
    destination: str | None = None
    departure_time: datetime | None = None
    mode: Literal["driving", "walking", "drive_walk"] | None = None
    max_duration_s: float | None = Field(default=None, gt=0)
    arrival_deadline: datetime | None = None
    poi_category: Literal["cafe", "restaurant", "hospital"] | None = None
    poi_name: str | None = None
    poi_required: bool = False
    multiple_stops: bool = False
    stop_duration_s: float | None = Field(default=None, ge=0)
    avoid_highways: bool = False
    prefer_avoid_highways: bool = False
    prefer_avoid_congestion: bool = False
    max_congestion_fraction: float | None = Field(default=None, ge=0, le=1)
    require_open: bool = False
    max_walking_m: float | None = Field(default=None, ge=0)
    weather_required: bool = False
    prefer_avoid_rain: bool = False
    require_dry: bool = False
    parking_name: str | None = None
    parking_duration_s: float | None = Field(default=None, ge=0)
    evidence: dict[str, str] = Field(default_factory=dict)
class ToolResult(Record):
    tool: str
    call_id: str
    arguments: dict[str, Any]
    status: str
    source: str = "online"
    cached: bool = False
    duration_ms: float = 0
    summary: str = ""
    evidence_ids: list[str] = Field(default_factory=list)
class CheckResult(Record):
    constraint: str
    status: Literal["pass", "fail", "unknown"]
    hard: bool = True
    actual: Any = None
    required: Any = None
    reason: str
    evidence_ids: list[str] = Field(default_factory=list)
class RouteLeg(Record):
    id: str
    mode: Literal["driving", "walking"]
    origin: Place
    destination: Place
    geometry: dict[str, Any]
    distance_m: float = Field(ge=0)
    provider_duration_s: float = Field(ge=0)
    source: str = "OSRM"
    evidence_ids: list[str] = Field(default_factory=list)
    highways: bool | None = None
    traffic_coverage: float | None = None
    traffic_duration_s: float | None = None
    traffic_lower_s: float | None = None
    traffic_upper_s: float | None = None
    congestion_fraction: float | None = None
    traffic_retrieved_at: datetime | None = None
    segments: list[dict[str, Any]] = Field(default_factory=list)
class WeatherContext(Record):
    source: str = "Open-Meteo"
    status: Literal["available", "unknown"] = "unknown"
    valid_start: datetime | None = None
    valid_end: datetime | None = None
    areas: list[dict[str, Any]] = Field(default_factory=list)
    samples: list[dict[str, Any]] = Field(default_factory=list)
    rain_fraction: float | None = None
    evidence_ids: list[str] = Field(default_factory=list)
    mapping_basis: str = "沿途有限坐标采样对应的天气模型网格预报，非逐路段观测。"
    risk_basis: str = "比较值为采样点及行程小时内的最大降水概率；不是整程淋雨概率。"
class RouteCandidate(Record):
    id: str
    mode: Literal["driving", "walking", "drive_walk"]
    origin: Place
    destination: Place
    legs: list[RouteLeg]
    geometry: dict[str, Any]
    poi: Place | None = None
    parking: Place | None = None
    distance_m: float
    driving_s: float = 0
    walking_s: float = 0
    walking_m: float = 0
    stop_s: float = 0
    parking_s: float | None = None
    provider_total_s: float | None = None
    total_s: float | None = None
    time_basis: str = "OSRM 基础时间，非实时交通时间"
    traffic_coverage: float | None = None
    congestion_fraction: float | None = None
    weather: WeatherContext | None = None
    score: float | None = None
    checks: list[CheckResult] = Field(default_factory=list)
    feasibility: Literal["verified", "violated", "unverified"] = "unverified"
    evidence_ids: list[str] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)
class TaskState(Record):
    request: TravelRequest
    tools: list[ToolResult] = Field(default_factory=list)
    stages: list[dict[str, Any]] = Field(default_factory=list)
    candidates: list[RouteCandidate] = Field(default_factory=list)
    attempts: list[dict[str, Any]] = Field(default_factory=list)
class PlanningResult(Record):
    schema_version: int = 2
    run_id: str
    status: Literal["needs_clarification", "verified", "unverified", "search_exhausted", "tool_error"]
    message: str
    request: TravelRequest
    state: TaskState
    questions: dict[str, str] = Field(default_factory=dict)
    recommended: RouteCandidate | None = None
    alternatives: list[RouteCandidate] = Field(default_factory=list)
    unverified: list[RouteCandidate] = Field(default_factory=list)
    explanation: str = ""
    evidence: list[Evidence] = Field(default_factory=list)
    context: dict[str, Any] = Field(default_factory=dict)
    metrics: dict[str, Any] = Field(default_factory=dict)
