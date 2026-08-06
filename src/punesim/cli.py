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
