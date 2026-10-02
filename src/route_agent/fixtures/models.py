from datetime import datetime
from typing import Any, Literal
from pydantic import BaseModel, Field, ConfigDict


class Record(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Place(Record):
    id: str
    name: str
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    category: str | None = None
    node: int | None = None
    snap_m: float = 0
    opening_hours: str | None = None
    source: str = "OSM"


class TravelRequest(Record):
    original_text: str
    origin: str | None = None
    destination: str | None = None
    departure_time: datetime | None = None
    mode: Literal["driving", "walking", "transit"] | None = None
    max_duration_s: float | None = Field(default=None, gt=0)
    arrival_deadline: datetime | None = None
    poi_category: str | None = None
    poi_name: str | None = None
    resolved_poi_id: str | None = None
    multiple_stops: bool = False
    poi_required: bool = False
    stop_duration_s: float | None = Field(default=None, ge=0)
    avoid_highways: bool = False
    prefer_avoid_highways: bool = False
    prefer_avoid_congestion: bool = False
    max_congestion_fraction: float | None = Field(default=None, ge=0, le=1)
    require_open: bool = False
    max_walking_m: float | None = Field(default=None, ge=0)
    weather_required: bool = False
    evidence: dict[str, str] = Field(default_factory=dict)


class ToolResult(Record):
    tool: str
    arguments: dict[str, Any]
    status: str
    source: str
    observed_at: str | None = None
    cached: bool = False
    duration_ms: float = 0
    summary: str = ""


class CheckResult(Record):
    constraint: str
    status: Literal["pass", "fail", "unknown"]
    hard: bool = True
    actual: Any = None
    required: Any = None
    reason: str
    evidence: str = ""


class RouteCandidate(Record):
    id: str
    strategy: str
    nodes: list[int]
    edges: list[str]
    geometry: dict[str, Any]
    origin: Place
    destination: Place
    poi: Place | None = None
    distance_m: float
    driving_s: float
    stop_s: float
    total_s: float
    poi_arrival_s: float | None = None
    traffic_coverage: float
    congestion_fraction: float
    unknown_fraction: float
    highways: bool = False
    closed: bool = False
    walking_m: float = 0
    score: float | None = None
    checks: list[CheckResult] = Field(default_factory=list)
    feasibility: Literal["verified", "violated", "unverified"] = "unverified"
    evidence: dict[str, Any] = Field(default_factory=dict)


class TaskState(Record):
    request: TravelRequest
    tools: list[ToolResult] = Field(default_factory=list)
    stages: list[dict[str, Any]] = Field(default_factory=list)
    candidates: list[RouteCandidate] = Field(default_factory=list)
    attempts: list[dict[str, Any]] = Field(default_factory=list)
    search: dict[str, Any] = Field(default_factory=dict)


class PlanningResult(Record):
    run_id: str
    status: Literal["needs_clarification", "verified", "unverified", "search_exhausted", "tool_error"]
    message: str
    request: TravelRequest
    questions: dict[str, str] = Field(default_factory=dict)
    recommended: RouteCandidate | None = None
    alternatives: list[RouteCandidate] = Field(default_factory=list)
    unverified: list[RouteCandidate] = Field(default_factory=list)
    state: TaskState
    explanation: str = ""
    context: dict[str, Any] = Field(default_factory=dict)
    metrics: dict[str, Any] = Field(default_factory=dict)
