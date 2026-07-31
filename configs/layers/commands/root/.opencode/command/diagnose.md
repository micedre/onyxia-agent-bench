---
description: Run the Onyxia service/job diagnostic runbook (red service, 403, failed workflow, OOM…)
agent: build
---

Something on the platform is misbehaving. Load the `onyxia-diagnostics` skill
and work through its runbook now, using read-only commands only
(`kubectl get/describe/logs/top`, `argo list/get/logs`, `aws s3 ls`).

User-described symptom (may be empty): $ARGUMENTS

- If no symptom was given, start from `kubectl get pods` and the golden
  shortcuts table, and ask the user only what you cannot observe yourself.
- End with the skill's report format: symptom → evidence (exact command output
  line) → cause → proposed remediation, flagging anything destructive
  (relaunching a service loses non-persisted files) before suggesting it.
