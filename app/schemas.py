from pydantic import BaseModel
from typing import Optional

class StopBase(BaseModel):
    stop_id: str
    name: str
    lat: float
    lon: float
    desc: Optional[str] = None

class StopOut(StopBase):
    id: int
    distance_m: Optional[float] = None

    class Config:
        orm_mode = True

class LiveUpdate(BaseModel):
    vehicle_id: str
    lat: float
    lon: float
    timestamp: Optional[str] = None
