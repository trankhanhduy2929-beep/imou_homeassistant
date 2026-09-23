# Imou Connect for Home Assistant

Custom integration cho Home Assistant: đăng nhập trực tiếp bằng tài khoản Imou Life và đưa
camera, cảm biến, công tắc và các thuộc tính thing-model vào Home Assistant.

Không cần MQTT broker. Motion/person nhận qua kênh MQTT push của Imou gần như tức thời,
polling chỉ là dự phòng.

## Yêu cầu

- Home Assistant đang hoạt động; khuyến nghị bản mới (đã đối chiếu HA 2026.2.3+).
- Một tài khoản Imou Life (Imou Life / Imou Cloud) đang dùng bình thường trên app.
- Camera/thiết bị đã được thêm vào tài khoản đó.
- Kết nối Internet ra Imou cloud (`*.easy4ipcloud.com`, MQTT TLS của Imou).
- Home Assistant cài được `aiomqtt==2.5.1` (HACS/HA tự tải khi thêm integration).

## Cài đặt

### Cài bằng HACS (khuyến nghị)

1. Mở HACS → **Integrations** → menu ba chấm → **Custom repositories**.
2. Dán `https://github.com/trankhanhduy2929-beep/imou_homeassistant`, chọn loại **Integration** → **Add**.
3. Tìm **Imou Connect** → **Download**.
4. Restart Home Assistant.

### Cài chép tay

1. Tải mã nguồn (nút **Code → Download ZIP** hoặc `git clone`).
2. Chép thư mục `custom_components/imou_connect` vào `/config/custom_components/imou_connect` trong Home Assistant.
3. Restart Home Assistant.

### Thêm tài khoản

1. Vào **Settings → Devices & services → Add integration → Imou Connect**.
2. Nhập **tài khoản** và **mật khẩu** Imou Life.
3. Nếu Imou yêu cầu xác minh:
   - **CAPTCHA**: Home Assistant hiển thị liên kết xác minh. Mở liên kết, hoàn tất CAPTCHA (GeeTest hoặc ảnh 4 ký tự), rồi quay lại.
   - **OTP 6 số** (mã `12112`): nhập mã SMS/email mới nhất vào form xác minh. Nếu mã hết hạn, bấm gửi lại mã.
4. Sau khi đăng nhập thành công, camera và các entity sẽ được tạo tự động.

Khi token hết hạn hoặc Imou yêu cầu xác minh lại, Home Assistant sẽ tạo **reauth flow** và chỉ hỏi lại mật khẩu/OTP.

## Entity được tạo

- **Camera** cho mỗi channel được cấu hình RTSP local (không dùng P2P; tạo qua **Configure**).
- **Binary sensor online** cho device và từng channel.
- **Binary sensor chuyển động** và **phát hiện người** theo channel (nhận qua MQTT push gần như tức thời, polling là dự phòng).
- **Thing-model property** thành `sensor`, `binary_sensor`, `switch`, `number`, `select` hoặc `text`.
- **Button** cho service thing-model không có input.
- **Nút PTZ** (8 hướng và zoom vào/ra) cho thiết bị tự khai báo hỗ trợ PTZ.

Các thuộc tính chẩn đoán an toàn (không chứa URL ký/token):

- Cảm biến chuyển động/người: `mqtt_connected`, `alarm_poll`.

## PTZ (tuỳ theo model)

Thiết bị hỗ trợ PTZ sẽ có thêm các **nút bấm** đặt ngay cạnh thiết bị:

- 8 hướng: lên, xuống, trái, phải và 4 hướng chéo.
- `PTZ zoom vào`, `PTZ zoom ra`.

Mỗi lần bấm gửi một lệnh ngắn 200 ms qua API đám mây `things.ptz.PtzMove`, dùng đúng trục
chuẩn hóa và dấu như app Imou Life `10.1.6` (`±0.625` cho pan/tilt, `±0.5` cho zoom).
Giống app, lệnh được gửi tới **host stream-entry của thiết bị** (trường `streamEntryAddr` từ
`device.list.DetailInfoQuery`), không phải host tài khoản — host tài khoản trả `12100` (không có
quyền) cho camera đám mây. Không dùng `streamEntryAddrV4` vì đó là host MQTT (`:8883`).
Integration thử lần lượt các host ứng viên (raw → cache → DetailInfoQuery → host tài khoản) và
giữ host chạy được; request tới host stream-entry dùng thêm CA riêng của Imou
(`certificates/*.crt`) nên không bị `CERTIFICATE_VERIFY_FAILED`. Chỉ tạo nút khi model thiết
bị/trường `ability` khai báo có PTZ; nếu tất cả host đều bị từ chối, nút PTZ tự chuyển
`unavailable`.

Muốn điều khiển bằng automation hoặc dashboard, dùng service `imou_connect.ptz_move`:

```yaml
service: imou_connect.ptz_move
data:
  device_id: "AAABBBCCCDDD"   # serial thiết bị Imou
  channel_id: "0"             # tuỳ chọn, mặc định kênh đầu tiên
  direction: left             # up/down/left/right/left_up/left_down/right_up/right_down/zoom_in/zoom_out
  duration: 500               # tuỳ chọn, đơn vị ms (mặc định 200)
```

## Realtime

Sau khi kết nối MQTT, integration đăng ký nhận sự kiện trực tiếp
(`user.push.SetClientPushConfig` với `mqttPushId` là client-id MQTT), giống app Android.
Motion/person được đẩy tới gần như ngay thay vì chờ chu kỳ poll 30 giây; polling vẫn giữ
làm dự phòng khi MQTT tạm mất.

## RTSP trên LAN (tuỳ chọn)

Login mặc định chỉ tạo sensors/settings/entities, không probe P2P. Khi muốn xem camera:
vào **Settings → Imou Connect → Configure**, chọn từng camera và nhập IP, cổng và tài khoản
RTSP local.

Trước khi lưu, integration **thử mở một khung hình RTSP** và báo rõ nguyên nhân nếu lỗi:
sai tài khoản/mật khẩu local, sai đường dẫn/kênh, không tới được IP-cổng, quá thời gian,
không có video hoặc không phải luồng RTSP hợp lệ. Có ô bỏ qua kiểm tra khi camera tạm offline.
Camera entity được tạo sau khi lưu cấu hình và cấu hình đã lưu không bị tự kiểm tra lại.

## Tính năng theo thiết bị

- Một số property do model thiết bị khai báo nhưng cloud không trả giá trị sẽ hiển thị `unknown`/`unavailable`; đây là giới hạn phía Imou, không phải lỗi cài đặt.
- Motion/person cần bật phát hiện chuyển động và thông báo sự kiện trong app Imou Life.
- Trạng thái chuyển động tự trở về `off` sau 30 giây nếu cloud không gửi sự kiện clear.
- Automation có thể nghe event Home Assistant `imou_connect_event`; URL ảnh và giá trị nhạy cảm bị loại khỏi payload.

## Nâng cấp

- **HACS**: mở HACS → tìm Imou Connect → **Update**.
- **Chép tay**: chép đè thư mục `custom_components/imou_connect`, giữ nguyên config entry, rồi restart Home Assistant.
- Không cần xóa integration hoặc đăng nhập lại để nâng cấp, trừ khi tài liệu bản phát hành yêu cầu.

> **Chuyển từ bản `imou_life` cũ:** domain đổi sang `imou_connect` nên không giữ nguyên entry cũ.
> Gỡ entry Imou Life cũ (sensors/entity cũ), restart, cài Imou Connect và đăng nhập lại.

## Xử lý sự cố

| Hiện tượng | Kiểm tra |
| --- | --- |
| Không thêm được integration | Xem log Home Assistant; bảo đảm đã restart sau khi chép file và HA tải được `aiomqtt==2.5.1` |
| Báo sai tài khoản/mật khẩu | Thử đúng tài khoản trên app Imou Life; tài khoản có thể bị tạm khóa nếu sai nhiều lần |
| Đăng nhập xong báo lỗi không rõ (mã `12112`/`12116`) | Imou coi thiết bị chưa tin cậy và yêu cầu OTP; nhập mã SMS/email 6 số ở bước xác minh. Nếu không thấy form, cập nhật lên `0.1.21` rồi thử lại |
| Không mở được liên kết CAPTCHA | Home Assistant phải có URL mà trình duyệt truy cập được (gợi ý: đặt đúng external URL trong Settings → System → Network) |
| Sensor luôn `unknown` | Property đó cloud không trả giá trị, hoặc thiết bị offline; kiểm tra binary sensor online và thuộc tính chẩn đoán |
| Sensor không tự cập nhật | Kiểm tra kết nối MQTT (`mqtt_connected`) và thử reload integration; polling 30 giây là dự phòng |
| Camera không phát được | Mở **Configure**, chọn lại camera để được kiểm tra RTSP và báo nguyên nhân. Nếu luồng chính (main, HEVC nặng) không phát, thử đường dẫn `subtype=1` (luồng phụ) |
| Chỉ thấy vài entity, thiếu setting/nút | Cập nhật lên `0.1.20` rồi reload integration để discovery/model và entity được làm mới; nếu vẫn thiếu, gửi log đã che thông tin |
| Log lặp `DeviceListPageGet code=404` | Cập nhật lên `0.1.22`: endpoint legacy không có trên endpoint khu vực đó sẽ được ghi nhớ 1 giờ và chỉ ghi DEBUG, không ảnh hưởng thiết bị |
| Không thấy nút PTZ | Cập nhật lên `0.1.23`. Nút chỉ xuất hiện khi model thiết bị khai báo PTZ; kiểm tra thiết bị có PTZ thật không, thử phát trực tiếp trong app Imou Life, và xem log đã che thông tin |
| Bấm PTZ báo `code=12100` | Cập nhật lên `0.1.25`: lệnh phải gửi tới host stream-entry (`streamEntryAddr`) của thiết bị. Nếu vẫn lỗi, thiết bị/tài khoản không cho phép PTZ qua cloud; nút sẽ tự chuyển `unavailable` và log chỉ còn DEBUG |
| PTZ báo `CERTIFICATE_VERIFY_FAILED` | Cập nhật lên `0.1.25`: host stream-entry ký bằng CA riêng của Imou; bản mới nạp `certificates/*.crt` cho request PTZ |
| PTZ vẫn lỗi sau `0.1.26` | Vào **Settings → Imou Connect → ba chấm → Download diagnostics**, gửi phần thiết bị (che sẵn password/token/p2p/URL) gồm `streamEntryAddr`, `streamEntryAddrV3/V4` và `thing_model.services` để kiểm tra tiếp |

Khi báo lỗi, gửi log Home Assistant và thuộc tính chẩn đoán. **Không gửi mật khẩu, OTP hoặc token vào issue/chat.**

## Bảo mật và dữ liệu

- **Repo này không dùng ticket, token hay tài khoản của bạn.** Tài khoản/mật khẩu do bạn nhập và chỉ lưu trong cấu hình Home Assistant của bạn.
- Integration giữ mật khẩu trong config entry của Home Assistant và dùng để đăng nhập/token refresh; hãy bảo vệ bản backup Home Assistant.
- Token, mật khẩu, property nhạy cảm (password/token/secret/credential/access key) và URL media có chữ ký bị loại khỏi entity state và log.
- Giao diện CAPTCHA/OTP chạy trên endpoint cùng Home Assistant, bảo vệ bằng token ngẫu nhiên ngắn hạn; nên dùng HTTPS khi truy cập từ bên ngoài.
- Đây là API private của Imou, có thể thay đổi bất kỳ lúc nào; tài khoản có thể bị yêu cầu CAPTCHA/OTP lại.

## Phạm vi đã kiểm thử

- Đăng nhập cloud, liệt kê device/channel, đọc thing-model và property, nhận sự kiện/alarm và tạo entity đã được chạy đối chiếu với tài khoản thật (chế độ **chỉ đọc**: không điều khiển thiết bị, không kiểm thử streaming video).
- Đăng ký push MQTT (`user.push.SetClientPushConfig`) được chấp nhận; MQTT nhận sự kiện trực tiếp trong khi chưa đăng ký thì không.
- Các trường hợp dữ liệu bị chia nhiều chunk, UTF-8 và giới hạn kích thước được kiểm thử tự động.
- Motion/person kích hoạt vật lý có thể chưa được xác minh đầy đủ trên mọi model; hãy tự kiểm tra với camera của bạn.
- Kiểm thử được thực hiện với tài khoản người dùng cung cấp; không đưa thông tin tài khoản lên repo.
- Camera LAN `192.168.5.155` phát được 2304×1296 HEVC 5 frame trong 0.08s qua RTSP TCP và ONVIF kết nối được.
- Kiểm tra RTSP khi Configure đã thử thật: host không tới trả `stream_timeout`, sai mật khẩu camera trả `stream_unauthorized`.
- Discovery bổ sung và làm mới entity dùng fixture tổng hợp; **chưa xác minh payload thật của tài khoản từng bị thiếu entity**.
- PTZ: payload `things.ptz.PtzMove` và các giá trị trục/dấu được đối chiếu từ APK `10.1.6` và kiểm thử tự động; **chưa chạy trên camera PTZ thật**, hãy tự kiểm tra với thiết bị của bạn.
- Motion/person kích hoạt vật lý có thể chưa được xác minh đầy đủ trên mọi model; hãy tự kiểm tra với camera của bạn.

## Ghi nhận

Cảm ơn `dh-p2p` (khoanguyen-3fc) cho phần tham khảo P2P. Giấy phép MIT kèm theo trong
[THIRD_PARTY_NOTICES.md](custom_components/imou_connect/THIRD_PARTY_NOTICES.md).

## Giấy phép

[MIT](LICENSE)