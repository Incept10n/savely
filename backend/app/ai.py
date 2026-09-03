import json
import logging
from datetime import datetime

import requests
from flask import current_app

logger = logging.getLogger(__name__)

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

    # Isolate the outermost JSON object to drop any trailing explanation or
    # leading prose the model may add around the data.
    start = text.find("{")
    if start != -1:
        depth = 0
        in_string = False
        escaped = False
        for idx in range(start, len(text)):
            ch = text[idx]
            if escaped:
                escaped = False
                continue
            if ch == "\\":
                escaped = True
                continue
            if ch == '"':
                in_string = not in_string
                continue
            if in_string:
                continue
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    text = text[start : idx + 1]
                    break

    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        logger.warning("AI JSON parse failed (%s); raw text:\n%s", exc, text)
        # Escape raw control chars inside string values; keep structural
        # whitespace (pretty-printed JSON) intact.
        return json.loads(_escape_control_chars(text))


def _escape_control_chars(text):
    # Only escape control characters that appear INSIDE double-quoted JSON
    # strings, so structural whitespace between tokens stays intact. Tracks
    # string state while respecting backslash escapes.
    out = []
    in_string = False
    escaped = False
    for ch in text:
        if escaped:
            out.append(ch)
            escaped = False
            continue
        if ch == "\\":
            out.append(ch)
            escaped = True
            continue
        if ch == '"':
            in_string = not in_string
            out.append(ch)
            continue
        if in_string and ord(ch) < 0x20:
            out.append("\\u%04x" % ord(ch))
        else:
            out.append(ch)
    return "".join(out)


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
