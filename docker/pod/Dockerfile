# Image des cellules du benchmark (`bench run --isolation pod`) : l'image Onyxia r-python-julia
# (variante vscode, qui embarque opencode) + Claude Code + les bibliotheques que supposent les
# taches. Construite et publiee par .github/workflows/pod-image.yml sur
# ghcr.io/micedre/onyxia-agent-bench-pod.
#
#   docker build -f docker/pod/Dockerfile -t onyxia-agent-bench-pod docker/pod
#
# Tout le build est fait par les scripts de ce dossier (comme dans InseeFrLab/images-datascience).
# On garde le CMD/EXPOSE de l'image de base : elle reste utilisable comme service VS Code Onyxia ;
# les pods du benchmark remplacent la commande par `sleep`.
ARG BASE_IMAGE=inseefrlab/onyxia-vscode-r-python-julia:r4.6.1-py3.13.15-2026.09.07
FROM ${BASE_IMAGE}

# Repete apres FROM : un ARG d'avant FROM n'est pas visible dans les etapes suivantes.
ARG BASE_IMAGE
ARG CLAUDE_VERSION=2.1.286

LABEL maintainer="micedre" \
      org.opencontainers.image.description="onyxia-agent-bench cell image: Onyxia r-python-julia + opencode + Claude Code + task libraries" \
      org.opencontainers.image.base.name="${BASE_IMAGE}"

USER root

COPY --chmod=0755 install-claude.sh write-manifest.sh /opt/
COPY requirements.txt /tmp/requirements.txt

# - claude : binaire natif, version epinglee, sha256 verifie (/usr/local/bin/claude)
# - python : bibliotheques des taches, par-dessus celles de l'image de base
# - R      : testthat et targets (t05, t26) ; renv, devtools, lintr existent deja
RUN /opt/install-claude.sh "${CLAUDE_VERSION}" && \
    uv pip install --system --no-cache -r /tmp/requirements.txt && \
    install2.r --ncpus -1 --error testthat targets styler && \
    BASE_IMAGE_REF="${BASE_IMAGE}" /opt/write-manifest.sh && \
    # Fix user permissions
    /opt/fix-user-permissions.sh && \
    # Clean
    /opt/clean.sh

# La version installee est epinglee : elle ne doit pas se mettre a jour pendant un run.
ENV DISABLE_AUTOUPDATER=1

USER 1000
