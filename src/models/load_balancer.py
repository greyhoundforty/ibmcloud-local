"""Pydantic models for IBM Cloud VPC Load Balancers."""

from typing import Optional
from pydantic import BaseModel

from src.models.vpc import ResourceReference, ResourceGroupReference


class HealthMonitor(BaseModel):
    type: str = "http"
    delay: int = 5
    max_retries: int = 2
    timeout: int = 2
    url_path: str = "/"


class LoadBalancerCreate(BaseModel):
    name: str
    is_public: bool = True
    subnets: list[ResourceReference]
    resource_group: Optional[ResourceGroupReference] = None


class LoadBalancer(BaseModel):
    id: str
    crn: str = ""
    href: str = ""
    name: str
    hostname: str = ""
    is_public: bool = True
    operating_status: str = "offline"
    provisioning_status: str = "create_pending"
    subnets: list[ResourceReference] = []
    listeners: list[ResourceReference] = []
    pools: list[ResourceReference] = []
    resource_group: ResourceGroupReference = ResourceGroupReference()
    created_at: str = ""


class ListenerCreate(BaseModel):
    port: int
    protocol: str = "http"   # "http" | "https" | "tcp"
    default_pool: Optional[ResourceReference] = None


class Listener(BaseModel):
    id: str
    href: str = ""
    port: int
    protocol: str
    default_pool: Optional[ResourceReference] = None
    provisioning_status: str = "active"


class PoolCreate(BaseModel):
    name: str
    algorithm: str = "round_robin"   # "round_robin" | "least_connections" | "weighted_round_robin"
    protocol: str = "http"
    health_monitor: Optional[dict] = None
    members: list[dict] = []


class Pool(BaseModel):
    id: str
    href: str = ""
    name: str
    algorithm: str
    protocol: str
    health_monitor: dict = {}
    members: list[ResourceReference] = []
    provisioning_status: str = "active"


class PoolMemberCreate(BaseModel):
    target: dict   # {"address": "10.0.0.5"} or instance reference
    port: int
    weight: int = 50


class PoolMember(BaseModel):
    id: str
    href: str = ""
    target: dict
    port: int
    weight: int = 50
    health: str = "ok"
    provisioning_status: str = "active"
