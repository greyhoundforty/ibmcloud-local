variable "ibmcloud_api_key" {
  description = "API key used to authenticate with the emulator (any non-empty string in permissive mode)."
  type        = string
  sensitive   = true
  default     = "local-dev-key"
}


variable "emulator_url" {
  description = "Base URL of the ibmcloud-local emulator."
  type        = string
  default     = "http://localhost:4515"
}

variable "region" {
  description = "IBM Cloud region. The emulator accepts any value; this is passed to the provider for SDK compatibility."
  type        = string
  default     = "us-south"
}

variable "prefix" {
  description = "Prefix applied to all resource names to avoid collisions between workspaces."
  type        = string
  default     = "bluestack"
}

variable "resource_group_id" {
  description = "Resource group ID. Defaults to the emulator's built-in 'default-resource-group'."
  type        = string
  default     = "default-resource-group"
}

# ── Dev VPC ───────────────────────────────────────────────────────────

variable "dev_vpc_name" {
  description = "Name for the dev VPC."
  type        = string
  default     = "vpc-tf-dev"
}

variable "dev_zones" {
  description = "Zones to create subnets in for the dev VPC."
  type        = list(string)
  default     = ["us-south-1", "us-south-2"]
}

variable "dev_address_prefixes" {
  description = "Address prefixes for the dev VPC subnets."
  type = list(object({
    name     = string
    location = string
    ip_range = string
  }))
  default = [
    { name = "prefix-dev-us-south-1", location = "us-south-1", ip_range = "10.100.0.0/24" },
    { name = "prefix-dev-us-south-2", location = "us-south-2", ip_range = "10.100.1.0/24" },
  ]
}

# ── Prod VPC ──────────────────────────────────────────────────────────

variable "prod_vpc_name" {
  description = "Name for the prod VPC."
  type        = string
  default     = "vpc-tf-prod"
}

variable "prod_zones" {
  description = "Zones to create subnets in for the prod VPC."
  type        = list(string)
  default     = ["us-south-1", "us-south-2", "us-south-3"]
}

variable "prod_address_prefixes" {
  description = "Address prefixes for the prod VPC subnets."
  type = list(object({
    name     = string
    location = string
    ip_range = string
  }))
  default = [
    { name = "prefix-prod-us-south-1", location = "us-south-1", ip_range = "10.101.0.0/24" },
    { name = "prefix-prod-us-south-2", location = "us-south-2", ip_range = "10.101.1.0/24" },
    { name = "prefix-prod-us-south-3", location = "us-south-3", ip_range = "10.101.2.0/24" },
  ]
}
