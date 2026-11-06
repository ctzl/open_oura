#!/usr/bin/env python3
"""Try captured AES-256 keys against the .pt.enc assets.
Format per docs/algorithms/sleepnet.md: [12-byte IV][AES-GCM ct + 16-byte tag].
Usage: python tools/try_model_keys.py <apk-or-dir> [key_hex ...]"""
import sys, zipfile, io
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

KEYS = [
    "deadbeef00000000000000000000000000000000000000000000000000000001",
    "deadbeef00000000000000000000000000000000000000000000000000000002",
    "deadbeef00000000000000000000000000000000000000000000000000000003",
    "deadbeef00000000000000000000000000000000000000000000000000000004",
]

def try_key(data, key_hex):
    iv, ct = data[:12], data[12:]
    try:
        pt = AESGCM(bytes.fromhex(key_hex)).decrypt(iv, ct, None)
        return pt
    except Exception:
        return None

def main():
    src = sys.argv[1]
    keys = sys.argv[2:] or KEYS
    if src.endswith(".apk"):
        z = zipfile.ZipFile(src)
        items = [(n, z.read(n)) for n in z.namelist() if n.endswith(".pt.enc")]
    else:
        import os
        items = []
        for root, _, files in os.walk(src):
            for f in files:
                if f.endswith(".pt.enc"):
                    items.append((f, open(os.path.join(root, f), "rb").read()))
    hits = {}
    for name, data in items:
        for k in keys:
            pt = try_key(data, k)
            if pt is not None:
                magic = pt[:4]
                print(f"OK  {name}  key={k[:16]}...  magic={magic!r}")
                hits.setdefault(k, []).append(name)
                break
        else:
            print(f"--  {name}: no key worked")
    print()
    for k, names in hits.items():
        print(f"key {k} decrypts {len(names)} models")

if __name__ == "__main__":
    main()
