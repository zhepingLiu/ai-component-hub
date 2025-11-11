import yaml
from pathlib import Path
from .config import settings

class RouteTable:
    def __init__(self, path: str):
        self._file = Path(path)
        self._routes = {}
        self.reload()

    def reload(self):
        self._routes = yaml.safe_load(self._file.read_text()) or {}

    def resolve(self, category: str, action: str) -> str | None:
        return self._routes.get(f"{category}.{action}")
    
    def __setitem__(self, key: str, value: str):
        self._routes[key] = value