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


if __name__ == "__main__":
    app()
