import os
import torch
import pandas as pd
import boto3
import mlflow
import mlflow.data
from io import StringIO, BytesIO
from contextlib import asynccontextmanager
from fastapi import FastAPI, BackgroundTasks, HTTPException
from transformers import AutoTokenizer, AutoModel
from schemas import UserRequest

os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "true"

S3_BUCKET = os.getenv("S3_BUCKET")
S3_CSV_KEY = os.getenv("S3_CSV_KEY")
MODEL_NAME = os.getenv("MODEL_NAME", "intfloat/multilingual-e5-small")
MLFLOW_URI = os.getenv("MLFLOW_TRACKING_URI", "http://your-mlflow-server:5000")

mlflow.set_tracking_uri(MLFLOW_URI)
mlflow.set_experiment("Savely_Spends_Production_API")

ml_models = {}


def get_embedding(text, model, tokenizer):
    """Генерация точного нормализованного вектора для модели E5"""
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


def download_csv_from_s3_to_memory() -> str:
    """Скачивание файла из S3 напрямую в RAM в виде строки"""
    if not S3_BUCKET:
        raise ValueError(
            "Критическая ошибка: Переменная окружения S3_BUCKET не задана!"
        )

    s3_client = boto3.client("s3")
    buffer = BytesIO()
    s3_client.download_fileobj(S3_BUCKET, S3_CSV_KEY, buffer)
    return buffer.getvalue().decode("utf-8")


def build_vector_space_worker(run_name="manual_data_reload", context="dynamic_reload"):
    """
    Фоновый воркер: скачивает CSV, строит векторную базу в RAM,
    логирует метаданные и структуру данных в MLflow Dataset,
    после чего атомарно обновляет активную модель.
    """
    tokenizer = ml_models["tokenizer"]
    model = ml_models["model"]

    with mlflow.start_run(run_name=run_name) as run:
        try:
            import time

            start_time = time.time()

            csv_content = download_csv_from_s3_to_memory()

            df = pd.read_csv(
                StringIO(csv_content), sep=None, engine="python", on_bad_lines="skip"
            )
            df.columns = df.columns.str.strip()
            df["comment"] = df["comment"].astype(str).str.strip().str.lower()
            df["category"] = df["category"].astype(str).str.strip()
            df = df.dropna(subset=["comment", "category"])

            new_embeddings = torch.cat(
                [
                    get_embedding(f"passage: {text}", model, tokenizer)
                    for text in df["comment"]
                ]
            )
            new_labels = df["category"].tolist()

            ml_models["dataset_embeddings"] = new_embeddings
            ml_models["dataset_labels"] = new_labels

            mlflow_dataset = mlflow.data.from_pandas(
                df, targets="category", name="savely_spends_base"
            )
            mlflow.log_input(mlflow_dataset, context=context)

            generation_time = time.time() - start_time
            mlflow.log_metric(
                "vector_generation_latency_sec", round(generation_time, 2)
            )
            mlflow.log_metric("total_indexed_rows", len(df))

        except Exception as e:
            print(f"Критическая ошибка при фоновом обновлении векторов: {str(e)}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Шаг 1: Загружаем модель Hugging Face один раз при старте контейнера
    print("Инициализация API: Загрузка модели Hugging Face...")
    tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME)
    model = AutoModel.from_pretrained(MODEL_NAME)
    ml_models["tokenizer"] = tokenizer
    ml_models["model"] = model

    # Шаг 2: Холодный старт — синхронно качаем базу с S3 в RAM, чтобы API не открылся пустым
    print("Первоначальный запуск: Построение векторного пространства...")
    build_vector_space_worker(run_name="api_cold_start_s3", context="cold_start")

    yield
    # Очистка памяти при выключении приложения
    ml_models.clear()


app = FastAPI(lifespan=lifespan)


@app.post("/predict")
def predict_category_api(request: UserRequest):
    if "dataset_embeddings" not in ml_models:
        raise HTTPException(
            status_code=503,
            detail="Векторная база данных инициализируется. Попробуйте позже.",
        )

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


@app.post("/reload_data")
def reload_data(background_tasks: BackgroundTasks):
    """
    Эндпоинт для ручного или автоматического обновления базы.
    FastAPI сразу возвращает JSON-ответ, не заставляя вас ждать завершения вычислений,
    а сам процесс векторизации и логирования в MLflow уходит выполняться на фоне.
    """
    background_tasks.add_task(
        build_vector_space_worker, "manual_api_reload", "manual_trigger"
    )
    return {
        "status": "Процесс обновления векторной базы успешно запущен в фоновом режиме."
    }
