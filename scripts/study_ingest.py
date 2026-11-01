#!/usr/bin/env python3
"""Загрузка PDF по системному анализу в базу знаний Open WebUI (RAG).

Читает .env: WEBUI_ADMIN_EMAIL/PASSWORD и STUDY_KNOWLEDGE_NAME / STUDY_DOCS_DIR.
Создаёт (если нет) базу знаний ``STUDY_KNOWLEDGE_NAME`` и загружает в неё все PDF
из ``STUDY_DOCS_DIR``: файл -> /api/v1/files/ -> ожидание обработки -> добавление в базу.

Идемпотентно: уже загруженные файлы (по имени) пропускаются; --force перезаливает.
Печатает knowledge_id (нужен для webui_add_tutor_model.py) и число извлечённых
символов по каждому PDF (предупреждение при ~0 — вероятен скан без текстового слоя).

Примеры:
    python scripts\\study_ingest.py --dry-run
    python scripts\\study_ingest.py
    python scripts\\study_ingest.py --dir "C:\\Users\\Ilya\\Desktop\\Учёба\\Системный анализ"
"""

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from e2e_loop import load_env  # noqa: E402

BASE = os.environ.get("WEBUI_BASE", "http://127.0.0.1:3000")
SYLLABUS_FILE = Path("study/syllabus.json")
DEFAULT_DOCS_DIR = r"C:\Users\Ilya\Desktop\Учёба\Системный анализ"


def req(method, path, token=None, body=None, timeout=120):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(BASE + path, data=data, method=method)
    request.add_header("Content-Type", "application/json")
    if token:
        request.add_header("Authorization", "Bearer " + token)
    try:
        resp = urllib.request.urlopen(request, timeout=timeout)
        text = resp.read().decode("utf-8", "replace")
        try:
            return resp.status, (json.loads(text) if text.strip() else {})
        except json.JSONDecodeError:
            return resp.status, text[:400]
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")[:600]


def upload(token, filepath):
    boundary = "----chumba" + uuid.uuid4().hex
    content = filepath.read_bytes()
    body = b"".join(
        [
            f"--{boundary}\r\n".encode(),
            f'Content-Disposition: form-data; name="file"; filename="{filepath.name}"\r\n'.encode(),
            b"Content-Type: application/pdf\r\n\r\n",
            content,
            b"\r\n",
            f"--{boundary}--\r\n".encode(),
        ]
    )
    request = urllib.request.Request(BASE + "/api/v1/files/", data=body, method="POST")
    request.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    request.add_header("Authorization", "Bearer " + token)
    try:
        resp = urllib.request.urlopen(request, timeout=600)
        return resp.status, json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")[:600]


def knowledge_items(token):
    status, res = req("GET", "/api/v1/knowledge/", token)
    if status != 200:
        return []
    if isinstance(res, list):
        return res
    if isinstance(res, dict):
        return res.get("items") or []
    return []


def find_knowledge(token, name):
    for item in knowledge_items(token):
        if isinstance(item, dict) and item.get("name") == name:
            return item
    return None


def existing_names(token, kid):
    names = set()
    status, details = req("GET", f"/api/v1/knowledge/{kid}", token)
    if status != 200 or not isinstance(details, dict):
        return names
    for entry in details.get("files") or []:
        if not isinstance(entry, dict):
            continue
        meta = entry.get("meta") or {}
        for candidate in (entry.get("filename"), entry.get("name"), entry.get("title"), meta.get("name")):
            if candidate:
                names.add(str(candidate))
    return names


def extracted_chars(token, file_id):
    status, res = req("GET", f"/api/v1/files/{file_id}/content", token)
    if status == 200 and isinstance(res, dict):
        content = res.get("content")
        if isinstance(content, str):
            return len(content)
    return None


def syllabus_files():
    """Набор имён PDF из карты тем (для --dry-run и предупреждения о неизвестных файлах)."""
    try:
        data = json.loads(SYLLABUS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return set()
    names = set()
    for sec in data.get("sections", []):
        for top in sec.get("topics", []):
            names.update(top.get("files", []))
    for task in data.get("tasks", []):
        if task.get("file"):
            names.add(task["file"])
    return names


def main():
    parser = argparse.ArgumentParser(description="Загрузка PDF в базу знаний Open WebUI")
    parser.add_argument("--dir", default=None, help="Папка с PDF (по умолчанию STUDY_DOCS_DIR)")
    parser.add_argument("--dry-run", action="store_true", help="Только показать план, без загрузки")
    parser.add_argument("--force", action="store_true", help="Перезаливать уже загруженные файлы")
    parser.add_argument("--only", default=None, help="Обработать только файлы, чьё имя содержит подстроку")
    parser.add_argument("--limit", type=int, default=0, help="Ограничить число файлов (0 = все)")
    args = parser.parse_args()

    cfg = load_env()
    docs_dir = Path(args.dir or cfg.get("STUDY_DOCS_DIR") or DEFAULT_DOCS_DIR)
    knowledge_name = cfg.get("STUDY_KNOWLEDGE_NAME") or "Системный анализ"

    if not docs_dir.is_dir():
        sys.exit(f"папка с документами не найдена: {docs_dir}")
    pdfs = sorted(p for p in docs_dir.glob("*.pdf") if p.is_file())
    if args.only:
        pdfs = [p for p in pdfs if args.only in p.name]
    if args.limit and args.limit > 0:
        pdfs = pdfs[: args.limit]
    known = syllabus_files()
    unknown = [p.name for p in pdfs if known and p.name not in known]

    print(f"docs_dir: {docs_dir}")
    print(f"knowledge: {knowledge_name}")
    print(f"pdf найдено: {len(pdfs)}; вне карты тем: {len(unknown)}")
    if unknown:
        for name in unknown:
            print(f"  ! вне syllabus: {name}")

    if args.dry_run:
        for path in pdfs:
            print(f"  {path.name}  ({path.stat().st_size // 1024} KiB)")
        return 0

    status, res = req(
        "POST",
        "/api/v1/auths/signin",
        body={"email": cfg.get("WEBUI_ADMIN_EMAIL", ""), "password": cfg.get("WEBUI_ADMIN_PASSWORD", "")},
    )
    if status != 200 or not isinstance(res, dict):
        sys.exit(f"signin failed: {status} {res}")
    token = res["token"]

    kb = find_knowledge(token, knowledge_name)
    if kb:
        kid = kb["id"]
        print(f"knowledge найден: {kid}")
    else:
        status, kb = req(
            "POST",
            "/api/v1/knowledge/create",
            token,
            {"name": knowledge_name, "description": "Материалы по системному анализу (study_ingest.py)"},
        )
        if status != 200 or not isinstance(kb, dict) or not kb.get("id"):
            sys.exit(f"knowledge create failed: {status} {kb}")
        kid = kb["id"]
        print(f"knowledge создан: {kid}")

    already = existing_names(token, kid)
    print(f"уже в базе: {len(already)}")

    uploaded = skipped = failed = 0
    low_text = []
    for path in pdfs:
        if path.name in already and not args.force:
            skipped += 1
            continue

        status, up = upload(token, path)
        if status != 200 or not isinstance(up, dict) or not up.get("id"):
            print(f"  FAIL upload {path.name}: {status} {str(up)[:200]}")
            failed += 1
            continue
        file_id = up["id"]

        deadline = time.time() + 600
        file_status = ""
        while time.time() < deadline:
            _, fdet = req("GET", f"/api/v1/files/{file_id}", token)
            if isinstance(fdet, dict):
                file_status = fdet.get("status") or (fdet.get("data") or {}).get("status") or ""
                if file_status in ("completed", "processed", "success") or fdet.get("data"):
                    break
            time.sleep(3)

        _, add = req("POST", f"/api/v1/knowledge/{kid}/file/add", token, {"file_id": file_id})
        chars = extracted_chars(token, file_id)
        note = f"{chars} симв." if chars is not None else "текст не проверен"
        if chars is not None and chars < 50:
            low_text.append(path.name)
            note += "  ! похоже на скан без текстового слоя"
        print(f"  OK {path.name}: status={file_status or '?'}, {note}")
        uploaded += 1

    print("-" * 60)
    print(f"knowledge_id={kid}")
    print(f"загружено={uploaded} пропущено={skipped} ошибок={failed} мало_текста={len(low_text)}")
    if low_text:
        print("мало текста (нужен OCR): " + ", ".join(low_text))
    print(f"KNOWLEDGE_ID={kid}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
