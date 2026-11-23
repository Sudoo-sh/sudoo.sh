"""`python -m sudoo.engines.kubernetes ...`: the engine's commands (cli.py)."""

import sys

from .cli import main

sys.exit(main())
