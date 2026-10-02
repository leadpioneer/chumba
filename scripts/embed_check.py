#!/usr/bin/env python3
"""Проверка эмбеддингов bge-m3: размерность и семантическая близость RU/EN."""

import json
import math
import sys
import urllib.request

URL = "http://127.0.0.1:8087/v1/embeddings"


def embed(texts):
    data = json.dumps({"model": "bge-m3", "input": texts}).encode("utf-8")
    req = urllib.request.Request(URL, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    resp = urllib.request.urlopen(req, timeout=180)
    payload = json.loads(resp.read().decode("utf-8", "replace"))
    return [item["embedding"] for item in payload["data"]]


def cos(a, b):
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb)


def main():
    vecs = embed(
        [
            "Как увеличить контекст в llama.cpp?",
            "How to increase the context size in llama.cpp?",
            "Кошка сидит на окне",
            "Банк выдал кредит на строительство",
        ]
    )
    related = cos(vecs[0], vecs[1])
    unrelated = cos(vecs[2], vecs[3])
    print("dim=", len(vecs[0]), "count=", len(vecs))
    print("cos_ru_en_related =", round(related, 4))
    print("cos_unrelated     =", round(unrelated, 4))
    ok = len(vecs[0]) == 1024 and related > unrelated + 0.1
    print("EMBED_OK=" + str(ok))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
