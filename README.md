# Imou Life for Home Assistant

Tích hợp Imou Life cho Home Assistant: đăng nhập trực tiếp bằng tài khoản Imou Life và đưa
camera, cảm biến, công tắc và các thuộc tính thing-model vào Home Assistant.

Repo cung cấp hai cách dùng, chọn **một** trong hai:

| Cách dùng | Cài ở đâu | Đặc điểm |
| --- | --- | --- |
| Custom integration `imou_life` | HACS hoặc chép tay vào `config/custom_components` | Không cần MQTT broker, có camera live stream (P2P RTSP nội bộ), entity thing-model |
| Add-on `Imou Life Bridge` | Add-on Store (Home Assistant OS / Supervised) | Cầu MQTT Discovery, camera snapshot, phù hợp hệ thống cần MQTT |

> Không cài và chạy đồng thời cả hai để tránh tạo entity trùng.

## Yêu cầu

- Home Assistant đang hoạt động; khuyến nghị bản mới (đã đối chiếu HA 2026.2.3+).
- Một tài khoản Imou Life (Imou Life / Imou Cloud) đang dùng bình thường trên app.
- Camera/thiết bị đã được thêm vào tài khoản đó.
- Kết nối Internet ra Imou cloud (`*.easy4ipcloud.com`, MQTT TLS của Imou).
- Custom integration: Home Assistant có thể cài `aiomqtt==2.5.1` (HACS/HA tự tải khi thêm integration).
- Add-on: Home Assistant OS hoặc Supervised, MQTT integration và một MQTT broker (ví dụ Mosquitto broker add-on).

## Cách 1 — Cài custom integration

### 1.1 Cài bằng HACS (khuyến nghị)

1. Mở HACS → **Integrations** → menu ba chấm → **Custom repositories**.
2. Dán `https://github.com/trankhanhduy2929-beep/imou_homeassistant`, chọn loại **Integration** → **Add**.
3. Tìm **Imou Life** → **Download**.
4. Restart Home Assistant.

### 1.2 Cài chép tay

1. Tải mã nguồn (nút **Code → Download ZIP** hoặc `git clone`).
2. Chép thư mục `custom_components/imou_life` vào `/config/custom_components/imou_life` trong Home Assistant.
3. Restart Home Assistant.

### 1.3 Thêm tài khoản

1. Vào **Settings → Devices & services → Add integration → Imou Life**.
2. Nhập **tài khoản** và **mật khẩu** Imou Life.
3. Nếu Imou yêu cầu xác minh:
   - **CAPTCHA**: Home Assistant hiển thị liên kết xác minh. Mở liên kết, hoàn tất CAPTCHA (GeeTest hoặc ảnh 4 ký tự), rồi quay lại.
   - **OTP 6 số** (mã `12112`): nhập mã SMS/email mới nhất vào form xác minh. Nếu mã hết hạn, bấm gửi lại mã.
4. Sau khi đăng nhập thành công, camera và các entity sẽ được tạo tự động.

Khi token hết hạn hoặc Imou yêu cầu xác minh lại, Home Assistant sẽ tạo **reauth flow** và chỉ hỏi lại mật khẩu/OTP.

## Cách 2 — Cài add-on MQTT bridge

1. Trong Home Assistant: **Settings → Add-ons → Add-on Store**.
2. Menu ba chấm → **Repositories** → thêm `https://github.com/trankhanhduy2929-beep/imou_homeassistant`.
3. Cài **Imou Life Bridge** (kiến trúc hỗ trợ: `amd64`, `aarch64`).
4. Mở tab **Configuration**, nhập tài khoản/mật khẩu Imou Life, lưu.
5. Start add-on và mở **Web UI** để hoàn tất CAPTCHA/OTP nếu Imou yêu cầu.
6. Add-on tự tạo MQTT Discovery cho device/channel, sensor, binary sensor, switch, number, select, text, camera snapshot, event và button.

MQTT topic mặc định:

- Discovery: `homeassistant`
- State/command/event: `imou_life`
- Event đã lọc thông tin nhạy cảm: `imou_life/events`
- Lỗi command: `imou_life/errors`
- Availability cầu: `imou_life/bridge/availability`

## Entity được tạo

- **Camera** cho mỗi channel (custom integration: P2P RTSP nội bộ, fallback URL cloud; add-on: snapshot).
- **Binary sensor online** cho device và từng channel.
- **Binary sensor chuyển động** và **phát hiện người** theo channel (nhận qua MQTT push gần như tức thời, polling là dự phòng).
- **Thing-model property** thành `sensor`, `binary_sensor`, `switch`, `number`, `select` hoặc `text`.
- **Button** cho service thing-model không có input.

Các thuộc tính chẩn đoán an toàn (không chứa URL ký/token):

- Camera: `stream_status`, `p2p_status`.
- Cảm biến chuyển động/người: `mqtt_connected`, `alarm_poll`.

## Tính năng theo thiết bị

- Một số property do model thiết bị khai báo nhưng cloud không trả giá trị sẽ hiển thị `unknown`/`unavailable`; đây là giới hạn phía Imou, không phải lỗi cài đặt.
- Motion/person cần bật phát hiện chuyển động và thông báo sự kiện trong app Imou Life.
- Trạng thái chuyển động tự trở về `off` sau 30 giây nếu cloud không gửi sự kiện clear.
- Automation có thể nghe event Home Assistant `imou_life_event`; URL ảnh và giá trị nhạy cảm bị loại khỏi payload.

## Nâng cấp

- **HACS**: mở HACS → tìm Imou Life → **Update**.
- **Chép tay**: chép đè thư mục `custom_components/imou_life`, giữ nguyên config entry, rồi restart Home Assistant.
- **Add-on**: cài bản mới trong Add-on Store và restart add-on.
- Không cần xóa integration/add-on hoặc đăng nhập lại để nâng cấp, trừ khi tài liệu bản phát hành yêu cầu.

## Xử lý sự cố

| Hiện tượng | Kiểm tra |
| --- | --- |
| Không thêm được integration | Xem log Home Assistant; bảo đảm đã restart sau khi chép file và HA tải được `aiomqtt==2.5.1` |
| Báo sai tài khoản/mật khẩu | Thử đúng tài khoản trên app Imou Life; tài khoản có thể bị tạm khóa nếu sai nhiều lần |
| Không mở được liên kết CAPTCHA | Home Assistant phải có URL mà trình duyệt truy cập được (gợi ý: đặt đúng external URL trong Settings → System → Network) |
| Sensor luôn `unknown` | Property đó cloud không trả giá trị, hoặc thiết bị offline; kiểm tra binary sensor online và thuộc tính chẩn đoán |
| Sensor không tự cập nhật | Kiểm tra chu kỳ poll (mặc định 30 giây) và kết nối MQTT; thử reload integration |
| Camera không phát được | Thiết bị chỉ trả transport riêng của SDK hoặc P2P không thiết lập được; xem `stream_status`, `p2p_status` trong thuộc tính camera |
| Add-on không start | Add-on cần Home Assistant OS/Supervised và MQTT broker đang chạy |

Khi báo lỗi, gửi log Home Assistant và thuộc tính chẩn đoán. **Không gửi mật khẩu, OTP hoặc token vào issue/chat.**

## Bảo mật và dữ liệu

- **HACS/repo này không dùng ticket, token hay tài khoản của bạn.** Tài khoản/mật khẩu do bạn nhập và chỉ lưu trong cấu hình Home Assistant của bạn (hoặc add-on options).
- Integration giữ mật khẩu trong config entry của Home Assistant và dùng để đăng nhập/token refresh; hãy bảo vệ bản backup Home Assistant.
- Add-on lưu terminal identity trong `/data` để ổn định đăng nhập; giữ `/data` qua restart/backup.
- Token, mật khẩu, property nhạy cảm (password/token/secret/credential/access key) và URL media có chữ ký bị loại khỏi entity state, MQTT và log.
- Giao diện CAPTCHA/OTP chạy trên endpoint cùng Home Assistant, bảo vệ bằng token ngẫu nhiên ngắn hạn; nên dùng HTTPS khi truy cập từ bên ngoài.
- Đây là API private của Imou, có thể thay đổi bất kỳ lúc nào; tài khoản có thể bị yêu cầu CAPTCHA/OTP lại.

## Phạm vi đã kiểm thử

- Integration/add-on đăng nhập cloud, liệt kê device/channel, đọc thing-model và property, nhận sự kiện/alarm và tạo entity đã được chạy đối chiếu với tài khoản thật (chế độ **chỉ đọc**: không điều khiển thiết bị, không kiểm thử streaming video).
- Các trường hợp dữ liệu bị chia nhiều chunk, UTF-8 và giới hạn kích thước được kiểm thử tự động.
- Motion/person kích hoạt vật lý có thể chưa được xác minh đầy đủ trên mọi model; hãy tự kiểm tra với camera của bạn.
- Kiểm thử được thực hiện với tài khoản người dùng cung cấp; không đưa thông tin tài khoản lên repo.

## Ghi nhận

Cảm ơn `dh-p2p` (khoanguyen-3fc) cho phần tham khảo P2P. Giấy phép MIT kèm theo trong
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

## Giấy phép

[MIT](LICENSE)