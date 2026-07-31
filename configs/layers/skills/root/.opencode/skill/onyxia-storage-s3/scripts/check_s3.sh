#!/usr/bin/env bash
# check_s3.sh — S3/MinIO diagnostic for Onyxia (SSP Cloud) services.
#
# Checks that the injected AWS_* credentials are present and still valid,
# lists the buckets the token can access, and interprets common failures
# (403 = expired 7-day token, missing CLI, ...).
#
# Exit code: 0 if everything looks good, 1 otherwise.
# Never prints credential values.

set -uo pipefail   # no -e: we want to report failures, not die silently

FAIL=0

say()  { printf '%s\n' "$*"; }
ok()   { printf '  \342\234\223 %s\n' "$*"; }            # ✓
bad()  { printf '  \342\234\227 %s\n' "$*"; FAIL=1; }     # ✗

say "== Onyxia S3/MinIO diagnostic =="
say ""

# ---------------------------------------------------------------------------
# 1. Injected environment variables (values are never printed)
# ---------------------------------------------------------------------------
say "-- 1. Environment variables --"
for var in AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN \
           AWS_S3_ENDPOINT AWS_DEFAULT_REGION; do
    if [ -n "${!var:-}" ]; then
        ok "$var is set"
    else
        bad "$var is NOT set"
    fi
done

if [ -z "${AWS_S3_ENDPOINT:-}" ]; then
    say ""
    say "AWS_S3_ENDPOINT is missing: cannot test connectivity."
    say "On SSP Cloud it should be 'minio.lab.sspcloud.fr'. These variables are"
    say "injected automatically when the Onyxia service starts; if they are"
    say "absent, relaunch the service or export fresh credentials from the"
    say "Onyxia console ('My account' -> 'Connect to storage')."
    exit 1
fi

ENDPOINT_URL="https://$AWS_S3_ENDPOINT"

# ---------------------------------------------------------------------------
# 2. Personal bucket / bucket listing
# ---------------------------------------------------------------------------
say ""
say "-- 2. Bucket access --"

# Onyxia convention: the personal bucket is named after the SSP Cloud
# username. Careful: on pods, $USERNAME is the generic 'onyxia' user — the
# real username is in VAULT_TOP_DIR or the KUBERNETES_NAMESPACE ('user-<name>').
GUESSED_BUCKET="${VAULT_TOP_DIR:-}"
BUCKET_SOURCE="\$VAULT_TOP_DIR"
if [ -z "$GUESSED_BUCKET" ] && [ -n "${KUBERNETES_NAMESPACE:-}" ]; then
    GUESSED_BUCKET="${KUBERNETES_NAMESPACE#user-}"
    BUCKET_SOURCE="\$KUBERNETES_NAMESPACE"
fi
if [ -z "$GUESSED_BUCKET" ] && [ -n "${USERNAME:-}" ] && [ "${USERNAME}" != "onyxia" ]; then
    GUESSED_BUCKET="$USERNAME"
    BUCKET_SOURCE="\$USERNAME"
fi
if [ -n "$GUESSED_BUCKET" ]; then
    say "  Personal bucket (Onyxia convention, from $BUCKET_SOURCE): $GUESSED_BUCKET"
else
    say "  Cannot guess the personal bucket name (VAULT_TOP_DIR, KUBERNETES_NAMESPACE and USERNAME unhelpful)."
fi

if ! command -v aws >/dev/null 2>&1; then
    bad "the 'aws' CLI is not installed"
    say ""
    say "Install it with:  pip install awscli"
    say "Or use the MinIO client instead:  mc ls s3/  (the 'mc' client with its"
    say "'s3' alias is preinstalled on Onyxia data science images)."
    exit 1
fi
ok "'aws' CLI found ($(command -v aws))"

# Prefer listing the personal bucket (ListBuckets on 's3://' can hang on
# some MinIO setups); fall back to the global bucket listing.
if [ -n "$GUESSED_BUCKET" ]; then
    TARGET="s3://$GUESSED_BUCKET/"
else
    TARGET="s3://"
fi
say "  Listing via: aws s3 ls $TARGET --endpoint-url $ENDPOINT_URL"
STDERR_FILE="$(mktemp)"
trap 'rm -f "$STDERR_FILE"' EXIT

# Bound the network call so the diagnostic never hangs.
AWS_CMD=(aws s3 ls "$TARGET" --endpoint-url "$ENDPOINT_URL" --cli-connect-timeout 15 --cli-read-timeout 30)
if command -v timeout >/dev/null 2>&1; then
    AWS_CMD=(timeout 60 "${AWS_CMD[@]}")
fi

if OUTPUT="$("${AWS_CMD[@]}" 2>"$STDERR_FILE")"; then
    ok "listing of $TARGET succeeded"
    if [ -n "$OUTPUT" ]; then
        printf '%s\n' "$OUTPUT" | head -20 | sed 's/^/    /'
    else
        say "    (listing is empty - access works but nothing is stored there yet)"
    fi
else
    RC=$?
    ERR="$(cat "$STDERR_FILE")"
    bad "bucket listing failed (exit code $RC)"
    printf '%s\n' "$ERR" | sed 's/^/    /'
    say ""
    # ------------------------------------------------------------------
    # 3. Interpret the failure
    # ------------------------------------------------------------------
    if [ "$RC" -eq 124 ]; then
        say "  Diagnosis: the request TIMED OUT. The endpoint '$ENDPOINT_URL'"
        say "  is unreachable from here (network restriction or outage), or the"
        say "  host in AWS_S3_ENDPOINT is wrong."
    else
    case "$ERR" in
        *403*|*AccessDenied*|*ExpiredToken*|*InvalidAccessKeyId*|*SignatureDoesNotMatch*)
            say "  Diagnosis: this looks like an EXPIRED OR INVALID TOKEN."
            say "  Onyxia S3/MinIO tokens expire after 7 DAYS. This is by far the"
            say "  most common cause of 403/AccessDenied on the datalab."
            say "  Remedies:"
            say "    - Renew credentials in the Onyxia console: 'My account' ->"
            say "      'Connect to storage', then re-export the AWS_* variables; or"
            say "    - Save your code/data and RELAUNCH the service (a fresh"
            say "      service gets a fresh 7-day token)."
            ;;
        *"Could not connect"*|*"Connection was closed"*|*EndpointConnectionError*|*"Name or service not known"*)
            say "  Diagnosis: cannot reach the endpoint '$ENDPOINT_URL'."
            say "  Check that AWS_S3_ENDPOINT is the bare host (e.g."
            say "  'minio.lab.sspcloud.fr', no scheme) and that the network is up."
            ;;
        *)
            say "  Diagnosis: unrecognized error - see stderr above. Verify the"
            say "  AWS_* variables and the endpoint, or try the MinIO client:"
            say "    mc ls s3/"
            ;;
    esac
    fi
fi

# ---------------------------------------------------------------------------
# 4. Verdict
# ---------------------------------------------------------------------------
say ""
if [ "$FAIL" -eq 0 ]; then
    say "== All good: S3/MinIO access looks healthy. =="
    exit 0
else
    say "== Problems detected (see above). =="
    exit 1
fi
