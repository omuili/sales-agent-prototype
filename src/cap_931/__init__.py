"""Sales Account Assistant: multi-agent account research for sales reps."""

import sys
from pathlib import Path


def main() -> None:
    """Launch the Streamlit app (`uv run cap-931`)."""
    from streamlit.web import cli as stcli

    sys.argv = ["streamlit", "run", str(Path(__file__).with_name("app.py")), *sys.argv[1:]]
    sys.exit(stcli.main())


def alerts_main() -> None:
    """Run every keyword watch once (`uv run cap-931-alerts`), for cron or another scheduler."""
    from .alerts import run_all

    run_all()
