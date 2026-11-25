from dataclasses import dataclass
from pathlib import Path
import sqlite3
from typing import Iterable

@dataclass(frozen=True)
class TelemetryRecord:
    sim_time_s: float
    hour_str: str
    demand_mw: float
    solar_mw: float
    thermal_mw: float
    bess_mw: float
    bess_soc: float
    freq_hz: float
    rocof_hz_s: float


class SQLiteTelemetryRepository:

    def __init__(self, db_path: str = "data/grid_telemetry.db"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _init_schema(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("DROP TABLE IF EXISTS telemetry")
            conn.execute("""
                CREATE TABLE telemetry (
                    sim_time_s REAL PRIMARY KEY,
                    hour_str TEXT NOT NULL,
                    demand_mw REAL NOT NULL,
                    solar_mw REAL NOT NULL,
                    thermal_mw REAL NOT NULL,
                    bess_mw REAL NOT NULL,
                    bess_soc REAL NOT NULL,
                    freq_hz REAL NOT NULL,
                    rocof_hz_s REAL NOT NULL
                )
            """)
            conn.commit()

    def save_batch(self, records: Iterable[TelemetryRecord]) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.executemany(
                """
                INSERT INTO telemetry (
                    sim_time_s, hour_str, demand_mw, solar_mw,
                    thermal_mw, bess_mw, bess_soc, freq_hz, rocof_hz_s
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        r.sim_time_s, r.hour_str, r.demand_mw, r.solar_mw,
                        r.thermal_mw, r.bess_mw, r.bess_soc, r.freq_hz, r.rocof_hz_s
                    )
                    for r in records
                ]
            )
            conn.commit()