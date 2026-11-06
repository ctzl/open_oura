// Hook BoringSSL's AEAD API (used by Tink C++ / native crypto) and capture
// every 32-byte AES key passed to EVP_AEAD_CTX_init. Also hooks
// EVP_DecryptInit_ex for plain CBC/GCM paths.
// Run: frida -U -n Oura -l tools/frida/boringssl_key_hook.js
// (frida >= 17; no Java bridge needed.)
const seen = new Set();
function hex(p, n) {
  const u = new Uint8Array(p.readByteArray(n));
  let s = "";
  for (let i = 0; i < n; i++) s += ("0" + u[i].toString(16)).slice(-2);
  return s;
}
function report(tag, keyPtr) {
  try {
    const h = hex(keyPtr, 32);
    if (!seen.has(h)) { seen.add(h); console.log("AES-256 key (" + tag + "): " + h); }
  } catch (e) {}
}
// BoringSSL: int EVP_AEAD_CTX_init(EVP_AEAD_CTX *ctx, const EVP_AEAD *aead,
//                                  const uint8_t *key, size_t key_len, ...)
function findExport(name) {
  if (Module.findGlobalExportByName) return Module.findGlobalExportByName(name);
  return Module.findExportByName(null, name);
}
["EVP_AEAD_CTX_init", "EVP_AEAD_CTX_init_with_direction"].forEach(function (name) {
  const p = findExport(name);
  if (p) Interceptor.attach(p, {
    onEnter: function (args) { if (args[3].toInt32() === 32) report(name, args[2]); }
  });
});
// EVP_DecryptInit_ex(ctx, evp, impl, key[32], iv)
const d = findExport("EVP_DecryptInit_ex");
if (d) Interceptor.attach(d, {
  onEnter: function (args) { if (!args[3].isNull()) report("EVP_DecryptInit_ex", args[3]); }
});
