"""Backend entrypoint: configure logging before Uvicorn starts."""

from __future__ import annotations

import logging.config

import uvicorn
import yaml

from app.config import get_settings
from app.main import app

# Configure logging as early as possible, before Uvicorn touches the root
# logger. This ensures our structured format (trace_id / task_id) is used
# for every backend log line, not just application logs.
settings = get_settings()
config_path = settings.config_directory / "logging.yaml"
if config_path.exists():
    with config_path.open(encoding="utf-8") as config_file:
        logging_config = yaml.safe_load(config_file)
    logging.config.dictConfig(logging_config)

if __name__ == "__main__":
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8000,
        proxy_headers=True,
        forwarded_allow_ips="*",
        log_level="warning",
        access_log=False,
    )
