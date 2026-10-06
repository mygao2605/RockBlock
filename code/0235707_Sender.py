# -*- coding: utf-8 -*-
"""
🛰️ HỆ THỐNG TRUYỀN DỮ LIỆU QUA VỆ TINH IRIDIUM - ROCKBLOCK 9603
📡 MODULE GỬI (SENDER) - SERIAL / ID: 0235707 ➡️ TARGET: 0235708

Nguyên lý định tuyến trực tiếp RockBLOCK-to-RockBLOCK (Direct Addressing):
  - Tiền tố: RB + <Serial_7_ký_tự_nhận> + <Nội_dung> (Ví dụ: RB0235708Hello)
  - Gateway Rock Seven tự động bóc 9 ký tự tiền tố 'RB0235708' trước khi trao cho bên nhận.
"""

import sys
import time
import argparse
from datetime import datetime
import serial
import serial.tools.list_ports

# Đảm bảo hiển thị Unicode/Emoji mượt mà trên Windows console
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass


class RockBlockSender:
    """Lớp điều khiển module RockBLOCK gửi thông điệp SBD qua mạng vệ tinh Iridium."""

    MO_STATUS_DESC = {
        0: "Thành công: MO message đã được trạm vệ tinh Iridium tiếp nhận hoàn tất!",
        1: "Thành công: MO message đã gửi thành công, nhưng kích thước quá lớn đối với bên nhận.",
        2: "Thành công: MO message đã gửi thành công, nhưng trạm không xác định được vị trí vệ tinh.",
        32: "Thất bại: Không bắt được sóng vệ tinh Iridium hoặc tín hiệu quá yếu (Timeout).",
        33: "Thất bại: Mất kết nối vô tuyến trong quá trình truyền dữ liệu.",
        34: "Thất bại: Giao thức mạng vệ tinh báo bận / nghẽn kênh.",
        35: "Thất bại: Modem Iridium bị khóa hoặc SIM chưa đăng ký gói cước SBD."
    }

    def __init__(self, port, baudrate=19200, timeout=3, log_file=None):
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout
        self.log_file = log_file
        self.ser = None
        self.imei = None

    def log(self, text):
        """In ra màn hình và ghi log kèm mốc thời gian."""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        line = f"[{timestamp}] {text}"
        print(line)
        if self.log_file:
            try:
                with open(self.log_file, "a", encoding="utf-8") as f:
                    f.write(line + "\n")
            except Exception as e:
                print(f"[!] Lỗi ghi file log: {e}")

    def connect(self):
        """Mở cổng Serial kết nối với module RockBLOCK."""
        try:
            self.ser = serial.Serial(
                port=self.port,
                baudrate=self.baudrate,
                bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                timeout=self.timeout
            )
            self.log(f"Đã mở thành công cổng {self.port} @ {self.baudrate} baud.")
            return True
        except Exception as e:
            self.log(f"[LỖI KẾT NỐI] Không thể mở cổng {self.port}: {e}")
            return False

    def disconnect(self):
        """Đóng kết nối Serial an toàn."""
        if self.ser and self.ser.is_open:
            self.ser.close()
            self.log(f"Đã đóng cổng {self.port}.")

    def send_cmd(self, cmd, wait=2.0):
        """Gửi lệnh AT, chờ và đọc chuỗi phản hồi từ modem."""
        if not self.ser or not self.ser.is_open:
            self.log("Cổng Serial chưa được mở!")
            return ""

        self.ser.reset_input_buffer()
        self.ser.write((cmd + "\r").encode("ascii"))
        time.sleep(wait)

        response = self.ser.read_all().decode("ascii", errors="ignore").strip()
        return response

    def init_modem(self):
        """Khởi tạo modem, tắt Echo (ATE0) và đọc số IMEI thiết bị."""
        self.log("--- Khởi tạo cấu hình Modem ---")
        resp = self.send_cmd("AT", wait=1.0)
        if "OK" not in resp:
            self.log(f"Modem không phản hồi OK với lệnh AT. Phản hồi: {resp}")
            return False

        # Tắt echo lệnh (ATE0) và tắt điều khiển luồng phần cứng (AT&K0)
        self.send_cmd("ATE0", wait=1.0)
        self.send_cmd("AT&K0", wait=1.0)

        # Đọc số IMEI (AT+CGSN)
        imei_resp = self.send_cmd("AT+CGSN", wait=1.0)
        for line in imei_resp.splitlines():
            line = line.strip()
            if line.isdigit() and len(line) >= 14:
                self.imei = line
                break

        # Đọc phiên bản firmware (AT+CGMR)
        fw = self.send_cmd("AT+CGMR", wait=1.0)

        self.log(f"🔢 IMEI Sender   : {self.imei if self.imei else 'Không rõ'}")
        self.log(f"💾 Firmware      : {fw.replace('OK', '').strip()}")
        self.log("✅ Modem đã sẵn sàng hoạt động!")
        return True

    def check_signal(self):
        """Kiểm tra mức sóng vệ tinh Iridium (AT+CSQ). Trả về số nguyên từ 0 đến 5."""
        resp = self.send_cmd("AT+CSQ", wait=2.0)
        if "+CSQ:" in resp:
            try:
                bars_str = resp.split("+CSQ:")[1].strip().split()[0]
                bars = int(bars_str)
                bar_display = "■" * bars + "□" * (5 - bars)
                self.log(f"📶 Mức tín hiệu vệ tinh: [{bar_display}] ({bars}/5 vạch)")
                return bars
            except Exception:
                pass
        self.log(f"⚠️ Không đọc được mức sóng. Phản hồi: {resp}")
        return 0

    def wait_for_signal(self, min_bars=2, max_attempts=10, interval=5):
        """Vòng lặp chờ sóng vệ tinh đạt mức tối thiểu trước khi gửi."""
        self.log(f"⏳ Đang chờ sóng vệ tinh đạt tối thiểu {min_bars}/5 vạch...")
        for i in range(1, max_attempts + 1):
            bars = self.check_signal()
            if bars >= min_bars:
                self.log(f"🎉 Đã bắt được sóng vệ tinh đủ mạnh ({bars}/5 vạch)!")
                return True
            if i < max_attempts:
                self.log(f"   [Lần {i}/{max_attempts}] Sóng chưa đủ. Đợi {interval}s (Lưu ý: Hướng anten thẳng lên trời)...")
                time.sleep(interval)
        self.log(f"❌ Sau {max_attempts} lần kiểm tra vẫn chưa có đủ sóng vệ tinh.")
        return False

    def send_message(self, message_text, target_serial="0235708", wait_sbd=20):
        """
        Nạp tin nhắn và truyền qua vệ tinh Iridium tới target_serial.
        Theo chuẩn RockBLOCK-to-RockBLOCK Direct Addressing:
        - Tiền tố: 'RB' + <Serial 7 chữ số của thiết bị nhận>
        - Gateway Rock Seven tự động bóc tiền tố trước khi đưa vào hộp thư bên nhận.
        """
        if target_serial:
            clean_serial = f"{int(target_serial):07d}"
            full_payload = f"RB{clean_serial}{message_text}"
            prefix_info = f"RB{clean_serial}"
        else:
            clean_serial = "None"
            full_payload = message_text
            prefix_info = "None"

        self.log("=" * 60)
        self.log(f"📤 BẮT ĐẦU QUÁ TRÌNH GỬI THÔNG ĐIỆP ĐẾN ROCKBLOCK {clean_serial}:")
        self.log(f"🎯 Đích đến (Target Serial): {clean_serial}")
        self.log(f"🏷️  Tiền tố Direct Addressing: {prefix_info}")
        self.log(f"📝 Nội dung thực tế        : \"{message_text}\"")
        self.log(f"📦 Toàn bộ chuỗi gửi đi (AT): \"{full_payload}\"")
        self.log(f"📏 Tổng độ dài nạp vào modem: {len(full_payload.encode('ascii'))} bytes")
        self.log("=" * 60)

        # Bước 1: Xóa bộ đệm MO cũ
        self.send_cmd("AT+SBDD0", wait=1.0)

        # Bước 2: Nạp chuỗi có tiền tố RB vào MO Buffer qua lệnh AT+SBDWT
        write_cmd = f"AT+SBDWT={full_payload}"
        write_resp = self.send_cmd(write_cmd, wait=1.0)
        if "OK" not in write_resp:
            self.log(f"❌ Không thể nạp tin nhắn vào bộ đệm! Phản hồi: {write_resp}")
            return False
        self.log("✅ Đã nạp thông điệp vào bộ đệm MO thành công.")

        # Bước 3: Kích hoạt phiên truyền dữ liệu vệ tinh SBD (AT+SBDIX)
        self.log(f"🚀 Đang gọi AT+SBDIX (Đang kết nối vệ tinh Iridium, vui lòng đợi {wait_sbd}s)...")
        sbdix_resp = self.send_cmd("AT+SBDIX", wait=wait_sbd)
        self.log(f"📥 Phản hồi từ phiên SBDIX:\n{sbdix_resp}")

        # Bước 4: Phân tích kết quả trả về từ +SBDIX
        if "+SBDIX:" in sbdix_resp:
            try:
                raw_params = sbdix_resp.split("+SBDIX:")[1].strip().split(",")
                mo_status = int(raw_params[0].strip())
                momsn = int(raw_params[1].strip())
                mt_status = int(raw_params[2].strip())
                mt_len = int(raw_params[4].strip())

                desc = self.MO_STATUS_DESC.get(mo_status, f"Mã trạng thái MO không xác định: {mo_status}")
                self.log(f"📊 Kết quả MO Status = {mo_status}: {desc}")
                self.log(f"🔢 MOMSN (Số thứ tự tin nhắn gửi): {momsn}")

                if mt_status == 1:
                    self.log(f"📬 [Thông báo kèm theo] Modem nhận được 1 tin nhắn MT ({mt_len} bytes) từ Gateway!")

                # Bước 5: Xóa bộ đệm MO sau khi gửi
                self.send_cmd("AT+SBDD0", wait=1.0)

                if mo_status in [0, 1, 2]:
                    self.log("✨ KẾT QUẢ: GỬI TIN NHẮN QUA VỆ TINH THÀNH CÔNG RỰC RỠ!")
                    self.log(f"ℹ️  Gateway Rock Seven sẽ bóc bỏ 'RB{clean_serial}' và chuyển nội dung '\"{message_text}\"' đến RockBLOCK {clean_serial}.")
                    return True
                else:
                    self.log(f"⚠️ GỬI THẤT BẠI: Mã lỗi MO = {mo_status}. (Gợi ý: Cần anten ngoài trời, góc quét bầu trời thoáng)")
                    return False
            except Exception as ex:
                self.log(f"❌ Lỗi khi phân tích chuỗi phản hồi SBDIX: {ex}")
                return False
        else:
            self.log("❌ Modem không trả về chuỗi kết quả +SBDIX. Vui lòng kiểm tra lại cáp và nguồn cấp!")
            return False

    def read_buffer_content(self):
        """Đọc nội dung bản tin nhận được từ MT Buffer qua AT+SBDRT."""
        raw_msg = self.send_cmd("AT+SBDRT", wait=1.5)
        lines = [line.strip() for line in raw_msg.splitlines() if line.strip() and line.strip() != "OK"]
        cleaned_msg = "\n".join(lines) if lines else raw_msg
        self.send_cmd("AT+SBDD1", wait=1.0)
        return cleaned_msg

    def check_for_reply(self, wait_sbd=20):
        """Kiểm tra xem Receiver đã gửi lại bản tin phản hồi (ACK) hay chưa."""
        self.log("📡 Đang kiểm tra hộp thư vệ tinh (AT+SBDIX) để tìm bản tin phản hồi (ACK)...")
        sbdix_resp = self.send_cmd("AT+SBDIX", wait=wait_sbd)
        if "+SBDIX:" in sbdix_resp:
            try:
                params = sbdix_resp.split("+SBDIX:")[1].strip().split(",")
                mt_status = int(params[2].strip())
                if mt_status == 1:
                    ack_msg = self.read_buffer_content()
                    self.log(f"🎉 NHẬN ĐƯỢC BẢN TIN PHẢN HỒI (ACK): \"{ack_msg}\"")
                    return True, ack_msg
                else:
                    self.log("📭 Hộp thư trống, Receiver chưa gửi lại bản tin ACK nào.")
                    return False, None
            except Exception as e:
                self.log(f"❌ Lỗi giải mã phản hồi SBDIX: {e}")
                return False, None
        return False, None


def list_serial_ports():
    """Liệt kê các cổng COM hiện có trên máy tính."""
    print("🔍 Danh sách các cổng COM khả dụng:")
    ports = list(serial.tools.list_ports.comports())
    if not ports:
        print("   [!] Không tìm thấy cổng COM nào. Hãy kiểm tra kết nối cáp USB!")
    for p in ports:
        print(f"   - {p.device}: {p.description}")
    return ports


def main():
    parser = argparse.ArgumentParser(description="RockBLOCK 9603 - Sender Script (0235707)")
    parser.add_argument("--port", default="COM14", help="Cổng COM của thiết bị gửi (Mặc định: COM14)")
    parser.add_argument("--baud", type=int, default=19200, help="Baudrate (Mặc định: 19200)")
    parser.add_argument("--sender", default="0235707", help="Serial máy gửi (Mặc định: 0235707)")
    parser.add_argument("--target", default="0235708", help="Serial máy nhận (Mặc định: 0235708)")
    parser.add_argument("--msg", default=None, help="Nội dung tin nhắn muốn gửi ngay qua CLI")
    parser.add_argument("--wait-ack", action="store_true", help="Chờ kiểm tra ACK sau khi gửi")
    args = parser.parse_args()

    list_serial_ports()

    log_file = f"rockblock_sender_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
    print("\n--- THÔNG TIN CẤU HÌNH ---")
    print(f"Cổng Serial      : {args.port} @ {args.baud} bps")
    print(f"Sender Serial    : {args.sender}")
    print(f"Target Receiver  : {args.target} (Tiền tố: RB{int(args.target):07d})")
    print(f"File lưu log     : {log_file}")

    sender = RockBlockSender(port=args.port, baudrate=args.baud, log_file=log_file)
    if not sender.connect():
        print(f"\n❌ Không thể kết nối tới {args.port}. Bạn có thể truyền đối số --port COMx khi chạy lại.")
        return

    try:
        if not sender.init_modem():
            print("⚠️ Khởi tạo modem thất bại. Vui lòng kiểm tra lại cáp kết nối.")
            return

        # Nếu truyền tin nhắn qua dòng lệnh (--msg)
        if args.msg:
            sender.wait_for_signal(min_bars=1, max_attempts=5, interval=3)
            ok = sender.send_message(args.msg, target_serial=args.target, wait_sbd=20)
            if ok and args.wait_ack:
                print("\n⏳ Chờ 15s để Receiver kịp xử lý rồi kiểm tra ACK...")
                time.sleep(15)
                sender.check_for_reply(wait_sbd=20)
            return

        # Menu điều khiển trực tiếp
        while True:
            print("\n" + "=" * 50)
            print("🕹️  MENU ĐIỀU KHIỂN ROCKBLOCK SENDER (0235707)")
            print("=" * 50)
            print("1. Kiểm tra mức sóng vệ tinh (AT+CSQ)")
            print("2. Chờ sóng đủ mạnh (tối thiểu 2 vạch)")
            print("3. Gửi tin nhắn mẫu (HELLO kèm timestamp)")
            print("4. Nhập tin nhắn tùy chỉnh để gửi")
            print("5. Gửi dữ liệu cảm biến / định vị (Telemetry)")
            print("6. Kiểm tra bản tin phản hồi (ACK) từ Receiver")
            print("0. Thoát chương trình")
            print("=" * 50)

            choice = input("👉 Chọn chức năng (0-6): ").strip()
            if choice == "0":
                break
            elif choice == "1":
                sender.check_signal()
            elif choice == "2":
                sender.wait_for_signal(min_bars=2, max_attempts=8, interval=5)
            elif choice == "3":
                msg = f"HELLO FROM {args.sender} TO {args.target} TIME={datetime.now().strftime('%H:%M:%S')}"
                sender.send_message(msg, target_serial=args.target, wait_sbd=20)
            elif choice == "4":
                user_msg = input("Nhập nội dung cần gửi: ").strip()
                if user_msg:
                    sender.send_message(user_msg, target_serial=args.target, wait_sbd=20)
                else:
                    print("⚠️ Nội dung không được để trống!")
            elif choice == "5":
                telemetry_msg = f"GPS:21.0285,105.8542,T:28.5,B:98,TIME:{datetime.now().strftime('%H%M%S')}"
                print(f"📦 Đang gửi gói tin Telemetry: {telemetry_msg}")
                sender.send_message(telemetry_msg, target_serial=args.target, wait_sbd=20)
            elif choice == "6":
                sender.check_for_reply(wait_sbd=20)
            else:
                print("Lựa chọn không hợp lệ!")

    except KeyboardInterrupt:
        print("\n🛑 Đã nhận tín hiệu dừng từ bàn phím (Ctrl+C).")
    finally:
        sender.disconnect()
        print(f"📄 Toàn bộ log đã được lưu tại: {log_file}")


if __name__ == "__main__":
    main()
