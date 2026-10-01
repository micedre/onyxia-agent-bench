#!/bin/bash
# Installe le binaire natif de Claude Code (linux-x64) dans ${INSTALL_DIR:-/usr/local/bin}.
# Meme principe que `install-opencode.sh` de InseeFrLab/images-datascience : telechargement d'une
# version DONNEE puis verification du sha256 publie dans le manifeste de la release - sans passer
# par l'installeur `curl | bash` (qui ecrit dans $HOME, modifie le shell et met a jour la version).
#
#   install-claude.sh <X.Y.Z | stable | latest>
#
# `stable`/`latest` sont resolus en un numero de version, qui est alors affiche : une image doit
# toujours savoir quelle version elle embarque. DOWNLOAD_BASE_URL permet un miroir interne.
set -euo pipefail

VERSION="${1:?usage: install-claude.sh <X.Y.Z|stable|latest>}"
BASE="${DOWNLOAD_BASE_URL:-https://downloads.claude.ai/claude-code-releases}"
PLATFORM="linux-x64"
INSTALL_DIR="${INSTALL_DIR:-/usr/local/bin}"

if [[ "$VERSION" == "stable" || "$VERSION" == "latest" ]]; then
    VERSION="$(curl -fsSL "${BASE}/${VERSION}")"
fi
if [[ ! "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    echo "version de Claude Code invalide : '${VERSION}'" >&2
    exit 1
fi

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "${TMP_DIR}"' EXIT

MANIFEST="$(curl -fsSL "${BASE}/${VERSION}/manifest.json")"
SHA256="$(jq -er --arg p "${PLATFORM}" '.platforms[$p].checksum' <<<"${MANIFEST}")"
if [[ ! "$SHA256" =~ ^[0-9a-f]{64}$ ]]; then
    echo "checksum illisible dans le manifeste de ${VERSION} : '${SHA256}'" >&2
    exit 1
fi

curl -fsSL "${BASE}/${VERSION}/${PLATFORM}/claude" -o "${TMP_DIR}/claude"
echo "${SHA256}  ${TMP_DIR}/claude" | sha256sum --check --strict

mkdir -p "${INSTALL_DIR}"
install -m 0755 "${TMP_DIR}/claude" "${INSTALL_DIR}/claude"
echo "claude ${VERSION} installe dans ${INSTALL_DIR}/claude (sha256 ${SHA256})"
