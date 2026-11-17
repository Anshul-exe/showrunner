# vendor-mock

A stand-in for a third-party recommendations vendor: a Lambda function URL with no
auth (public on purpose, like a vendor API), outside the VPC, so calls to it really go
through the NAT instance.

Chaos comes from each customer's AppConfig `chaos` profile (`vendor_latency_ms`,
`error_rate`), keyed by the `x-customer` header, so `./sr demo s1 start` slows the
vendor for acme-tv only. Latency is capped at 5 s.

**Why a mock:** scenario S1 (P9: "the CDN/vendor is slow, not us") needs a dependency
we can break on demand, without touching a real third party.
