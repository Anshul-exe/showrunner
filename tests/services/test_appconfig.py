import io
import json

from srkit.appconfig import AppConfigProfile


class FakeData:
    def __init__(self, bodies):
        self.bodies = list(bodies)
        self.sessions = 0
        self.polls = 0

    def start_configuration_session(self, **kw):
        self.sessions += 1
        assert kw["RequiredMinimumPollIntervalInSeconds"] == 15
        return {"InitialConfigurationToken": "t0"}

    def get_latest_configuration(self, ConfigurationToken):
        self.polls += 1
        body = self.bodies.pop(0)
        if isinstance(body, Exception):
            raise body
        return {"NextPollConfigurationToken": f"t{self.polls}", "Configuration": io.BytesIO(body)}


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def make(bodies):
    clock = Clock()
    data = FakeData(bodies)
    prof = AppConfigProfile("app", "dev", "flags", {"vendor_fallback": False, "ttl": 1}, client=data, clock=clock)
    return prof, data, clock


def test_polls_at_most_every_interval_and_merges_defaults():
    prof, data, clock = make([json.dumps({"vendor_fallback": True}).encode(), b""])
    assert prof.get() == {"vendor_fallback": True, "ttl": 1}
    clock.t = 10
    prof.get()
    assert data.polls == 1  # cached inside the 15 s interval
    clock.t = 16
    assert prof.get()["vendor_fallback"] is True  # empty body = unchanged
    assert data.polls == 2 and data.sessions == 1


def test_error_keeps_last_good_config_and_restarts_session():
    prof, data, clock = make([json.dumps({"vendor_fallback": True}).encode(), RuntimeError("down"), b"{}"])
    prof.get()
    clock.t = 20
    assert prof.get()["vendor_fallback"] is True
    assert prof.last_error == "RuntimeError"
    clock.t = 40
    prof.get()
    assert data.sessions == 2


def test_unreachable_from_the_start_serves_defaults():
    prof, _, _ = make([RuntimeError("no route")])
    assert prof.get() == {"vendor_fallback": False, "ttl": 1}
