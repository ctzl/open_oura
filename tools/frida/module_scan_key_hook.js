// Capture AES-256 keys used to decrypt the .pt.enc models.
// BoringSSL may be statically linked inside app .so files, so we scan every
// loaded module's symbol table for EVP init functions, not just global exports.
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
const TARGETS = {
  "EVP_AEAD_CTX_init": function (a) { if (a[3].toInt32() === 32) report("AEAD_CTX_init", a[2]); },
  "EVP_AEAD_CTX_init_with_direction": function (a) { if (a[3].toInt32() === 32) report("AEAD_init_dir", a[2]); },
  "EVP_DecryptInit_ex": function (a) { if (!a[3].isNull()) report("DecryptInit_ex", a[3]); },
  "EVP_CipherInit_ex": function (a) { if (!a[3].isNull()) report("CipherInit_ex", a[3]); },
  "EVP_EncryptInit_ex": function (a) { if (!a[3].isNull()) report("EncryptInit_ex", a[3]); },
};
let hooked = 0;
// also trace model-asset opens so we know when a load is attempted
["open", "openat", "fopen"].forEach(function (name) {
  const p = (Module.findGlobalExportByName || Module.findExportByName).call(Module, name);
  if (!p) return;
  Interceptor.attach(p, { onEnter: function (a) {
    try {
      const s = (name === "openat" ? a[1] : a[0]).readCString();
      if (s && (s.includes("models.apk") || s.includes(".pt"))) console.log("OPEN[" + name + "]: " + s);
    } catch (e) {}
  }});
});
Process.enumerateModules().forEach(function (m) {
  let syms;
  try { syms = m.enumerateExports(); } catch (e) { return; }
  syms.forEach(function (s) {
    const handler = TARGETS[s.name];
    if (!handler || s.type !== "function" || s.address.isNull()) return;
    try {
      Interceptor.attach(s.address, { onEnter: handler });
      hooked++;
    } catch (e) {}
  });
});
console.log("hooked " + hooked + " EVP functions across " + Process.enumerateModules().length + " modules");
