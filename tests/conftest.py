import os
from pathlib import Path

# Tests always run against the fictional example profile, never a real one.
os.environ["PROFILE_DIR"] = str(Path(__file__).parent.parent / "profile/example")
