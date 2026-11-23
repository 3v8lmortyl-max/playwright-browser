# Editorial source notes — Microsoft Agent Framework PyPI compromise

Research cutoff: 23 November 2026.

## Core facts used in narration

- The affected PyPI package is `agent-framework`; malicious versions are 1.26.1 and 1.26.2.
- Both releases appeared on 17 November 2026; 1.26.1 at 20:35 UTC and 1.26.2 at 21:08 UTC.
- The malicious wheels used a `.pth` startup hook. Python processes executable import lines in `.pth` files during interpreter startup, so importing the framework itself was not required.
- Analysis reports collection of environment variables, credential files and process environment data; Azure managed-identity token requests; the 1.26.2 release additionally targeted GitHub Actions OIDC token paths.
- GitHub Advisory Database identifies 1.26.1 and 1.26.2 as malicious and 1.26.0 as the last clean release.
- Public reporting has not attributed the incident to a known threat group or established the number of affected environments.

## Sources

1. GitHub Advisory Database: https://github.com/advisories/GHSA-764m-3ppc-5m73
2. JFrog Security Research: https://research.jfrog.com/post/agent-framework-hijack/
3. Wiz Research: https://www.wiz.io/blog/malicious-version-of-microsofts-agent-framework-published-to-pypi
4. Elastic Security Labs: https://www.elastic.co/security-labs/threat-command/we-caught-the-agent-framework-pypi-compromise-six-minutes-after-it-published
5. Python documentation, site module and .pth files: https://docs.python.org/3/library/site.html#pth-files

## Editorial safeguards

- The four generated explainer cards are editorial diagrams, not screenshots or official footage.
- Source-page screenshots are evidence captures, not claims that the pages contain official video.
- Do not claim that all collected credentials were successfully abused; the public reporting does not establish that.
- Do not attribute the attack to a named actor.
- Incident response guidance: isolate affected environments, rebuild them, rotate reachable credentials and review cloud/CI activity.
