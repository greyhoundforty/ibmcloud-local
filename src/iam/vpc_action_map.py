"""
VPC action map — maps (HTTP method, URL path) to IBM Cloud IAM action strings.

Action format: "<service>.<resource-type>.<verb>"
e.g. "is.vpc.vpc.create", "is.vpc.instance.list"

Path matching uses a simple segment-by-segment approach: literal segments
must match exactly; segments containing only digits or starting with known
IBM Cloud ID prefixes are treated as wildcards.
"""

from __future__ import annotations

import re

# Each entry: (method, path_pattern) → iam_action
# Path patterns use {id} as a wildcard placeholder for any resource ID segment.
_ACTION_MAP: list[tuple[str, str, str]] = [
    # VPCs
    ("GET",    "/v1/vpcs",              "is.vpc.vpc.list"),
    ("POST",   "/v1/vpcs",              "is.vpc.vpc.create"),
    ("GET",    "/v1/vpcs/{id}",         "is.vpc.vpc.read"),
    ("PATCH",  "/v1/vpcs/{id}",         "is.vpc.vpc.update"),
    ("DELETE", "/v1/vpcs/{id}",         "is.vpc.vpc.delete"),

    # Subnets
    ("GET",    "/v1/subnets",           "is.vpc.subnet.list"),
    ("POST",   "/v1/subnets",           "is.vpc.subnet.create"),
    ("GET",    "/v1/subnets/{id}",      "is.vpc.subnet.read"),
    ("PATCH",  "/v1/subnets/{id}",      "is.vpc.subnet.update"),
    ("DELETE", "/v1/subnets/{id}",      "is.vpc.subnet.delete"),
    # Subnet public gateway attachment
    ("GET",    "/v1/subnets/{id}/public_gateway",    "is.vpc.subnet.read"),
    ("PUT",    "/v1/subnets/{id}/public_gateway",    "is.vpc.subnet.update"),
    ("DELETE", "/v1/subnets/{id}/public_gateway",    "is.vpc.subnet.update"),

    # Instances
    ("GET",    "/v1/instances",         "is.vpc.instance.list"),
    ("POST",   "/v1/instances",         "is.vpc.instance.create"),
    ("GET",    "/v1/instances/{id}",    "is.vpc.instance.read"),
    ("PATCH",  "/v1/instances/{id}",    "is.vpc.instance.update"),
    ("DELETE", "/v1/instances/{id}",    "is.vpc.instance.delete"),
    ("POST",   "/v1/instances/{id}/actions", "is.vpc.instance.operate"),

    # Security Groups
    ("GET",    "/v1/security_groups",              "is.vpc.security-group.list"),
    ("POST",   "/v1/security_groups",              "is.vpc.security-group.create"),
    ("GET",    "/v1/security_groups/{id}",         "is.vpc.security-group.read"),
    ("DELETE", "/v1/security_groups/{id}",         "is.vpc.security-group.delete"),
    ("GET",    "/v1/security_groups/{id}/rules",   "is.vpc.security-group.read"),
    ("POST",   "/v1/security_groups/{id}/rules",   "is.vpc.security-group.update"),
    ("GET",    "/v1/security_groups/{id}/rules/{id}", "is.vpc.security-group.read"),
    ("PATCH",  "/v1/security_groups/{id}/rules/{id}", "is.vpc.security-group.update"),
    ("DELETE", "/v1/security_groups/{id}/rules/{id}", "is.vpc.security-group.update"),

    # Floating IPs
    ("GET",    "/v1/floating_ips",      "is.vpc.floating-ip.list"),
    ("POST",   "/v1/floating_ips",      "is.vpc.floating-ip.create"),
    ("GET",    "/v1/floating_ips/{id}", "is.vpc.floating-ip.read"),
    ("DELETE", "/v1/floating_ips/{id}", "is.vpc.floating-ip.delete"),

    # Network ACLs
    ("GET",    "/v1/network_acls",              "is.vpc.network-acl.list"),
    ("POST",   "/v1/network_acls",              "is.vpc.network-acl.create"),
    ("GET",    "/v1/network_acls/{id}",         "is.vpc.network-acl.read"),
    ("PATCH",  "/v1/network_acls/{id}",         "is.vpc.network-acl.update"),
    ("DELETE", "/v1/network_acls/{id}",         "is.vpc.network-acl.delete"),
    ("GET",    "/v1/network_acls/{id}/rules",   "is.vpc.network-acl.read"),
    ("POST",   "/v1/network_acls/{id}/rules",   "is.vpc.network-acl.update"),
    ("GET",    "/v1/network_acls/{id}/rules/{id}", "is.vpc.network-acl.read"),
    ("PATCH",  "/v1/network_acls/{id}/rules/{id}", "is.vpc.network-acl.update"),
    ("DELETE", "/v1/network_acls/{id}/rules/{id}", "is.vpc.network-acl.update"),

    # Public Gateways
    ("GET",    "/v1/public_gateways",      "is.vpc.public-gateway.list"),
    ("POST",   "/v1/public_gateways",      "is.vpc.public-gateway.create"),
    ("GET",    "/v1/public_gateways/{id}", "is.vpc.public-gateway.read"),
    ("PATCH",  "/v1/public_gateways/{id}", "is.vpc.public-gateway.update"),
    ("DELETE", "/v1/public_gateways/{id}", "is.vpc.public-gateway.delete"),

    # Load Balancers
    ("GET",    "/v1/load_balancers",       "is.vpc.load-balancer.list"),
    ("POST",   "/v1/load_balancers",       "is.vpc.load-balancer.create"),
    ("GET",    "/v1/load_balancers/{id}",  "is.vpc.load-balancer.read"),
    ("PATCH",  "/v1/load_balancers/{id}",  "is.vpc.load-balancer.update"),
    ("DELETE", "/v1/load_balancers/{id}",  "is.vpc.load-balancer.delete"),
    # LB sub-resources share the parent's action space
    ("GET",    "/v1/load_balancers/{id}/listeners",                  "is.vpc.load-balancer.read"),
    ("POST",   "/v1/load_balancers/{id}/listeners",                  "is.vpc.load-balancer.update"),
    ("GET",    "/v1/load_balancers/{id}/listeners/{id}",             "is.vpc.load-balancer.read"),
    ("PATCH",  "/v1/load_balancers/{id}/listeners/{id}",             "is.vpc.load-balancer.update"),
    ("DELETE", "/v1/load_balancers/{id}/listeners/{id}",             "is.vpc.load-balancer.update"),
    ("GET",    "/v1/load_balancers/{id}/pools",                      "is.vpc.load-balancer.read"),
    ("POST",   "/v1/load_balancers/{id}/pools",                      "is.vpc.load-balancer.update"),
    ("GET",    "/v1/load_balancers/{id}/pools/{id}",                 "is.vpc.load-balancer.read"),
    ("PATCH",  "/v1/load_balancers/{id}/pools/{id}",                 "is.vpc.load-balancer.update"),
    ("DELETE", "/v1/load_balancers/{id}/pools/{id}",                 "is.vpc.load-balancer.update"),
    ("GET",    "/v1/load_balancers/{id}/pools/{id}/members",         "is.vpc.load-balancer.read"),
    ("POST",   "/v1/load_balancers/{id}/pools/{id}/members",         "is.vpc.load-balancer.update"),
    ("GET",    "/v1/load_balancers/{id}/pools/{id}/members/{id}",    "is.vpc.load-balancer.read"),
    ("PATCH",  "/v1/load_balancers/{id}/pools/{id}/members/{id}",    "is.vpc.load-balancer.update"),
    ("DELETE", "/v1/load_balancers/{id}/pools/{id}/members/{id}",    "is.vpc.load-balancer.update"),
]

# Compile patterns once at import time
_WILDCARD = re.compile(r"^\{.+\}$")


def _pattern_to_regex(pattern: str) -> re.Pattern:
    parts = pattern.strip("/").split("/")
    segments = []
    for p in parts:
        if _WILDCARD.match(p):
            segments.append("[^/]+")
        else:
            segments.append(re.escape(p))
    return re.compile(r"^/" + "/".join(segments) + r"$")


_COMPILED: list[tuple[str, re.Pattern, str]] = [
    (method, _pattern_to_regex(pattern), action)
    for method, pattern, action in _ACTION_MAP
]


def resolve_action(method: str, path: str) -> str | None:
    """
    Return the IAM action for a given HTTP method + path, or None if unmapped.
    Strips query strings from path before matching.
    """
    clean_path = path.split("?")[0].rstrip("/") or "/"
    for m, regex, action in _COMPILED:
        if m == method.upper() and regex.match(clean_path):
            return action
    return None
