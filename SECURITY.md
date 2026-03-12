# Security Policy

## Supported Versions

This project is early-stage and maintained as a rolling release.

- `main`: supported
- Latest tagged release: supported
- Older tags/commits: best effort only

## Reporting A Vulnerability

Please do not open public issues for security problems.

Preferred process:

1. Use GitHub's private vulnerability reporting ("Report a vulnerability") for this repository.
2. Include clear reproduction steps, affected files/endpoints, and impact.
3. If possible, include a minimal proof of concept and suggested mitigation.

If private vulnerability reporting is not available, contact the repository maintainer privately via the contact method listed on their GitHub profile.

## Response Targets

- Initial triage response: within 7 days
- Follow-up status update: within 14 days
- Fix timeline: depends on severity and maintainer availability

## Scope Notes

Typical in-scope issues include:

- Authentication/authorization bypass
- Token/secret leakage paths
- Host/origin validation bypass
- Unsafe defaults that expose local data unintentionally

Out of scope:

- Vulnerabilities only present in unsupported, heavily modified forks
- Social engineering or phishing unrelated to this codebase
