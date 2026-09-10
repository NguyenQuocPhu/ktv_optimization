"""Read the supplied QOS CSV files without depending on Excel workbooks."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

import pandas as pd

from .schema import (
    CHECKIN_DTYPES,
    CHECKIN_FILENAME,
    MAINTENANCE_DTYPES,
    MAINTENANCE_FILENAME,
)


def _default_data_dir() -> Path:
    candidates = [Path.cwd() / "data"]
    candidates.extend(parent / "data" for parent in Path(__file__).parents)
    for candidate in candidates:
        if (candidate / MAINTENANCE_FILENAME).is_file():
            return candidate
    return Path.cwd() / "data"


@dataclass(frozen=True, slots=True)
class QosCsvPaths:
    maintenance: Path  # Đường dẫn CSV checklist maintenance.
    checkins: Path  # Đường dẫn CSV check-in/check-out.

    @classmethod
    def from_data_dir(cls, data_dir: str | Path | None = None) -> "QosCsvPaths":
        root = Path(data_dir) if data_dir is not None else _default_data_dir()
        return cls(
            maintenance=root / MAINTENANCE_FILENAME,
            checkins=root / CHECKIN_FILENAME,
        )

    def ensure_available(self) -> None:
        missing = [
            str(path)
            for path in (self.maintenance, self.checkins)
            if not path.is_file()
        ]
        if missing:
            raise FileNotFoundError(
                "Missing required CSV file(s): " + ", ".join(missing)
            )


class QosMaintenanceCsvSource:
    """Pandas adapter for full and chunked reads of the two QOS files."""

    def __init__(self, data_dir: str | Path | None = None) -> None:
        # Hai đường dẫn CSV đầu vào đã resolve từ thư mục data.
        self.paths = QosCsvPaths.from_data_dir(data_dir)

    def validate_paths(self) -> None:
        self.paths.ensure_available()

    def read_maintenance(
        self,
        *,
        nrows: int | None = None,
        usecols: list[str] | tuple[str, ...] | None = None,
    ) -> pd.DataFrame:
        self.validate_paths()
        return pd.read_csv(
            self.paths.maintenance,
            encoding="utf-8-sig",
            dtype=MAINTENANCE_DTYPES,
            nrows=nrows,
            usecols=usecols,
        )

    def read_checkins(
        self,
        *,
        nrows: int | None = None,
        usecols: list[str] | tuple[str, ...] | None = None,
    ) -> pd.DataFrame:
        self.validate_paths()
        return pd.read_csv(
            self.paths.checkins,
            encoding="utf-8-sig",
            dtype=CHECKIN_DTYPES,
            nrows=nrows,
            usecols=usecols,
        )

    def iter_maintenance(self, chunksize: int = 100_000) -> Iterator[pd.DataFrame]:
        self.validate_paths()
        yield from pd.read_csv(
            self.paths.maintenance,
            encoding="utf-8-sig",
            dtype=MAINTENANCE_DTYPES,
            chunksize=chunksize,
        )

    def iter_checkins(self, chunksize: int = 100_000) -> Iterator[pd.DataFrame]:
        self.validate_paths()
        yield from pd.read_csv(
            self.paths.checkins,
            encoding="utf-8-sig",
            dtype=CHECKIN_DTYPES,
            chunksize=chunksize,
        )
