# Changelog

## 1.0.31

- Đăng ký nhận alarm/push qua MQTT giống app Android: gọi `user.push.SetClientPushConfig` với `mqttPushId` là client-id MQTT sau khi kết nối.
- Giữ polling alarm làm dự phòng; lỗi đăng ký push chỉ ghi cảnh báo, không ngắt kết nối.

## 1.0.30

- Sửa đọc HTTP body bị chia nhiều chunk: đọc đến EOF, giới hạn JSON 16 MiB và snapshot 5 MiB.
- Bổ sung kiểm thử dữ liệu chia chunk, UTF-8 và giới hạn kích thước.
- Không có thay đổi phá vỡ cấu hình; nâng cấp giữ nguyên config entry/add-on options.

## 1.0.29

- Poll motion/person qua API alarm mới, tách alarm channel/device/AP.
- Property control theo ref số, boolean `1/0`, `syncLocalCache=true`, fallback MQTT/HTTP và legacy.

## 1.0.28

- Live stream theo thing-model: service ref `96500` và input/output ref `96501`–`96529`.
- Thêm binary sensor Person; giải mã event MQTT dạng URI và event ref số.

## 1.0.27

- Khớp revision `191204` và wrapper IoT property/service của APK.
- Bổ sung polling alarm cloud làm dự phòng cho MQTT push.