# Chuẩn bị

Add-on cần Home Assistant OS hoặc Supervised, MQTT integration và một MQTT broker cung cấp Supervisor MQTT service, ví dụ Mosquitto broker add-on.

# Gỡ custom integration cũ

1. Xóa cấu hình **Imou Life** trong **Settings → Devices & services**.
2. Xóa thư mục `/config/custom_components/imou_life`.
3. Restart Home Assistant.

# Cài add-on

1. Chép thư mục `imou_life_bridge` vào `/addons/imou_life_bridge`.
2. Vào **Settings → Add-ons → Add-on Store**, chọn **Check for updates**.
3. Cài **Imou Life Bridge**.
4. Trong tab **Configuration**, nhập account/password Imou Life rồi lưu. Số Việt Nam có thể nhập dạng `084…`, `84…`, `+84…` hoặc `0084…`.
5. Start add-on và mở **Web UI**.
6. Nếu Imou yêu cầu CAPTCHA, Web UI hiển thị GeeTest4 hoặc ảnh bốn ký tự của đúng phiên account đang đăng nhập.
7. Nếu Imou trả mã `12112`, Web UI tự yêu cầu mã xác minh hai bước và hiển thị ô nhập sáu số. Nhập mã SMS/email mới nhất hoặc bấm **Gửi lại mã**.
8. Add-on gọi `GrantingCredit`, chạy lại `GetToken`/`Login`, lấy thiết bị và tạo entity qua MQTT Discovery.

# Luồng CAPTCHA theo APK

Bản `1.0.26` đăng nhập account/password và không đổi mã `12114` thành QR. Phân tích APK Imou Life `10.1.6` cho thấy:

- `GetToken` dùng account signer và profile Android/phone trên `app-v3.easy4ipcloud.com`.
- Mã `12114` mang challenge GeeTest4; các mã `9000`, `10007` và `12110` chọn CAPTCHA ảnh bốn ký tự.
- Trước các API CAPTCHA, ứng dụng khôi phục **default app signer**. Vì vậy `CheckGeeTest4`, `GetImageValidCode` và `CheckImageValidCode` không được ký bằng account signer.
- CAPTCHA dùng revision `191204`, `Content-Type: application/json; charset=utf-8` và client-UA Android.
- Account trong `CheckGeeTest4` được mã hóa AES-GCM bằng IV ngẫu nhiên 12 byte, sau đó Base64 theo dạng `IV || ciphertext || tag`.
- Ứng dụng tạo MD5 hex chữ thường và SHA-256 hex của password, rồi SDK HTTP băm từng chuỗi hex thêm một lần nữa. Vì vậy account signer thực tế dùng `MD5(MD5-hex(password))` và `SHA256(SHA256-hex(password))` làm khóa HMAC.
- Tầng băm kép chỉ áp dụng cho signer `account\\...`; default app signer của CAPTCHA và signer `uuid\\...` từ token vẫn dùng một tầng băm như APK.
- Sau CAPTCHA hợp lệ, add-on gọi lại `GetToken` revision `185606`, rồi `Login` revision `198828` để nhận phiên cloud/MQTT.

Giao diện GeeTest dùng đúng asset `gl4.js` và metadata SDK `1.8.11` lấy từ APK. `verifyToken`, app credential, account và password chỉ nằm phía server add-on; trình duyệt chỉ nhận challenge công khai và gửi lại kết quả GeeTest.

# Luồng xác minh hai bước theo APK

Mã `12112` từ `user.account.GetToken` được APK ánh xạ sang màn hình xác minh hai bước. Add-on thực hiện cùng thứ tự:

1. Gọi `common.validcode.GetValidCode` với `usage=GrantingCredit` để Imou gửi mã đến số điện thoại/email đã liên kết.
2. Hiển thị form sáu chữ số trong Web UI Ingress.
3. Gọi `user.account.GrantingCredit` với mã người dùng nhập.
4. Chạy lại `GetToken` revision `185606` và `Login` revision `198828` bằng profile Android/phone.

Hai API OTP dùng revision `191204`, `Content-Type: application/json; charset=utf-8` và signer `account\\...` đã băm password hai tầng. Implementation secure-v1 của APK bỏ qua IV `0` mà request model truyền vào, tự tạo nonce ngẫu nhiên 12 byte và Base64 theo dạng `nonce || ciphertext || tag`, giống định dạng account của GeeTest.

Nếu `GetValidCode` hoặc `GrantingCredit` yêu cầu CAPTCHA, Web UI tạm chuyển sang CAPTCHA hiện có. Add-on gửi đúng usage `GrantingCredit`; CAPTCHA ảnh khi gửi mã dùng `GetValidCodeToPhone` hoặc `GetValidCodeToEmail`. Sau khi CAPTCHA hợp lệ, add-on quay lại bước gửi/kiểm tra OTP mà không tạo QR.

# Chẩn đoán đăng nhập

- Log `GetToken code=12114 ... auth_type=account client_profile=phone` là bước yêu cầu CAPTCHA bình thường.
- Log `GetToken code=12112 ... auth_type=account client_profile=phone` nghĩa là Imou yêu cầu xác minh hai bước cho terminal hiện tại; đây không phải thông báo password sai. Mở Web UI, nhận và nhập mã sáu số.
- Log `GetValidCode code=11010` trên bản `1.0.25` là payload account OTP thiếu nonce prefix, không phải account/password sai. Cài `1.0.26`, restart add-on và yêu cầu mã mới.
- `GetValidCode` và `GrantingCredit` phải có `auth_type=account`; các API CAPTCHA vẫn phải có `auth_type=default`.
- Các request `CheckGeeTest4`, `GetImageValidCode` và `CheckImageValidCode` phải có `auth_type=default`. Nếu log vẫn ghi `auth_type=account`, add-on đang chạy bản cũ.
- Mã `11010` sau khi xác minh thường là challenge đã hết hạn/đã dùng hoặc request CAPTCHA bị ký sai. Bấm làm mới CAPTCHA một lần; không dùng lại kết quả của ảnh/widget cũ.
- Nếu CAPTCHA API tạm trả lỗi nhưng `GetToken` kế tiếp đã thành công, bản `1.0.22` giữ trạng thái đăng nhập thành công thay vì ghi đè thành `captcha_rejected`.
- Mã `22001` không phải mã từ API CAPTCHA. APK tự tạo mã này khi response `GetToken` có `failNum`: dưới `2` là account hoặc password không đúng, từ `2` đến `4` là cảnh báo password sai, từ `5` trở lên là tài khoản đã khóa.
- Bản `1.0.24` sửa trường hợp `22001` giả do add-on cũ chỉ thực hiện tầng băm đầu tiên của password. Sau khi cài bản mới, restart add-on và tạo challenge CAPTCHA mới; không dùng lại challenge đã xác minh ở bản cũ.
- Bản `1.0.25` tách riêng `12112` khỏi lỗi account/password và tự hoàn tất `GrantingCredit`; không tiếp tục đổi sang QR khi gặp mã này.
- Bản `1.0.26` sửa định dạng mã hóa OTP và usage CAPTCHA riêng của bước gửi mã.
- Bản `1.0.23` dừng retry tự động khi gặp `22001`. Hãy đăng nhập thử trực tiếp trong Imou Life, sửa account/password trong cấu hình rồi restart add-on; không tiếp tục gửi CAPTCHA nhiều lần.
- Khi thành công, Web UI chuyển qua `Đăng nhập thành công, đang lấy thiết bị…`, sau đó `Đã đăng nhập và đang đồng bộ MQTT.`

Không gửi account, password, mã OTP, ảnh CAPTCHA hoặc token khi báo lỗi. Nếu password đã từng được đăng trong log/chat, hãy đổi password Imou Life trước khi chạy add-on.

# MQTT được tạo

- Device/channel online binary sensors.
- Motion binary sensor cho từng channel.
- Person/occupancy binary sensor cho từng channel.
- MQTT camera snapshot cho từng channel.
- Toàn bộ primitive property an toàn: sensor, binary sensor, switch, number, select hoặc text.
- Button cho thing service không có input.
- MQTT event entity cho mỗi thiết bị, gồm motion/human/person/intrusion/doorbell/alarm/property/other.
- Sự kiện đã lọc thông tin nhạy cảm tại topic `imou_life/events`.
- Lỗi command tại topic `imou_life/errors`.

State và discovery config được retain. Event không retain. Command từ Home Assistant được gửi ngược lên Imou cloud qua property/service API.

# Điều khiển, stream và motion

- Lệnh property/service dùng wrapper giống APK (`revision=191204`, `channelId`, `qos=1`, `timeout=10000`). Identifier của thing model được đổi sang ref số trên wire, boolean thành `1/0`, property write dùng `syncLocalCache=true`; add-on tự thử MQTT/HTTP, route modern/legacy và request device-level không có `channelId` khi thiết bị trả `11001`, `1201` hoặc `40999`.
- API media dùng chung thử `RTSP`, `HLS`, `FLV`, `RTMP`, transfer chuẩn rồi service `cm_getRealTransferStreamUrl` ref `96500`; input/output được đổi sang ref `96501`–`96529`.
- Motion và Person được lấy từ MQTT push dạng URI/ref số và polling cloud `GetDeviceLatestAlarmMixMessage` revision `193687`; endpoint `GetDeviceLatestAlarmMessage` cũ chỉ là fallback. Lần polling đầu chỉ tạo baseline để không phát lại cảnh báo cũ.
- Status Web UI có `alarm_poll`; binary sensor trong custom integration có thêm `mqtt_connected`, `alarm_poll`, còn camera custom có `stream_status`.
- Camera MQTT của add-on là camera snapshot. URL `RTSV1`/`RTSV2`/`lchttp` là transport riêng của SDK native, không phải RTSP/HLS tiêu chuẩn để Home Assistant phát trực tiếp.

# Tùy chọn

- `poll_interval`: chu kỳ đọc lại property.
- `max_properties`: số property primitive tối đa mỗi thiết bị; mặc định 1000 và property nhạy cảm luôn bị loại.
- `snapshot_interval`: `0` để không tải thumbnail định kỳ, hoặc số giây giữa hai lần tải.
- `event_images`: tải ảnh alarm và đẩy vào MQTT camera khi Imou gửi URL ảnh.
- `log_level`: không bật `DEBUG` lâu dài.
