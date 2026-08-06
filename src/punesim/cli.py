"""Console entry point. Commands grow with each slice (V0: run, replay, inject, interview)."""

import typer

app = typer.Typer(no_args_is_help=True)


@app.callback()
def main() -> None:
    """Pune Sim console."""


@app.command()
def version() -> None:
    """Print the installed version."""
    from punesim import __version__

    typer.echo(f"punesim {__version__}")


@app.command()
def run(
    days: int = typer.Option(1, help="Sim days to run"),
    db: str = typer.Option("runs/dev/events.db", help="Event log path (recreated)"),
    seed: int = typer.Option(None, help="Run seed; default from .env"),
    households: int = typer.Option(80, help="Household count"),
) -> None:
    """Synthesize the Kasba block and run clockwork days (zero LLM calls)."""
    from pathlib import Path

    from punesim import config, engine
    from punesim.kernel.log import EventLog
    from punesim.population import synthesize
    from punesim.world.block import Block

    cfg = config.from_env()
    run_seed = seed if seed is not None else cfg.run_seed
    path = Path(db)
    path.parent.mkdir(parents=True, exist_ok=True)
    for suffix in ("", "-wal", "-shm"):
        p = Path(str(path) + suffix)
        if p.exists():
            p.unlink()

    block = Block.load()
    hhs, people = synthesize(run_seed, block, n_households=households)
    log = EventLog(path)
    n = engine.run_days(log, run_seed, block, people, days=days)
    typer.echo(f"seed={run_seed}  households={len(hhs)}  people={len(people)}")
    typer.echo(f"events committed : {n} over {days} day(s)")
    typer.echo(f"determinism hash : {log.determinism_hash()}")
    typer.echo(f"log              : {path}")


@app.command()
def follow(
    person_id: str = typer.Argument(help="e.g. person:012.1 (try `punesim census` for ids)"),
    db: str = typer.Option("runs/dev/events.db"),
    seed: int = typer.Option(None),
) -> None:
    """Print a person's card and their day as a timeline from the event log."""
    from rich.console import Console

    from punesim import config
    from punesim.kernel.log import EventLog
    from punesim.kernel.timebase import to_datetime
    from punesim.population import synthesize
    from punesim.world.block import Block

    cfg = config.from_env()
    run_seed = seed if seed is not None else cfg.run_seed
    block = Block.load()
    _, people = synthesize(run_seed, block)
    person = people.get(person_id)
    console = Console()
    if person is None:
        console.print(f"[red]unknown person {person_id}[/red]")
        raise typer.Exit(1)

    def place_name(pid: str) -> str:
        p = block.get(pid)
        return p.name if p and p.name else pid

    console.print(
        f"[bold]{person.name}[/bold] ({person.age}, {person.occupation}) — "
        f"household {person.household_id}, home {place_name(person.home_id)}"
    )
    log = EventLog(db)
    for e in log.events():
        if e.payload.get("person") != person_id:
            continue
        t = to_datetime(e.sim_time).strftime("%a %H:%M")
        if e.type == "trip.start":
            console.print(f"  {t}  walks from {place_name(e.payload['from'])} to {place_name(e.payload['to'])} ({e.payload['purpose']})")
        elif e.type == "trip.end":
            console.print(f"  {t}  arrives at {place_name(e.payload['at'])}")
        elif e.type == "activity.start":
            console.print(f"  {t}  {e.payload['activity']} at {place_name(e.payload['at'])}")
        else:
            console.print(f"  {t}  {e.type} {e.payload}")


@app.command()
def census(seed: int = typer.Option(None), households: int = typer.Option(80)) -> None:
    """Summarize the synthesized population (regenerated from the seed)."""
    from collections import Counter

    from punesim import config
    from punesim.population import synthesize
    from punesim.world.block import Block

    cfg = config.from_env()
    run_seed = seed if seed is not None else cfg.run_seed
    block = Block.load()
    hhs, people = synthesize(run_seed, block, n_households=households)
    typer.echo(f"seed={run_seed}: {len(hhs)} households, {len(people)} people")
    typer.echo(f"templates : {dict(Counter(h.template for h in hhs))}")
    typer.echo(f"religion  : {dict(Counter(p.religion for p in people.values()))}")
    typer.echo(f"occupation: {dict(Counter(p.occupation for p in people.values()).most_common(12))}")
    sample = list(people.values())[:6]
    for p in sample:
        typer.echo(f"  {p.id}  {p.name}, {p.age}, {p.occupation}")


@app.command()
def doctor() -> None:
    """Check environment: keys present, mode, models, cassette path."""
    from punesim import config

    cfg = config.from_env()
    typer.echo(f"llm_mode        : {cfg.llm_mode}")
    typer.echo(f"scene_gate_mode : {cfg.scene_gate_mode}")
    typer.echo(f"openrouter key  : {'set' if cfg.openrouter_api_key else 'MISSING (copy .env.example to .env)'}")
    typer.echo(f"base_url        : {cfg.openrouter_base_url}")
    typer.echo(f"workhorse       : {cfg.model_workhorse}")
    typer.echo(f"flash           : {cfg.model_flash}")
    typer.echo(f"premium         : {cfg.model_premium}")
    typer.echo(f"cassettes       : {cfg.cassette_path}")
    typer.echo(f"run_seed        : {cfg.run_seed}")


if __name__ == "__main__":
    app()
