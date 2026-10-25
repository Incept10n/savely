"""Weekly export of hand-confirmed labels to the classifier's S3 dataset.

The classifier (`classifier/knn.py`) builds its vector space from a single CSV
in S3. That file has to grow as the user fixes categories in the UI, otherwise
the model never learns anything beyond its initial seed.

What is exported: **only** rows whose category a human confirmed
(`spend_categories.source = 'manual'`). The classifier's own output
(`source = 'knn'`) is deliberately never exported — training on a model's own
predictions just cements its mistakes.

Run by the `dataset-export` CronJob; exits non-zero only on a real failure, so
a quiet week (nothing new to learn) is not a red build.

Safety rules, all deliberate:

* **merge, never replace** — rows already in the object are kept, so a bad week
  cannot shrink the dataset;
* a confirmed label wins over the stored one for the same comment;
* if the object is missing and there are fewer than `MIN_ROWS_WITHOUT_BASE`
  rows, refuse to upload: an almost-empty dataset would make the classifier
  useless, and it is better to keep whatever was there before;
* if nothing changed, the object is not rewritten at all (no pointless
  `reload_data` either);
* the reload call is best-effort — the upload already happened, and the
  classifier picks the new CSV up on its next restart anyway.
"""

import csv
import io
import os
import sys
from datetime import datetime

import boto3
import requests
from botocore.exceptions import BotoCoreError, ClientError

from app import create_app, db

# Matches the file already in the bucket: `date;comment;amount;category`.
# The classifier only reads `comment` and `category`; the other two are kept so
# the file stays human-readable in the S3 browser.
FIELDS = ["date", "comment", "amount", "category"]
DELIMITER = ";"

# Below this many rows, writing a *new* object would produce a dataset that is
# worse than nothing. Only applies when there is no object to merge into.
MIN_ROWS_WITHOUT_BASE = int(os.environ.get("DATASET_MIN_ROWS", "30"))


def normalize(comment):
    """Same key the app and the classifier use for "the same comment"."""
    return " ".join((comment or "").strip().casefold().split())


def format_date(value):
    """`dd.mm.yyyy` for both drivers: MySQL returns a datetime, SQLite a string."""
    if hasattr(value, "strftime"):
        return value.strftime("%d.%m.%Y")
    text = str(value or "")[:10]
    try:
        return datetime.strptime(text, "%Y-%m-%d").strftime("%d.%m.%Y")
    except ValueError:
        return text


def s3_client():
    endpoint = os.environ.get("S3_ENDPOINT_URL")
    if not endpoint:
        raise SystemExit("S3_ENDPOINT_URL is not set")
    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        region_name=os.environ.get("AWS_DEFAULT_REGION", "us-east-1"),
    )


def read_existing(client, bucket, key):
    """`{normalized comment: row}` from the object, `{}` if it is absent."""
    try:
        obj = client.get_object(Bucket=bucket, Key=key)
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        if code in ("NoSuchKey", "404", "NoSuchBucket"):
            return {}
        raise
    text = obj["Body"].read().decode("utf-8-sig")
    rows = {}
    for row in csv.DictReader(io.StringIO(text), delimiter=DELIMITER):
        comment = (row.get("comment") or "").strip()
        category = (row.get("category") or "").strip()
        if not comment or not category:
            continue
        rows[normalize(comment)] = {
            "date": (row.get("date") or "").strip(),
            "comment": comment,
            "amount": (row.get("amount") or "").strip(),
            "category": category,
        }
    return rows


def merge(rows, labels):
    """Apply confirmed labels on top of the existing dataset."""
    added = updated = 0
    for label in labels:
        comment = (label["comment"] or "").strip()
        if not comment:
            continue
        key = normalize(comment)
        spent_at = label["spent_at"]
        new_row = {
            "date": format_date(spent_at),
            "comment": comment,
            "amount": f"{float(label['amount']):.2f}",
            "category": label["category"],
        }
        current = rows.get(key)
        if current is None:
            rows[key] = new_row
            added += 1
        elif current["category"] != new_row["category"]:
            rows[key] = new_row
            updated += 1
        elif not current["date"] and new_row["date"]:
            current["date"] = new_row["date"]
            current["amount"] = new_row["amount"]
    return rows, added, updated


def render(rows):
    buf = io.StringIO()
    writer = csv.DictWriter(
        buf, fieldnames=FIELDS, delimiter=DELIMITER, lineterminator="\n"
    )
    writer.writeheader()
    for row in rows.values():
        writer.writerow({field: row.get(field, "") for field in FIELDS})
    return buf.getvalue().encode("utf-8")


def reload_classifier():
    url = (os.environ.get("CLASSIFIER_URL") or "").rstrip("/")
    if not url:
        print("CLASSIFIER_URL is empty, skipping reload")
        return
    try:
        resp = requests.post(f"{url}/reload_data", timeout=10)
        print(f"reload_data -> {resp.status_code} {resp.text[:120]}")
    except requests.RequestException as exc:
        print(f"reload_data failed (the CSV is uploaded anyway): {exc}")


def should_upload(had_base, rows):
    """A brand-new dataset must be big enough to be worth having."""
    return had_base or len(rows) >= MIN_ROWS_WITHOUT_BASE


def main():
    bucket = os.environ.get("S3_BUCKET")
    key = os.environ.get("S3_CSV_KEY")
    if not bucket or not key:
        raise SystemExit("S3_BUCKET / S3_CSV_KEY are not set")

    # INIT_DB=0: a CronJob must never create tables behind the app's back.
    app = create_app({"INIT_DB": False, "CLASSIFIER_URL": ""})
    with app.app_context():
        labels = db.list_manual_labels()
    print(f"confirmed labels in the database: {len(labels)}")

    client = s3_client()
    try:
        rows = read_existing(client, bucket, key)
    except (BotoCoreError, ClientError) as exc:
        raise SystemExit(f"cannot read s3://{bucket}/{key}: {exc}")
    had_base = bool(rows)
    print(f"rows already in s3://{bucket}/{key}: {len(rows)}")

    rows, added, updated = merge(rows, labels)
    if added == 0 and updated == 0:
        print("nothing new to learn, keeping the dataset as is")
        return 0

    if not should_upload(had_base, rows):
        print(
            f"refusing to write {len(rows)} rows (< DATASET_MIN_ROWS="
            f"{MIN_ROWS_WITHOUT_BASE}) — a dataset this small is worse than none"
        )
        return 0

    payload = render(rows)
    try:
        client.put_object(
            Bucket=bucket,
            Key=key,
            Body=payload,
            ContentType="text/csv; charset=utf-8",
        )
    except (BotoCoreError, ClientError) as exc:
        raise SystemExit(f"cannot upload s3://{bucket}/{key}: {exc}")

    print(f"uploaded {len(rows)} rows (+{added} new, {updated} relabelled)")
    reload_classifier()
    return 0


if __name__ == "__main__":
    sys.exit(main())
