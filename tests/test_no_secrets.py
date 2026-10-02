"""Garde-fou : aucune vraie valeur de secret dans la documentation versionnee.

Cas reel : le bloc d'exemple `bench-secret.env` d'argo/README.md a ete rempli localement avec de vrais
identifiants (mot de passe MLflow, cle d'API, jeton Claude) pendant qu'on suivait les instructions, puis
a failli etre commite et pousse. Les exemples de la doc doivent rester des gabarits."""
import re
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SENSITIVE = re.compile(r"^\s*(?:export\s+)?([A-Z][A-Z0-9_]*(?:PASSWORD|API_KEY|TOKEN|SECRET)[A-Z0-9_]*)=(\S.*)$")
PLACEHOLDER = re.compile(r"^(<.*>|\.\.\.|changeme|xxx+|\$\{?[A-Za-z_]+\}?|\"\$\{?[A-Za-z_]+\}?\"|'<.*>'|\"<.*>\"|.*<[^>]+>.*)$", re.I)
TOKEN_SHAPES = [re.compile(p) for p in (r"sk-ant-[a-z]{3}\d{2}-[A-Za-z0-9_-]{20,}", r"ghp_[A-Za-z0-9]{30,}",
                                         r"github_pat_[A-Za-z0-9_]{20,}", r"AKIA(?![0-9A-Z]*EXAMPLE)[0-9A-Z]{16}",
                                         r"-----BEGIN [A-Z ]*PRIVATE KEY-----")]
DOC_GLOBS = ("README.md", "argo/**/*", "docs/**/*", ".env.example", "configs/**/*.md")


def _value(m: re.Match) -> str:
    """Valeur sans commentaire de fin de ligne (`KEY=...   # ou dans .env`)."""
    return re.split(r"\s+#", m.group(2))[0].strip()


def _doc_files():
    out = []
    for g in DOC_GLOBS:
        out += [p for p in REPO.glob(g) if p.is_file() and p.suffix in ("", ".md", ".yaml", ".yml", ".txt", ".example")]
    return sorted(set(out))


def test_documentation_examples_hold_placeholders_not_real_secrets():
    bad = []
    for f in _doc_files():
        for n, line in enumerate(f.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            m = SENSITIVE.match(line)
            if m and not PLACEHOLDER.match(_value(m)):
                bad.append(f"{f.relative_to(REPO)}:{n} {m.group(1)}=<valeur non masquee>")
    assert not bad, "valeur(s) de secret non masquee(s) dans la doc :\n" + "\n".join(bad)


def test_no_known_token_shape_in_tracked_text_files():
    files = subprocess.run(["git", "ls-files"], cwd=REPO, capture_output=True, text=True).stdout.split()
    # ces fichiers contiennent a dessein des cles factices ou des motifs de detection (valeurs d'exemple AWS...)
    allowed = {"bench/grading.py", "bench/opencode_driver.py", "tests/test_grading.py", "tests/test_candidate_tasks.py",
               "tests/test_no_secrets.py", "tests/test_tasks.py", "tests/test_outcome_tasks.py",
               "tests/test_t02_outcome_grading.py"}
    bad = []
    for name in files:
        p = REPO / name
        if name in allowed or not p.is_file() or p.stat().st_size > 2_000_000 or name.startswith(("configs/layers/", "runs/")):
            continue
        try:
            text = p.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for shape in TOKEN_SHAPES:
            if shape.search(text):
                bad.append(f"{name} : motif {shape.pattern[:30]}...")
    assert not bad, "motif(s) de secret connu dans des fichiers versionnes :\n" + "\n".join(bad)


def test_the_guard_itself_catches_a_real_looking_value():
    line = "MLFLOW_TRACKING_PASSWORD=abcd1234efgh5678"
    m = SENSITIVE.match(line)
    assert m and not PLACEHOLDER.match(m.group(2))
    for ok in ("export CLAUDE_CODE_OAUTH_TOKEN=...       # ou le mettre dans .env",
               "OPENCODE_ONYXIA_API_KEY=<clé>", "OPENCODE_ONYXIA_API_KEY=changeme", "MLFLOW_TRACKING_PASSWORD=...",
               "API_TOKEN=$VAULT_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN=<jeton de `claude setup-token`>"):
        m = SENSITIVE.match(ok)
        assert m and PLACEHOLDER.match(_value(m)), ok
    m = SENSITIVE.match("export CLAUDE_CODE_OAUTH_TOKEN=sk-ant-oat01-abcdef   # vrai jeton")
    assert m and not PLACEHOLDER.match(_value(m))                 # un commentaire ne masque pas une vraie valeur
