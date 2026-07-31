---
description: Scan the working tree and staged changes for leaked secrets before committing
agent: build
---

Scan this repository for secrets before a commit. Read-only: report, do not
fix anything without asking.

1. **Staged + working tree**: search tracked and staged files (skip
   `.git/`, binaries, and anything in `.gitignore`) for credential patterns:
   - key-like assignments: `(api[_-]?key|secret|password|passwd|token)\s*[:=]`
     with a non-placeholder value;
   - known token shapes: `ghp_[A-Za-z0-9]{36}`, `github_pat_`, `sk-[A-Za-z0-9]`,
     `AKIA[0-9A-Z]{16}`, `hvs.` (Vault), `eyJ` (JWT), private key headers
     (`-----BEGIN`);
   - suspicious files: `.env`, `*.pem`, `id_rsa*`, `.Renviron` with values.
2. **Git config**: check `git remote -v` for tokens embedded in remote URLs
   (`https://<token>@github.com/...`) — a common leak on throwaway pods.
3. **Recent history**: `git log -p -5 | grep -iE '(api[_-]?key|secret|token|password)\s*[:=]'`
   for anything that already slipped in.
4. **Report** a table: file/location → pattern matched → severity
   (confirmed secret / suspicious / false positive with reason).
5. If a real secret is found: point to the remediation procedure in the
   `git-workflow-ds` skill (revoke/rotate FIRST, then purge history) and to
   `vault-secrets-onyxia` for proper storage. Do not print the secret value
   back in full — show only enough to locate it.
