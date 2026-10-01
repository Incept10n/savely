import json
import time
from datetime import datetime

import requests
from flask import current_app

ENDPOINT = "https://llm.api.cloud.yandex.net/foundationModels/v1/completion"

_TIMEOUT = 120
_MAX_ATTEMPTS = 3
_BACKOFF_BASE = 1.0
_RETRYABLE_STATUS = (429, 500, 502, 503, 504)

# Closed category list. The model must not invent names: a fixed vocabulary is
# what keeps categories stable from month to month.
CATEGORIES = [
    "Продукты",
    "Кафе и рестораны",
    "Транспорт и такси",
    "Авто и заправки",
    "Коммуналка",
    "Связь и интернет",
    "Здоровье и аптека",
    "Одежда и обувь",
    "Товары для дома",
    "Электроника",
    "Развлечения и подписки",
    "Спорт и отдых",
    "Образование",
    "Путешествия",
    "Другое",
]
FALLBACK_CATEGORY = "Другое"
_ALLOWED_CATEGORIES = {name.casefold(): name for name in CATEGORIES}

# None = untested, True = provider accepted responseFormat, False = rejected.
_JSON_MODE = None


def month_bounds(now=None):
    # `spent_at` holds naive local time, so month limits are local too.
    now = now or datetime.now()
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if start.month == 12:
        end = start.replace(year=start.year + 1, month=1)
    else:
        end = start.replace(month=start.month + 1)
    return start, end


def normalize_comment(comment):
    # Mirrors LOWER(TRIM(comment)) so the reported unique count predicts exactly
    # what a GROUP BY on the normalized comment would yield.
    return " ".join((comment or "").strip().casefold().split())


def unique_comment_count(spends):
    return len({normalize_comment(s.get("comment")) for s in spends})


def normalize_category(name):
    text = (name or "").strip().casefold()
    if not text:
        return FALLBACK_CATEGORY
    if text in _ALLOWED_CATEGORIES:
        return _ALLOWED_CATEGORIES[text]
    # Tolerate decorated names like "Продукты / супермаркет".
    matches = [name for key, name in _ALLOWED_CATEGORIES.items() if key in text]
    if matches:
        return max(matches, key=len)
    return FALLBACK_CATEGORY


def _build_prompt(spends):
    # No dates: rows are resolved back by index, so a date per spend is pure
    # input-token overhead. Amounts stay, the notice needs them to judge which
    # category dominates.
    lines = []
    for i, s in enumerate(spends):
        lines.append(
            f"{i}. {s.get('amount'):.2f} ₽ {s.get('comment', '')}".rstrip()
        )
    body = "\n".join(lines) if lines else "(нет трат за этот месяц)"

    listed = "\n".join(f"- {name}" for name in CATEGORIES)
    examples = (
        "продукты, пятёрочка, магнит, лента, перекрёсток, ашан, grocery → Продукты\n"
        "кофе, кофейня, обед, ужин, доставка еды, ресторан, суши, шаверма → Кафе и рестораны\n"
        "метро, троллейбус, автобус, такси, яндекс го, билет на поезд → Транспорт и такси\n"
        "бензин, азс, лукойл, шины, мойка, парковка, гибдд → Авто и заправки\n"
        "жкх, квитанция, счётчик, вода, электричество, отопление, управляйка → Коммуналка\n"
        "мтс, билайн, мегафон, интернет, роутер → Связь и интернет\n"
        "аптека, горздрав, поликлиника, стоматолог, анализы, бАДы → Здоровье и аптека\n"
        "одежда, обувь, ботинки, куртка, джинсы → Одежда и обувь\n"
        "икеа, леруа, посуда, хозтовары, ремонт, мебель → Товары для дома\n"
        "телефон, ноутбук, наушники, видеокарта, мвидео, элдорадо → Электроника\n"
        "кино, театр, концерт, steam, netflix, кинопоиск, подписка, игры → Развлечения и подписки\n"
        "фитнес, зал, бассейн, абонемент, лыжи → Спорт и отдых\n"
        "курсы, репетитор, универ, книги, учебники → Образование\n"
        "отель, авиабилет, отпуск, виза, экскурсия → Путешествия\n"
        "жанр, сленг, сокращения и опечатки в комментариях — норма, разбирай смысл, "
        "а не написание"
    )

    system = (
        "Ты финансовый аналитик. Разнеси каждую трату по категориям из "
        "ФИКСИРОВАННОГО списка ниже.\n\n"
        f"Список категорий (используй ТОЛЬКО эти названия, без синонимов и уточнений):\n{listed}\n\n"
        "Правила:\n"
        "- каждая трата попадает ровно в одну категорию\n"
        "- одно название = одна категория, не придумывай новых\n"
        "- НЕ повторяй сами траты в ответе, только их индексы\n"
        "- если комментарий непонятен или не подходит ни под одну категорию — "
        "Другое\n\n"
        f"Примеры (комментарий → категория):\n{examples}\n\n"
        "Верни ТОЛЬКО валидный JSON без пояснений, без markdown-обёртки и без "
        "повторения текста трат, в следующей схеме:\n"
        '{"categories":[{"name":"Название категории","indices":[0,1,2]}],'
        '"notice":"короткое резюме аналитики на русском"}'
    )
    user = (
        "Данные трат за текущий месяц (в рублях), каждое пронумеровано:\n"
        f"{body}\n\n"
        "Отнеси каждую трату к ровно одной категории из списка и укажи её индекс "
        "из массива indices. НЕ повторяй сами траты, только индексы. Каждый индекс "
        "должен использоваться ровно один раз. 2-3 коротких предложения общей "
        "аналитики в notice: если всё хорошо, скажи что-то вроде «Всё отлично, "
        "лишнего не тратишь»; если есть перекосы, укажи категорию с наибольшими "
        "тратами по сумме и приведи примеры расходов из неё."
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
    except json.JSONDecodeError:
        # Escape raw control chars inside string values; keep structural
        # whitespace (pretty-printed JSON) intact.
        return json.loads(_escape_control_chars(text))


def _escape_control_chars(text):
    # Only escape control characters that appear INSIDE double-quoted JSON
    # strings, keeping structural whitespace intact.
    out = []
    in_string = False
    escaped = False
    for ch in text:
        if escaped:
            out.append(ch)
            escaped = False
            continue
        if ch == "\\":
            escaped = True
            out.append(ch)
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


def _post_completion(payload):
    global _JSON_MODE

    headers = {
        "Authorization": f"Api-Key {current_app.config['YANDEX_AI_API_KEY']}",
        "Content-Type": "application/json",
        "x-folder-id": current_app.config["YANDEX_AI_FOLDER_ID"],
    }
    json_mode = bool(current_app.config["YANDEX_AI_RESPONSE_FORMAT"]) and _JSON_MODE is not False

    for attempt in range(_MAX_ATTEMPTS):
        body = dict(payload)
        if json_mode:
            body["responseFormat"] = {"type": "JSON_OBJECT"}

        try:
            resp = requests.post(ENDPOINT, headers=headers, json=body, timeout=_TIMEOUT)
        except requests.RequestException:
            if attempt + 1 == _MAX_ATTEMPTS:
                raise
            time.sleep(_BACKOFF_BASE * 2**attempt)
            continue

        # The model may not accept responseFormat; drop it for this process.
        if resp.status_code == 400 and json_mode and _JSON_MODE is not True:
            _JSON_MODE = False
            json_mode = False
            continue

        if resp.status_code in _RETRYABLE_STATUS and attempt + 1 < _MAX_ATTEMPTS:
            time.sleep(_BACKOFF_BASE * 2**attempt)
            continue

        resp.raise_for_status()
        if json_mode:
            _JSON_MODE = True
        return resp.json()

    raise RuntimeError("Yandex completion failed")


def _bucketize(spends, categories):
    # Sums are computed here, never by the model: exact, no rounding drift.
    grand_total = sum(float(s.get("amount") or 0) for s in spends)
    buckets = []
    assigned = set()

    for cat in categories:
        name = normalize_category(cat.get("name"))
        rows = []
        for raw_idx in cat.get("indices") or []:
            try:
                idx = int(raw_idx)
            except (TypeError, ValueError):
                continue
            if idx in assigned or not 0 <= idx < len(spends):
                continue
            assigned.add(idx)
            rows.append(
                {
                    "date": spends[idx].get("date", ""),
                    "amount": spends[idx].get("amount"),
                    "comment": spends[idx].get("comment", ""),
                }
            )
        buckets.append(_with_totals(name, rows, grand_total))

    # Anything the model skipped or pointed at a bad index must still show up.
    leftover = [
        {
            "date": s.get("date", ""),
            "amount": s.get("amount"),
            "comment": s.get("comment", ""),
        }
        for i, s in enumerate(spends)
        if i not in assigned
    ]
    if leftover:
        for bucket in buckets:
            if bucket["name"] == FALLBACK_CATEGORY:
                bucket["spends"].extend(leftover)
                bucket["total"] = round(
                    bucket["total"] + sum(float(s["amount"] or 0) for s in leftover), 2
                )
                bucket["count"] += len(leftover)
                bucket["share"] = _share(bucket["total"], grand_total)
                break
        else:
            buckets.append(_with_totals(FALLBACK_CATEGORY, leftover, grand_total))

    buckets = [b for b in buckets if b["count"]]
    buckets.sort(key=lambda b: b["total"], reverse=True)
    return buckets


def _with_totals(name, rows, grand_total):
    total = round(sum(float(r.get("amount") or 0) for r in rows), 2)
    return {
        "name": name,
        "spends": rows,
        "total": total,
        "count": len(rows),
        "share": _share(total, grand_total),
    }


def _share(total, grand_total):
    return round(total / grand_total * 100, 1) if grand_total else 0.0


def analyze_spends(spends):
    # `spends` is already scoped to one month by the caller.
    system, user = _build_prompt(spends)

    payload = {
        "modelUri": current_app.config["YANDEX_AI_MODEL_URI"],
        "completionOptions": {
            "stream": False,
            # Classification is a lookup, not creative writing.
            "temperature": 0,
            "maxTokens": int(current_app.config["YANDEX_AI_MODEL_MAX_TOKENS"]),
        },
        "messages": [
            {"role": "system", "text": system},
            {"role": "user", "text": user},
        ],
    }

    result = _post_completion(payload).get("result", {})

    text = ""
    for alt in result.get("alternatives") or []:
        message = alt.get("message") or {}
        if message.get("text"):
            text = message["text"]
            break

    parsed = _parse_result(text) if text else {}
    return {
        "categories": _bucketize(spends, parsed.get("categories") or []),
        "notice": parsed.get("notice", ""),
        "usage": _extract_usage(result),
        "month": datetime.now().strftime("%Y-%m"),
    }


def compute_cost(usage, input_price, output_price):
    return (usage["input_tokens"] / 1000.0 * input_price) + (
        usage["output_tokens"] / 1000.0 * output_price
    )


def estimate_tokens(text):
    # Conservative heuristic: ~4 chars per token for mixed RU/EN text.
    # Intentionally OVER-estimates so the cost guard can never slip past.
    return max(1, (len(text) + 3) // 4)


def estimate_request_cost(system, user, output_tokens):
    input_tokens = estimate_tokens(system) + estimate_tokens(user)
    input_cost = input_tokens / 1000.0 * current_app.config["YANDEX_AI_INPUT_PRICE_PER_1K"]
    output_cost = output_tokens / 1000.0 * current_app.config["YANDEX_AI_OUTPUT_PRICE_PER_1K"]
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cost_rub": round(input_cost + output_cost, 2),
    }
