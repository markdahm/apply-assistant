"""The letter worker's idle behaviour.

Every poll costs one Vercel Blob ``list()`` — a metered operation — whether or not
there is work. A flat 20s loop left running is ~4,300 calls a day finding an empty
queue, which is what exhausted the free tier on 27 August 2026. So the loop backs
off when idle and stops entirely after a while.

Both of those are invisible in normal use: nothing fails when they regress, and the
only place it shows up is the bill. That is exactly the kind of behaviour that needs
a test asserting the COST, not just the result — the letters served are identical
whether the loop polls 29 times or 4,300.

The clock and the queue are both stubbed, so this runs in microseconds and spends
nothing.
"""

import pytest

from apply_assistant import letter_worker


class FakeClock:
    """A clock that only moves when the worker sleeps."""

    def __init__(self):
        self.now = 1_000_000.0
        self.sleeps = []

    def time(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


@pytest.fixture
def clock(monkeypatch):
    c = FakeClock()
    monkeypatch.setattr(letter_worker, "time", c)
    return c


def _quiet(monkeypatch, serve_on=(), cap=300):
    """Stub process_once. Serves a letter on the given (1-based) poll numbers.

    The cap matters as much as the stub. With a fake clock, a worker that has lost
    its auto-exit does not fail these tests — it spins forever and the suite HANGS,
    which is worse than a false pass because it looks like an infrastructure problem
    rather than a regression. Tripping the cap raises KeyboardInterrupt, which the
    loop already handles as a clean stop, and every test asserts the cap was NOT
    reached. So "it never stopped" fails in milliseconds and says so.
    """
    calls = {"n": 0, "capped": False}

    def fake(verbose=True):
        calls["n"] += 1
        if calls["n"] > cap:
            calls["capped"] = True
            raise KeyboardInterrupt
        served = 1 if calls["n"] in serve_on else 0
        return {"served": served, "failed": 0}

    monkeypatch.setattr(letter_worker, "process_once", fake)
    return calls


def test_it_stops_after_five_idle_minutes_by_default(clock, monkeypatch):
    calls = _quiet(monkeypatch)

    letter_worker.main(watch=True)          # defaults, deliberately not spelled out

    assert not calls["capped"], "the worker never stopped on its own"
    assert sum(clock.sleeps) >= 5 * 60, "gave up before five minutes had passed"
    assert sum(clock.sleeps) < 7 * 60, "ran well past five minutes before stopping"
    # The point of the whole exercise: a flat 20s loop would have spent 15x this.
    assert calls["n"] < 20, f"{calls['n']} polls to wait out five idle minutes"


def test_a_served_letter_resets_the_idle_clock(clock, monkeypatch):
    # Work arriving on the third poll must buy another full idle window, or the
    # worker walks out in the middle of a review session.
    idle_only = _quiet(monkeypatch)
    letter_worker.main(watch=True)
    baseline = idle_only["n"]

    clock.__init__()
    with_work = _quiet(monkeypatch, serve_on=(3,))
    letter_worker.main(watch=True)

    assert not with_work["capped"], "the worker never stopped on its own"
    assert with_work["n"] > baseline, "serving a letter did not extend the run"


def test_it_polls_fast_while_busy_and_slowly_when_not(clock, monkeypatch):
    calls = _quiet(monkeypatch, serve_on=(1,))
    letter_worker.main(watch=True, interval=20, idle_interval=90)

    assert not calls["capped"], "the worker never stopped on its own"

    assert 20 in clock.sleeps, "never used the fast interval, so a click waits 90s"
    assert 90 in clock.sleeps, "never backed off, so idle polling stays expensive"
    # Fast polling is a window after work, not the steady state.
    assert clock.sleeps.count(90) >= 1
    assert clock.sleeps.index(90) > clock.sleeps.index(20), "backed off before working"


def test_zero_means_run_forever(clock, monkeypatch):
    # The escape hatch for a long review session. Stopping early here would be the
    # regression that matters most, because the flag exists to prevent it.
    stop = RuntimeError("far enough")
    calls = {"n": 0}

    def fake(verbose=True):
        calls["n"] += 1
        if calls["n"] > 200:
            raise stop
        return {"served": 0, "failed": 0}

    monkeypatch.setattr(letter_worker, "process_once", fake)

    # The loop swallows exceptions from process_once on purpose, so it will keep
    # going; that it reached 200 idle polls at all is the assertion.
    def stop_sleeping(seconds):
        clock.now += seconds
        if calls["n"] > 200:
            raise KeyboardInterrupt
    monkeypatch.setattr(letter_worker.time, "sleep", stop_sleeping)

    letter_worker.main(watch=True, max_idle_minutes=0)

    assert calls["n"] > 200, "stopped despite max_idle_minutes=0"
    assert clock.now - 1_000_000.0 > 5 * 60, "did not even outlast the default timeout"


def test_a_single_pass_never_loops(monkeypatch):
    calls = _quiet(monkeypatch)
    letter_worker.main(watch=False)
    assert calls["n"] == 1, "a one-shot run polled more than once"
