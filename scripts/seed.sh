#!/usr/bin/env bash
# seed.sh — populate ibmcloud-local with a realistic multi-region environment
#
# Usage:
#   ./scripts/seed.sh                        # default: http://localhost:4515
#   BASE_URL=http://localhost:9000 ./scripts/seed.sh
#   APIKEY=my-key ./scripts/seed.sh
#
# Requires: curl, jq
#
# What gets created:
#   Regions / VPCs:
#     us-south  vpc-us-south-dev   (web + app subnets, public gateway, sg, 2 instances)
#     us-south  vpc-us-south-prod  (frontend + backend subnets, gateway, 2 sgs, 3 instances, LB)
#     us-east   vpc-us-east-dr     (primary + secondary subnets, gateway, sg, 2 instances)
#
#   Transit Gateways:
#     tgw-us-south-global  (us-south, global routing) — connects all 3 VPCs
#     tgw-us-east-local    (us-east,  local routing)  — connects us-east VPC + PowerVS workspace

set -euo pipefail

BASE_URL="${BASE_URL:-http://localhost:4515}"
APIKEY="${APIKEY:-local-dev-key}"
TGW_VERSION="${TGW_VERSION:-2024-01-01}"

# ── colours ──────────────────────────────────────────────────────────
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m'

log()    { echo -e "${BLUE}-->${NC} $*"; }
ok()     { echo -e "  ${GREEN}✓${NC} $*"; }
header() { echo -e "\n${YELLOW}=== $* ===${NC}"; }

# ── helpers ───────────────────────────────────────────────────────────

get_token() {
  curl -sf -X POST "${BASE_URL}/identity/token" \
    -H "Content-Type: application/x-www-form-urlencoded" \
    -d "grant_type=urn%3Aibm%3Aparams%3Aoauth%3Agrant-type%3Apikey&apikey=${APIKEY}" \
    | jq -r '.access_token'
}

TOKEN=$(get_token)

vpc() {
  # vpc METHOD PATH [JSON-BODY]
  local method="$1" path="$2" body="${3:-}"
  if [[ -n "$body" ]]; then
    curl -sf -X "$method" "${BASE_URL}/v1${path}" \
      -H "Authorization: Bearer ${TOKEN}" \
      -H "Content-Type: application/json" \
      -d "$body"
  else
    curl -sf -X "$method" "${BASE_URL}/v1${path}" \
      -H "Authorization: Bearer ${TOKEN}"
  fi
}

tgw() {
  # tgw METHOD PATH [JSON-BODY]
  local method="$1" path="$2" body="${3:-}"
  if [[ -n "$body" ]]; then
    curl -sf -X "$method" "${BASE_URL}/v1${path}?version=${TGW_VERSION}" \
      -H "Authorization: Bearer ${TOKEN}" \
      -H "Content-Type: application/json" \
      -d "$body"
  else
    curl -sf -X "$method" "${BASE_URL}/v1${path}?version=${TGW_VERSION}" \
      -H "Authorization: Bearer ${TOKEN}"
  fi
}

id_of()  { echo "$1" | jq -r '.id'; }
crn_of() { echo "$1" | jq -r '.crn'; }

# ── preflight ─────────────────────────────────────────────────────────

header "Preflight"
log "Emulator: ${BASE_URL}"
log "Checking health..."
health=$(curl -sf "${BASE_URL}/_emulator/health")
echo "  $(echo "$health" | jq -r '"services: " + (.services | join(", "))')"

log "Fetching token..."
TOKEN=$(get_token)
ok "Token acquired"

# ── us-south dev VPC ──────────────────────────────────────────────────

header "VPC: vpc-us-south-dev (us-south)"

log "Creating VPC..."
DEV_VPC=$(vpc POST /vpcs '{"name":"vpc-us-south-dev"}')
DEV_VPC_ID=$(id_of "$DEV_VPC")
DEV_VPC_CRN=$(crn_of "$DEV_VPC")
ok "vpc-us-south-dev  id=${DEV_VPC_ID}"

log "Creating subnets..."
DEV_SN_WEB=$(vpc POST /subnets \
  "{\"name\":\"subnet-web-us-south-1\",\"vpc\":{\"id\":\"${DEV_VPC_ID}\"},\"zone\":{\"name\":\"us-south-1\"},\"ipv4_cidr_block\":\"10.240.0.0/24\"}")
DEV_SN_WEB_ID=$(id_of "$DEV_SN_WEB")
ok "subnet-web-us-south-1  cidr=10.240.0.0/24"

DEV_SN_APP=$(vpc POST /subnets \
  "{\"name\":\"subnet-app-us-south-1\",\"vpc\":{\"id\":\"${DEV_VPC_ID}\"},\"zone\":{\"name\":\"us-south-1\"},\"ipv4_cidr_block\":\"10.240.1.0/24\"}")
DEV_SN_APP_ID=$(id_of "$DEV_SN_APP")
ok "subnet-app-us-south-1   cidr=10.240.1.0/24"

log "Creating public gateway..."
DEV_PGW=$(vpc POST /public_gateways \
  "{\"name\":\"pgw-dev-us-south-1\",\"vpc\":{\"id\":\"${DEV_VPC_ID}\"},\"zone\":{\"name\":\"us-south-1\"}}")
DEV_PGW_ID=$(id_of "$DEV_PGW")
vpc PUT "/subnets/${DEV_SN_WEB_ID}/public_gateway" "{\"id\":\"${DEV_PGW_ID}\"}" > /dev/null
ok "pgw-dev-us-south-1  attached to subnet-web"

log "Creating security group..."
DEV_SG=$(vpc POST /security_groups "{\"name\":\"sg-dev-web\",\"vpc\":{\"id\":\"${DEV_VPC_ID}\"}}")
DEV_SG_ID=$(id_of "$DEV_SG")
vpc POST "/security_groups/${DEV_SG_ID}/rules" \
  '{"direction":"inbound","protocol":"tcp","port_min":80,"port_max":80,"remote":{"cidr_block":"0.0.0.0/0"}}' > /dev/null
vpc POST "/security_groups/${DEV_SG_ID}/rules" \
  '{"direction":"inbound","protocol":"tcp","port_min":443,"port_max":443,"remote":{"cidr_block":"0.0.0.0/0"}}' > /dev/null
vpc POST "/security_groups/${DEV_SG_ID}/rules" \
  '{"direction":"inbound","protocol":"tcp","port_min":22,"port_max":22,"remote":{"cidr_block":"10.0.0.0/8"}}' > /dev/null
ok "sg-dev-web  rules: TCP 80, 443 (0.0.0.0/0), TCP 22 (10.0.0.0/8)"

log "Creating instances..."
vpc POST /instances \
  "{\"name\":\"web-server-1\",\"zone\":{\"name\":\"us-south-1\"},\"vpc\":{\"id\":\"${DEV_VPC_ID}\"},\"primary_network_interface\":{\"name\":\"eth0\",\"subnet\":{\"id\":\"${DEV_SN_WEB_ID}\"},\"security_groups\":[{\"id\":\"${DEV_SG_ID}\"}]},\"profile\":{\"name\":\"bx2-2x8\"},\"image\":{\"id\":\"ibm-ubuntu-22-04-minimal-amd64-1\"}}" > /dev/null
ok "web-server-1  profile=bx2-2x8  subnet=web"

vpc POST /instances \
  "{\"name\":\"app-server-1\",\"zone\":{\"name\":\"us-south-1\"},\"vpc\":{\"id\":\"${DEV_VPC_ID}\"},\"primary_network_interface\":{\"name\":\"eth0\",\"subnet\":{\"id\":\"${DEV_SN_APP_ID}\"},\"security_groups\":[{\"id\":\"${DEV_SG_ID}\"}]},\"profile\":{\"name\":\"bx2-4x16\"},\"image\":{\"id\":\"ibm-ubuntu-22-04-minimal-amd64-1\"}}" > /dev/null
ok "app-server-1  profile=bx2-4x16  subnet=app"

# ── us-south prod VPC ─────────────────────────────────────────────────

header "VPC: vpc-us-south-prod (us-south)"

log "Creating VPC..."
PROD_VPC=$(vpc POST /vpcs '{"name":"vpc-us-south-prod"}')
PROD_VPC_ID=$(id_of "$PROD_VPC")
PROD_VPC_CRN=$(crn_of "$PROD_VPC")
ok "vpc-us-south-prod  id=${PROD_VPC_ID}"

log "Creating subnets..."
PROD_SN_FE=$(vpc POST /subnets \
  "{\"name\":\"subnet-frontend-us-south-2\",\"vpc\":{\"id\":\"${PROD_VPC_ID}\"},\"zone\":{\"name\":\"us-south-2\"},\"ipv4_cidr_block\":\"10.241.0.0/24\"}")
PROD_SN_FE_ID=$(id_of "$PROD_SN_FE")
ok "subnet-frontend-us-south-2  cidr=10.241.0.0/24"

PROD_SN_BE=$(vpc POST /subnets \
  "{\"name\":\"subnet-backend-us-south-2\",\"vpc\":{\"id\":\"${PROD_VPC_ID}\"},\"zone\":{\"name\":\"us-south-2\"},\"ipv4_cidr_block\":\"10.241.1.0/24\"}")
PROD_SN_BE_ID=$(id_of "$PROD_SN_BE")
ok "subnet-backend-us-south-2   cidr=10.241.1.0/24"

log "Creating public gateway..."
PROD_PGW=$(vpc POST /public_gateways \
  "{\"name\":\"pgw-prod-us-south-2\",\"vpc\":{\"id\":\"${PROD_VPC_ID}\"},\"zone\":{\"name\":\"us-south-2\"}}")
PROD_PGW_ID=$(id_of "$PROD_PGW")
vpc PUT "/subnets/${PROD_SN_FE_ID}/public_gateway" "{\"id\":\"${PROD_PGW_ID}\"}" > /dev/null
ok "pgw-prod-us-south-2  attached to subnet-frontend"

log "Creating security groups..."
PROD_SG_PUB=$(vpc POST /security_groups "{\"name\":\"sg-prod-public\",\"vpc\":{\"id\":\"${PROD_VPC_ID}\"}}")
PROD_SG_PUB_ID=$(id_of "$PROD_SG_PUB")
vpc POST "/security_groups/${PROD_SG_PUB_ID}/rules" \
  '{"direction":"inbound","protocol":"tcp","port_min":80,"port_max":80,"remote":{"cidr_block":"0.0.0.0/0"}}' > /dev/null
vpc POST "/security_groups/${PROD_SG_PUB_ID}/rules" \
  '{"direction":"inbound","protocol":"tcp","port_min":443,"port_max":443,"remote":{"cidr_block":"0.0.0.0/0"}}' > /dev/null
ok "sg-prod-public  rules: TCP 80, 443 (0.0.0.0/0)"

PROD_SG_PRIV=$(vpc POST /security_groups "{\"name\":\"sg-prod-private\",\"vpc\":{\"id\":\"${PROD_VPC_ID}\"}}")
PROD_SG_PRIV_ID=$(id_of "$PROD_SG_PRIV")
vpc POST "/security_groups/${PROD_SG_PRIV_ID}/rules" \
  '{"direction":"inbound","protocol":"tcp","port_min":8080,"port_max":8080,"remote":{"cidr_block":"10.241.0.0/16"}}' > /dev/null
vpc POST "/security_groups/${PROD_SG_PRIV_ID}/rules" \
  '{"direction":"inbound","protocol":"tcp","port_min":22,"port_max":22,"remote":{"cidr_block":"10.241.0.0/16"}}' > /dev/null
ok "sg-prod-private  rules: TCP 8080, 22 (10.241.0.0/16)"

log "Creating instances..."
vpc POST /instances \
  "{\"name\":\"web-1\",\"zone\":{\"name\":\"us-south-2\"},\"vpc\":{\"id\":\"${PROD_VPC_ID}\"},\"primary_network_interface\":{\"name\":\"eth0\",\"subnet\":{\"id\":\"${PROD_SN_FE_ID}\"},\"security_groups\":[{\"id\":\"${PROD_SG_PUB_ID}\"}]},\"profile\":{\"name\":\"bx2-2x8\"},\"image\":{\"id\":\"ibm-ubuntu-22-04-minimal-amd64-1\"}}" > /dev/null
ok "web-1  profile=bx2-2x8  subnet=frontend"

vpc POST /instances \
  "{\"name\":\"web-2\",\"zone\":{\"name\":\"us-south-2\"},\"vpc\":{\"id\":\"${PROD_VPC_ID}\"},\"primary_network_interface\":{\"name\":\"eth0\",\"subnet\":{\"id\":\"${PROD_SN_FE_ID}\"},\"security_groups\":[{\"id\":\"${PROD_SG_PUB_ID}\"}]},\"profile\":{\"name\":\"bx2-2x8\"},\"image\":{\"id\":\"ibm-ubuntu-22-04-minimal-amd64-1\"}}" > /dev/null
ok "web-2  profile=bx2-2x8  subnet=frontend"

vpc POST /instances \
  "{\"name\":\"api-server-1\",\"zone\":{\"name\":\"us-south-2\"},\"vpc\":{\"id\":\"${PROD_VPC_ID}\"},\"primary_network_interface\":{\"name\":\"eth0\",\"subnet\":{\"id\":\"${PROD_SN_BE_ID}\"},\"security_groups\":[{\"id\":\"${PROD_SG_PRIV_ID}\"}]},\"profile\":{\"name\":\"bx2-4x16\"},\"image\":{\"id\":\"ibm-ubuntu-22-04-minimal-amd64-1\"}}" > /dev/null
ok "api-server-1  profile=bx2-4x16  subnet=backend"

log "Creating load balancer..."
LB=$(vpc POST /load_balancers \
  "{\"name\":\"lb-prod-web\",\"is_public\":true,\"subnets\":[{\"id\":\"${PROD_SN_FE_ID}\"}]}")
LB_ID=$(id_of "$LB")
ok "lb-prod-web  id=${LB_ID}  status=$(echo "$LB" | jq -r '.provisioning_status')"

POOL=$(vpc POST "/load_balancers/${LB_ID}/pools" \
  '{"name":"pool-web","algorithm":"round_robin","protocol":"http","health_monitor":{"delay":5,"max_retries":2,"timeout":2,"type":"http","url_path":"/health"}}')
POOL_ID=$(id_of "$POOL")
vpc POST "/load_balancers/${LB_ID}/listeners" \
  "{\"port\":80,\"protocol\":\"http\",\"default_pool\":{\"id\":\"${POOL_ID}\"}}" > /dev/null
vpc POST "/load_balancers/${LB_ID}/pools/${POOL_ID}/members" \
  '{"port":8080,"target":{"address":"10.241.0.10"},"weight":50}' > /dev/null
vpc POST "/load_balancers/${LB_ID}/pools/${POOL_ID}/members" \
  '{"port":8080,"target":{"address":"10.241.0.11"},"weight":50}' > /dev/null
ok "lb-prod-web  listener=:80 → pool-web (2 members)"

# ── us-east DR VPC ────────────────────────────────────────────────────

header "VPC: vpc-us-east-dr (us-east)"

log "Creating VPC..."
DR_VPC=$(vpc POST /vpcs '{"name":"vpc-us-east-dr"}')
DR_VPC_ID=$(id_of "$DR_VPC")
DR_VPC_CRN=$(crn_of "$DR_VPC")
ok "vpc-us-east-dr  id=${DR_VPC_ID}"

log "Creating subnets..."
DR_SN_PRI=$(vpc POST /subnets \
  "{\"name\":\"subnet-primary-us-east-1\",\"vpc\":{\"id\":\"${DR_VPC_ID}\"},\"zone\":{\"name\":\"us-east-1\"},\"ipv4_cidr_block\":\"10.242.0.0/24\"}")
DR_SN_PRI_ID=$(id_of "$DR_SN_PRI")
ok "subnet-primary-us-east-1    cidr=10.242.0.0/24"

DR_SN_SEC=$(vpc POST /subnets \
  "{\"name\":\"subnet-secondary-us-east-2\",\"vpc\":{\"id\":\"${DR_VPC_ID}\"},\"zone\":{\"name\":\"us-east-2\"},\"ipv4_cidr_block\":\"10.242.1.0/24\"}")
DR_SN_SEC_ID=$(id_of "$DR_SN_SEC")
ok "subnet-secondary-us-east-2  cidr=10.242.1.0/24"

log "Creating public gateway..."
DR_PGW=$(vpc POST /public_gateways \
  "{\"name\":\"pgw-dr-us-east-1\",\"vpc\":{\"id\":\"${DR_VPC_ID}\"},\"zone\":{\"name\":\"us-east-1\"}}")
DR_PGW_ID=$(id_of "$DR_PGW")
vpc PUT "/subnets/${DR_SN_PRI_ID}/public_gateway" "{\"id\":\"${DR_PGW_ID}\"}" > /dev/null
ok "pgw-dr-us-east-1  attached to subnet-primary"

log "Creating security group..."
DR_SG=$(vpc POST /security_groups "{\"name\":\"sg-dr-default\",\"vpc\":{\"id\":\"${DR_VPC_ID}\"}}")
DR_SG_ID=$(id_of "$DR_SG")
vpc POST "/security_groups/${DR_SG_ID}/rules" \
  '{"direction":"inbound","protocol":"tcp","port_min":443,"port_max":443,"remote":{"cidr_block":"0.0.0.0/0"}}' > /dev/null
vpc POST "/security_groups/${DR_SG_ID}/rules" \
  '{"direction":"inbound","protocol":"tcp","port_min":22,"port_max":22,"remote":{"cidr_block":"10.0.0.0/8"}}' > /dev/null
ok "sg-dr-default  rules: TCP 443 (0.0.0.0/0), TCP 22 (10.0.0.0/8)"

log "Creating instances..."
vpc POST /instances \
  "{\"name\":\"dr-primary-1\",\"zone\":{\"name\":\"us-east-1\"},\"vpc\":{\"id\":\"${DR_VPC_ID}\"},\"primary_network_interface\":{\"name\":\"eth0\",\"subnet\":{\"id\":\"${DR_SN_PRI_ID}\"},\"security_groups\":[{\"id\":\"${DR_SG_ID}\"}]},\"profile\":{\"name\":\"bx2-4x16\"},\"image\":{\"id\":\"ibm-ubuntu-22-04-minimal-amd64-1\"}}" > /dev/null
ok "dr-primary-1  profile=bx2-4x16  subnet=primary"

vpc POST /instances \
  "{\"name\":\"dr-secondary-1\",\"zone\":{\"name\":\"us-east-2\"},\"vpc\":{\"id\":\"${DR_VPC_ID}\"},\"primary_network_interface\":{\"name\":\"eth0\",\"subnet\":{\"id\":\"${DR_SN_SEC_ID}\"},\"security_groups\":[{\"id\":\"${DR_SG_ID}\"}]},\"profile\":{\"name\":\"bx2-4x16\"},\"image\":{\"id\":\"ibm-ubuntu-22-04-minimal-amd64-1\"}}" > /dev/null
ok "dr-secondary-1  profile=bx2-4x16  subnet=secondary"

# ── Transit Gateways ──────────────────────────────────────────────────

header "Transit Gateway: tgw-us-south-global (global routing)"

log "Creating transit gateway..."
TGW_GLOBAL=$(tgw POST /transit_gateways \
  '{"name":"tgw-us-south-global","location":"us-south","global":true}')
TGW_GLOBAL_ID=$(id_of "$TGW_GLOBAL")
ok "tgw-us-south-global  id=${TGW_GLOBAL_ID}  global=true"

log "Attaching VPCs..."
tgw POST "/transit_gateways/${TGW_GLOBAL_ID}/connections" \
  "{\"network_type\":\"vpc\",\"network_id\":\"${DEV_VPC_CRN}\",\"name\":\"conn-vpc-us-south-dev\"}" > /dev/null
ok "conn-vpc-us-south-dev  attached"

tgw POST "/transit_gateways/${TGW_GLOBAL_ID}/connections" \
  "{\"network_type\":\"vpc\",\"network_id\":\"${PROD_VPC_CRN}\",\"name\":\"conn-vpc-us-south-prod\"}" > /dev/null
ok "conn-vpc-us-south-prod  attached"

tgw POST "/transit_gateways/${TGW_GLOBAL_ID}/connections" \
  "{\"network_type\":\"vpc\",\"network_id\":\"${DR_VPC_CRN}\",\"name\":\"conn-vpc-us-east-dr\"}" > /dev/null
ok "conn-vpc-us-east-dr  attached"

header "Transit Gateway: tgw-us-east-local (local routing)"

log "Creating transit gateway..."
TGW_LOCAL=$(tgw POST /transit_gateways \
  '{"name":"tgw-us-east-local","location":"us-east","global":false}')
TGW_LOCAL_ID=$(id_of "$TGW_LOCAL")
ok "tgw-us-east-local  id=${TGW_LOCAL_ID}  global=false"

log "Attaching connections..."
tgw POST "/transit_gateways/${TGW_LOCAL_ID}/connections" \
  "{\"network_type\":\"vpc\",\"network_id\":\"${DR_VPC_CRN}\",\"name\":\"conn-vpc-us-east-dr\"}" > /dev/null
ok "conn-vpc-us-east-dr  attached"

tgw POST "/transit_gateways/${TGW_LOCAL_ID}/connections" \
  "{\"network_type\":\"power_virtual_server\",\"network_id\":\"crn:v1:bluemix:public:power-iaas:us-east:a/local-emulator::workspace:pvs-us-east-01\",\"name\":\"conn-pvs-us-east\"}" > /dev/null
ok "conn-pvs-us-east  attached (power_virtual_server)"

# ── Summary ───────────────────────────────────────────────────────────

header "Summary"

VPC_COUNT=$(vpc GET /vpcs | jq '.total_count')
SUBNET_COUNT=$(vpc GET /subnets | jq '.subnets | length')
INSTANCE_COUNT=$(vpc GET /instances | jq '.instances | length')
SG_COUNT=$(vpc GET /security_groups | jq '.security_groups | length')
PGW_COUNT=$(vpc GET /public_gateways | jq '.public_gateways | length')
LB_COUNT=$(vpc GET /load_balancers | jq '.load_balancers | length')
TGW_COUNT=$(tgw GET /transit_gateways | jq '.transit_gateways | length')
CONN_COUNT=$(tgw GET /connections | jq '.connections | length')

echo ""
echo "  VPCs:             ${VPC_COUNT}  (vpc-us-south-dev, vpc-us-south-prod, vpc-us-east-dr)"
echo "  Subnets:          ${SUBNET_COUNT}"
echo "  Instances:        ${INSTANCE_COUNT}"
echo "  Security Groups:  ${SG_COUNT}"
echo "  Public Gateways:  ${PGW_COUNT}"
echo "  Load Balancers:   ${LB_COUNT}  (lb-prod-web)"
echo "  Transit Gateways: ${TGW_COUNT}  (tgw-us-south-global, tgw-us-east-local)"
echo "  TGW Connections:  ${CONN_COUNT}"
echo ""
echo -e "${GREEN}Seed complete.${NC} Emulator: ${BASE_URL}/api/dashboard"
