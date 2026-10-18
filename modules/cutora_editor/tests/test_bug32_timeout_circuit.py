# -*- coding: utf-8 -*-
"""BUG-32: second timeout continues; three in a row trip the circuit; load budget."""
from __future__ import annotations

import pytest

from cutora.asr.clip_worker import (
    ASR_LOAD_BUDGET_SEC,
    AsrCircuitOpen,
    ClipWatchdogFailed,
    TimeoutCircuit,
    transcribe_with_downgrade,
)
from cutora.asr.tier import WatchdogTimeout, watchdog_timeout_sec


class _FakeClient:
    def __init__(self, fail_times=99):
        self.n = 0
        self.fail_times = fail_times
        self.needs_load_budget = False
        self.last_timeout = None
        self.timeouts = []
        self.tier_id = "S"

    def transcribe(self, path, dur, language, timeout_sec):
        self.n += 1
        self.last_timeout = timeout_sec
        self.timeouts.append(timeout_sec)
        if self.n <= self.fail_times:
            raise WatchdogTimeout("hang")
        return [("ok", 0.0, 0.1)], False, 1

    def drop_and_restart(self, language="en"):
        self.needs_load_budget = True
        return "E"

    def start(self, language="en"):
        self.needs_load_budget = True


def test_second_timeout_is_clip_failure_not_abort():
    client = _FakeClient(fail_times=2)
    circuit = TimeoutCircuit()
    with pytest.raises(ClipWatchdogFailed):
        transcribe_with_downgrade(
            client, "a.wav", 8.0, "en", log=lambda s: None, circuit=circuit)
    assert circuit.streak == 1
    assert circuit.failed_paths == ["a.wav"]


def test_circuit_trips_on_three_consecutive_clip_timeouts():
    circuit = TimeoutCircuit()
    with pytest.raises(AsrCircuitOpen) as ei:
        for name in ("a.wav", "b.wav", "c.wav"):
            client = _FakeClient(fail_times=2)
            try:
                transcribe_with_downgrade(
                    client, name, 4.0, "en", log=lambda s: None, circuit=circuit)
            except ClipWatchdogFailed:
                continue
    assert "UserData/logs/asr/" in str(ei.value)
    assert circuit.streak >= 3
    assert circuit.failed_paths == ["a.wav", "b.wav", "c.wav"]


def test_restart_first_clip_gets_load_budget():
    client = _FakeClient(fail_times=1)
    circuit = TimeoutCircuit()
    words, incomplete, tier = transcribe_with_downgrade(
        client, "a.wav", 8.0, "en", log=lambda s: None, circuit=circuit)
    assert words == [("ok", 0.0, 0.1)]
    assert client.last_timeout == watchdog_timeout_sec(8.0) + ASR_LOAD_BUDGET_SEC
    assert circuit.streak == 0


def test_fresh_worker_first_clip_gets_load_budget():
    client = _FakeClient(fail_times=0)
    client.needs_load_budget = True
    transcribe_with_downgrade(
        client, "a.wav", 4.0, "en", log=lambda s: None, circuit=TimeoutCircuit())
    assert client.last_timeout == watchdog_timeout_sec(4.0) + ASR_LOAD_BUDGET_SEC
