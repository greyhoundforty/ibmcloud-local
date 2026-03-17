"""
ibmcloud-local CLI — command-line interface for the IBM Cloud Local Emulator.

This is the primary interface for starting, stopping, and managing the
emulator. Designed to feel like the `ibmcloud` CLI but for local dev.

Usage:
    ibmcloud-local start          # Start the emulator server
    ibmcloud-local stop           # Stop the emulator
    ibmcloud-local reset          # Wipe all emulated state
    ibmcloud-local routes         # Print the routing table
    ibmcloud-local status         # Show emulator health + resource counts
    ibmcloud-local env            # Print env vars to point IBM Cloud SDK at emulator

Or via mise:
    mise run start
    mise run routes
"""

import os
import sys
import subprocess

import click
from rich.console import Console
from rich.table import Table
from rich.panel import Panel

console = Console()

# Default settings (overridable via env vars or mise.toml)
DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 4515


@click.group()
def cli():
    """IBM Cloud Local Emulator — like LocalStack, but for IBM Cloud."""
    pass


@cli.command()
@click.option("--host", default=DEFAULT_HOST, envvar="IBMCLOUD_LOCAL_HOST")
@click.option("--port", default=DEFAULT_PORT, type=int, envvar="IBMCLOUD_LOCAL_PORT")
@click.option("--reload", is_flag=True, help="Enable auto-reload for development")
def start(host: str, port: int, reload: bool):
    """Start the emulator server."""
    console.print(Panel.fit(
        f"[bold cyan]IBM Cloud Local Emulator[/bold cyan]\n"
        f"Listening on [green]{host}:{port}[/green]",
        border_style="cyan",
    ))

    # Print the env vars people should set
    console.print("\n[dim]Point your IBM Cloud SDK at the emulator:[/dim]")
    console.print(f"  export IBMCLOUD_VPC_API_ENDPOINT=http://localhost:{port}")
    console.print(f"  export IBMCLOUD_COS_ENDPOINT=http://localhost:{port}")
    console.print()

    # Start uvicorn
    cmd = [
        sys.executable, "-m", "uvicorn",
        "src.server:app",
        "--host", host,
        "--port", str(port),
    ]
    if reload:
        cmd.append("--reload")

    try:
        subprocess.run(cmd, check=True)
    except KeyboardInterrupt:
        console.print("\n[yellow]Shutting down...[/yellow]")


@cli.command()
def stop():
    """Stop the emulator server (sends SIGTERM to the uvicorn process)."""
    # Simple approach: find and kill the uvicorn process
    import subprocess
    result = subprocess.run(
        ["pkill", "-f", "uvicorn src.server:app"],
        capture_output=True
    )
    if result.returncode == 0:
        console.print("[green]✓ Emulator stopped[/green]")
    else:
        console.print("[yellow]No running emulator found[/yellow]")


@cli.command()
def reset():
    """Reset all emulator state via the control plane API."""
    import httpx

    port = os.environ.get("IBMCLOUD_LOCAL_PORT", DEFAULT_PORT)
    try:
        resp = httpx.post(f"http://localhost:{port}/_emulator/reset")
        if resp.status_code == 200:
            console.print("[green]✓ All state reset[/green]")
        else:
            console.print(f"[red]Error: {resp.text}[/red]")
    except httpx.ConnectError:
        console.print("[red]Cannot connect — is the emulator running?[/red]")


@cli.command()
def routes():
    """
    Print the routing table — Traefik-style view of all registered API routes.

    Fetches route data from the running emulator's dashboard API
    and renders it as a Rich table in the terminal.
    """
    import httpx

    port = os.environ.get("IBMCLOUD_LOCAL_PORT", DEFAULT_PORT)
    try:
        resp = httpx.get(f"http://localhost:{port}/api/dashboard/routes")
        data = resp.json()
    except httpx.ConnectError:
        console.print("[red]Cannot connect — is the emulator running?[/red]")
        console.print("[dim]Showing static route definitions instead...[/dim]\n")
        # Fallback: import and show routes statically (without a running server)
        _show_static_routes()
        return

    # Build a Rich table styled like a Traefik routing dashboard
    table = Table(
        title="[bold]Routing Table[/bold]",
        show_header=True,
        header_style="bold cyan",
        border_style="dim",
    )
    table.add_column("Method", style="bold", width=8)
    table.add_column("Path", style="green")
    table.add_column("Service", style="yellow")
    table.add_column("Handler", style="dim")

    # Color-code HTTP methods like Traefik does
    method_colors = {
        "GET": "green",
        "POST": "blue",
        "PATCH": "yellow",
        "DELETE": "red",
        "PUT": "magenta",
    }

    for route in data.get("routes", []):
        method = route["method"]
        color = method_colors.get(method, "white")
        table.add_row(
            f"[{color}]{method}[/{color}]",
            route["path"],
            route["service"],
            route.get("handler", ""),
        )

    console.print(table)
    console.print(f"\n[dim]Total routes: {data.get('total_routes', 0)}[/dim]")

    # Also show service summary
    services = data.get("services", [])
    if services:
        console.print()
        svc_table = Table(title="[bold]Services[/bold]", border_style="dim")
        svc_table.add_column("Service", style="cyan")
        svc_table.add_column("Status", style="green")
        svc_table.add_column("Routes", justify="right")
        svc_table.add_column("Description", style="dim")

        for svc in services:
            svc_table.add_row(
                svc["name"],
                f"[green]●[/green] {svc['status']}",
                str(svc["route_count"]),
                svc["description"],
            )
        console.print(svc_table)


@cli.command()
def status():
    """Show emulator health, registered services, and resource counts."""
    import httpx

    port = os.environ.get("IBMCLOUD_LOCAL_PORT", DEFAULT_PORT)
    try:
        resp = httpx.get(f"http://localhost:{port}/api/dashboard")
        data = resp.json()
    except httpx.ConnectError:
        console.print("[red]● Emulator is not running[/red]")
        return

    console.print(Panel.fit(
        f"[bold green]● Emulator is running[/bold green]\n"
        f"Version: {data.get('version', 'unknown')}\n"
        f"Routes:  {data.get('total_routes', 0)}",
        title="ibmcloud-local",
        border_style="green",
    ))

    # Resource counts
    state = data.get("state_summary", {})
    if state:
        console.print("\n[bold]Resource Counts:[/bold]")
        for ns, count in sorted(state.items()):
            console.print(f"  {ns:20s} {count}")
    else:
        console.print("\n[dim]No resources created yet[/dim]")


@cli.command()
def env():
    """
    Print environment variables to point IBM Cloud tools at the emulator.

    Usage:
        eval $(ibmcloud-local env)

    This sets the endpoint overrides so the IBM Cloud CLI, Terraform provider,
    and Python/Go SDKs talk to the local emulator instead of real IBM Cloud.
    """
    port = os.environ.get("IBMCLOUD_LOCAL_PORT", DEFAULT_PORT)
    base = f"http://localhost:{port}"

    # These are the actual env vars the IBM Cloud SDK/CLI respects
    env_vars = {
        "IBMCLOUD_VPC_API_ENDPOINT": base,
        "IBMCLOUD_IAM_API_ENDPOINT": base,
        "IBM_COS_ENDPOINT": base,
    }

    for key, value in env_vars.items():
        # Output in shell-sourceable format
        click.echo(f"export {key}={value}")

    # Also print a human-readable summary to stderr (so eval doesn't capture it)
    console.print("\n[dim]# Run: eval $(ibmcloud-local env)[/dim]", stderr=True)


def _show_static_routes():
    """Fallback: show routes by importing providers directly (no running server needed)."""
    from src.providers.vpc import VpcProvider

    table = Table(title="[bold]Static Route Definitions[/bold]", border_style="dim")
    table.add_column("Method", style="bold", width=8)
    table.add_column("Path", style="green")
    table.add_column("Service", style="yellow")

    provider = VpcProvider()
    for route in provider.get_route_info():
        table.add_row(route["method"], route["path"], route["service"])

    console.print(table)


# ── __main__ support so you can run: python -m cli.ibmcloud_local ────
if __name__ == "__main__":
    cli()
