# Related projects and why ThrottleDeck exists

Checked on 2026-09-14. This is a focused comparison, not a claim that every API
gateway or rate limiter has been audited.

## Closest small references

- [Switchboard](https://github.com/jchristn/Switchboard) is a small MIT-licensed
  reverse proxy with a browser UI and bounded statistics. ThrottleDeck borrows
  the idea that operational history should be useful but explicitly bounded.
- [nim-proxy](https://github.com/miztertea/nim-proxy) is a small MIT-licensed,
  rate-limit-aware OpenAI-compatible proxy. It keeps limiter state separate from
  presentation and avoids storing request bodies; both are good defaults here.
- [SentinelFlow](https://gitlab.com/echeparesmanuel36/sentinelflow) presents
  itself as a dashboard for API and security events. The inspected checkout had
  unresolved merge markers, so it was not used as an implementation reference.

## Larger gateways

[Kong](https://github.com/Kong/kong), [Apache APISIX](https://github.com/apache/apisix),
[Tyk](https://github.com/TykTechnologies/tyk), and
[Envoy](https://github.com/envoyproxy/envoy) already provide mature gateway and
rate-limiting systems. They are the better choice for multi-host, distributed,
or internet-facing infrastructure.

ThrottleDeck targets a narrower local problem: several desktop applications
share one third-party API budget and need a low-overhead read broker, per-caller
attribution, an operator control window, and persistent local stop policy. It is
not trying to replace a production edge gateway.

## Design choices borrowed or rejected

- Keep metric history bounded and aggregate labels by configured caller rather
  than recording raw request bodies.
- Show capacity separately from traffic; an unused bucket is not proof that an
  application is inactive.
- Preserve limiter state when access is stopped and resumed.
- Keep caller identity revocable and configuration-owned.
- Do not add distributed storage, plugin execution, or raw traffic inspection
  to a loopback-only desktop tool.
