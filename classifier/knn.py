import os
import torch
import pandas as pd
from contextlib import asynccontextmanager
from fastapi import FastAPI
from transformers import AutoTokenizer, AutoModel
from schemas import UserRequest

os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "true"

ml_models = {}


def get_embedding(text, model, tokenizer):
    """Получение точного нормализованного вектора с учетом маски внимания для E5"""
    inputs = tokenizer(
        text, padding=True, truncation=True, max_length=64, return_tensors="pt"
    )
    with torch.no_grad():
        outputs = model(**inputs)

    attention_mask = inputs["attention_mask"]
    token_embeddings = outputs.last_hidden_state
    input_mask_expanded = (
        attention_mask.unsqueeze(-1).expand(token_embeddings.size()).float()
    )
    sum_embeddings = torch.sum(token_embeddings * input_mask_expanded, 1)
    sum_mask = torch.clamp(input_mask_expanded.sum(1), min=1e-9)
    embeddings = sum_embeddings / sum_mask

    return embeddings / embeddings.norm(dim=-1, keepdim=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    model_name = "intfloat/multilingual-e5-small"
    tokenizer = AutoTokenizer.from_pretrained(model_name)
    model = AutoModel.from_pretrained(model_name)

    csv_path = "savely_spends.csv"
    if not os.path.exists(csv_path):
        raise FileNotFoundError("Problem with filepath. No file found")

    df = pd.read_csv(csv_path, sep=None, engine="python", on_bad_lines="skip")
    df.columns = df.columns.str.strip()

    df["comment"] = df["comment"].astype(str).str.strip().str.lower()
    df["category"] = df["category"].astype(str).str.strip()
    df = df.dropna(subset=["comment", "category"])
    dataset_embeddings = torch.cat(
        [get_embedding(f"passage: {text}", model, tokenizer) for text in df["comment"]]
    )

    ml_models["tokenizer"] = tokenizer
    ml_models["model"] = model
    ml_models["dataset_embeddings"] = dataset_embeddings
    ml_models["dataset_labels"] = df["category"].tolist()
    ml_models["dataset_texts"] = df["comment"].tolist()
    yield
    ml_models.clear()


app = FastAPI(lifespan=lifespan)


@app.post("/predict")
def predict_category_api(request: UserRequest):
    tokenizer = ml_models["tokenizer"]
    model = ml_models["model"]
    dataset_embeddings = ml_models["dataset_embeddings"]
    dataset_labels = ml_models["dataset_labels"]

    clean_word = request.word.strip().lower()
    word_embedding = get_embedding(f"query: {clean_word}", model, tokenizer)
    similarities = torch.matmul(word_embedding, dataset_embeddings.T).squeeze(0)
    best_match_idx = torch.argmax(similarities).item()
    best_similarity = similarities[best_match_idx].item()

    predicted_category = dataset_labels[best_match_idx]
    confidence = round(((best_similarity + 1) / 2) * 100, 2)

    if best_similarity < 0.40:
        predicted_category = "Другое"

    return {"category": predicted_category, "confidence": confidence}
