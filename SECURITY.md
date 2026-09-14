# Security policy

ThrottleDeck is a loopback-only local service. Please do not publish a suspected
credential leak, authentication bypass, unsafe control-state transition, or
network exposure as a public issue.

Use GitHub's **Report a vulnerability** flow for security reports. Include the
affected commit, a minimal reproduction, and the impact. Do not include live API
keys, signed headers, account data, or real-money order details.

The project intentionally stores no venue credentials. Caller-owned
authentication is forwarded in memory and request headers are never logged.
