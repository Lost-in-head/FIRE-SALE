"""
Pytest configuration — adds the backend directory to sys.path so all
backend modules can be imported without installing a package.
"""

import sys
from pathlib import Path

# Ensure `import db`, `import add_lead`, etc. work from any directory.
BACKEND_DIR = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND_DIR))
