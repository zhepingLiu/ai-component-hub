from pydantic import BaseModel

class AddReq(BaseModel):
    a: float
    b: float

class StdResp(BaseModel):
    code: int = 0
    message: str = "ok"
    data: dict | list | None = None
