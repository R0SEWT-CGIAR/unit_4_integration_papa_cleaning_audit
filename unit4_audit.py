# Test retrieval of ALL REPINV and REPTEC documents from Unit4 API with pagination

import os
import sys
import time
import json
import base64
import re
import hashlib
import csv
from pathlib import Path
from typing import Any, Optional

import requests
from requests.auth import HTTPBasicAuth
from dotenv import load_dotenv


load_dotenv()


# -----------------------------
# RATE LIMITING & METRICS
# -----------------------------
class RateLimiter:
    def __init__(self, min_interval_sec: float = 0.25) -> None:
        self.min_interval_sec = min_interval_sec
        self._last_ts = 0.0

    def wait(self, metrics: dict | None = None) -> None:
        now = time.time()
        elapsed = now - self._last_ts
        if elapsed < self.min_interval_sec:
            sleep_sec = self.min_interval_sec - elapsed
            sleep_with_metrics(sleep_sec, metrics)
        self._last_ts = time.time()


def sleep_with_metrics(seconds: float, metrics: dict | None = None) -> None:
    if seconds <= 0:
        return
    time.sleep(seconds)
    if metrics is not None:
        metrics["sleep_seconds"] = metrics.get("sleep_seconds", 0.0) + seconds


def backoff_seconds(attempt: int, base: float = 1.5, cap: float = 30.0) -> float:
    return min(cap, base * (2 ** attempt))


def load_jsonl_items(path: Path) -> list[dict]:
    if not path.exists():
        return []
    items = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            items.append(json.loads(line))
    return items


def append_jsonl_items(path: Path, items: list[dict]) -> None:
    if not items:
        return
    with open(path, "a", encoding="utf-8") as f:
        for item in items:
            f.write(json.dumps(item, ensure_ascii=False))
            f.write("\n")


def load_checkpoint(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_checkpoint(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def save_metrics(path: Path, metrics: dict) -> None:
    path.write_text(json.dumps(metrics, indent=2, ensure_ascii=False), encoding="utf-8")


def record_failure_and_maybe_break(metrics: dict | None, threshold: int = 5, cooldown_sec: int = 60) -> None:
    if metrics is None:
        return
    metrics["consecutive_failures"] = metrics.get("consecutive_failures", 0) + 1
    if metrics["consecutive_failures"] >= threshold:
        print(f"[circuit-breaker] {threshold} failures, cooling down {cooldown_sec}s...")
        sleep_with_metrics(cooldown_sec, metrics)
        metrics["consecutive_failures"] = 0


def record_success(metrics: dict | None) -> None:
    if metrics is None:
        return
    metrics["consecutive_failures"] = 0


# -----------------------------
# HTTP
# -----------------------------
def make_request(
    url: str,
    params: dict,
    auth: HTTPBasicAuth,
    timeout: int = 120
) -> tuple[requests.Response, int]:
    """
    Executes a GET request and returns response + latency in ms
    """
    headers = {"Accept": "application/json"}

    t0 = time.time()
    response = requests.get(
        url,
        params=params,
        headers=headers,
        auth=auth,
        timeout=timeout
    )
    latency_ms = int((time.time() - t0) * 1000)

    return response, latency_ms


# -----------------------------
# VALIDATION & LOGGING
# -----------------------------
def validate_response(r: requests.Response) -> bool:
    """
    Validates HTTP response and prints useful debug info
    """
    if r.status_code in (401, 403):
        print("[smoke] auth failed")
        print("WWW-Authenticate:", r.headers.get("WWW-Authenticate"))
        print("Body:", r.text[:500])
        return False

    if r.status_code not in (200, 201):
        print("[smoke] unexpected status code:", r.status_code)
        print("Body:", r.text[:500])
        return False

    return True


def print_json_preview(r: requests.Response) -> dict | None:
    """
    Prints a short JSON preview and returns parsed JSON
    """
    ct = r.headers.get("Content-Type", "")
    if "application/json" not in ct:
        print("[smoke] non-json content-type:", ct)
        return None

    data = r.json()

    if isinstance(data, dict):
        preview = {k: data[k] for k in list(data.keys())[:8]}
    elif isinstance(data, list):
        preview = data[:1]
    else:
        preview = data

    print("[smoke] json preview:")
    print(json.dumps(preview, indent=2)[:1500])

    return data


# -----------------------------
# DOCUMENT HANDLING
# -----------------------------
def download_documents(
    data: dict,
    output_dir: str = ".",
    auth: HTTPBasicAuth = None,
    base_url: str = None,
    max_retries: int = 3,
    timeout: int = 180,
    rate_limiter: RateLimiter | None = None,
    metrics: dict | None = None
) -> bool:
    """
    Downloads document content either from fileContent or by fetching individually
    """
    if not isinstance(data, dict) or "items" not in data:
        print("FAIL: respuesta inesperada")
        return False

    Path(output_dir).mkdir(parents=True, exist_ok=True)

    total_docs = len(data["items"])
    downloaded = 0
    failed = 0
    rate_limiter = rate_limiter or RateLimiter()

    for idx, doc in enumerate(data["items"], 1):
        b64 = doc.get("fileContent", "").strip()
        filename = doc.get("fileName", f"document_{idx}.bin")
        filename = filename.replace("/", "_").replace("\\", "_")
        doc_id = doc.get("id")
        filepath = Path(output_dir) / filename

        if filepath.exists() and filepath.stat().st_size > 0:
            print(f"  [{idx}/{total_docs}] SKIP {filename} (already exists)")
            if metrics is not None:
                metrics["files_skipped"] = metrics.get("files_skipped", 0) + 1
            continue

        if not b64 and auth and base_url:
            params = {
                "companyId": "P2",
                "indexes": "P2",
                "id": doc_id,
                "withFileContent": True,
            }

            for attempt in range(max_retries):
                try:
                    print(f"  [{idx}/{total_docs}] Fetching {filename}...", end=" ")
                    rate_limiter.wait(metrics)
                    response, _ = make_request(base_url, params, auth, timeout=timeout)

                    if metrics is not None:
                        metrics["requests_total"] = metrics.get("requests_total", 0) + 1

                    if response.status_code == 429:
                        if metrics is not None:
                            metrics["http_429"] = metrics.get("http_429", 0) + 1
                        retry_after = response.headers.get("Retry-After")
                        wait_sec = float(retry_after) if retry_after else backoff_seconds(attempt)
                        print(f"RATE-LIMIT (wait {wait_sec:.1f}s)")
                        sleep_with_metrics(wait_sec, metrics)
                        record_failure_and_maybe_break(metrics)
                        continue

                    if response.status_code >= 500:
                        if metrics is not None:
                            metrics["http_5xx"] = metrics.get("http_5xx", 0) + 1
                        wait_sec = backoff_seconds(attempt)
                        print(f"SERVER-ERR (wait {wait_sec:.1f}s)")
                        sleep_with_metrics(wait_sec, metrics)
                        record_failure_and_maybe_break(metrics)
                        continue

                    if not validate_response(response):
                        if metrics is not None:
                            metrics["http_other"] = metrics.get("http_other", 0) + 1
                        print(f"FAIL (status {response.status_code})")
                        sleep_with_metrics(2, metrics)
                        record_failure_and_maybe_break(metrics)
                        continue

                    response_data = response.json()
                    items = response_data.get("items", [])
                    if items:
                        b64 = items[0].get("fileContent", "").strip()

                    if not b64:
                        print("FAIL (no fileContent)")
                        sleep_with_metrics(2, metrics)
                        continue

                    record_success(metrics)
                    break

                except requests.exceptions.Timeout:
                    if attempt < max_retries - 1:
                        print(f"TIMEOUT (retry {attempt + 1}/{max_retries})")
                        if metrics is not None:
                            metrics["timeouts"] = metrics.get("timeouts", 0) + 1
                        sleep_with_metrics(2 + attempt, metrics)
                        record_failure_and_maybe_break(metrics)
                        continue
                    print("TIMEOUT (max retries)")
                except Exception as e:
                    print(f"FAIL ({e})")
                    sleep_with_metrics(2, metrics)
                    record_failure_and_maybe_break(metrics)

            if not b64:
                failed += 1
                continue

        if not b64:
            print(f"  [{idx}/{total_docs}] SKIP {filename} (no fileContent)")
            failed += 1
            continue

        try:
            b64_clean = re.sub(r"^data:.*;base64,", "", b64)
            binary = base64.b64decode(b64_clean)

            sha256 = hashlib.sha256(binary).hexdigest()
            filepath.write_bytes(binary)
            size_kb = len(binary) / 1024
            print(f"OK ({size_kb:.1f} KB)")
            downloaded += 1
            if metrics is not None:
                metrics["files_downloaded"] = metrics.get("files_downloaded", 0) + 1
                metrics["bytes_downloaded"] = metrics.get("bytes_downloaded", 0) + len(binary)
            record_success(metrics)
        except Exception as e:
            print(f"FAIL ({e})")
            failed += 1
            if metrics is not None:
                metrics["files_failed"] = metrics.get("files_failed", 0) + 1
            record_failure_and_maybe_break(metrics)

    print(f"\n[summary] Downloaded: {downloaded}/{total_docs}, Failed: {failed}")
    return failed == 0


# -----------------------------
def extract_metadata(data: dict) -> list[dict[str, Any]]:
    """
    Extracts metadata from API response items (excluding fileContent)
    """
    if not isinstance(data, dict) or "items" not in data:
        return []

    metadata_list = []
    for doc in data["items"]:
        metadata = {
            "id": doc.get("id"),
            "fileName": doc.get("fileName"),
            "mimeType": doc.get("mimeType"),
            "docType": doc.get("docType"),
            "companyId": doc.get("companyId"),
            "status": doc.get("status"),
            "revisionNo": doc.get("revisionNo"),
            "updatedAt": doc.get("lastUpdate", {}).get("updatedAt"),
            "updatedBy": doc.get("lastUpdate", {}).get("updatedBy"),
        }
        metadata_list.append(metadata)

    return metadata_list


def save_metadata_csv(metadata_list: list[dict], filename: str) -> bool:
    """
    Saves metadata list to CSV file
    """
    if not metadata_list:
        print(f"No metadata to save for {filename}")
        return False

    try:
        with open(filename, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=metadata_list[0].keys())
            writer.writeheader()
            writer.writerows(metadata_list)
        print(f"✓ Metadata saved: {filename} ({len(metadata_list)} rows)")
        return True
    except Exception as e:
        print(f"Error saving metadata: {e}")
        return False


def save_response_json(data: dict, filename: str) -> bool:
    """
    Saves full API response to JSON file (excluding fileContent to keep size reasonable)
    """
    if not data or "items" not in data:
        print(f"No data to save for {filename}")
        return False

    try:
        data_clean = data.copy()
        data_clean["items"] = [
            {k: v for k, v in item.items() if k != "fileContent"}
            for item in data_clean.get("items", [])
        ]

        with open(filename, "w", encoding="utf-8") as f:
            json.dump(data_clean, f, indent=2, ensure_ascii=False)

        size_mb = Path(filename).stat().st_size / (1024 * 1024)
        print(f"✓ Response JSON saved: {filename} ({size_mb:.1f} MB)")
        return True
    except Exception as e:
        print(f"Error saving response JSON: {e}")
        return False


# ----
# PAGINATION & BATCH PROCESSING
# ----
def fetch_all_documents(
    url: str,
    params_base: dict,
    auth: HTTPBasicAuth,
    limit: int = 50,
    max_retries: int = 3,
    rate_limiter: RateLimiter | None = None,
    metrics: dict | None = None,
    checkpoint_path: Path | None = None,
    items_path: Path | None = None,
    min_limit: int = 10
) -> Optional[list[dict]]:
    """
    Fetches ALL documents using pagination with retry logic
    Returns list of all items, or None if failed
    """
    rate_limiter = rate_limiter or RateLimiter()

    all_items: list[dict] = []
    start = 0
    page = 1
    current_limit = limit

    if items_path is not None and items_path.exists():
        all_items = load_jsonl_items(items_path)
        start = len(all_items)
        if metrics is not None:
            metrics["resumed_items"] = start

    if checkpoint_path is not None:
        checkpoint = load_checkpoint(checkpoint_path)
        start = max(start, int(checkpoint.get("start", 0)))
        if start:
            page = (start // max(current_limit, 1)) + 1

    print(f"Starting pagination (limit={current_limit})...")

    while True:
        params = {**params_base, "start": start, "limit": current_limit}

        for attempt in range(max_retries):
            try:
                rate_limiter.wait(metrics)
                response, latency_ms = make_request(url, params, auth, timeout=180)

                if metrics is not None:
                    metrics["requests_total"] = metrics.get("requests_total", 0) + 1
                    metrics["latency_ms_total"] = metrics.get("latency_ms_total", 0) + latency_ms

                if response.status_code == 429:
                    if metrics is not None:
                        metrics["http_429"] = metrics.get("http_429", 0) + 1
                    retry_after = response.headers.get("Retry-After")
                    wait_sec = float(retry_after) if retry_after else backoff_seconds(attempt)
                    print(f"  ⚠ Rate limited on page {page} (wait {wait_sec:.1f}s)")
                    sleep_with_metrics(wait_sec, metrics)
                    record_failure_and_maybe_break(metrics)
                    continue

                if response.status_code >= 500:
                    if metrics is not None:
                        metrics["http_5xx"] = metrics.get("http_5xx", 0) + 1
                    wait_sec = backoff_seconds(attempt)
                    print(f"  ⚠ Server error on page {page} (wait {wait_sec:.1f}s)")
                    sleep_with_metrics(wait_sec, metrics)
                    current_limit = max(min_limit, current_limit // 2)
                    record_failure_and_maybe_break(metrics)
                    continue

                if not validate_response(response):
                    if metrics is not None:
                        metrics["http_other"] = metrics.get("http_other", 0) + 1
                    print(f"  ✗ Page {page} failed (status {response.status_code})")
                    record_failure_and_maybe_break(metrics)
                    return None

                data = response.json()
                items = data.get("items", [])
                total = data.get("total", 0)
                count = len(items)

                if count == 0:
                    print(f"  ✓ Page {page}: No more documents (total collected: {len(all_items)})")
                    return all_items

                all_items.extend(items)
                if items_path is not None:
                    append_jsonl_items(items_path, items)

                print(f"  ✓ Page {page}: {count} docs | Total so far: {len(all_items)}/{total} | Latency: {latency_ms}ms")

                start += count
                page += 1

                if checkpoint_path is not None:
                    save_checkpoint(checkpoint_path, {
                        "start": start,
                        "total": total,
                        "collected": len(all_items),
                        "updatedAt": time.strftime("%Y-%m-%d %H:%M:%S")
                    })

                record_success(metrics)
                break

            except requests.exceptions.Timeout:
                if attempt < max_retries - 1:
                    wait_time = 5 * (attempt + 1)
                    print(f"  ⚠ Timeout on page {page}, retry {attempt + 1}/{max_retries} (waiting {wait_time}s)...")
                    if metrics is not None:
                        metrics["timeouts"] = metrics.get("timeouts", 0) + 1
                    sleep_with_metrics(wait_time, metrics)
                    current_limit = max(min_limit, current_limit // 2)
                    record_failure_and_maybe_break(metrics)
                    continue
                print(f"  ✗ Max retries exceeded for page {page}")
                return None
            except Exception as e:
                print(f"  ✗ Error on page {page}: {e}")
                record_failure_and_maybe_break(metrics)
                return None
    return all_items


# -----------------------------
# MAIN
# -----------------------------
def main() -> int:
    base = os.environ["UNIT4_BASE"].rstrip("/")
    user = os.environ.get("UNIT4_USER")
    pwd = os.environ.get("UNIT4_PASS")
    min_interval = float(os.environ.get("UNIT4_MIN_INTERVAL", "0.25"))
    max_retries = int(os.environ.get("UNIT4_MAX_RETRIES", "3"))
    limit = int(os.environ.get("UNIT4_LIMIT", "50"))
    output_root = Path(os.environ.get("UNIT4_OUT_DIR", "artifacts"))

    if not user or not pwd:
        print("Faltan UNIT4_USER / UNIT4_PASS")
        return 2

    url = f"{base}/documents"
    auth = HTTPBasicAuth(user, pwd)

    docs_root = output_root / "docs"
    csv_root = output_root / "csv"
    json_root = output_root / "json"
    metrics_root = output_root / "metrics"
    checkpoints_root = output_root / "checkpoints"
    items_root = output_root / "items"
    logs_root = output_root / "logs"

    for p in (docs_root, csv_root, json_root, metrics_root, checkpoints_root, items_root, logs_root):
        p.mkdir(parents=True, exist_ok=True)

    doc_types = [
        ("REPINV", "repinv_docs"),
        ("REPTEC", "reptec_docs"),
    ]

    for doc_type, output_folder in doc_types:
        print(f"\n{'='*60}")
        print(f"Downloading ALL {doc_type} documents to {output_folder}/")
        print(f"{'='*60}\n")

        rate_limiter = RateLimiter(min_interval_sec=min_interval)
        metrics = {
            "docType": doc_type,
            "outputFolder": output_folder,
            "startTime": time.strftime("%Y-%m-%d %H:%M:%S"),
            "minIntervalSec": min_interval,
            "maxRetries": max_retries,
            "limit": limit
        }

        items_path = items_root / f"{output_folder}_items.jsonl"
        checkpoint_path = checkpoints_root / f"{output_folder}_checkpoint.json"
        metrics_path = metrics_root / f"{output_folder}_metrics.json"
        docs_dir = docs_root / output_folder

        params_base = {
            "companyId": "P2",
            "indexes": "P2",
            "docType": doc_type,
            "withFileContent": False,
        }

        print("[step 1] Fetching metadata for all documents (no fileContent)...")
        all_items = fetch_all_documents(
            url,
            params_base,
            auth,
            limit=limit,
            max_retries=max_retries,
            rate_limiter=rate_limiter,
            metrics=metrics,
            checkpoint_path=checkpoint_path,
            items_path=items_path
        )

        if all_items is None:
            print(f"FAIL: Could not fetch {doc_type}")
            metrics["endTime"] = time.strftime("%Y-%m-%d %H:%M:%S")
            save_metrics(metrics_path, metrics)
            continue

        if not all_items:
            print(f"No documents found for {doc_type}")
            metrics["endTime"] = time.strftime("%Y-%m-%d %H:%M:%S")
            save_metrics(metrics_path, metrics)
            continue

        print(f"\n[info] Total {doc_type} documents collected: {len(all_items)}\n")

        response_data = {
            "total": len(all_items),
            "items": all_items
        }

        print("[step 2] Downloading file content for each document...")
        if not download_documents(
            response_data,
            str(docs_dir),
            auth,
            url,
            max_retries=max_retries,
            timeout=180,
            rate_limiter=rate_limiter,
            metrics=metrics
        ):
            print(f"FAIL: Could not download {doc_type}")
            metrics["endTime"] = time.strftime("%Y-%m-%d %H:%M:%S")
            save_metrics(metrics_path, metrics)
            continue

        print("[step 3] Extracting and saving metadata...")
        metadata = extract_metadata(response_data)
        metadata_file = str(csv_root / f"{output_folder}_metadata.csv")
        save_metadata_csv(metadata, metadata_file)

        response_file = str(json_root / f"{output_folder}_response.json")
        save_response_json(response_data, response_file)

        metrics["endTime"] = time.strftime("%Y-%m-%d %H:%M:%S")
        save_metrics(metrics_path, metrics)

        print(f"[smoke] PASS - All {doc_type} documents saved to {output_folder}/\n")

    return 0


if __name__ == "__main__":
    sys.exit(main())
