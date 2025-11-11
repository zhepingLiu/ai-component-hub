from pydantic import BaseModel

class StdResp(BaseModel):
    code: int
    message: str = "ok"
    data: dict | list | None = None
