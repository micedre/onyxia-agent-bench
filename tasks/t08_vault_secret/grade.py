"""Notation offline de T08 : le CODE lit Vault via les variables d'env sanctionnees et
n'ecrit jamais la cle en clair (pas d'appel reel a un serveur Vault dans cette v0)."""
from bench.grading import (
    code_text, env_vars_used, file_exists, no_hardcoded_secrets, references_vault_api,
)


def grade(ctx):
    checks = []
    checks.append(file_exists(ctx.workspace, ["*.py"], name="script_present"))
    text = code_text(ctx.workspace, ["*.py"])
    checks.append(env_vars_used(text, ["VAULT_ADDR", "VAULT_TOKEN"], name="reads_vault_env"))
    checks.append(references_vault_api(text))
    checks.append(no_hardcoded_secrets(text))
    return checks
