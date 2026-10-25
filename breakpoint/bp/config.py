import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"


def _load_dotenv() -> None:
    env = ROOT / ".env"
    if not env.exists():
        return
    for line in env.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.split("#", 1)[0].strip())


_load_dotenv()

DATABASE_URL = os.environ.get("BP_DATABASE_URL", "postgresql+psycopg://bp:bp@localhost:5433/breakpoint")
MODEL = os.environ.get("BP_MODEL", "claude-opus-5-5")
SEED = int(os.environ.get("SEED", "42"))
APPROVAL_LIMIT_USD = 2500  # the approver agent may approve credits up to this from an active playbook


def world_dir(seed: int = SEED, clean: bool = False) -> Path:
    return DATA / "world" / (f"seed{seed}" + ("-clean" if clean else ""))
