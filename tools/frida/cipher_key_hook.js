// Capture AES-256 keys handed to javax.crypto.Cipher — catches the Tink
// AES-GCM decryption of *.pt.enc model assets (PyTorchModelAsset).
// Run:  adb root && adb push tools/frida-server /data/local/tmp/ && \
//       adb shell "/data/local/tmp/frida-server &" && \
//       frida -U -n Oura -l tools/frida/cipher_key_hook.js
// The app decrypts models during daily calculations; keep it attached and
// open the Oura app's day view to trigger one.
Java.perform(() => {
  const seen = new Set();
  const Cipher = Java.use('javax.crypto.Cipher');
  Cipher.init.overload('int', 'java.security.Key').implementation = function (mode, key) {
    try {
      if (mode === 2) { // DECRYPT_MODE
        const enc = key.getEncoded();
        if (enc !== null && enc.length === 32) {
          let hex = '';
          for (let i = 0; i < enc.length; i++) hex += ('0' + (enc[i] & 0xff).toString(16)).slice(-2);
          if (!seen.has(hex)) { seen.add(hex); console.log('AES-256 key: ' + hex); }
        }
      }
    } catch (e) {}
    return this.init(mode, key);
  };
});
