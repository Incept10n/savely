import os
import torch
import pandas as pd
from transformers import AutoTokenizer, AutoModel


def get_embedding(text, model, tokenizer):
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


def main():
    model_name = "intfloat/multilingual-e5-small"
    tokenizer = AutoTokenizer.from_pretrained(model_name)
    model = AutoModel.from_pretrained(model_name)

    csv_path = "savely_spends.csv"
    if not os.path.exists(csv_path):
        raise FileNotFoundError(f"Файл {csv_path} не найден для локальной сборки!")

    df = pd.read_csv(csv_path, sep=None, engine="python", on_bad_lines="skip")
    df.columns = df.columns.str.strip()
    df["comment"] = df["comment"].astype(str).str.strip().str.lower()
    df["category"] = df["category"].astype(str).str.strip()
    df = df.dropna(subset=["comment", "category"])

    dataset_embeddings = torch.cat(
        [get_embedding(f"passage: {text}", model, tokenizer) for text in df["comment"]]
    )

    db_data = {
        "dataset_embeddings": dataset_embeddings,
        "dataset_labels": df["category"].tolist(),
    }
    torch.save(db_data, "vector_db.pt")


if __name__ == "__main__":
    main()
