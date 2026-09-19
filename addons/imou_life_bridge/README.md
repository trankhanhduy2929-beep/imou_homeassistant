# Imou Life Bridge

Add-on đăng nhập trực tiếp Imou Life, lấy thiết bị/thing-model/property, tạo Home Assistant
MQTT Discovery và chuyển sự kiện realtime từ Imou MQTT sang MQTT của Home Assistant.

Khi dùng add-on này không cần cài custom integration `custom_components/imou_life`.

## Yêu cầu

- Home Assistant OS hoặc Supervised.
- MQTT integration và một MQTT broker (ví dụ Mosquitto broker add-on).
- Kiến trúc `amd64` hoặc `aarch64`.
- Tài khoản Imou Life đang dùng bình thường trên app.

## Cài đặt

1. **Settings → Add-ons → Add-on Store**.
2. Menu ba chấm → **Repositories** → thêm `https://github.com/trankhanhduy2929-beep/imou_homeassistant`.
3. Cài **Imou Life Bridge**.
4. Tab **Configuration**, nhập `account`/`password` Imou Life, lưu.
5. Start add-on, mở **Web UI** và hoàn tất CAPTCHA/OTP nếu Imou yêu cầu.
6. Add-on tự lấy thiết bị và tạo entity qua MQTT Discovery.

## Chọn lọc cài đặt

- `account`, `password`: tài khoản Imou Life. Số Việt Nam có thể nhập `084…`, `84…`, `+84…` hoặc `0084…`.
- `poll_interval`: chu kỳ đọc lại property (giây).
- `request_timeout`: timeout mỗi request cloud (giây).
- `max_properties`: số property primitive tối đa mỗi thiết bị.
- `snapshot_interval`: `0` để tắt tải thumbnail định kỳ, hoặc số giây giữa hai lần tải.
- `event_images`: tải ảnh alarm và đẩy vào camera snapshot.
- `log_level`: `INFO`, `DEBUG`, `WARNING`, `ERROR`; không bật `DEBUG` lâu dài.

## Entity

- Device/channel online binary sensor.
- Motion và Person binary sensor cho từng channel.
- Camera snapshot cho từng channel.
- Thing-model property thành sensor, binary sensor, switch, number, select hoặc text.
- Button cho service thing-model không có input.
- Event entity cho mỗi thiết bị (motion/human/person/intrusion/doorbell/alarm/property/other).

## MQTT

- Discovery prefix: `homeassistant`
- State/command/event: `imou_life`
- Event đã lọc: `imou_life/events`
- Lỗi command: `imou_life/errors`
- Availability: `imou_life/bridge/availability`

State và discovery config được retain; event không retain. Lệnh từ Home Assistant được gửi
ngược lên Imou cloud qua property/service API.

## Camera và stream

Camera MQTT của add-on là **camera snapshot**. Transport `RTSV1`/`RTSV2`/`lchttp` là định dạng
riêng của SDK Imou, không phát trực tiếp được bằng Home Assistant. Nếu cần live stream, dùng
custom integration `imou_life` (P2P RTSP nội bộ).

## Bảo mật

- Account/password chỉ đọc từ Supervisor add-on options.
- Không đưa account, password, token hoặc signed image URL vào MQTT Discovery/event.
- Property có tên chứa password, token, secret, credential hoặc access key bị loại bỏ.
- Giao diện CAPTCHA/OTP chỉ đi qua Ingress dành cho quản trị viên.
- Giữ `/data` qua restart/backup để terminal identity ổn định.

## Xử lý sự cố

- Add-on không start: kiểm tra Home Assistant OS/Supervised và MQTT broker đang chạy.
- Báo sai tài khoản/mật khẩu: thử đúng tài khoản trên app; tránh đăng nhập sai nhiều lần.
- CAPTCHA/OTP: mở Web UI và hoàn tất xác minh; mã OTP phải là mã mới nhất.
- Sensor `unknown`: property đó cloud không trả giá trị hoặc thiết bị offline.

Khi báo lỗi, gửi log add-on. **Không gửi mật khẩu, OTP hoặc token vào issue/chat.**