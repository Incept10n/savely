from pydantic import BaseModel


class UserRequest(BaseModel):
    word: str


class PredictResponse(BaseModel):
    label: str
    confidence: float
