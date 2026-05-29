#!/usr/bin/env python3
"""Simple strategy performance vector store (no external deps)."""
import json, time, sys
from pathlib import Path

STORE = Path(__file__).resolve().parent / "embeddings" / "strategies.json"
STORE.parent.mkdir(parents=True, exist_ok=True)
if not STORE.exists():
    STORE.write_text(json.dumps({}, indent=2))

def upsert(sid, vector):
    data = json.loads(STORE.read_text())
    data[sid] = {"ts": time.time(), "vector": vector}
    STORE.write_text(json.dumps(data, indent=2))

def search(target):
    data = json.loads(STORE.read_text())
    def dot(a,b): return sum(x*y for x,y in zip(a,b))
    ranked = []
    for sid, item in data.items():
        ranked.append((sid, dot(target, item["vector"])))
    ranked.sort(key=lambda x: x[1], reverse=True)
    return ranked[:3]

if __name__ == "__main__":
    # test
    upsert("sma_cross", [0.8, 0.2, 0.5])
    print(search([0.7, 0.3, 0.4]))
