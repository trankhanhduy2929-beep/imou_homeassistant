"""Short-lived token-protected pages for Imou visual CAPTCHA."""

from __future__ import annotations

import html
import json
from typing import Any

from .api import ImouCaptchaChallenge

GEETEST_PACKAGE_NAME = "com.mm.android.smartlifeiot"
GEETEST_APP_VERSION = "10.1.6"
GEETEST_BUILD = "500542"
GEETEST_CLIENT_VERSION = "1.8.11"
GEETEST_CLIENT_TYPE = "android"


def geetest_config(
    challenge: ImouCaptchaChallenge,
    fingerprint: str,
    first_seen: int,
) -> dict[str, Any]:
    """Build the parameter object assembled by GeeLab SDK 1.8.11."""
    return {
        "product": "bind",
        "displayArea": "center",
        "displayMode": 1,
        "protocol": "https://",
        "captchaId": challenge.captcha_id,
        "challenge": challenge.web_challenge,
        "debug": False,
        "language": "vi-vn",
        "apiServers": [challenge.captcha_server],
        "timeout": 10000,
        "clientVersion": GEETEST_CLIENT_VERSION,
        "clientType": GEETEST_CLIENT_TYPE,
        "mask": {"outside": False},
        "mi": {
            "geeid": {
                "bd": "$unknown",
                "d": "$unknown",
                "e": "$unknown",
                "fp": fingerprint,
                "ts": str(first_seen),
                "ver": "1.0.0",
                "client_type": GEETEST_CLIENT_TYPE,
            },
            "packageName": GEETEST_PACKAGE_NAME,
            "displayName": "Imou%20Life",
            "appVer": GEETEST_APP_VERSION,
            "build": GEETEST_BUILD,
            "clientVersion": GEETEST_CLIENT_VERSION,
        },
    }


def _head(base_path: str) -> str:
    return f"""<!doctype html>
<html lang="vi"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<base href="{html.escape(base_path, quote=True)}"><meta name="referrer" content="no-referrer"><title>Xác minh Imou Connect</title>
<style>:root{{color-scheme:light dark;font-family:system-ui,sans-serif}}body{{padding:24px;text-align:center}}main{{max-width:620px;margin:auto}}#captcha-shell{{min-height:330px;display:grid;place-items:center;margin:18px auto;padding:12px;border:1px solid #7775;border-radius:12px;background:#fff;color:#111}}#captcha{{width:100%;min-height:300px}}button{{border:0;border-radius:8px;padding:12px 20px;font:inherit;font-weight:600;background:#687078;color:#fff;cursor:pointer}}button:disabled{{cursor:wait;opacity:.55}}#status{{white-space:pre-wrap;min-height:52px;margin:18px 0}}.hint{{font-size:.9rem;opacity:.75}}form{{display:flex;gap:10px;justify-content:center;align-items:center;flex-wrap:wrap}}input{{font:inherit;font-size:1.25rem;letter-spacing:.25rem;text-align:center;width:10rem;padding:10px;border:1px solid #777;border-radius:8px}}#captcha-image{{min-width:150px;min-height:50px;image-rendering:auto;background:#fff;border:10px solid #fff;border-radius:8px}}</style></head>"""


def _intro() -> str:
    return """<body><main><h2>Xác minh Imou Connect</h2>
<p>Hoàn tất CAPTCHA do Imou cung cấp để Home Assistant tiếp tục đăng nhập bằng account/password.</p>"""


def _footer() -> str:
    return """<p class="hint">Trang chỉ gửi kết quả CAPTCHA về Home Assistant. Account, password, verifyToken và app credential không được đưa vào trình duyệt.</p></main></body></html>"""


def _poll_script(generation: int) -> str:
    return """<script>
async function readStatus() {{ try {{ const response = await fetch("status", {{cache:"no-store"}}); const data = await response.json(); if (data.complete) {{ document.getElementById("status").textContent = data.message || "Đã xác minh. Bạn có thể đóng trang này."; return; }} if (data.generation !== generation) location.reload(); }} catch (error) {{}} }}
setInterval(readStatus, 2000);
</script>""".replace("{{", "{").replace("}}", "}")


def render_captcha_page(
    base_path: str,
    challenge: ImouCaptchaChallenge,
    generation: int,
    fingerprint: str,
    first_seen: int,
) -> str:
    """Render GeeTest4 or the four-character fallback image form."""
    if challenge.is_image:
        return (
            _head(base_path)
            + _intro()
            + f"""<p><img id="captcha-image" src="image?g={generation}" alt="Ảnh CAPTCHA Imou"></p>
<form id="captcha-form"><input id="captcha-code" name="code" inputmode="text" autocomplete="off" autocapitalize="characters" minlength="4" maxlength="4" pattern="[A-Za-z0-9]{{4}}" required aria-label="Mã CAPTCHA bốn ký tự"><button id="submit" type="submit">Xác minh</button></form>
<p><button id="refresh" type="button">Đổi ảnh CAPTCHA</button></p><div id="status" role="status" aria-live="polite">Nhập bốn ký tự trong ảnh.</div>
<script>
const generation = {generation};
const status = document.getElementById("status");
async function refreshCaptcha() {{ document.getElementById("refresh").disabled = true; status.textContent = "Đang lấy CAPTCHA mới…"; try {{ const response = await fetch("refresh", {{method:"POST"}}); const data = await response.json(); if (!response.ok || !data.ok) throw new Error(data.error || "Không đổi được CAPTCHA."); location.reload(); }} catch (error) {{ status.textContent = error.message; document.getElementById("refresh").disabled = false; }} }}
document.getElementById("refresh").addEventListener("click", refreshCaptcha);
document.getElementById("captcha-form").addEventListener("submit", async function(event) {{ event.preventDefault(); const button = document.getElementById("submit"); button.disabled = true; status.textContent = "Đang kiểm tra CAPTCHA…"; try {{ const response = await fetch("submit", {{method:"POST",headers:{{"Content-Type":"application/json"}},body:JSON.stringify({{generation:generation,code:document.getElementById("captcha-code").value}})}}); const data = await response.json(); if (!response.ok || !data.ok) {{ if (data.generation !== generation) location.reload(); throw new Error(data.error || "CAPTCHA không hợp lệ."); }} status.textContent = data.message || "Xác minh thành công. Quay lại Home Assistant để tiếp tục."; }} catch (error) {{ status.textContent = error.message; button.disabled = false; }} }});
</script>"""
            + _poll_script(generation)
            + _footer()
        )

    config_json = json.dumps(
        geetest_config(challenge, fingerprint, first_seen),
        ensure_ascii=False,
        separators=(",", ":"),
    ).replace("</", "<\\/")
    return (
        _head(base_path)
        + _intro()
        + f"""<div id="captcha-shell"><div id="captcha"></div></div>
<p><button id="refresh" type="button">Tạo CAPTCHA mới</button></p><div id="status" role="status" aria-live="polite">Đang tải CAPTCHA hình ảnh…</div>
<script src="gl4.js"></script><script>
const generation = {generation};
const status = document.getElementById("status");
const config = {config_json};
let submitting = false;
async function sendResult(result) {{ if (submitting) return; submitting = true; status.textContent = "Đã xác minh trên GeeTest. Đang gửi kết quả tới Imou…"; try {{ const response = await fetch("submit", {{method:"POST",headers:{{"Content-Type":"application/json"}},body:JSON.stringify(Object.assign({{generation:generation}},result || {{}}))}}); const data = await response.json(); if (!response.ok || !data.ok) {{ if (data.generation !== generation) {{ location.reload(); return; }} throw new Error(data.error || "Imou không chấp nhận CAPTCHA."); }} status.textContent = data.message || "CAPTCHA hợp lệ. Quay lại Home Assistant để tiếp tục."; }} catch (error) {{ status.textContent = error.message; submitting = false; }} }}
async function refreshCaptcha() {{ document.getElementById("refresh").disabled = true; status.textContent = "Đang tạo CAPTCHA mới…"; try {{ const response = await fetch("refresh", {{method:"POST"}}); const data = await response.json(); if (!response.ok || !data.ok) throw new Error(data.error || "Không tạo được CAPTCHA mới."); location.reload(); }} catch (error) {{ status.textContent = error.message; document.getElementById("refresh").disabled = false; }} }}
document.getElementById("refresh").addEventListener("click", refreshCaptcha);
config.onError = function(error) {{ status.textContent = "GeeTest không tải được CAPTCHA. Hãy bấm tạo CAPTCHA mới."; }};
if (typeof window.initGeetest4 !== "function") {{ status.textContent = "Không tải được thư viện GeeTest từ APK."; }} else {{ window.initGeetest4(config, function(captchaObj) {{ window.imouCaptcha = captchaObj; captchaObj.appendTo(document.getElementById("captcha")).onReady(function() {{ status.textContent = "Hãy hoàn tất CAPTCHA hình ảnh."; if (typeof captchaObj.showCaptcha === "function") captchaObj.showCaptcha(); else if (typeof captchaObj.showBox === "function") captchaObj.showBox(); }}).onSuccess(function() {{ sendResult(captchaObj.getValidate()); }}).onClose(function() {{ status.textContent = "CAPTCHA đã đóng. Bấm tạo CAPTCHA mới để thử lại."; }}).onError(config.onError).onFail(function() {{ status.textContent = "GeeTest báo xác minh chưa đạt. Hãy thử lại."; }}); }}); }}
</script>"""
        + _poll_script(generation)
        + _footer()
    )
