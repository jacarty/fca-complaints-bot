# Security Policy

## Reporting a Vulnerability

If you discover a security vulnerability in this project, please report it responsibly.

**Do not open a public issue.**

Instead, please use [GitHub's private vulnerability reporting](../../security/advisories/new) to submit a report. Include:

- A description of the vulnerability
- Steps to reproduce
- Potential impact
- Suggested fix (if you have one)

You should receive a response within a few days. The maintainer will work with you to understand the issue and coordinate a fix before any public disclosure.

> **Note:** Private vulnerability reporting must be enabled on the repository for the link above to work. Enable it under **Settings → Code security → Private vulnerability reporting**.

## Supported Versions

Only the latest version on the `main` branch is actively maintained.

## Security Best Practices

This repository includes several security measures:

- **`.claudeignore`** — prevents AI tools from reading sensitive files
- **`.gitignore`** — excludes secrets, credentials, and environment files from version control
- **Pre-commit hooks** — scan for secrets and credentials before they enter git history
- **Dependency management** — keep dependencies updated and audit regularly

## Disclosure Policy

Once a vulnerability is confirmed and fixed, a security advisory will be published with appropriate details.
