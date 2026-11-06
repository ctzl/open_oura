#!/usr/bin/env python3
"""Attach frida to the running Oura app and print AES-256 keys it uses to
decrypt (see tools/frida/cipher_key_hook.js). Prints keys to stdout, exits
after --seconds. Usage: python tools/capture_model_key.py [--seconds 120]"""
import argparse, sys, time
import frida

JS = open(__file__.replace("capture_model_key.py", "frida/cipher_key_hook.js"), encoding="utf-8").read()

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--seconds", type=int, default=120)
    p.add_argument("--pid", type=int, default=None)
    p.add_argument("--spawn", action="store_true", help="launch the app under frida (cold start)")
    p.add_argument("--js", default=None, help="override hook script path")
    a = p.parse_args()
    global JS
    if a.js:
        JS = open(a.js, encoding="utf-8").read()
    dev = frida.get_usb_device(timeout=10)
    if a.spawn:
        pid = dev.spawn(["com.ouraring.oura"])
        session = dev.attach(pid)
    elif a.pid:
        session = dev.attach(a.pid)
    else:
        session = dev.attach("Oura")
    keys = []
    def on_message(m, d):
        if m["type"] == "log":
            text = m.get("payload", "")
            if "AES-256 key" in text:
                keys.append(text.rsplit(": ", 1)[-1].strip())
                print("KEY", keys[-1], flush=True)
            else:
                print("LOG", text, flush=True)
        elif m["type"] == "error":
            print("ERR", m.get("description", m), file=sys.stderr, flush=True)
        else:
            print("MSG", m, flush=True)
    script = session.create_script(JS)
    script.on("message", on_message)
    script.load()
    if a.spawn:
        dev.resume(pid)
    t0 = time.time()
    while time.time() - t0 < a.seconds:
        time.sleep(1)
    print("DONE", len(keys), "keys", flush=True)
    session.detach()

if __name__ == "__main__":
    main()
