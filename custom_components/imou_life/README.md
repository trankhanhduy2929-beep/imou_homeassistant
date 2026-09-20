# Imou Life custom integration

Custom integration này đăng nhập trực tiếp Imou Life bằng account/password theo profile Android của APK `10.1.6`. Không cần add-on hoặc MQTT broker.

## Cài đặt

1. Giải nén gói phát hành vào `/config` để có thư mục `/config/custom_components/imou_life`.
2. Restart Home Assistant.
3. Mở **Settings → Devices & services → Add integration → Imou Life**.
4. Nhập tài khoản và mật khẩu Imou Life.
5. Nếu Imou yêu cầu CAPTCHA, mở liên kết xác minh do config flow hiển thị. Với mã `12112`, nhập mã SMS/email sáu số ở bước tiếp theo.

**Nâng cấp lên `0.1.16`:** giải nén `imou_life-custom-component-0.1.16.zip`, chép đè mã trong `/config/custom_components/imou_life`, rồi restart Home Assistant; giữ nguyên config entry và cấu hình hiện có.

Bản `0.1.16`:

- **Tách camera khỏi setup mặc định:** login chỉ tạo sensors/settings/entities, không probe P2P nên không treo khi thêm integration.
- **RTSP local tuỳ chọn:** vào **Settings → Imou Life → Configure** nhập IP camera trong cùng LAN để dùng RTSP trực tiếp; camera entity chỉ được tạo khi có IP.
- **Discovery lấy `familyId` thật** qua `family.manager.UserFamilyGet` trước `DeviceBasicInfoQueryV2`, sửa lỗi account có family riêng không thấy thiết bị.
- **P2P credential parsing sửa**: đọc đúng `p2pConfig.accountNew`/`p2pToken`/`ak`.
- **P2P handshake theo tham chiếu**: agent sign → device auth, đúng PTCP counter và cleanup.
- **MQTT alarm push**: đăng ký `SetClientPushConfig` nhận sự kiện gần như tức thời.
- **HTTP body fix**: đọc đến EOF, không cắt JSON.

**Nâng cấp lên `0.1.15`:** giải nén `imou_life-custom-component-0.1.15.zip`, chép đè mã trong `/config/custom_components/imou_life`, rồi restart Home Assistant; giữ nguyên config entry và cấu hình hiện có.

Bản `0.1.15` có:

- **OptionsFlow** để nhập IP camera trong cùng LAN; camera ưu tiên RTSP trực tiếp trên LAN khi có IP, fallback P2P/cloud khi cần. Vào **Settings → Devices & services → Imou Life → Configure → nhập IP camera**.
- Discovery lấy `familyId` thật qua `family.manager.UserFamilyGet` trước `DeviceBasicInfoQueryV2`, sửa lỗi account có family riêng không thấy camera/thiết bị.
- Credential P2P đọc đúng `p2pConfig.accountNew`/`p2pToken`/`ak` thay vì chỉ `account`/`password` cũ.
- P2P handshake theo tham chiếu Dahua/Imou: agent sign → device auth, đúng PTCP counter và cleanup; camera nhà dưới phát được 2304×1296 HEVC 5 frame trong 0.08s qua RTSP LAN.
- MQTT alarm push đăng ký `SetClientPushConfig` nhận sự kiện gần như tức thời (đã kiểm thử trên cloud thật: 6 sự kiện trong 90 giây).
- HTTP body đọc đến EOF, không cắt JSON.

**Nâng cấp lên `0.1.14`:** giải nén `imou_life-custom-component-0.1.14.zip`, chép đè mã trong `/config/custom_components/imou_life`, rồi restart Home Assistant; giữ nguyên config entry và cấu hình hiện có.

Bản `0.1.14` đăng ký client nhận alarm qua MQTT giống app Android: sau khi kết nối, integration gọi `user.push.SetClientPushConfig` với `mqttPushId` là client-id MQTT, nên Imou đẩy sự kiện trực tiếp thay vì chờ chu kỳ poll 30 giây. Kiểm thử cloud thật (chỉ đọc): đăng ký được chấp nhận và MQTT nhận 6 sự kiện trong 90 giây, so với 0 sự kiện khi chưa đăng ký. Poll vẫn giữ làm dự phòng khi MQTT push tạm mất.

**Nâng cấp lên `0.1.13`:** giải nén `imou_life-custom-component-0.1.13.zip`, chép đè mã trong `/config/custom_components/imou_life`, rồi restart Home Assistant; giữ nguyên config entry và cấu hình hiện có, không xóa entry hoặc đăng nhập lại để nâng cấp. Các bước thêm integration ở trên chỉ dành cho cài mới.

Bản `0.1.13` sửa HTTP body bị cắt khi truyền nhiều chunk: đọc `response.content` đến EOF, giới hạn JSON 16 MiB và snapshot 5 MiB. Bộ 144 test đã đạt, gồm kiểm thử tổng hợp chunk, ký tự UTF-8 bị chia giữa các chunk và giới hạn kích thước.

Kiểm thử cloud thực tế được cho phép, **chỉ đọc**: login + OTP được chấp nhận; rich discovery thấy 9/9 thiết bị online; tải thành công 9 thing model; trả 344 giá trị property trên 9 thiết bị; parse 9 bản ghi alarm mới nhất; kết nối MQTT TLS và 2/2 yêu cầu đọc property thành công. Hai lượt cập nhật coordinator Home Assistant tiếp theo vẫn giữ 344 giá trị. Một số property được model khai báo nhưng cloud không trả giá trị; không suy diễn hoặc điền giá trị thay thế.

Trong thời gian quan sát không có realtime push/alarm mới, nên motion/person kích hoạt vật lý **chưa được xác minh**. Home Assistant thực tế của người dùng chưa được nâng cấp; không thực hiện kiểm thử điều khiển, camera hoặc video. Giới hạn snapshot mới chỉ được kiểm thử tổng hợp, không phải kiểm chứng camera thực tế.

Bản `0.1.12` thêm relay RTSP nội bộ qua giao thức P2P Dahua/Imou được dùng trong native SDK của APK. Camera ưu tiên URL loopback `rtsp://127.0.0.1:<port>` và ép RTSP-over-TCP để Home Assistant/FFmpeg phát được; cloud URL chuẩn vẫn là fallback. Relay chỉ khởi động khi mở stream, dùng credential thiết bị do `DeviceBasicInfoQueryV2` trả về, không đưa credential vào state/log và tự đóng khi reload/unload integration. Thuộc tính camera `p2p_status` cho biết `idle`, `connecting`, `ready` hoặc bước lỗi an toàn.

Bản này cũng sửa motion/person bị mất khi Imou trả `channelId=0`, ID thiết bị khác hoa/thường, channel lệch 0/1 hoặc channel trống trên camera một kênh. Alarm còn mới được xử lý ngay ở poll đầu thay vì luôn bị bỏ làm baseline; timestamp giây/millisecond và chuỗi giờ theo timezone Home Assistant đều được hỗ trợ. Alarm cũ vẫn bị chặn để tránh bật sensor giả sau restart.

Bản `0.1.11` chuyển fallback motion/person sang đúng API APK `cloud.message.GetDeviceLatestAlarmMixMessage` revision `193687`, gửi đủ `productId`, channel/AP, Bluetooth, multi-view, category/sub-category và `isFamily`; endpoint cũ chỉ còn là fallback. Alarm channel, device và AP được tách riêng, nhận cả `typeInt`, `smartDetectList`, `detect` và event ref số lồng nhau. Hai binary sensor có thuộc tính `mqtt_connected` và `alarm_poll` để chẩn đoán nguồn cập nhật.

Điều khiển thing-model khớp wire protocol của SDK: identifier trong app được đổi thành ref số trước khi gửi cloud, boolean thành `1/0`, revision `191204`, `qos=1`, `timeout=10000` và `syncLocalCache=true`. Integration vẫn thử MQTT/HTTP, `SetIotProperties`/`GetIotProperties`, API legacy, có/không có `channelId`, rồi mới thử identifier như fallback. Sau khi lệnh được cloud chấp nhận, trạng thái Home Assistant được giữ theo giá trị vừa chọn nếu lần đọc tức thời của thiết bị chưa kịp đồng bộ.

Nếu P2P không thiết lập được, camera thử lần lượt `RTSP`, `HLS`, `FLV`, `RTMP`, transfer API và service `cm_getRealTransferStreamUrl` ref `96500`; input/output service được đổi sang ref `96501`–`96529`. Thuộc tính `stream_status` cho biết URL cloud chuẩn phát được hay Imou chỉ trả private transport. `RTSV1`/`RTSV2`/`lchttp` không được quảng cáo trực tiếp cho Home Assistant; relay P2P là đường phát cục bộ thay thế.

Bản `0.1.7` kết nối trực tiếp MQTT TLS do Imou cấp sau khi đăng nhập và tạo hai binary sensor realtime cho từng channel: **Chuyển động** và **Phát hiện người**. Human-event cũng bật sensor chuyển động; mỗi trạng thái tự trở về off sau 30 giây nếu cloud không gửi sự kiện clear. Payload automation được phát trên Home Assistant event bus với tên `imou_life_event`; URL ảnh và giá trị nhạy cảm bị loại khỏi payload.

Hai binary sensor realtime vẫn available khi MQTT Imou mất kết nối nhờ fallback polling. Camera cần bật phát hiện/chuyển động và thông báo sự kiện trong Imou Life; `motionEnable` hoặc `humanEnable` vẫn là công tắc cấu hình, không bị dùng làm trạng thái chuyển động giả.

Bản `0.1.6` chuẩn hóa ngôn ngữ Home Assistant thành định dạng Android `language_COUNTRY` (ví dụ `vi_VN`), gửi `neutralizeFlag=1` khi tải thing model giống APK và thay mọi tên property/service, nhãn enum hoặc đơn vị còn chứa chữ Trung Quốc bằng tên Việt/Anh an toàn. Tên thiết bị và channel do người dùng đặt vẫn được giữ nguyên.

Sau khi nâng cấp, restart Home Assistant hoặc reload integration để tải lại thing model. Tên hiển thị sẽ đổi theo metadata mới; `entity_id` cũ được Home Assistant giữ ổn định và có thể đổi thủ công nếu cần.

Bản `0.1.5` đã sửa trạng thái `validation pending` sau OTP bằng client-UA Android đầy đủ như SDK APK (`terminalName`, `country`, `darkMode`, TTID và định dạng version/OS). Khi cloud yêu cầu xác minh lại, hãy yêu cầu **mã OTP mới**; không dùng lại mã đã gửi cho flow cũ.

## Entity

- Một camera cho mỗi channel, dùng thumbnail cloud, relay P2P RTSP nội bộ và cloud URL làm fallback.
- Connectivity binary sensor cho device và từng channel.
- Thing-model primitive property thành `sensor`, `binary_sensor`, `switch`, `number`, `select` hoặc `text`.
- Thing-model service không có input thành `button`.

Property chứa tên nhạy cảm như password, token, secret, credential hoặc access key bị loại bỏ. Signed media URL không được lưu vào state hay log.

Trang CAPTCHA dùng URL HTTPS/HTTP tuyệt đối vì frontend Home Assistant chỉ tự mở loại URL này. Endpoint không cần cookie đăng nhập nhưng được bảo vệ bằng token ngẫu nhiên 256-bit, hết hạn sau 15 phút và không gửi referrer sang GeeTest.

## Lưu ý

Đây là API private được phân tích từ ứng dụng Android và có thể thay đổi phía Imou. Khi cloud yêu cầu CAPTCHA/OTP lại, Home Assistant sẽ tạo reauth flow.

`GetValidCode` và `GrantingCredit` dùng default app signer sau khi `GetToken` trả `12112`, đúng đường gọi của APK. Nếu quyền terminal vẫn chưa được ghi nhận, log chỉ in tên key và cờ boolean an toàn (`response_keys`, `response_flags`), không in account, password, OTP hoặc token.
