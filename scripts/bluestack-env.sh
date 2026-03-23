#!/usr/bin/env bash
# bluestack-env.sh — configure the IBM Terraform provider to target the local emulator
#
# Usage (source, not execute):
#   source ./scripts/bluestack-env.sh
#   source ./scripts/bluestack-env.sh http://localhost:9000   # custom port
#
# To revert to real IBM Cloud, run:
#   source ./scripts/bluestack-env.sh --unset

BLUESTACK_URL="${1:-http://localhost:4515}"

if [[ "${BLUESTACK_URL}" == "--unset" ]]; then
  unset IBMCLOUD_IAM_API_ENDPOINT
  unset IBMCLOUD_IS_NG_API_ENDPOINT
  unset IBMCLOUD_TG_API_ENDPOINT
  unset RIAAS_ENDPOINT
  unset IBMCLOUD_API_KEY
  echo "Bluestack env cleared — provider will target real IBM Cloud."
  return 0
fi

# IAM: provider reads IBMCLOUD_IAM_API_ENDPOINT and appends /identity/token.
export IBMCLOUD_IAM_API_ENDPOINT="${BLUESTACK_URL}"

# VPC: provider 1.79.x reads RIAAS_ENDPOINT; later versions read IBMCLOUD_IS_NG_API_ENDPOINT.
# Set both so the script works across provider versions.
export RIAAS_ENDPOINT="${BLUESTACK_URL}/v1"
export IBMCLOUD_IS_NG_API_ENDPOINT="${BLUESTACK_URL}/v1"

# Transit Gateway API — provider reads IBMCLOUD_TG_API_ENDPOINT.
export IBMCLOUD_TG_API_ENDPOINT="${BLUESTACK_URL}/v1"

# Any non-empty key works in permissive mode.
export IBMCLOUD_API_KEY="${IBMCLOUD_API_KEY:-local-dev-key}"

echo "Bluestack env set:"
echo "  IBMCLOUD_IAM_API_ENDPOINT   = ${IBMCLOUD_IAM_API_ENDPOINT}"
echo "  RIAAS_ENDPOINT              = ${RIAAS_ENDPOINT}"
echo "  IBMCLOUD_IS_NG_API_ENDPOINT = ${IBMCLOUD_IS_NG_API_ENDPOINT}"
echo "  IBMCLOUD_TG_API_ENDPOINT    = ${IBMCLOUD_TG_API_ENDPOINT}"
echo "  IBMCLOUD_API_KEY            = ${IBMCLOUD_API_KEY}"
echo ""
echo "Run 'terraform plan' inside terraform/ to test against bluestack."
