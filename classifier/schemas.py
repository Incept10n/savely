from pydantic import BaseModel, Field


class UserRequest(BaseModel):
    word: str = Field(min_length=1, description="Слово не должно быть пустым")


class PredictResponse(BaseModel):
    label: str
    confidence: float
