terraform {
  required_version = ">= 1.9.0"

  required_providers {
    ibm = {
      source  = "IBM-Cloud/ibm"
      version = "1.79.0"
    }
  }
}

# Endpoint overrides are supplied via environment variables rather than
# hardcoded here so the same config works against both the emulator and
# real IBM Cloud without any file edits.
#
# For local bluestack use, source the helper:
#   source ./scripts/bluestack-env.sh
#
# For real IBM Cloud, unset those vars (or don't source the helper).

provider "ibm" {
  ibmcloud_api_key = var.ibmcloud_api_key
  region           = var.region
}
