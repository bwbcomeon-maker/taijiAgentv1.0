"""Regression coverage for gateway cron ticker runtime heartbeat."""

from __future__ import annotations

import threading

from gateway import run as gateway_run


def test_cron_ticker_refreshes_runtime_status_after_each_tick(monkeypatch):
    from cron import scheduler
    from gateway import status as gateway_status

    stop_event = threading.Event()
    ticks: list[dict] = []
    writes: list[dict] = []

    def fake_tick(**kwargs):
        ticks.append(kwargs)
        stop_event.set()

    monkeypatch.setattr(scheduler, "tick", fake_tick)
    monkeypatch.setattr(gateway_status, "write_runtime_status", lambda **kwargs: writes.append(kwargs))

    gateway_run._start_cron_ticker(stop_event, adapters=["adapter"], loop="loop", interval=0.001)

    assert ticks == [{"verbose": False, "adapters": ["adapter"], "loop": "loop"}]
    assert writes == [{"gateway_state": "running"}]


def test_cron_ticker_refreshes_runtime_status_even_when_tick_raises(monkeypatch):
    from cron import scheduler
    from gateway import status as gateway_status

    stop_event = threading.Event()
    writes: list[dict] = []

    def fake_tick(**kwargs):
        stop_event.set()
        raise RuntimeError("scheduler failed")

    monkeypatch.setattr(scheduler, "tick", fake_tick)
    monkeypatch.setattr(gateway_status, "write_runtime_status", lambda **kwargs: writes.append(kwargs))

    gateway_run._start_cron_ticker(stop_event, interval=0.001)

    assert writes == [{"gateway_state": "running"}]
