#!/usr/bin/env python3
"""Decrypt Oura's model assets from the pulled models APK into notes/models/.

The Oura app ships its TorchScript models as `assets/*.pt.enc` inside a split
APK (`split_oura_models.apk`) pulled off a logged-in device. Each blob is
AES-256-GCM framed as `iv(12) || ciphertext + 16-byte tag`, keyed by a
**server-delivered** key (not in the APK, never on disk in plaintext) that has
to be captured from the running app — see docs/model-assets.md for the capture
procedure. The key is read from `model_key.hex` in the repo root (gitignored).

Usage:
  python tools/decrypt_models.py [apk] [--key-file model_key.hex] [--out notes/models]
                                 [--only NAME_SUBSTRING] [--list]
"""
import argparse
import sys
import zipfile
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

REPO = Path(__file__).resolve().parent.parent
DEFAULT_APK = REPO / "tools" / "captures" / "android" / "split_oura_models.apk"
IV_LEN = 12  # GCM IV prefix; the rest is ciphertext + 16-byte tag


def main():
    p = argparse.ArgumentParser()
    p.add_argument("apk", nargs="?", default=str(DEFAULT_APK))
    p.add_argument("--key-file", default=str(REPO / "model_key.hex"))
    p.add_argument("--out", default=str(REPO / "notes" / "models"))
    p.add_argument("--only", default=None, help="only assets whose name contains this")
    p.add_argument("--list", action="store_true", help="list assets, don't write")
    args = p.parse_args()

    key = Path(args.key_file).read_text().strip().lower()
    if len(key) != 64:
        sys.exit(f"{args.key_file}: expected 64 hex chars, got {len(key)}")
    key = bytes.fromhex(key)

    apk = Path(args.apk)
    if not apk.is_file():
        sys.exit(f"apk not found: {apk} (pull it off the phone — see docs/model-assets.md)")
    z = zipfile.ZipFile(apk)
    names = [n for n in z.namelist() if n.endswith(".pt.enc")]
    if args.only:
        names = [n for n in names if args.only in n]
    if not names:
        sys.exit("no matching .pt.enc assets")

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    ok, bad = 0, []
    for n in sorted(names):
        base = n.split("/")[-1][: -len(".enc")]  # foo.pt.enc -> foo.pt
        data = z.read(n)
        try:
            pt = AESGCM(key).decrypt(data[:IV_LEN], data[IV_LEN:], None)
        except Exception as e:
            bad.append((base, str(e)))
            print(f"FAIL {base}: {e}")
            continue
        if args.list:
            print(f"OK   {base}  {len(data)} -> {len(pt)} bytes  magic={pt[:4]!r}")
            ok += 1
            continue
        (out / base).write_bytes(pt)
        magic_ok = pt[:4] == b"PK\x03\x04"  # TorchScript = a zip container
        print(f"WROTE {out / base}  {len(pt)} bytes{'  (PK zip magic OK)' if magic_ok else '  (UNEXPECTED MAGIC!)'}")
        ok += 1
    print(f"\n{ok} decrypted, {len(bad)} failed -> {out}")
    if bad:
        print("wrong key? re-capture it: see docs/model-assets.md")
        sys.exit(1)


if __name__ == "__main__":
    main()
