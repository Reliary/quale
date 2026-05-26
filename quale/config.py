"""Config loader — reads .quale.yml from repo root."""

from __future__ import annotations

import os
import yaml


def load_config(path: str) -> dict:
    """Load .quale.yml from path if it exists, else return defaults."""
    config_path = os.path.join(os.path.abspath(path), ".quale.yml")
    if not os.path.isfile(config_path):
        config_path = os.path.join(os.path.abspath(path), ".quale.yaml")
    if not os.path.isfile(config_path):
        return dict(DEFAULT_CONFIG)

    try:
        import yaml
        with open(config_path, encoding="utf-8") as f:
            user_config = yaml.safe_load(f) or {}
        merged = dict(DEFAULT_CONFIG)
        for section, values in user_config.items():
            if section in merged and isinstance(values, dict):
                merged[section].update(values)
            else:
                merged[section] = values
        return merged
    except ImportError:
        # yaml not installed — skip config
        return dict(DEFAULT_CONFIG)
    except Exception:
        return dict(DEFAULT_CONFIG)
