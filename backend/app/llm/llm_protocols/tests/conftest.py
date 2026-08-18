import sys
from pathlib import Path

# Make the in-repo package importable when running `pytest` on this tests
# directory directly (without installing llm-protocols).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
