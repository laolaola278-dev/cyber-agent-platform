"""Locust User model for CAP Phase 22 CRUD validation."""

from __future__ import annotations

import os
import time

from locust import HttpUser, between, task


class CAPUser(HttpUser):
    wait_time = between(0.01, 0.05)

    def on_start(self) -> None:
        self.client.headers.update(
            {
                "X-CAP-User": os.getenv("CAP_USER", "administrator"),
                "X-CAP-Proxy-Secret": os.getenv(
                    "CAP_PROXY_SECRET", "change-me-proxy-secret"
                ),
            }
        )

    @task
    def asset_crud(self) -> None:
        suffix = f"{id(self)}-{time.time_ns()}"
        with self.client.post(
            "/assets",
            json={
                "asset_type": "HOST",
                "name": f"locust-{suffix}",
                "value": f"locust-{suffix}.example.test",
                "environment": "phase22",
                "tags": ["benchmark"],
            },
            name="POST /assets",
            catch_response=True,
        ) as created:
            if created.status_code != 201:
                created.failure(f"unexpected status {created.status_code}")
                return
            asset_id = created.json()["id"]
        self.client.get(f"/assets/{asset_id}", name="GET /assets/{id}")
        self.client.put(
            f"/assets/{asset_id}", json={"risk": "LOW"}, name="PUT /assets/{id}"
        )
        self.client.delete(f"/assets/{asset_id}", name="DELETE /assets/{id}")
