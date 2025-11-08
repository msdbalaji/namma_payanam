from sqlalchemy import Integer, Float, String, Column, Table
from sqlalchemy.orm import registry
from app.db import metadata

mapper_registry = registry(metadata=metadata)

stops_table = Table(
    'stops',
    metadata,
    Column('id', Integer, primary_key=True, autoincrement=True),
    Column('stop_id', String, unique=True, index=True, nullable=False),
    Column('name', String, nullable=False),
    Column('lat', Float, nullable=False),
    Column('lon', Float, nullable=False),
    Column('desc', String, nullable=True)
)

# simple model class for ORM convenience
class Stop:
    def __init__(self, stop_id: str, name: str, lat: float, lon: float, desc: str = None):
        self.stop_id = stop_id
        self.name = name
        self.lat = lat
        self.lon = lon
        self.desc = desc

mapper_registry.map_imperatively(Stop, stops_table)
