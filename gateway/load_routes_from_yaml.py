import sys
from pathlib import Path

import yaml

BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.append(str(BASE_DIR))

from app.config import settings
from app.route_table import RouteTable


def main():
    routes_path = Path(settings.ROUTE_FILE)
    if not routes_path.exists():
        fallback = BASE_DIR / "routes.yaml"
        if fallback.exists():
            routes_path = fallback
        else:
            raise FileNotFoundError(f"Routes file not found: {routes_path}")

    with routes_path.open("r") as f:
        routes = yaml.safe_load(f) or {}

    print(f"Loaded {len(routes)} routes from {routes_path}: {routes}")

    table = RouteTable(settings=settings)
    for key, value in routes.items():
        table[key] = value

    print(f"Successfully imported into Redis key '{table.redis_key}'")


if __name__ == "__main__":
    main()
