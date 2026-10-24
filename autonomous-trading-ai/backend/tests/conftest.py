import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("AI_PROVIDER", "none")
os.environ.setdefault("AGENT_AUTOSTART", "false")
os.environ.setdefault("ADMIN_PASSWORD", "test-password-123")
