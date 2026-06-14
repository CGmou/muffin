"""Pydantic models shared across the API surface. These describe the JSON that
flows between the web UI, the manager, and the workers."""

from typing import Any, Optional

from pydantic import BaseModel, Field


# ---- Job submission ----
class JobSubmit(BaseModel):
    name: str = Field(..., description="Display name for the job")
    dcc: str = Field(..., description="DCC plugin name, e.g. blender / maya / kick")
    renderer: str = Field("", description="Renderer override, e.g. CYCLES / arnold")
    scene_path: str = Field(..., description="Path to the scene file on the workers")
    output_path: str = Field("", description="Output directory / file pattern")
    frame_start: int = 1
    frame_end: int = 1
    frame_step: int = 1
    chunk_size: int = Field(1, ge=1, description="Frames per task")
    priority: int = Field(50, description="Higher runs first")
    extra: dict[str, Any] = Field(default_factory=dict, description="Renderer-specific knobs")
    submitter: str = Field("", description="Who submitted, e.g. user@host")
    batch: str = Field("", description="Group id — jobs sharing it display as one job")
    pool: str = Field("", description="Only workers in this pool may render the job")
    suspended: bool = Field(False, description="Submit paused — starts via Start")


# ---- Editing ----
class JobEdit(BaseModel):
    name: Optional[str] = None
    priority: Optional[int] = None
    frame_start: Optional[int] = None
    frame_end: Optional[int] = None
    frame_step: Optional[int] = None
    chunk_size: Optional[int] = None


class WorkerEdit(BaseModel):
    name: Optional[str] = None
    capabilities: Optional[list[str]] = None
    pool: Optional[str] = None


class PoolCreate(BaseModel):
    name: str


class PoolMembers(BaseModel):
    workers: list[str] = Field(default_factory=list)


# ---- Worker <-> manager ----
class WorkerRegister(BaseModel):
    name: str
    host: str = ""
    capabilities: list[str] = Field(default_factory=list)
    cpu: str = ""
    gpu: str = ""
    ram: str = ""


class WorkerHeartbeat(BaseModel):
    status: str = "idle"  # idle | busy | offline
    current_task_id: Optional[str] = None


class TaskProgress(BaseModel):
    progress: float = Field(0.0, ge=0.0, le=1.0)
    log: str = ""  # appended log chunk


class TaskResult(BaseModel):
    status: str  # done | failed
    log: str = ""
    exit_code: Optional[int] = None


# ---- What a worker receives when assigned a task ----
class TaskAssignment(BaseModel):
    task_id: str
    job_id: str
    job_name: str = ""
    dcc: str
    renderer: str
    scene_path: str
    output_path: str
    frame_start: int
    frame_end: int
    frame_step: int
    extra: dict[str, Any] = Field(default_factory=dict)
