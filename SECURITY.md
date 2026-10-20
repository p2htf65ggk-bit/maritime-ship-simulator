# Security and sensitive data

This is a simulator/test tool, not safety-critical navigation equipment.
Treat UDP/TCP/serial endpoints as trusted lab interfaces, not authenticated
Internet-facing services. Bind to loopback when only local access is required.

Do not publish provider keys, configuration exports, startup logs, network
captures or chart packages containing information you do not have permission
to share. Provider credentials entered at runtime can be present in the local
configuration and in configuration exports; these are not encrypted secrets
vaults. Restrict access to those files.

Use GitHub private vulnerability reporting if it is enabled for the public
repository. If no private reporting channel is available, open a minimal issue
asking for a private contact channel, without disclosing the vulnerability or
exploit details. Include the version and operating system when a private channel
is established. Never attach real keys or unredacted captures to a public issue.
No guaranteed response time or security-maintenance period is promised.
