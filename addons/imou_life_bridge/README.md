# Imou Life Bridge

Add-on độc lập đăng nhập trực tiếp Imou Life, lấy thiết bị/thing-model/property, tạo Home Assistant MQTT Discovery và chuyển sự kiện realtime từ Imou MQTT sang MQTT của Home Assistant.

Bản hiện tại `1.0.34` bỏ `image_encoding` rỗng khỏi MQTT Discovery của camera để Home Assistant nhận snapshot JPEG thô thay vì từ chối cấu hình và bỏ qua toàn bộ camera. Vẫn giữ HTTP body nhiều chunk, alarm mixed API, motion/person từ event ref lồng nhau và property control theo ref số với `syncLocalCache=true`; camera MQTT vẫn là snapshot, live stream chuẩn nằm trong custom component. Kiểm thử bản này chỉ đọc cloud, không kiểm thử điều khiển/camera/video hoặc xác minh motion/person kích hoạt vật lý; xem `CHANGELOG.md` để biết kết quả và giới hạn kiểm thử.

Custom integration `custom_components/imou_life` không cần thiết khi dùng add-on này.

## An toàn

- Account/password chỉ được đọc từ Supervisor add-on options.
- Không đưa account, password, Imou token hoặc signed image URL vào MQTT Discovery/event.
- Property có tên chứa password, token, secret, credential hoặc access key bị loại bỏ.
- Giao diện CAPTCHA/xác minh hai bước/status chỉ đi qua Ingress dành cho quản trị viên.
- Account, password, mã OTP, `verifyToken`, app credential và token Imou không được đưa vào URL, MQTT hoặc status JSON.

Xem `DOCS.md` để cài đặt.
