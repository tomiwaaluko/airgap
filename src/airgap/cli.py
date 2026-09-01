"""Console entry for Airgap. The only subcommand is the read-only watcher."""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Sequence

from airgap.watch import run_watch


def main(argv: Sequence[str] | None = None) -> None:
    args = list(sys.argv[1:] if argv is None else argv)
    if args != ["watch"]:
        raise SystemExit("usage: airgap watch")
    asyncio.run(run_watch())
