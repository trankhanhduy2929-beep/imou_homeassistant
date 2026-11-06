# Changelog

## 1.0.34

- Bỏ `image_encoding` rỗng khỏi MQTT Discovery của camera. Home Assistant coi việc có khóa này là bật giải mã Base64 và từ chối giá trị rỗng, khiến toàn bộ camera của 9 thiết bị bị bỏ qua.
- Snapshot vẫn được gửi dưới dạng JPEG nhị phân trên topic `image`; không đặt `image_encoding` để Home Assistant dùng payload thô. Discovery được publish lại do fingerprint cấu hình thay đổi.

## 1.0.31

- Đăng ký nhận alarm/push qua MQTT giống app Android: gọi `user.push.SetClientPushConfig` với `mqttPushId` là client-id MQTT sau khi kết nối, để Imou đẩy sự kiện trực tiếp thay vì chờ poll.
- Giữ polling alarm làm dự phòng khi MQTT push tạm mất; lỗi đăng ký push chỉ ghi cảnh báo, không ngắt kết nối.

## 1.0.30

- Sửa HTTP body nhiều chunk: đọc `response.content` đến EOF, giới hạn JSON 16 MiB và snapshot 5 MiB; bộ 144 test đã đạt, gồm chunk/UTF-8/giới hạn tổng hợp.
- Kiểm thử cloud được cho phép, chỉ đọc: login + OTP thành công, rich discovery 9/9 online, tải 9 model, 344 giá trị property trên 9 thiết bị, parse 9 alarm; MQTT TLS và 2/2 yêu cầu đọc property thành công. Hai lượt coordinator Home Assistant tiếp theo giữ 344 giá trị; property cloud không trả vẫn không có giá trị, không suy diễn.
- Không thấy realtime push/alarm mới trong thời gian quan sát; motion/person kích hoạt vật lý chưa xác minh. Chưa nâng cấp Home Assistant của người dùng; không kiểm thử điều khiển/camera/video.

## 1.0.29

- Chuyển polling motion/person sang `cloud.message.GetDeviceLatestAlarmMixMessage` revision `193687`, gửi đủ product/channel/AP/Bluetooth/multi-view/category và giữ endpoint cũ làm fallback.
- Tách channel/device/AP alarm, nhận `typeInt`, `smartDetectList`, `detect` và event ref lồng nhau; Web UI status thêm `alarm_poll`.
- Khớp property wire protocol của APK: gửi ref số, boolean `1/0`, `syncLocalCache=true`, thử MQTT/HTTP, modern/legacy và có/không có `channelId`; identifier chỉ là fallback.
- Custom API dùng chung thử `RTSP`, `HLS`, `FLV`, `RTMP` trước transfer/RTSV và ghi chẩn đoán an toàn khi cloud chỉ trả private transport.

## 1.0.28

- Sửa live stream theo thing-model của APK: gọi service ref `96500`, mã hóa input bằng ref `96501`–`96520` và chuẩn hóa output `96521`–`96529` về URL media.
- Khi property/service bị thiết bị từ chối với `11001`, `1201` hoặc `40999`, tự thử lại request device-level không có `channelId`.
- Giải mã event MQTT đúng dạng URI `...?content=...`, nhận event ref số (`305000`, `32100`, `33000`...) và bổ sung binary sensor Person cho add-on.

## 1.0.27

- Khớp revision `191204` và wrapper APK cho mọi lệnh IoT property/service, gồm `channelId`, `qos`, `timeout` và `syncLocalCache`; vẫn fallback route legacy khi thiết bị trả lỗi giao thức.
- Bổ sung polling `cloud.message.GetDeviceLatestAlarmMessage` làm dự phòng cho MQTT push, giải mã envelope `forcePop`/`skip` và nhận tên cảnh báo tiếng Trung.
- Ưu tiên stream transfer chuẩn; không quảng cáo transport private `RTSV1`/`RTSV2` như URL phát được bởi Home Assistant.

## 1.0.26

- Sửa `common.validcode.GetValidCode code=11010` của bản `1.0.25`: implementation `H6.d.Te` trong APK bỏ qua IV `0` mà caller truyền vào, tự tạo nonce ngẫu nhiên 12 byte và Base64 toàn bộ `nonce || ciphertext || tag`.
- Áp dụng đúng định dạng AES-GCM có nonce prefix cho cả `GetValidCode` và `GrantingCredit`; mỗi request tạo ciphertext mới như ứng dụng chính thức.
- Khớp `Content-Type: application/json; charset=utf-8` của `LCApi.POST` cho hai API xác minh terminal.
- Gắn đúng usage CAPTCHA theo request gốc: `Login`, `GrantingCredit`, `GetValidCodeToPhone` hoặc `GetValidCodeToEmail`, thay vì luôn gửi `Login`.
- Bổ sung test hồi quy giải mã payload OTP, vector có nonce prefix, signer/content type và CAPTCHA riêng của bước gửi mã.

## 1.0.25

- Sửa mã `12112`: đây là yêu cầu xác minh hai bước/tin cậy terminal, không phải account hoặc password sai.
- Khôi phục đúng luồng APK `GetToken` → `common.validcode.GetValidCode` với usage `GrantingCredit` → nhập mã sáu số → `user.account.GrantingCredit` → chạy lại `GetToken` và `Login`.
- Mã hóa account cho hai API OTP bằng AES-GCM với nonce 12 byte bằng `0`, Base64 chỉ phần `ciphertext || tag`, đồng thời ký request bằng signer `account\\...` đã băm password hai tầng.
- Thêm Web UI nhập mã sáu số, nút gửi lại mã, trạng thái giao nhận an toàn và hai endpoint Ingress không đưa account, password, OTP hoặc token vào URL/MQTT/status.
- Nếu bước gửi hoặc xác nhận OTP yêu cầu CAPTCHA, add-on chuyển sang CAPTCHA hiện có rồi tự quay lại luồng OTP sau khi xác minh.
- Bổ sung test hồi quy cho mã `12112`, vector mã hóa, payload/chữ ký OTP, CAPTCHA → OTP và đăng nhập hoàn chỉnh trước khi lấy entity/MQTT Discovery.

## 1.0.24

- Sửa nguyên nhân account/password đúng vẫn bị báo `22001`: APK băm password thành MD5/SHA-256 hex, sau đó SDK HTTP tiếp tục băm hai chuỗi hex này thêm một lần trước khi tạo khóa HMAC cho `GetToken`.
- Áp dụng tầng băm thứ hai riêng cho signer `account\\...`; default app signer dùng cho CAPTCHA và signer `uuid\\...` dùng token sau đăng nhập vẫn giữ đúng một tầng băm.
- Bổ sung vector hồi quy cho `MD5(MD5-hex(password))`, `SHA256(SHA256-hex(password))` và chữ ký thực tế của `GetToken`.
- Xác nhận request CAPTCHA mặc định và request `Login` bằng token không bị thay đổi bởi bản vá account signer.

## 1.0.23

- Sửa diễn giải mã `22001`: đây không phải CAPTCHA bị từ chối mà là lỗi cục bộ của APK khi `GetToken` thành công về giao thức nhưng trả `failNum`, nghĩa là account/password bị từ chối hoặc tài khoản đã khóa.
- Đối chiếu trực tiếp APK Imou Life `10.1.6`: password signer yếu là MD5 hex chữ thường, signer mạnh là SHA-256 hex; số điện thoại Việt Nam được gửi nguyên dạng cùng `areaCode=84`.
- Sau khi CAPTCHA được chấp nhận nhưng `GetToken` trả `failNum`, Web UI hiển thị đúng trạng thái account/password, phân loại theo ngưỡng của APK (`<2`, `2–4`, `>=5`) và không còn báo “Imou từ chối kết quả CAPTCHA”.
- Xóa challenge CAPTCHA đã dùng và dừng retry đăng nhập tự động cho tới khi restart add-on, tránh tăng `failNum` và khóa tài khoản.
- Bổ sung test hồi quy cho exception `22001` và lỗi account/password phát sinh sau CAPTCHA.

## 1.0.22

- Khôi phục đăng nhập trực tiếp bằng account/password theo profile Android của APK Imou Life `10.1.6`; mã `12114` nay mở GeeTest4 trong Ingress thay vì chuyển sang QR.
- Dùng đúng default app signer trước `CheckGeeTest4`, `GetImageValidCode` và `CheckImageValidCode`, sửa nguyên nhân các request CAPTCHA trước đây bị ký bằng `auth_type=account` rồi Imou trả `11010`.
- Khớp payload APK: revision CAPTCHA `191204`, content type có charset, client-UA Android, AES-GCM account với IV ngẫu nhiên, và các trường GeeTest4/ảnh CAPTCHA chính xác.
- Đóng gói asset GeeLab `gl4.js` từ APK, hỗ trợ cả GeeTest4 và ảnh bốn ký tự mà không đưa account, password, `verifyToken` hoặc app credential vào trình duyệt.
- Sau CAPTCHA, tự chạy `GetToken` revision `185606` → `Login` revision `198828`, lấy thiết bị/thực thể và xuất Home Assistant MQTT Discovery.
- Không ghi đè trạng thái thành CAPTCHA lỗi nếu request kiểm tra tạm bị từ chối nhưng lần `GetToken` làm mới đã hoàn tất đăng nhập.
- Bổ sung test signer, mã hóa, payload, giao diện Ingress và vòng đời CAPTCHA; tổng cộng `61` test.

## 1.0.21

- Sửa nguyên nhân terminal chỉ dừng ở `status=scanned`: lần account `GetToken` ban đầu nay dùng cùng client-UA PC, host PC và `terminalId` với mã QR, thay vì tạo phiên chờ bằng identity Android rồi mới chuyển sang PC.
- Nhánh retry `GetToken` sau khi quét cũng giữ nguyên identity PC; nếu token hết hạn sau đăng nhập, relogin tiếp tục dùng PC thay vì tự reset về Android.
- Thêm log an toàn `Imou account login profile selected client=pc host=...`, `client_profile=pc` và `Imou PC terminal GetToken accepted`; không ghi account, password, login code, token hoặc session.
- Thêm test hồi quy xác nhận `GetToken` trước QR, `Login`, MQTT và relogin đều dùng cùng terminal PC trên `app-v2.easy4ipcloud.com`.

## 1.0.20

- Bổ sung nhánh hoàn tất QR bằng cách retry `user.account.GetToken` sau `status=scanned`, tương ứng cờ `bGetToken` trong luồng đăng nhập của Imou Life PC.
- Vẫn tiếp tục `QueryLoginQRCode` và recovery chéo host; `GetToken` chỉ được thử mỗi 2 giây sau khi QR đã quét, không chạy trước đó.
- Khi quyền trên điện thoại đã mở cho terminal nhưng host QR chỉ trả `11001/expire`, add-on có thể nhận token account, gọi `Login` và tiếp tục lấy thiết bị mà không cần tạo QR khác.
- Không ghi cảnh báo `12114` lặp lại cho các lần kiểm tra quyền dự kiến sau quét; lần `12114` ban đầu vẫn được ghi để chẩn đoán.

## 1.0.19

- Đối chiếu APK Imou Life xác nhận mobile gửi `scanned` rồi `login` qua `user.account.UpdateLoginQRCode`, kèm cờ `pcAutoLogin`; phía PC vẫn query bằng đúng trường `loginCode` revision `137573`.
- Khi host tạo QR trả `11001` hoặc `expire` sau `scanned`, query cùng mã trên các host đồng bộ `app.easy4ipcloud.com` và `app-v3.easy4ipcloud.com` trước khi coi QR hết hạn.
- Tự chuyển sang host đang giữ trạng thái mới nhất và tiếp tục lấy token/kết nối host khu vực, thay vì giữ một bản trạng thái `expire` cũ rồi tạo QR khác.

## 1.0.18

- Cập nhật profile QR theo Imou Life PC 64-bit `5.18.9` phát hành ngày 4/6/2026: protocol `V9.7.0`, thay cho profile `5.17.1`/`V9.2.3` đã cũ.
- Ưu tiên host QR chính thức `app-v2.easy4ipcloud.com` trong `Settings.ini` của PC 5.18.9; tự fallback sang `app.easy4ipcloud.com` nếu Home Assistant gặp lỗi DNS/kết nối.
- Ghi log profile QR đã chọn chỉ gồm version, protocol và hostname, không ghi login code, token hoặc credential.

## 1.0.17

- Poll `QueryLoginQRCode` mỗi 200 ms, đúng timer của Imou Life PC, để không bỏ lỡ response token ngắn ngay sau khi điện thoại xác nhận.
- Sửa identity của request sau khi nhận token từ `token/<user>` thành header REST `uuid\\<user>`, đúng phép chuyển đổi trong SDK Imou hiện tại.
- Giữ thời gian chờ `expire` tạm sau `scanned` ở 15 giây dù chu kỳ poll nhanh hơn.

## 1.0.16

- Tạo và query QR PC trên `app.easy4ipcloud.com`, đúng host runtime mà Imou Life PC chuẩn hóa từ cấu hình `app-v2.easy4ipcloud.com`; không còn dùng host Android `app-v3.easy4ipcloud.com` cho QR.
- Sau trạng thái `scanned`, giữ nguyên QR tối đa 15 nhịp poll nếu backend tạm trả `expire`, cho phép phiên đã được điện thoại xác nhận hoàn tất thay vì lập tức hiện QR mới.
- Giữ host Android hiện tại cho đăng nhập account thông thường và chỉ tách host ở luồng QR PC.

## 1.0.15

- Sửa nguyên nhân `QueryLoginQRCode=11001` sau khi quét: QR loại PC nay được tạo và query bằng đúng app credential, app-id `easy4ipbasepc`, phiên bản `5.17.1` và protocol `V9.2.3` của Imou Life PC; không còn ký QR PC bằng identity Android.
- Giữ client-UA PC khi gọi `user.account.Login`, các API thiết bị và MQTT sau khi QR trả token, tránh trộn phiên PC với header Android.
- Nếu Imou tạm trả `11001` sau trạng thái `scanned`, tiếp tục poll đúng login code đến khi backend hoàn tất thay vì bỏ QR và bắt người dùng quét mã mới.
- Credential ứng dụng PC tiếp tục được lưu ở dạng ciphertext; không ghi access key, secret, login code, token hoặc session vào log và giao diện.

## 1.0.14

- Tạo và query QR bằng client-UA loại `pc` thay vì `phone`, để Imou Life mở đúng màn hình đăng nhập PC và hiển thị tùy chọn tự động đăng nhập.
- Xử lý đúng `supportPcAutoLogin=false`: người dùng đã xác nhận QR nhưng chưa tích **Tự động đăng nhập trong 30 ngày tới**, nên Imou không trả token.
- Không retry account `GetToken` vô ích sau trạng thái này; tự tạo QR mới và tiếp tục poll trong cùng phiên.
- Hiển thị hướng dẫn bắt buộc tích ô tự động đăng nhập cả trước và sau khi quét QR trên Web UI.

## 1.0.13

- Khi host toàn cầu trả `status=login` cùng `entryUrlV2`, `username` và `sessionId` nhưng thiếu `token`, hỏi lại `QueryLoginQRCode` trên đúng host khu vực trước khi fallback sang account `GetToken`.
- Hỗ trợ backend trả credential QR trong response header hoặc cookie có tên xác định thay vì trong JSON; không ghi giá trị credential vào log.
- Bổ sung log chẩn đoán an toàn gồm hostname khu vực, `supportPcAutoLogin`, tên response header và nguồn credential; không chứa account, session, token hoặc login code.

## 1.0.12

- Sửa revision theo lớp API chính thức trong APK: `user.account.GetToken=185606` và `user.account.Login=198828`, thay cho revision mặc định `191204` trước đây.
- Khi `QueryLoginQRCode` trả `status=login` nhưng không kèm token, retry account `GetToken` tối đa ba lần để hoàn tất phiên đã được điện thoại phê duyệt.
- Tìm credential trong phản hồi QR trực tiếp hoặc mapping lồng nhau, hỗ trợ các alias tên trường thường gặp.
- Ghi log an toàn danh sách tên trường khi Imou báo login nhưng không trả credential; không ghi giá trị token, session hoặc login code.
- Thêm test hồi quy cho đúng trường hợp `status=login credentials_ready=False` rồi `GetToken`/`Login` thành công.

## 1.0.11

- Xử lý trạng thái QR thực tế `expire`/`expired` do `QueryLoginQRCode` trả về.
- Tạo QR mới ngay trong cùng phiên poll khi mã hết hạn, tăng `qr_generation` để Ingress tự tải ảnh mới và tiếp tục chờ `scanned`/`login`.
- Chuẩn hóa thêm các alias `timeout`, `timedout` thành `expire` và `cancelled` thành `cancel`.
- Thêm test hồi quy đầy đủ cho chuỗi `expire` → QR mới → `scanned` → `login`.

## 1.0.10

- Đồng bộ thế hệ QR giữa backend và Ingress; nếu add-on phải tạo QR mới, trình duyệt tự tải đúng ảnh mới thay vì tiếp tục hiển thị QR cũ đã được điện thoại xác nhận.
- Poll trạng thái ngay sau khi tạo QR và mỗi giây, retry tối đa năm lỗi tạm thời mà không thay login code đang chờ.
- Giữ trạng thái `scanned` khi Imou tạm thời trả lùi về `unscan` và nhận token hoàn chỉnh như trạng thái `login` ngay cả khi trường `status` cập nhật chậm.
- Thêm log an toàn khi trạng thái QR thay đổi và các trường chẩn đoán `qr_generation`, `qr_poll_count`, `qr_last_poll`, `qr_last_error_code`; không chứa login code hoặc token.

## 1.0.9

- Xác định đúng mã máy chủ `12114` là GeeTest mới; bỏ fallback CAPTCHA ảnh vốn bị Imou từ chối với `11010`.
- Thêm đăng nhập QR chính thức qua `user.account.GetLoginQRCode` và `user.account.QueryLoginQRCode`.
- Hiển thị PNG QR trong Ingress, tự poll trạng thái `unscan`, `scanned`, `login`, `cancel` và tiếp tục `user.account.Login` sau khi xác nhận.
- Dùng đúng revision riêng của từng API khi ký request thay vì luôn gửi revision mặc định.
- Không đưa login code, token, account/password hoặc app credential vào Web UI, status JSON, MQTT hay log.

## 1.0.8

- Thay GeeTest Web/H5 bằng fallback CAPTCHA ảnh chính thức có sẵn trong Imou Life Android: `GetImageValidCode` và `CheckImageValidCode`.
- Hiển thị ảnh CAPTCHA `150x50` trong Ingress, cho nhập mã và đổi ảnh mà không cần dữ liệu GeeGuard native.
- Không gọi `common.validcode.CheckGeeTest4` từ giao diện add-on nữa, tránh lỗi `11010` do thiếu chữ ký thiết bị Android.

## 1.0.7

- Mã hóa account CAPTCHA bằng AES-GCM với IV ngẫu nhiên 12 byte và ghép IV trước ciphertext, đúng provider crypto của Imou Life Android.
- Loại bỏ IV toàn số 0 từng khiến payload mã hóa có thể bị Imou từ chối với mã `11010`.

## 1.0.6

- Gửi và ký `common.validcode.CheckGeeTest4` với đúng `Content-Type: application/json; charset=utf-8` như Imou Life Android.
- Ghi log chẩn đoán lỗi API chỉ gồm tên API, mã lỗi, HTTP status, revision, content type, host và loại xác thực; không ghi credential hoặc dữ liệu CAPTCHA.

## 1.0.5

- Removed the incomplete Android GeeGuard emulation; the bundled Android SDK signs native device data that a browser cannot reproduce.
- Switched CAPTCHA rendering to the standard GeeTest Web/H5 flow without forcing an Android client type.
- On the first `11010`, requests a completely fresh Imou CAPTCHA challenge instead of reusing consumed `captchaId` and `verifyToken` values.
- Tracks full Login completion separately from temporary GetToken credentials so login resumes correctly after CAPTCHA.

## 1.0.4

- Fixed GeeTest account encryption to match the Imou Android APK exactly: a zero 12-byte AES-GCM IV prefixed to the ciphertext before Base64 encoding.
- Retained the Android GeeLab SDK compatibility mode and one-time H5 fallback for error `11010`.

## 1.0.3

- Matched the bundled GeeLab Android SDK 1.8.11 client type and application metadata.
- Added an automatic one-time H5 CAPTCHA fallback when Imou returns error `11010`.
- Exposed the active CAPTCHA compatibility mode in the Ingress Web UI and status API.

## 1.0.2

- Added AES-GCM account encryption for CAPTCHA verification; corrected to the APK-compatible wire format in 1.0.4.
- Separated GeeTest validation from the following account login retry.
- Added the numeric Imou error code to CAPTCHA failures without exposing sensitive data.

## 1.0.1

- Fixed GeeTest bind CAPTCHA rendering by opening the widget with `showCaptcha()`.
- Added a visible retry button and timeout/error status in the Ingress Web UI.
- Accepted Vietnamese mobile accounts as `084…`, `84…`, `+84…`, or `0084…`.

## 1.0.0

- Standalone Imou account login and GeeTest Ingress flow.
- Device, thing-model, property, channel, motion, camera, and service discovery.
- Home Assistant MQTT Discovery, state publishing, and command handling.
- Imou cloud MQTT events forwarded to Home Assistant MQTT.
