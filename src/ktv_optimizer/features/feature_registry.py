"""Small registry so feature groups remain replaceable."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

import pandas as pd

FeatureBuilder = Callable[[pd.DataFrame], pd.DataFrame]


@dataclass
class FeatureRegistry:
    # Ánh xạ tên nhóm feature sang hàm tạo DataFrame tương ứng.
    builders: dict[str, FeatureBuilder] = field(default_factory=dict)

    def register(self, name: str, builder: FeatureBuilder) -> None:
        if name in self.builders:
            raise ValueError(f"Feature builder already registered: {name}")
        self.builders[name] = builder

    def build(self, name: str, frame: pd.DataFrame) -> pd.DataFrame:
        try:
            builder = self.builders[name]
        except KeyError as error:
            available = ", ".join(sorted(self.builders))
            raise KeyError(
                f"Unknown feature builder {name!r}. Available: {available}"
            ) from error
        return builder(frame)


def default_feature_registry() -> FeatureRegistry:
    from .job_features import build_job_features

    registry = FeatureRegistry()
    registry.register("job", build_job_features)
    return registry
