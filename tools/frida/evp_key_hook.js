// Capture AES-256 keys at every native EVP init (global exports — works even
// when javax.crypto drives the crypto, since JCE calls into libcrypto).
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
function findExport(name) {
  if (Module.findGlobalExportByName) return Module.findGlobalExportByName(name);
  return Module.findExportByName(null, name);
}
let hooked = 0;
[
  ["EVP_AEAD_CTX_init", function (a) { if (a[3].toInt32() === 32) report("AEAD_CTX_init", a[2]); }],
  ["EVP_AEAD_CTX_init_with_direction", function (a) { if (a[3].toInt32() === 32) report("AEAD_init_dir", a[2]); }],
  ["EVP_DecryptInit_ex", function (a) { if (!a[3].isNull()) report("DecryptInit_ex", a[3]); }],
  ["EVP_CipherInit_ex", function (a) { if (!a[3].isNull()) report("CipherInit_ex", a[3]); }],
  ["EVP_EncryptInit_ex", function (a) { if (!a[3].isNull()) report("EncryptInit_ex", a[3]); }],
].forEach(function (spec) {
  const p = findExport(spec[0]);
  if (!p) { console.log("no export: " + spec[0]); return; }
  Interceptor.attach(p, { onEnter: spec[1] });
  hooked++;
});
console.log("hooked " + hooked + " EVP functions");
