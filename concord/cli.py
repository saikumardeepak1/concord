"""Command-line entry points: serve, index, eval, repl."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import typer
import uvicorn
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from concord.config import get_settings
from concord.customers import CustomerNotFoundError, get_directory
from concord.models import SupportRequest
from concord.orchestrator import Concord
from concord.retrieval.service import get_retrieval_service
from concord.state import init_db

app = typer.Typer(no_args_is_help=True, add_completion=False, help="Concord CLI")
console = Console()


@app.command()
def serve(
    host: str = typer.Option(None, help="Bind host (default from settings)."),
    port: int = typer.Option(None, help="Bind port (default from settings)."),
    reload: bool = typer.Option(False, help="Reload on file changes (dev only)."),
) -> None:
    """Start the HTTP API + web UI."""
    settings = get_settings()
    uvicorn.run(
        "concord.api:app",
        host=host or settings.host,
        port=port or settings.port,
        reload=reload,
        log_level=settings.log_level.lower(),
    )


@app.command("index")
def index_cmd(
    path: Path = typer.Option(None, help="Knowledge directory (default from settings)."),
) -> None:
    """Index (or re-index) the knowledge base."""

    async def _go() -> None:
        await init_db()
        n = get_retrieval_service().index_knowledge_dir(path)
        console.print(f"[green]indexed[/green] {n} chunks")

    asyncio.run(_go())


@app.command()
def ask(
    message: str = typer.Argument(..., help="The customer's message."),
    customer_id: str = typer.Option(
        "cust-001",
        "--customer-id",
        "-c",
        help=(
            "Verified customer ID. Valid demo IDs: cust-001 (Alice / Pro active), "
            "cust-002 (Bob / Pro suspended), cust-003 (Carol / past-due), "
            "cust-004 (Dave / Enterprise), cust-005 (Eve / Free), "
            "cust-006 (Frank / churned). Any other ID is rejected at the "
            "identity-verification gate. See `concord customers` or "
            "docs/DEMO_SCENARIOS.md."
        ),
    ),
) -> None:
    """Submit a single support request and print the response."""

    async def _go() -> None:
        await init_db()
        get_retrieval_service().index_knowledge_dir()
        try:
            record = get_directory().verify(customer_id)
        except CustomerNotFoundError as exc:
            console.print(Panel.fit(
                f"[red]Identity verification failed.[/red]\n\n{exc}",
                title="customer_not_found",
                border_style="red",
            ))
            raise typer.Exit(code=1) from None
        concord = Concord()
        request = SupportRequest(
            customer=record.to_context(),
            message=message,
        )
        response = await concord.handle_request(request)
        console.print(Panel.fit(response.response_text, title=response.outcome.value))
        table = Table(title="Citations", show_header=True, header_style="bold")
        table.add_column("Source")
        table.add_column("Score", justify="right")
        for c in response.citations:
            table.add_row(c.source, f"{c.score:.2f}")
        if response.citations:
            console.print(table)
        console.print(f"[dim]customer: {record.name} ({record.customer_id}) · "
                      f"plan={record.plan} · status={record.account_status}[/dim]")
        console.print(f"[dim]trace: {response.trace_id}[/dim]")

    asyncio.run(_go())


@app.command()
def customers() -> None:
    """List the verified demo customers and their state.

    Use this to know which customer_id to pass to `concord ask -c ...`.
    Any other customer_id is rejected at the identity-verification gate,
    which is the demo's stand-in for what a real auth flow would enforce.
    """
    directory = get_directory()
    table = Table(title="Demo customer directory", show_header=True, header_style="bold")
    table.add_column("ID", style="cyan")
    table.add_column("Name")
    table.add_column("Plan")
    table.add_column("Status")
    table.add_column("Tenure")
    table.add_column("Charges")
    for cid in directory.known_customer_ids():
        rec = directory.verify(cid)
        table.add_row(
            rec.customer_id,
            rec.name,
            rec.plan,
            rec.account_status,
            f"{rec.tenure_days}d",
            str(len(rec.transactions)),
        )
    console.print(table)
    console.print(
        "\n[dim]Any customer_id not listed above is rejected at the "
        "identity-verification gate (the demo's stand-in for real auth). "
        "See docs/DEMO_SCENARIOS.md for testable failure modes.[/dim]"
    )


@app.command()
def evals(
    suite: str = typer.Option("all", help="Suite to run: happy_path|edge|adversarial|escalation|all"),
    output: Path = typer.Option(None, help="Optional JSON output path."),
) -> None:
    """Run the eval harness against the platform."""
    from evals.run import run_suite

    async def _go() -> None:
        await init_db()
        get_retrieval_service().index_knowledge_dir()
        results = await run_suite(suite)
        if output:
            output.write_text(json.dumps(results, indent=2))
        console.print(f"[bold]pass:[/bold] {results['passed']} / {results['total']}")
        for cat, stats in results["per_category"].items():
            console.print(f"  {cat}: {stats['passed']}/{stats['total']}")

    asyncio.run(_go())


if __name__ == "__main__":
    app()
