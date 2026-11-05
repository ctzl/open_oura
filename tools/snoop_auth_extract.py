#!/usr/bin/env python3
"""Extract the Oura app-auth (nonce, ciphertext) pair from a btsnoop HCI log and
brute-force candidate 16-byte keys offline.

The ring challenges with a 15-byte nonce (Oura packet ext 0x2c); the app replies
with AES-128/ECB/PKCS7(key, nonce) (ext 0x2d, 16-byte ciphertext). Given both,
key recovery is a pure offline brute force — no BLE needed.

Usage: python snoop_auth_extract.py btsnoop_hci.log candidates.txt
"""
import struct
import sys

# ---------- minimal AES-128 single-block encryption ----------
SBOX = [
    0x63,0x7c,0x77,0x7b,0xf2,0x6b,0x6f,0xc5,0x30,0x01,0x67,0x2b,0xfe,0xd7,0xab,0x76,
    0xca,0x82,0xc9,0x7d,0xfa,0x59,0x47,0xf0,0xad,0xd4,0xa2,0xaf,0x9c,0xa4,0x72,0xc0,
    0xb7,0xfd,0x93,0x26,0x36,0x3f,0xf7,0xcc,0x34,0xa5,0xe5,0xf1,0x71,0xd8,0x31,0x15,
    0x04,0xc7,0x23,0xc3,0x18,0x96,0x05,0x9a,0x07,0x12,0x80,0xe2,0xeb,0x27,0xb2,0x75,
    0x09,0x83,0x2c,0x1a,0x1b,0x6e,0x5a,0xa0,0x52,0x3b,0xd6,0xb3,0x29,0xe3,0x2f,0x84,
    0x53,0xd1,0x00,0xed,0x20,0xfc,0xb1,0x5b,0x6a,0xcb,0xbe,0x39,0x4a,0x4c,0x58,0xcf,
    0xd0,0xef,0xaa,0xfb,0x43,0x4d,0x33,0x85,0x45,0xf9,0x02,0x7f,0x50,0x3c,0x9f,0xa8,
    0x51,0xa3,0x40,0x8f,0x92,0x9d,0x38,0xf5,0xbc,0xb6,0xda,0x21,0x10,0xff,0xf3,0xd2,
    0xcd,0x0c,0x13,0xec,0x5f,0x97,0x44,0x17,0xc4,0xa7,0x7e,0x3d,0x64,0x5d,0x19,0x73,
    0x60,0x81,0x4f,0xdc,0x22,0x2a,0x90,0x88,0x46,0xee,0xb8,0x14,0xde,0x5e,0x0b,0xdb,
    0xe0,0x32,0x3a,0x0a,0x49,0x06,0x24,0x5c,0xc2,0xd3,0xac,0x62,0x91,0x95,0xe4,0x79,
    0xe7,0xc8,0x37,0x6d,0x8d,0xd5,0x4e,0xa9,0x6c,0x56,0xf4,0xea,0x65,0x7a,0xae,0x08,
    0xba,0x78,0x25,0x2e,0x1c,0xa6,0xb4,0xc6,0xe8,0xdd,0x74,0x1f,0x4b,0xbd,0x8b,0x8a,
    0x70,0x3e,0xb5,0x66,0x48,0x03,0xf6,0x0e,0x61,0x35,0x57,0xb9,0x86,0xc1,0x1d,0x9e,
    0xe1,0xf8,0x98,0x11,0x69,0xd9,0x8e,0x94,0x9b,0x1e,0x87,0xe9,0xce,0x55,0x28,0xdf,
    0x8c,0xa1,0x89,0x0d,0xbf,0xe6,0x42,0x68,0x41,0x99,0x2d,0x0f,0xb0,0x54,0xbb,0x16,
]
RCON = [0x01,0x02,0x04,0x08,0x10,0x20,0x40,0x80,0x1b,0x36]

def xtime(a):
    a <<= 1
    if a & 0x100:
        a = (a ^ 0x1b) & 0xff
    return a

def key_expansion(key):
    w = [list(key[i*4:i*4+4]) for i in range(4)]
    for i in range(4, 44):
        t = list(w[i-1])
        if i % 4 == 0:
            t = t[1:] + t[:1]
            t = [SBOX[b] for b in t]
            t[0] ^= RCON[i//4 - 1]
        w.append([w[i-4][j] ^ t[j] for j in range(4)])
    return w

def aes_encrypt_block(key_sched, block):
    s = [list(block[r::4]) for r in range(4)]  # column-major state
    def add_round_key(rnd):
        for c in range(4):
            for r in range(4):
                s[r][c] ^= key_sched[rnd*4 + c][r]
    add_round_key(0)
    for rnd in range(1, 10):
        s = [[SBOX[b] for b in row] for row in s]
        for r in range(1, 4):
            s[r] = s[r][r:] + s[r][:r]
        for c in range(4):
            a = [s[r][c] for r in range(4)]
            s[0][c] = xtime(a[0]) ^ (xtime(a[1]) ^ a[1]) ^ a[2] ^ a[3]
            s[1][c] = a[0] ^ xtime(a[1]) ^ (xtime(a[2]) ^ a[2]) ^ a[3]
            s[2][c] = a[0] ^ a[1] ^ xtime(a[2]) ^ (xtime(a[3]) ^ a[3])
            s[3][c] = (xtime(a[0]) ^ a[0]) ^ a[1] ^ a[2] ^ xtime(a[3])
        add_round_key(rnd)
    s = [[SBOX[b] for b in row] for row in s]
    for r in range(1, 4):
        s[r] = s[r][r:] + s[r][:r]
    add_round_key(10)
    return bytes(s[r][c] for c in range(4) for r in range(4))

def encrypt_nonce(key16, nonce):
    block = bytearray(nonce[:16])
    pad = 16 - len(nonce)
    block.extend([pad] * pad)
    return aes_encrypt_block(key_expansion(list(key16)), bytes(block))

# ---------- btsnoop parsing ----------
def iter_hci_packets(path):
    with open(path, "rb") as f:
        hdr = f.read(16)
        if hdr[:8] != b"btsnoop\x00":
            raise SystemExit("not a btsnoop file")
        while True:
            rh = f.read(24)
            if len(rh) < 24:
                return
            orig, incl, flags, drops, ts = struct.unpack(">iiiiq", rh)
            length = min(orig, incl)
            pkt = f.read(length)
            if len(pkt) < length:
                return
            if pkt and pkt[0] == 0x02:  # HCI ACL data packet
                yield orig, flags, pkt[1:]

def att_payloads(path):
    """Yield (direction, att_payload_bytes) for HCI ACL packets carrying ATT."""
    for orig, flags, pkt in iter_hci_packets(path):
        if len(pkt) < 6:
            continue
        # ACL: 2-byte handle/flags, 2-byte total length, then L2CAP
        info = struct.unpack(">H", pkt[:2])[0]
        total = struct.unpack("<H", pkt[2:4])[0]
        body = pkt[4:4+total]
        while len(body) >= 4:
            l2_len = struct.unpack("<H", body[:2])[0]
            cid = struct.unpack("<H", body[2:4])[0]
            payload = body[4:4+l2_len]
            if cid in (0x0004, 0x0005) or cid >= 0x0040:  # LE ATT / dynamic ATT
                yield ("sent" if orig == 1 else "rcvd"), payload
            body = body[4+l2_len:]

def find_auth_pairs(path):
    """Find (nonce, ciphertext) pairs from Oura ext 0x2c (nonce) + 0x2f/0x2d (auth)."""
    pairs = []
    pending_nonce = None
    for direction, p in att_payloads(path):
        # ATT: opcode(1) handle(2 LE) value...
        if len(p) < 4:
            continue
        opcode = p[0]
        if opcode in (0x12, 0x52, 0x1b, 0x1d):  # write req/cmd, handle val notif/ind
            value = p[3:]
        else:
            continue
        # scan value for Oura packets: [tag, len, ...]
        i = 0
        while i < len(value) - 1:
            tag, ln = value[i], value[i+1]
            if tag == 0x2f and 0 < ln <= len(value) - i - 2:
                ext = value[i+2]
                if ext == 0x2c and ln >= 2:  # nonce response
                    pending_nonce = value[i+3:i+2+ln]
                elif ext == 0x2d and ln >= 17:  # authenticate request
                    ct = value[i+3:i+2+ln]
                    if pending_nonce:
                        pairs.append((pending_nonce, ct))
                    pending_nonce = None
                i += 2 + ln
                continue
            i += 1
    return pairs

def main():
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)
    snoop, cand_file = sys.argv[1], sys.argv[2]
    pairs = find_auth_pairs(snoop)
    if not pairs:
        print("No (nonce, ciphertext) auth pair found in the snoop log.")
        return 1
    print(f"Found {len(pairs)} auth exchange(s) in the snoop:")
    for nonce, ct in pairs:
        print(f"  nonce={nonce.hex()} ct={ct.hex()}")
    keys = []
    with open(cand_file) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            hexpart = line.split("\t")[-1].strip()
            if len(hexpart) == 32:
                keys.append(bytes.fromhex(hexpart))
    keys = list(dict.fromkeys(keys))
    print(f"Testing {len(keys)} candidate keys offline...")
    for nonce, ct in pairs:
        for k in keys:
            if encrypt_nonce(k, nonce) == ct:
                print(f"\n*** KEY FOUND: {k.hex()} ***")
                return 0
    print("No candidate key matches the captured auth exchange.")
    return 2

if __name__ == "__main__":
    sys.exit(main())
