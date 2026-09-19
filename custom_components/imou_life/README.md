# Imou Life (custom integration)

Đăng nhập trực tiếp Imou Life bằng tài khoản/mật khẩu, tạo camera và entity thing-model trong
Home Assistant. Không cần add-on hoặc MQTT broker.

## Cài đặt qua HACS

1. HACS → **Integrations** → menu ba chấm → **Custom repositories**.
2. Thêm `https://github.com/trankhanhduy2929-beep/imou_homeassistant` với loại **Integration**.
3. Tìm **Imou Life** → **Download** → restart Home Assistant.
4. **Settings → Devices & services → Add integration → Imou Life**, nhập tài khoản/mật khẩu.

## Cài đặt chép tay

1. Chép thư mục `custom_components/imou_life` vào `/config/custom_components/imou_life`.
2. Restart Home Assistant.
3. **Settings → Devices & services → Add integration → Imou Life**, nhập tài khoản/mật khẩu.

## Đăng nhập và xác minh

- Nếu Imou yêu cầu CAPTCHA, Home Assistant hiển thị liên kết xác minh (GeeTest hoặc ảnh 4 ký tự).
- Nếu Imou yêu cầu OTP 6 số, nhập mã SMS/email mới nhất vào form xác minh. Có thể bấm gửi lại mã.
- Khi token hết hạn hoặc cần xác minh lại, Home Assistant tạo reauth flow và chỉ hỏi mật khẩu/OTP.

## Entity

- Camera cho mỗi channel (P2P RTSP nội bộ, fallback URL cloud; ép RTSP-over-TCP).
- Binary sensor online cho device và từng channel.
- Binary sensor chuyển động và phát hiện người cho từng channel.
- Thing-model property thành sensor, binary_sensor, switch, number, select hoặc text.
- Button cho service thing-model không có input.

Thuộc tính chẩn đoán an toàn: camera có `stream_status` và `p2p_status`; cảm biến
motion/person có `mqtt_connected` và `alarm_poll`.

## Automation và event

- Motion/person tự trở về `off` sau 30 giây nếu cloud không gửi sự kiện clear.
- Automation có thể nghe event Home Assistant `imou_life_event`; URL ảnh và giá trị nhạy cảm
  bị loại khỏi payload.

## Lưu ý

- Camera cần bật phát hiện chuyển động và thông báo sự kiện trong app Imou Life.
- Một số property do model khai báo nhưng cloud không trả giá trị sẽ hiển thị `unknown`.
- Nếu P2P không thiết lập được và thiết bị chỉ trả transport riêng của SDK, camera có thể
  không phát được; kiểm tra `stream_status`/`p2p_status`.
- Đây là API private của Imou và có thể thay đổi bất kỳ lúc nào.

Tài liệu đầy đủ: [README repo](../../README.md) (cài đặt, xử lý sự cố, add-on MQTT bridge).

## Nâng cấp

- HACS: mở HACS → **Imou Life** → **Update**.
- Chép tay: chép đè `custom_components/imou_life`, giữ nguyên config entry, restart Home Assistant.
- Không cần xóa integration hoặc đăng nhập lại để nâng cấp.

## Ghi nhận

Phần P2P tham khảo `dh-p2p` (khoanguyen-3fc), giấy phép MIT trong
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).