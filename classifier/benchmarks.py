import asyncio
from datetime import datetime
import time
import requests
import httpx

URL = "http://127.0.0.1:8000/predict"
MOCK_DATA = [
    "горшочек",
    "булочка",
    "грунт",
    "прическа",
    "Элка",
    "Каршеринг",
    "умывание и крем для лица.",
    "ozon",
    "невалинк",
    "Горшок под цветы",
]


DELAY_SECONDS = 1.0


def get_current_time():
    """Возвращает текущее время в формате ЧЧ:ММ:СС.ммм"""
    return datetime.now().strftime("%H:%M:%S.%f")[:-3]


async def send_request_async(client, text, request_id):
    payload = {"word": text}

    # Логируем отправку
    start_time = time.perf_counter()
    print(f"🚀 [{get_current_time()}] Запрос #{request_id} ОТПРАВЛЕН | Текст: '{text}'")

    try:
        response = await client.post(URL, json=payload)
        end_time = time.perf_counter()
        latency_ms = (end_time - start_time) * 1000

        if response.status_code == 200:
            data = response.json()
            category = data.get("category", "Неизвестно")
            # Логируем успешное завершение
            print(
                f"✅ [{get_current_time()}] Запрос #{request_id} ЗАВЕРШЕН  | Ответ за {latency_ms/1000:.2f} сек | Класс: {category}"
            )
            return latency_ms
        else:
            print(
                f"❌ [{get_current_time()}] Запрос #{request_id} ОШИБКА     | Код сервера: {response.status_code}"
            )
            return None

    except Exception as e:
        print(
            f"💥 [{get_current_time()}] Запрос #{request_id} СБОЙ СЕТИ   | Ошибка: {e}"
        )
        return None


async def run_spaced_benchmark():
    timeout = httpx.Timeout(60.0, connect=10.0)

    async with httpx.AsyncClient(timeout=timeout) as client:
        print("⏳ Прогрев модели...")
        try:
            await client.post(URL, json={"word": "прогрев"})
        except Exception:
            print("❌ Ошибка: Сервер FastAPI не запущен! Сначала запустите main.py.")
            return

        print(
            f"\n🏃 Имитация нагрузки: отправка каждые {DELAY_SECONDS} сек. Ждем обработку...\n"
        )

        tasks = []
        total_start = time.perf_counter()

        for i, text in enumerate(MOCK_DATA, 1):
            # Создаем асинхронную задачу на отправку (она начинает выполняться в фоне)
            task = asyncio.create_task(send_request_async(client, text, i))
            tasks.append(task)

            # Делаем паузу перед спавном следующего запроса, не дожидаясь ответа текущего
            if i < len(MOCK_DATA):
                await asyncio.sleep(DELAY_SECONDS)

        # Ждем, пока абсолютно все запущенные запросы завершатся (так как они долгие)
        latencies = await asyncio.gather(*tasks)

        total_duration = time.perf_counter() - total_start
        valid_latencies = [l for l in latencies if l is not None]

        print("\n" + "=" * 50)
        print("📊 ИТОГОВАЯ СТАТИСТИКА ТЕСТА:")
        print(
            f"Всего запросов отправлено: {len(MOCK_DATA)} за {total_duration:.2f} сек"
        )
        if valid_latencies:
            print(
                f"Среднее время чистого ответа: {sum(valid_latencies)/len(valid_latencies)/1000:.2f} сек"
            )
        print("=" * 50)


if __name__ == "__main__":
    asyncio.run(run_spaced_benchmark())
