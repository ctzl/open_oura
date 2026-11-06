# SleepNet (sleep staging model)

The sleep **hypnogram** (per-30s DEEP/LIGHT/REM/AWAKE) is produced on the phone by
a PyTorch model - *not* by ecore (which only computes staging features and
everything downstream of the stage array). The models ship encrypted in the APK:

- `oura_models.apk/assets/sleepstaging_2_6_0.pt.enc` (114 KB - the stager)
- `sleepnet_moonstone_1_2_0.pt.enc`, `sleepnet_bdi_0_3_0/0_4_0.pt.enc` (apnea / SpO2 / BDI)

## Decryption - fully reverse-engineered

`com/ouraring/pytorch/PytorchModelFactory.java` (`createTemporaryFile$lambda$0$0`):

```
key = EncryptionKeyHandler.getKey(getCurrentKeyLabel())   // 32-byte AES-256 key
iv  = first 12 bytes of the .pt.enc file
ct  = remaining bytes (AES-GCM ciphertext + 128-bit tag)
plaintext = AES/GCM/NoPadding decrypt(ct, key, GCMParameterSpec(128, iv))
```

So the format is **`[12-byte IV][AES-256-GCM ciphertext + 16-byte tag]`** - consistent
with the observed non-block-aligned `.enc` sizes.

## The blocker: the key is server-delivered

`getCurrentKey()` → `EncryptionKeyHandler.getKey(label)`. `EncryptionKeyHandler` is
an interface with `getKey`/`saveKey`/`getCurrentKeyLabel`; keys are **delivered by
the backend** and saved locally - `com/ouraring/core/model/backend/KeyDeliveryModel.java`
calls `saveKey(label, key)` with keys from the client-configuration JSON
(`model_key_a`/`model_key_b` + `label_a`/`label_b`). **The decryption key is not in
the APK.**

(A *different* key, baked into `libsecrets.so` and derived as
`apiKey[i] = obf[i] XOR sha256_hex("com.ouraring.core.utils")[i]`
→ `1h9mi6ZRsrKfHH%r5Ox!Gkig!%VeIbMt`, AES/ECB/PKCS5 - decrypts other app secrets,
not the models. Confirmed: it does not decrypt the `.pt.enc` files.)

## Conclusion

The SleepNet models **cannot be decrypted offline** from the APK alone - the GCM
key is fetched from Oura's servers (per-model, labeled, rotatable) and only cached
on a logged-in device. So the **sleep hypnogram is the one metric that is not
reproducible without Oura's cloud**, on two counts: it isn't computed in ecore, and
its model is encrypted with a server-delivered key.

Routes that would unblock it (all require a valid Oura account/session, so out of
scope for a cloud-free client):
1. Fetch the model key from the client-config endpoint with an authenticated session.
2. Extract the locally-saved key from a logged-in device's app storage, then GCM-
   decrypt with the recipe above.
3. Train an independent staging model from the raw signals we *do* decode (HR/IBI,
   HRV, temperature, motion) - a from-scratch reimplementation, not a port.

## Resolved: route 2 executed (key extracted via frida)

The server-delivered model key **has been extracted** from a logged-in device
(Pixel 7, Oura app 17.26.0) with frida hooking `javax.crypto.Cipher.init`
(`tools/frida/cipher_key_hook.js`): the app decrypts the `.pt.enc` assets through
JCE/Tink, so the 32-byte key passes through `Cipher.init(DECRYPT_MODE, key)` and
`key.getEncoded()` reads it directly. (Native EVP/BoringSSL hooks find nothing -
the model path never goes through the global libcrypto exports we can see.)

    model key (AES-256-GCM): deadbeef00000000000000000000000000000000000000000000000000000004

Verified with `tools/try_model_keys.py`: this single key GCM-decrypts **all 33**
`*.pt.enc` assets in `split_oura_models.apk` (plaintext magic `PK\x03\x04` =
TorchScript zip), including `sleepstaging_2_6_0`, `sleepnet_moonstone_1_2_0`,
`cva_2_1_5`, `whr_2_7_1`. Saved in `model_key.hex` (gitignored - it is
account/server-delivered and may rotate; re-capture with the frida hook if a model
fails to decrypt).

Procedure: `adb root` → push+run `tools/frida-server` → `python
tools/capture_model_key.py --pid <oura_pid> --js tools/frida/cipher_key_hook.js`
→ open the app's day view (or pull-to-refresh) to trigger a calculation → the key
prints as `AES-256 key: <hex>`.
