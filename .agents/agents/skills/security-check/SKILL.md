---

name: security-check

description: Use when reviewing code or configuration for secrets, authentication, authorization, injection risks, unsafe file handling, logging, dependency risks, or destructive operations.

---



# Security Check Skill



Use this skill for security-sensitive reviews.



## Review priorities



1. Secrets and credentials

2. Authentication

3. Authorization

4. Input validation

5. Injection risks

6. File handling

7. Logging and error messages

8. Dependency and configuration risk

9. Destructive operations



## Rules



- Never expose, print, log, or commit secrets.

- Treat API keys, tokens, passwords, cookies, private keys, and `.env` values as secrets.

- Check authorization before accessing user-owned or tenant-owned data.

- Validate input at trust boundaries.

- Avoid shell execution with unsanitized input.

- Avoid raw SQL with unsanitized input.

- Prevent path traversal in file handling.

- Do not log sensitive personal data.

- Do not leak internal exception details to clients.

- Require explicit confirmation for destructive operations.



## Output format



Classify findings as:



- Critical

- High

- Medium

- Low



For each issue, include:



- Location

- Risk

- Exploit or failure scenario

- Recommended fix



## Checklist



Check for:



- Hardcoded secrets

- Secrets in logs

- Missing authentication

- Missing authorization

- Unsafe file upload handling

- SQL injection

- Shell injection

- Path traversal

- SSRF risk

- XSS risk

- CSRF risk

- Insecure CORS

- Overly broad permissions

- Dangerous defaults

