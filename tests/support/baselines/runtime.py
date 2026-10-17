"""Trusted evaluation worker selection; uses the shared bounded transport."""

from app.infrastructure.offline_worker import execute_worker


async def execute_python(operation, inputs, timeout=60):
    return await execute_worker(operation, inputs, "tests.support.baselines.worker", timeout)
