output "dev_vpc_id" {
  description = "ID of the dev VPC."
  value       = module.vpc_dev.vpc.vpc_id
}

output "dev_vpc_crn" {
  description = "CRN of the dev VPC."
  value       = module.vpc_dev.vpc.crn
}

output "dev_subnet_ids" {
  description = "Subnet IDs created in the dev VPC, keyed by zone."
  value       = { for s in module.vpc_dev.vpc.subnet_zone_list : s.zone => s.id }
}

output "prod_vpc_id" {
  description = "ID of the prod VPC."
  value       = module.vpc_prod.vpc.vpc_id
}

output "prod_vpc_crn" {
  description = "CRN of the prod VPC."
  value       = module.vpc_prod.vpc.crn
}

output "prod_subnet_ids" {
  description = "Subnet IDs created in the prod VPC, keyed by zone."
  value       = { for s in module.vpc_prod.vpc.subnet_zone_list : s.zone => s.id }
}

output "transit_gateway_id" {
  description = "ID of the global transit gateway."
  value       = ibm_tg_gateway.global.id
}
