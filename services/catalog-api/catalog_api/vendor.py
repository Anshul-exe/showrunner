"""Client for the external recommendations vendor. Public HTTPS, so from the private
subnets the call leaves through the NAT instance.

The timeout is the important part: VENDOR_TIMEOUT_S (2.5 s) < the Lambda timeout (6 s),
so a slow vendor can make requests slow but can't hold every function instance (and
the account's concurrency) until the Lambda timeout.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request


class VendorError(Exception):
    pass


class VendorClient:
    def __init__(self, base_url: str, customer: str, timeout_s: float) -> None:
        self.base_url = base_url.rstrip("/")
        self.customer = customer
        self.timeout_s = timeout_s

    def recommendations(self, title_id: str, base_url: str | None = None) -> list[str]:
        """`base_url` overrides the configured URL (the runtime flag `vendor_url`)."""
        base = (base_url or self.base_url).rstrip("/")
        url = f"{base}/recs?{urllib.parse.urlencode({'title': title_id})}"
        req = urllib.request.Request(
            url, headers={"x-customer": self.customer, "accept": "application/json"}
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:  # noqa: S310 (https URL from config)
                body = json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            raise VendorError(f"vendor HTTP {exc.code}") from None
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise VendorError(f"vendor unreachable: {type(exc).__name__}") from None
        except ValueError:
            raise VendorError("vendor returned invalid JSON") from None
        recs = body.get("recs")
        if not isinstance(recs, list):
            raise VendorError("vendor response has no recs")
        return [str(r) for r in recs][:10]
