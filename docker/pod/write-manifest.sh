#!/bin/bash
# Ecrit /opt/bench-image-manifest.txt : ce que l'image contient reellement (versions des outils,
# paquets Python et R). Un resultat du benchmark peut ainsi etre rattache a l'environnement exact
# dans lequel l'agent a travaille : `docker run --rm <image> cat /opt/bench-image-manifest.txt`.
# Chaque commande est tolerante : un outil absent s'ecrit "absent", il ne casse pas le build.
set -uo pipefail

OUT="${1:-/opt/bench-image-manifest.txt}"

ver() { "$@" 2>&1 | head -n 1 || true; }
tool() { printf '%-10s %s\n' "$1" "$(command -v "$2" >/dev/null 2>&1 && ver "$2" "${@:3}" || echo absent)"; }

{
    echo "# onyxia-agent-bench pod image - $(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo "base_image ${BASE_IMAGE_REF:-unknown}"
    echo
    echo "## outils"
    tool claude claude --version
    tool opencode opencode --version
    tool python python --version
    tool uv uv --version
    tool R R --version
    tool quarto quarto --version
    tool git git --version
    tool jq jq --version
    echo
    echo "## paquets python (pip freeze)"
    python -m pip freeze 2>/dev/null || uv pip freeze --system 2>/dev/null || echo absent
    echo
    echo "## paquets R"
    Rscript -e 'ip <- installed.packages()[, c("Package", "Version")]; ip <- ip[order(ip[, 1]), ]; writeLines(paste(ip[, 1], ip[, 2]))' 2>/dev/null || echo absent
} >"${OUT}"

echo "manifeste ecrit : ${OUT} ($(wc -l <"${OUT}") lignes)"
