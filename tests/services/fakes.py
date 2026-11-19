"""Small fakes shared by the service tests."""

import time


class FakeValkey:
    """Enough of the valkey client for catalog-api: get/set with ex and nx."""

    def __init__(self, fail: bool = False, delay_s: float = 0.0):
        self.data: dict[str, str] = {}
        self.fail = fail
        self.delay_s = delay_s
        self.closed = False

    def close(self):
        self.closed = True

    def ping(self):
        if self.fail:
            raise ConnectionError("valkey down")
        return True

    def get(self, key):
        if self.fail:
            raise ConnectionError("valkey down")
        time.sleep(self.delay_s)
        return self.data.get(key)

    def set(self, key, value, ex=None, nx=False):
        if self.fail:
            raise ConnectionError("valkey down")
        if nx and key in self.data:
            return None
        self.data[key] = value
        return True


class FakeVendor:
    def __init__(self, recs=None, error=None, delay_s: float = 0.0):
        self.recs = recs or ["t02", "t03"]
        self.error = error
        self.delay_s = delay_s
        self.calls = 0
        self.urls: list[str | None] = []

    def recommendations(self, title_id, base_url=None):
        self.calls += 1
        self.urls.append(base_url)
        time.sleep(self.delay_s)
        if self.error:
            raise self.error
        return self.recs
