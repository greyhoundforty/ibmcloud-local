# ibmcloud-local / bluestack — Terraform root module
#
# Tests the IBM VPC Terraform module against the local emulator.
# Run with:
#   terraform init
#   terraform plan
#   terraform apply
#
# To target the real IBM Cloud instead of the emulator, override emulator_url:
#   terraform apply -var="emulator_url=https://us-south.iaas.cloud.ibm.com"

# ── Dev VPC ───────────────────────────────────────────────────────────
# Two-zone VPC with public gateways — mirrors the seed script's dev environment.

module "vpc_dev" {
  source  = "terraform-ibm-modules/vpc/ibm"
  version = "1.5.4"

  vpc_name          = var.dev_vpc_name
  resource_group_id = var.resource_group_id

  locations           = var.dev_zones
  address_prefixes    = var.dev_address_prefixes
  subnet_name_prefix  = "${var.prefix}-dev-subnet"
  number_of_addresses = 16

  create_gateway             = true
  public_gateway_name_prefix = "${var.prefix}-dev-gw"

  default_network_acl_name    = "${var.prefix}-dev-nacl"
  default_routing_table_name  = "${var.prefix}-dev-rt"
  default_security_group_name = "${var.prefix}-dev-sg"

  clean_default_sg_acl = false
}

# ── Prod VPC ──────────────────────────────────────────────────────────
# Three-zone VPC with public gateways — higher availability layout.

module "vpc_prod" {
  source  = "terraform-ibm-modules/vpc/ibm"
  version = "1.5.4"

  vpc_name          = var.prod_vpc_name
  resource_group_id = var.resource_group_id

  locations           = var.prod_zones
  address_prefixes    = var.prod_address_prefixes
  subnet_name_prefix  = "${var.prefix}-prod-subnet"
  number_of_addresses = 32

  create_gateway             = true
  public_gateway_name_prefix = "${var.prefix}-prod-gw"

  default_network_acl_name    = "${var.prefix}-prod-nacl"
  default_routing_table_name  = "${var.prefix}-prod-rt"
  default_security_group_name = "${var.prefix}-prod-sg"

  clean_default_sg_acl = true
}

# ── Transit Gateway ───────────────────────────────────────────────────
# Connect both VPCs via a global transit gateway using the ibm_tg_* resources
# directly (no community module exists yet for TGW).

resource "ibm_tg_gateway" "global" {
  name           = "${var.prefix}-tgw-global"
  location       = var.region
  global         = true
  resource_group = var.resource_group_id
}

resource "ibm_tg_connection" "dev_vpc" {
  gateway      = ibm_tg_gateway.global.id
  network_type = "vpc"
  name         = "conn-${var.dev_vpc_name}"
  network_id   = module.vpc_dev.vpc.crn
}

resource "ibm_tg_connection" "prod_vpc" {
  gateway      = ibm_tg_gateway.global.id
  network_type = "vpc"
  name         = "conn-${var.prod_vpc_name}"
  network_id   = module.vpc_prod.vpc.crn
}
