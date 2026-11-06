// Diagnostic: list crypto modules, hook open() for .pt files, hook all EVP init variants.
const mods = Process.enumerateModules().map(m => m.name).filter(n =>
  /crypto|ssl|sodium|tinycrypt|conscrypt|jni|flutter|oura/i.test(n));
console.log("MODULES: " + JSON.stringify(mods));

function findExport(name) {
  if (Module.findGlobalExportByName) return Module.findGlobalExportByName(name);
  return Module.findExportByName(null, name);
}

// libc open/openat: log any open of a .pt file
["open", "openat", "fopen"].forEach(function (name) {
  const p = findExport(name);
  if (!p) return;
  Interceptor.attach(p, {
    onEnter: function (args) {
      const pathArg = name === "openat" ? args[1] : args[0];
      try {
        const s = pathArg.readCString();
        if (s && (s.includes(".pt") || s.includes("model") || s.includes("assa")))
          console.log("OPEN[" + name + "]: " + s);
      } catch (e) {}
    }
  });
});

// every EVP cipher init variant
["EVP_CipherInit_ex", "EVP_CipherInit", "EVP_EncryptInit_ex", "EVP_DecryptInit_ex",
 "EVP_AEAD_CTX_init", "EVP_AEAD_CTX_seal", "EVP_AEAD_CTX_open",
 "EVP_CIPHER_CTX_ctrl"].forEach(function (name) {
  const p = findExport(name);
  if (!p) { console.log("no export: " + name); return; }
  Interceptor.attach(p, { onEnter: function (args) { console.log("CALL: " + name); } });
});
