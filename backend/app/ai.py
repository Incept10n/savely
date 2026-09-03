import json
from datetime import datetime

import requests
from flask import current_app

ENDPOINT = "https://llm.api.cloud.yandex.net/foundationModels/v1/completion"


def _month_spends(spends):
    now = datetime.now()
    prefix = now.strftime("%Y-%m")
    return [s for s in spends if (s.get("date") or "").startswith(prefix)]


def _build_prompt(spends):
    lines = []
    for s in sorted(spends, key=lambda x: x.get("date") or ""):
        lines.append(f"{s.get('date', '')[:10]} {s.get('amount'):.2f} ₽ {s.get('comment','')}".rstrip())
    body = "\n".join(lines) if lines else "(нет трат за этот месяц)"

    system = (
        "Ты финансовый аналитик. Разнеси каждую трату по категориям "
        "(например: Продукты, Транспорт, Развлечения, Кафе/Рестораны, " 
        "Коммуналка, Здоровье, Покупки, Другое). "
        "Верни ТОЛЬКО валидный JSON без пояснений и без markdown-обёртки "
        "в следующей схеме:\n"
        '{"categories":[{"name":"Название категории",'
        '"spends":[{"date":"YYYY-MM-DD","amount":123.45,"comment":"комментарий"}]}],'
        '"notice":"короткое резюме аналитики на русском"}'
    )
    user = (
        "Данные трат за текущий месяц (в рублях):\n"
        f"{body}\n\n"
        "Отнеси каждую трату к категории, сохрани amount, date и comment как есть. "
        "В notice дай 2-3 коротких предложения общей аналитики: если всё хорошо, "
        "скажи что-то вроде «Всё отлично, лишнего не тратишь»; если есть перекосы, "
        "укажи категорию с наибольшими тратами и приведи примеры расходов из неё."
    )
    return system, user


def _parse_result(text):
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()
    return json.loads(text)


def _extract_usage(result):
    usage = result.get("usage") or {}
    return {
        "input_tokens": int(usage.get("inputTextTokens") or 0),
        "output_tokens": int(usage.get("completionTokens") or 0),
    }


def analyze_spends(spends):
    month_spends = _month_spends(spends)
    system, user = _build_prompt(month_spends)

    payload = {
        "modelUri": current_app.config["YANDEX_AI_MODEL_URI"],
        "completionOptions": {
            "stream": False,
            "temperature": 0.1,
            "maxTokens": 1500,
        },
        "messages": [
            {"role": "system", "text": system},
            {"role": "user", "text": user},
        ],
    }

    headers = {
        "Authorization": f"Api-Key {current_app.config['YANDEX_AI_API_KEY']}",
        "Content-Type": "application/json",
        "x-folder-id": current_app.config["YANDEX_AI_FOLDER_ID"],
    }

    resp = requests.post(ENDPOINT, headers=headers, json=payload, timeout=120)
    resp.raise_for_status()
    result = resp.json().get("result", {})

    alternatives = result.get("alternatives") or []
    text = ""
    for alt in alternatives:
        message = alt.get("message") or {}
        if message.get("text"):
            text = message["text"]
            break

    parsed = _parse_result(text)
    return {
        "categories": parsed.get("categories", []),
        "notice": parsed.get("notice", ""),
        "usage": _extract_usage(result),
        "month": datetime.now().strftime("%Y-%m"),
    }


def compute_cost(usage, input_price, output_price):
    return (usage["input_tokens"] / 1000.0 * input_price) + (
        usage["output_tokens"] / 1000.0 * output_price
    )
