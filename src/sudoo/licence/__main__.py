"""`python -m sudoo.licence`: the bundled agreement as the web app gets it (GET /api/v1/licence/text), as JSON. The
demo (web/src/demo/backend/licence.json) is made with it; tests/test_licence.py checks the two agree."""

import json

from . import rendered

print(json.dumps(rendered(), ensure_ascii=False, indent=2))
