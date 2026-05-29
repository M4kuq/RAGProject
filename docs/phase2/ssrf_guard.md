# PR-35 SSRF Guard

## Purpose

The PR-35 URL ingest path fetches a single admin-supplied HTTP(S) URL. The SSRF
guard prevents that feature from reaching local, private, metadata, or otherwise
unsafe network targets.

## Allowed Schemes

Allowed:

- `http`
- `https`

Rejected:

- `file`
- `ftp`
- `gopher`
- `data`
- `javascript`
- `mailto`
- `ssh`
- `smb`
- `ldap`
- `unix`
- any URL with username or password information

## Host And IP Policy

The guard resolves the hostname before fetching and validates every resolved IP.
The same validation is repeated for each redirect target.

Rejected targets include:

- `localhost`
- `*.localhost`
- `.local`
- loopback addresses
- private IPv4 and IPv6 ranges
- link-local ranges
- multicast and unspecified addresses
- non-global/reserved addresses when private blocking is enabled
- `169.254.169.254`
- recognizable cloud metadata hostnames such as `metadata.google.internal`

## Redirects

Redirects are followed manually with a small limit. Each `Location` URL is joined
against the current URL, normalized, DNS-resolved, and revalidated before the
next request is made.

Too many redirects, missing `Location`, unsafe redirect targets, and non-2xx
final responses are rejected with safe validation errors.

## Timeout, Size, And Content Type

URL ingest enforces:

- request timeout
- maximum redirects
- maximum response bytes
- streaming read with byte limit
- allowlisted HTML/XML content types
- explicit user agent

Binary content and unsupported content types are rejected before document ingest.

## Redaction

Logs, responses, metadata, and artifacts use safe URL forms. Query strings and
fragments are removed from stored/displayed `source_url` and `final_url` values.
Authorization headers, cookies, custom headers, and authenticated URLs are not
supported in PR-35.

## Limitations

PR-35 does not implement connect-level DNS pinning or a crawler. The guard is
designed for one URL fetch with deterministic pre-request and redirect-time
validation. Recursive web ingest and deeper SSRF hardening are deferred to
future PRs if needed.
