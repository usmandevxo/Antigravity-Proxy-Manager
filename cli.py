#!/usr/bin/env python3
"""
AGPM CLI - Command line interface for Antigravity Proxy Manager.
"""

import sys
import os
import json
import time
import argparse
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich import print as rprint

import core
import proxy

console = Console()

def is_port_in_use(port):
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(('127.0.0.1', port)) == 0

def show_status():
    """Show overall system status."""
    config = core.load_config()
    portal_cfg = core.get_portal_config()
    proxy_cfg = core.get_proxy_config()
    accounts = core.get_accounts()
    
    # Proxy Status
    proxy_running = proxy.is_proxy_running()
    proxy_status_text = "[bold green]Running[/bold green]" if proxy_running else "[bold red]Stopped[/bold red]"
    
    # Portal Status (check if port is in use as a proxy for running)
    portal_running = is_port_in_use(portal_cfg['port'])
    portal_status_text = "[bold green]Running[/bold green]" if portal_running else "[bold red]Stopped[/bold red]"

    status_table = Table(title="AGPM System Status", box=None)
    status_table.add_column("Component", style="cyan")
    status_table.add_column("Status", style="white")
    status_table.add_column("Port", style="magenta")
    
    status_table.add_row("AGPM Unified Server", portal_status_text, str(portal_cfg['port']))
    
    console.print(Panel(status_table, expand=False, border_style="bold blue"))
    
    # Account Summary
    active_count = len([a for a in accounts if a['status'] == 'active'])
    rprint(f"  [bold]Accounts:[/bold] {len(accounts)} total, {active_count} active")
    rprint(f"  [bold]API Endpoint:[/bold] http://127.0.0.1:{portal_cfg['port']}/v1")

def list_accounts():
    """List all accounts in a table."""
    accounts = core.get_accounts()
    if not accounts:
        rprint("[yellow]No accounts found.[/yellow]")
        return

    table = Table(title="AGPM Account Fleet", header_style="bold magenta")
    table.add_column("Email", style="cyan", no_wrap=True)
    table.add_column("Status", style="bold")
    table.add_column("Provider", style="green")
    table.add_column("Active", justify="center")
    table.add_column("Last Used", style="dim")
    table.add_column("Quota (Approx)", justify="right")

    for acc in accounts:
        status_style = "green" if acc['status'] == 'active' else "red" if acc['status'] == 'rejected' else "yellow"
        is_active = "✅" if acc.get('is_active') else ""
        
        # Format last used
        last_used = "Never"
        if acc.get('last_used'):
            last_used = time.strftime('%Y-%m-%d %H:%M', time.localtime(acc['last_used']/1000))
            
        # Format quota
        quota_text = "N/A"
        models = acc.get('quota', {}).get('models', {})
        if models:
            # Show percentage of the first model as a representative
            first_model = next(iter(models.values()))
            perc = first_model.get('percentage', 0)
            quota_text = f"{perc}%"

        table.add_row(
            acc['email'],
            f"[{status_style}]{acc['status'].upper()}[/{status_style}]",
            acc['provider'],
            is_active,
            last_used,
            quota_text
        )

    console.print(table)

def add_account_manual():
    """Add an account manually via refresh token."""
    email = console.input("[bold cyan]Enter Email: [/bold cyan]")
    token = console.input("[bold cyan]Enter Refresh Token: [/bold cyan]")
    
    with console.status("[bold green]Verifying account..."):
        success = core.add_account(email, token)
        
    if success:
        rprint(f"[bold green]Successfully added {email}![/bold green]")
        # Try to refresh quota immediately
        with console.status("[bold green]Fetching initial quota..."):
            msg = core.refresh_account_quota(email)
            rprint(f"  [dim]{msg}[/dim]")
    else:
        rprint("[bold red]Failed to add account. It might already exist.[/bold red]")

def remove_account(email):
    """Remove an account by email."""
    if not email:
        rprint("[bold red]Error: Email is required.[/bold red]")
        return
        
    if console.input(f"Are you sure you want to remove [bold red]{email}[/bold red]? (y/N): ").lower() == 'y':
        success = core.remove_account(email)
        if success:
            rprint(f"[bold green]Successfully removed {email}.[/bold green]")
        else:
            rprint(f"[bold red]Account {email} not found.[/bold red]")

def refresh_accounts(email=None):
    """Refresh quota for one or all accounts."""
    accounts = core.get_accounts()
    if email:
        targets = [a for a in accounts if a['email'] == email]
        if not targets:
            rprint(f"[bold red]Account {email} not found.[/bold red]")
            return
    else:
        targets = accounts

    if not targets:
        rprint("[yellow]No accounts to refresh.[/yellow]")
        return

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console
    ) as progress:
        for acc in targets:
            task_id = progress.add_task(description=f"Refreshing {acc['email']}...", total=1)
            msg = core.refresh_account_quota(acc['email'])
            progress.update(task_id, completed=1, description=f"Refreshed {acc['email']}: [dim]{msg}[/dim]")

def set_active(email):
    """Set an account as active."""
    success = core.set_active_account(email)
    if success:
        rprint(f"[bold green]Account {email} is now the primary active account.[/bold green]")
    else:
        rprint(f"[bold red]Failed to set {email} as active. Check if the email exists.[/bold red]")

def manage_proxy(action):
    """Start or stop the proxy server."""
    if action == "start":
        config = core.get_proxy_config()
        msg = proxy.start_proxy(config['port'])
        rprint(f"[bold green]{msg}[/bold green]")
        if proxy.is_proxy_running():
            rprint("[dim]Press Ctrl+C to stop the proxy (in this session) or run 'cli.py proxy stop' later.[/dim]")
            try:
                while True:
                    time.sleep(1)
            except KeyboardInterrupt:
                proxy.stop_proxy()
                rprint("\n[bold yellow]Proxy stopped.[/bold yellow]")
    elif action == "stop":
        # This only works if the proxy was started in THIS process or we find the PID
        # For now, we'll just report that CLI-managed proxy is stopped
        msg = proxy.stop_proxy()
        rprint(f"[bold yellow]{msg}[/bold yellow]")

def restart_services():
    """Restart systemd services."""
    import subprocess
    try:
        with console.status("[bold yellow]Restarting AGPM services..."):
            subprocess.run(["systemctl", "--user", "restart", "agpm-web.service"], check=True)
        rprint("[bold green]Successfully restarted AGPM services![/bold green]")
    except Exception as e:
        rprint(f"[bold red]Failed to restart services: {e}[/bold red]")

def register_command():
    """Register 'agpm' as a global command in ~/.local/bin."""
    bin_dir = os.path.expanduser("~/.local/bin")
    os.makedirs(bin_dir, exist_ok=True)
    
    executable_path = os.path.join(bin_dir, "agpm")
    cli_path = os.path.abspath(__file__)
    
    script_content = f'#!/bin/bash\npython3 "{cli_path}" "$@"\n'
    
    try:
        with open(executable_path, "w") as f:
            f.write(script_content)
        os.chmod(executable_path, 0o755)
        rprint(f"[bold green]Successfully registered 'agpm' command![/bold green]")
        rprint(f"[dim]You can now use 'agpm status', 'agpm accounts list', etc.[/dim]")
    except Exception as e:
        rprint(f"[bold red]Failed to register command: {e}[/bold red]")

def interactive_menu():
    """Main interactive menu loop."""
    while True:
        console.clear()
        rprint(Panel("[bold cyan]🌌 AGPM - Antigravity Proxy Manager[/bold cyan]\n[dim]Interactive Management Console[/dim]", border_style="cyan"))
        
        show_status()
        rprint("\n[bold]Main Menu:[/bold]")
        rprint(" [1] [cyan]Account Fleet[/cyan] - List, Add, Remove, Refresh")
        rprint(" [2] [green]Proxy Control[/green] - Start/Stop Server")
        rprint(" [3] [magenta]Registration[/magenta] - Update 'agpm' command")
        rprint(" [0] [red]Exit[/red]")
        
        choice = console.input("\n[bold cyan]Select an option: [/bold cyan]")
        
        if choice == "1":
            account_menu()
        elif choice == "2":
            proxy_menu()
        elif choice == "3":
            register_command()
            console.input("\nPress Enter to continue...")
        elif choice == "0":
            rprint("[yellow]Goodbye![/yellow]")
            break
        else:
            rprint("[red]Invalid choice. Try again.[/red]")
            time.sleep(1)

def account_menu():
    """Sub-menu for account management."""
    while True:
        console.clear()
        rprint(Panel("[bold magenta]👤 Account Management[/bold magenta]", border_style="magenta"))
        list_accounts()
        
        rprint("\n[bold]Account Options:[/bold]")
        rprint(" [1] Add New Account (Refresh Token)")
        rprint(" [2] Refresh All Quotas")
        rprint(" [3] Set Primary (Active) Account")
        rprint(" [4] Remove Account")
        rprint(" [0] Back to Main Menu")
        
        choice = console.input("\n[bold magenta]Select an option: [/bold magenta]")
        
        if choice == "1":
            add_account_manual()
            console.input("\nPress Enter to continue...")
        elif choice == "2":
            refresh_accounts()
            console.input("\nPress Enter to continue...")
        elif choice == "3":
            email = console.input("[bold cyan]Enter Email to set active: [/bold cyan]")
            set_active(email)
            console.input("\nPress Enter to continue...")
        elif choice == "4":
            email = console.input("[bold red]Enter Email to remove: [/bold red]")
            remove_account(email)
            console.input("\nPress Enter to continue...")
        elif choice == "0":
            break

def proxy_menu():
    """Sub-menu for proxy control."""
    while True:
        console.clear()
        rprint(Panel("[bold green]🔌 Proxy Control[/bold green]", border_style="green"))
        
        # Check current status
        config = core.get_proxy_config()
        is_running = proxy.is_proxy_running()
        status_text = "[bold green]RUNNING[/bold green]" if is_running else "[bold red]STOPPED[/bold red]"
        
        rprint(f"Current Status: {status_text} on port [bold cyan]{config['port']}[/bold cyan]\n")
        
        rprint(" [1] Start Proxy Server")
        rprint(" [2] Stop Proxy Server")
        rprint(" [0] Back to Main Menu")
        
        choice = console.input("\n[bold green]Select an option: [/bold green]")
        
        if choice == "1":
            manage_proxy("start")
            console.input("\nPress Enter to return to menu...")
        elif choice == "2":
            manage_proxy("stop")
            console.input("\nPress Enter to continue...")
        elif choice == "0":
            break

def main():
    parser = argparse.ArgumentParser(description="AGPM Command Line Interface")
    subparsers = parser.add_subparsers(dest="command", help="Command to execute")

    # Status command
    subparsers.add_parser("status", help="Show system status")

    # Register command
    subparsers.add_parser("register", help="Register 'agpm' as a global command")

    # Accounts command
    acc_parser = subparsers.add_parser("accounts", help="Manage accounts")
    acc_sub = acc_parser.add_subparsers(dest="acc_command", help="Account subcommand")
    acc_sub.add_parser("list", help="List all accounts")
    acc_sub.add_parser("add", help="Add a new account manually")
    
    rem_parser = acc_sub.add_parser("remove", help="Remove an account")
    rem_parser.add_argument("email", help="Email of the account to remove")
    
    act_parser = acc_sub.add_parser("active", help="Set an account as active")
    act_parser.add_argument("email", help="Email of the account to set active")
    
    ref_parser = acc_sub.add_parser("refresh", help="Refresh account quota")
    ref_parser.add_argument("--email", help="Specific email to refresh (optional)")

    log_parser = acc_sub.add_parser("login", help="Login Antigravity IDE with an account")
    log_parser.add_argument("--email", help="Email of the account")
    log_parser.add_argument("--token", help="Refresh token of the account")

    # Proxy command
    proxy_parser = subparsers.add_parser("proxy", help="Manage proxy server")
    proxy_parser.add_argument("action", choices=["start", "stop"], help="Action to perform")

    # Restart command
    subparsers.add_parser("restart", help="Restart all AGPM services")

    args = parser.parse_args()

    if args.command == "status":
        show_status()
    elif args.command == "restart":
        restart_services()
    elif args.command == "register":
        register_command()
    elif args.command == "accounts":
        if args.acc_command == "list":
            list_accounts()
        elif args.acc_command == "add":
            add_account_manual()
        elif args.acc_command == "remove":
            remove_account(args.email)
        elif args.acc_command == "active":
            set_active(args.email)
        elif args.acc_command == "refresh":
            refresh_accounts(args.email)
        elif args.acc_command == "login":
            if args.token and args.email:
                with console.status("[bold green]Injecting token into Antigravity IDE..."):
                    success = core.inject_token_to_ide(args.email, args.token)
                if success:
                    rprint(f"[bold green]Successfully logged into Antigravity IDE as {args.email}![/bold green]")
                    rprint("[dim]Please restart your Antigravity IDE to apply changes.[/dim]")
                else:
                    rprint("[bold red]Failed to inject token. Make sure Antigravity IDE is installed and its database is accessible.[/bold red]")
            else:
                rprint("[bold red]Error: Both --email and --token are required.[/bold red]")
        else:
            acc_parser.print_help()
    elif args.command == "proxy":
        manage_proxy(args.action)
    elif args.command is None:
        interactive_menu()
    else:
        parser.print_help()

if __name__ == "__main__":
    main()
