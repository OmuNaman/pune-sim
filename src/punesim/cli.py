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
    scenes: bool = typer.Option(False, "--scenes", help="Fire LLM morning scenes (uses .env key)"),
    k: int = typer.Option(5, help="Households per morning under the spotlight gate"),
    inject: str = typer.Option(None, help="JSON file of injections [{day,time,type,place,participants,severity}]"),
    hazards: bool = typer.Option(True, "--hazards/--no-hazards", help="Sample random hazards (V1 un-injected ripples)"),
    follow: list[str] = typer.Option(None, "--follow", help="Household or person id rendered every day, ADDITIVE to k (repeatable; raises cost and narrows coverage of everyone else)"),
    talk: bool = typer.Option(True, "--talk/--no-talk", help="Render the day's one cross-household exchange (needs --scenes; one extra call/day)"),
) -> None:
    """Synthesize the Kasba block and run sim days (clockwork; --scenes adds minds)."""
    from pathlib import Path

    import orjson

    from punesim import config, engine
    from punesim.kernel.log import EventLog
    from punesim.llm import Cassette, Gateway
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

    injections = None
    if inject:
        injections = [engine.Injection.parse(o) for o in orjson.loads(Path(inject).read_bytes())]
    gateway = Gateway(cfg, Cassette(cfg.cassette_path), log=log) if scenes else None

    n, _state = engine.run_simulation(
        log, run_seed, block, hhs, people,
        days=days, gateway=gateway, scenes_k=k,
        scene_gate_mode=cfg.scene_gate_mode, injections=injections,
        hazards=hazards, follow=tuple(follow or ()), talk=talk,
    )
    typer.echo(f"seed={run_seed}  households={len(hhs)}  people={len(people)}")
    typer.echo(f"events committed : {n} over {days} day(s)"
               + (f"  (scenes on, k={k}, gate={cfg.scene_gate_mode})" if scenes else "  (zero LLM)"))
    if injections:
        typer.echo(f"injections       : {len(injections)}")
    if follow:
        typer.echo(f"following        : {', '.join(follow)}")
    typer.echo(f"determinism hash : {log.determinism_hash()}")
    typer.echo(f"log              : {path}")


@app.command()
def scenes(db: str = typer.Option("runs/dev/events.db")) -> None:
    """Print every rendered morning scene from the log."""
    from rich.console import Console

    from punesim.kernel.log import EventLog
    from punesim.kernel.timebase import to_datetime

    console = Console()
    log = EventLog(db)
    found = 0
    for e in log.events(type="scene.morning"):
        found += 1
        t = to_datetime(e.sim_time).strftime("%a %d %b, %H:%M")
        console.rule(f"[bold]{e.payload['household']}[/bold] — {t}")
        if e.payload.get("narration"):
            console.print(f"[italic]{e.payload['narration']}[/italic]")
        if e.payload.get("transcript"):
            console.print(e.payload["transcript"])
    if not found:
        console.print("no scenes in this log (run with --scenes)")


@app.command()
def interview(
    person_id: str = typer.Argument(help="e.g. person:005.1"),
    question: str = typer.Argument(help="what the journalist asks"),
    db: str = typer.Option("runs/dev/events.db"),
    seed: int = typer.Option(None),
    ghost: bool = typer.Option(False, "--ghost", help="observe only; the person keeps no memory"),
) -> None:
    """Pause time and talk to any resident (premium model)."""
    from rich.console import Console

    from punesim import config
    from punesim.kernel.log import EventLog
    from punesim.llm import Cassette, Gateway
    from punesim.minds.interview import interview as _interview
    from punesim.population import synthesize
    from punesim.world.block import Block

    cfg = config.from_env()
    run_seed = seed if seed is not None else cfg.run_seed
    block = Block.load()
    _, people = synthesize(run_seed, block)
    if person_id not in people:
        typer.echo(f"unknown person {person_id}")
        raise typer.Exit(1)
    log = EventLog(db)
    gateway = Gateway(cfg, Cassette(cfg.cassette_path), log=log)
    console = Console()
    p = people[person_id]
    console.print(f"[dim]interviewing {p.name} ({p.age}, {p.occupation})...[/dim]")
    answer = _interview(log, gateway, block, people, person_id, question, ghost=ghost)
    console.print(f"[bold]{p.name}:[/bold] {answer}")


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
    mine = sorted(
        (e for e in log.events() if e.payload.get("person") == person_id),
        key=lambda e: (e.sim_time, e.seq),
    )
    for e in mine:
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
def serve(
    db: str = typer.Option("runs/exam/events.db", help="Event log to view"),
    seed: int = typer.Option(None),
    port: int = typer.Option(8618),
    households: int = typer.Option(80),
) -> None:
    """Serve the map viewer at http://127.0.0.1:<port>."""
    import uvicorn

    from punesim import config
    from punesim.viewer import create_app

    cfg = config.from_env()
    run_seed = seed if seed is not None else cfg.run_seed
    application = create_app(
        db, run_seed, n_households=households,
        cfg=cfg if cfg.openrouter_api_key else None,
    )
    typer.echo(f"Pune Sim viewer -> http://127.0.0.1:{port}  (log: {db}, seed: {run_seed})")
    uvicorn.run(application, host="127.0.0.1", port=port, log_level="warning")


@app.command()
def compile(
    text: str = typer.Argument(help="free-text scenario, e.g. 'the city DM was killed in broad daylight near Shaniwar Wada on day 2 at noon'"),
    day: int = typer.Option(0, help="default day if the text names none"),
    save: str = typer.Option(None, help="append the compiled injection to this scenario JSON"),
    seed: int = typer.Option(None),
    households: int = typer.Option(80),
) -> None:
    """Compile free text into a grounded, validated injection (V2)."""
    from pathlib import Path

    import orjson
    from rich.console import Console

    from punesim import config
    from punesim.llm import Cassette, Gateway
    from punesim.minds.compiler import CompileError, compile_injection
    from punesim.population import synthesize
    from punesim.world.block import Block

    cfg = config.from_env()
    run_seed = seed if seed is not None else cfg.run_seed
    block = Block.load()
    _, people = synthesize(run_seed, block, n_households=households)
    gateway = Gateway(cfg, Cassette(cfg.cassette_path))
    console = Console()
    try:
        out = compile_injection(gateway, block, people, text, default_day=day)
    except CompileError as e:
        console.print("[red]could not compile:[/red]")
        for err in e.errors:
            console.print(f"  - {err}")
        raise typer.Exit(1) from e
    console.rule("[bold]compiled injection")
    console.print(out.preview)
    if save:
        path = Path(save)
        existing = orjson.loads(path.read_bytes()) if path.exists() else []
        inj = out.injection
        obj = {
            "day": inj.day,
            "time": f"{inj.time_s // 3600:02d}:{inj.time_s % 3600 // 60:02d}",
            "type": inj.type,
            "place": inj.place,
            "participants": list(inj.participants),
            "severity": inj.severity,
            "payload": inj.payload,
        }
        existing.append(obj)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(orjson.dumps(existing, option=orjson.OPT_INDENT_2))
        console.print(f"\nappended to {save}  ({len(existing)} injection(s)) — run it with:")
        console.print(f"  punesim run --days {max(inj.day + 2, 3)} --scenes --inject {save} --db runs/dev/events.db")


@app.command()
def branch(
    source: str = typer.Argument(help="source event log to fork, e.g. runs/exam/events.db"),
    what_if: str = typer.Argument(None, help="free-text what-if (compiled) — or use --inject"),
    inject: str = typer.Option(None, help="JSON scenario file of extra injections"),
    add_days: int = typer.Option(0, help="extend the horizon by this many days"),
    day: int = typer.Option(0, help="default day for the compiled what-if"),
    db: str = typer.Option(None, help="branch output db (default runs/branches/<n>.db)"),
    scenes: bool = typer.Option(False, "--scenes", help="LLM scenes in the branch (uses .env key)"),
    k: int = typer.Option(5),
) -> None:
    """Fork a world, add a what-if, run both timelines, and diff them (V2)."""
    from pathlib import Path

    import orjson
    from rich.console import Console

    from punesim import branch as branch_mod
    from punesim import config, engine
    from punesim.kernel.diff import diff_logs
    from punesim.kernel.log import EventLog
    from punesim.llm import Cassette, Gateway
    from punesim.minds.compiler import CompileError, compile_injection
    from punesim.population import synthesize
    from punesim.world.block import Block

    console = Console()
    cfg = config.from_env()
    block = Block.load()
    src_log = EventLog(source)
    meta = branch_mod.read_meta(src_log)
    src_log.close()
    if meta is None:
        console.print("[red]source log has no run.meta — re-run it once on this version first[/red]")
        raise typer.Exit(1)
    _, people = synthesize(int(meta["seed"]), block, n_households=int(meta["households"]))

    extra: list[engine.Injection] = []
    if inject:
        extra += [engine.Injection.parse(o) for o in orjson.loads(Path(inject).read_bytes())]
    if what_if:
        gw = Gateway(cfg, Cassette(cfg.cassette_path))
        try:
            out = compile_injection(gw, block, people, what_if, default_day=day)
        except CompileError as e:
            console.print("[red]could not compile the what-if:[/red]")
            for err in e.errors:
                console.print(f"  - {err}")
            raise typer.Exit(1) from e
        console.rule("compiled what-if")
        console.print(out.preview)
        extra.append(out.injection)
    if not extra:
        console.print("[red]nothing to inject — pass a what-if text or --inject file[/red]")
        raise typer.Exit(1)

    if db is None:
        db = f"runs/branches/{Path(source).parent.name or 'run'}-b1/events.db"
    console.print(f"[dim]branching {source} -> {db} …[/dim]")
    res = branch_mod.branch_run(
        source, db, block=block, synthesize=synthesize,
        extra_injections=extra, add_days=add_days,
        gateway=Gateway(cfg, Cassette(cfg.cassette_path)) if scenes else None,
        scenes_k=k, scene_gate_mode=cfg.scene_gate_mode,
    )
    console.print(f"branch: {res.events} events over {res.days} days ({res.injections} injections)")

    names = {p.id: p.name for p in people.values()}
    a, b = EventLog(source), EventLog(res.db_path)
    rep = diff_logs(a, b, names)
    a.close(), b.close()
    console.rule("[bold]what changed")
    for line in rep.headline:
        console.print(f"  • {line}")
    console.print(f"\n[dim]full diff: punesim diff {source} {res.db_path}[/dim]")


@app.command()
def diff(
    a: str = typer.Argument(help="event log A (source)"),
    b: str = typer.Argument(help="event log B (branch)"),
    seed: int = typer.Option(None),
    households: int = typer.Option(80),
) -> None:
    """Compare two timelines: first divergence, changed lives, deltas."""
    from rich.console import Console

    from punesim import branch as branch_mod
    from punesim import config
    from punesim.kernel.diff import diff_logs
    from punesim.kernel.log import EventLog
    from punesim.population import synthesize
    from punesim.world.block import Block

    console = Console()
    cfg = config.from_env()
    log_a, log_b = EventLog(a), EventLog(b)
    meta = branch_mod.read_meta(log_a) or {}
    run_seed = seed if seed is not None else int(meta.get("seed", cfg.run_seed))
    n_hh = int(meta.get("households", households))
    block = Block.load()
    _, people = synthesize(run_seed, block, n_households=n_hh)
    names = {p.id: p.name for p in people.values()}
    rep = diff_logs(log_a, log_b, names)
    log_a.close(), log_b.close()

    console.rule("[bold]headline")
    for line in rep.headline:
        console.print(f"  • {line}")
    if rep.identical:
        return
    fd = rep.first_divergence
    console.rule("first divergence")
    console.print(f"  day {fd['day']}, {fd['hm']}")
    console.print(f"  A: {fd['a']}")
    console.print(f"  B: {fd['b']}")
    if rep.people_changed:
        console.rule(f"changed lives ({len(rep.people_changed)})")
        for pid, days in sorted(rep.people_changed.items())[:20]:
            console.print(f"  {names.get(pid, pid):28s} days {', '.join(map(str, days))}")
        if len(rep.people_changed) > 20:
            console.print(f"  … and {len(rep.people_changed) - 20} more")
    if rep.type_deltas:
        console.rule("event deltas (B − A)")
        for t, d in rep.type_deltas.items():
            console.print(f"  {t:26s} {'+' if d > 0 else ''}{d}")


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
