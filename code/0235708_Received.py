# -*- coding: utf-8 -*-
"""
🛰️ HỆ THỐNG TRUYỀN DỮ LIỆU QUA VỆ TINH IRIDIUM - ROCKBLOCK 9603
📥 MODULE NHẬN & TỰ ĐỘNG PHẢN HỒI (RECEIVER & AUTO-REPLY) - SERIAL / ID: 0235708

Cơ chế nhận và tự động phản hồi (Auto-ACK / 2-way):
  1. Kiểm tra hộp thư vệ tinh Iridium bằng lệnh AT+SBDIX (Mailbox check).
  2. Khi MT Status == 1: Đọc nội dung qua AT+SBDRT và xóa buffer bằng AT+SBDD1.
  3. Tự động nạp bản tin phản hồi (ACK) vào bộ đệm MO (AT+SBDWT) kèm tiền tố 'RB0235707'.
  4. Bắn bản tin phản hồi ngược về thiết bị gửi qua AT+SBDIX.
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


class RockBlockReceiver:
    """Lớp điều khiển module RockBLOCK nhận tín hiệu MT và tự động gửi lại phản hồi MO qua vệ tinh Iridium."""

    MT_STATUS_DESC = {
        0: "Không có tin nhắn MT nào chờ trong hộp thư Gateway.",
        1: "Thành công: Đã nhận được 1 tin nhắn MT từ Gateway về bộ đệm modem!",
        2: "Lỗi trong quá trình nhận tin nhắn MT từ Gateway."
    }

    MO_STATUS_DESC = {
        0: "Thành công: MO message đã được trạm vệ tinh Iridium tiếp nhận hoàn tất!",
        1: "Thành công: MO message gửi thành công, nhưng kích thước quá lớn.",
        2: "Thành công: MO message gửi thành công, nhưng không định vị được trạm.",
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

    def log(self, message):
        """In log ra console và lưu vào file log với timestamp."""
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        log_entry = f"[{now}] {message}"
        print(log_entry)
        if self.log_file:
            try:
                with open(self.log_file, "a", encoding="utf-8") as f:
                    f.write(log_entry + "\n")
            except Exception as e:
                print(f"[!] Lỗi ghi file log: {e}")

    def connect(self):
        """Mở cổng Serial kết nối tới RockBLOCK."""
        try:
            self.ser = serial.Serial(
                port=self.port,
                baudrate=self.baudrate,
                bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                timeout=self.timeout
            )
            self.log(f"✅ Đã kết nối thành công tới cổng {self.port} @ {self.baudrate} baud.")
            return True
        except serial.SerialException as e:
            self.log(f"❌ Lỗi mở cổng Serial {self.port}: {e}")
            return False

    def disconnect(self):
        """Đóng kết nối Serial an toàn."""
        if self.ser and self.ser.is_open:
            self.ser.close()
            self.log("🔌 Đã ngắt kết nối cổng Serial.")

    def send_cmd(self, cmd, wait=1.5):
        """Gửi lệnh AT tới modem và đọc phản hồi."""
        if not self.ser or not self.ser.is_open:
            self.log("❌ Cổng Serial chưa mở!")
            return ""

        self.ser.reset_input_buffer()
        self.ser.write((cmd + "\r").encode("ascii"))
        time.sleep(wait)

        response = self.ser.read_all().decode("ascii", errors="ignore").strip()
        return response

    def init_modem(self):
        """Khởi tạo cấu hình ban đầu cho RockBLOCK."""
        self.log("--- Bắt đầu khởi tạo cấu hình modem bên nhận ---")
        resp = self.send_cmd("AT", wait=1.0)
        if "OK" not in resp:
            self.log(f"⚠️ Modem không phản hồi lệnh AT: {resp}")
            return False

        self.send_cmd("ATE0", wait=1.0)
        self.send_cmd("AT&K0", wait=1.0)

        fw = self.send_cmd("AT+CGMR", wait=1.5)
        self.imei = self.send_cmd("AT+CGSN", wait=1.5).replace("OK", "").strip()

        self.log(f"📱 IMEI thiết bị nhận: {self.imei}")
        self.log(f"💾 Firmware          : {fw.replace('OK', '').strip()}")
        self.log("✅ Modem nhận đã sẵn sàng!")
        return True

    def check_signal(self):
        """Kiểm tra mức sóng vệ tinh Iridium (AT+CSQ). Trả về 0-5 vạch."""
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

    def read_buffer_content(self):
        """Đọc nội dung tin nhắn văn bản từ MT Buffer bằng lệnh AT+SBDRT, sau đó xóa buffer bằng AT+SBDD1."""
        self.log("📖 Đang đọc nội dung từ MT Buffer (AT+SBDRT)...")
        raw_msg = self.send_cmd("AT+SBDRT", wait=1.5)

        cleaned_msg = raw_msg
        lines = [line.strip() for line in raw_msg.splitlines() if line.strip() and line.strip() != "OK"]
        if lines:
            cleaned_msg = "\n".join(lines)

        self.log("=" * 60)
        self.log("📩 NỘI DUNG TIN NHẮN NHẬN ĐƯỢC:")
        self.log(f"👉 \"{cleaned_msg}\"")
        self.log("=" * 60)

        self.send_cmd("AT+SBDD1", wait=1.0)
        return cleaned_msg

    def check_mailbox(self, wait_sbd=20):
        """
        Kiểm tra hộp thư Iridium bằng lệnh AT+SBDIX.
        Trả về: (has_message: bool, message_text: str hoặc None, queued_count: int)
        """
        self.log("📡 Đang gửi lệnh AT+SBDIX để kiểm tra hộp thư vệ tinh (Mailbox Check)...")
        sbdix_resp = self.send_cmd("AT+SBDIX", wait=wait_sbd)
        self.log(f"📥 Phản hồi phiên SBDIX:\n{sbdix_resp}")

        if "+SBDIX:" in sbdix_resp:
            try:
                params = sbdix_resp.split("+SBDIX:")[1].strip().split(",")
                mt_status = int(params[2].strip())
                mtmsn = int(params[3].strip())
                mt_len = int(params[4].strip())
                mt_queued = int(params[5].strip())

                desc = self.MT_STATUS_DESC.get(mt_status, f"Mã trạng thái MT không xác định: {mt_status}")
                self.log(f"📊 Kết quả MT Status = {mt_status}: {desc}")
                self.log(f"🔢 MTMSN: {mtmsn} | Độ dài: {mt_len} bytes | Còn trong hàng đợi: {mt_queued}")

                if mt_status == 1:
                    msg_text = self.read_buffer_content()
                    return True, msg_text, mt_queued
                else:
                    return False, None, mt_queued

            except Exception as e:
                self.log(f"❌ Lỗi giải mã phản hồi SBDIX: {e}")
                return False, None, 0
        else:
            self.log("⚠️ Không nhận được phản hồi +SBDIX từ modem.")
            return False, None, 0

    def send_reply(self, reply_text, target_serial="0235707", use_rb_prefix=True, wait_sbd=20):
        """
        Gửi lại 1 bản tin phản hồi (Reply / ACK) ngược về phía Sender qua vệ tinh.
        - reply_text: Nội dung tin nhắn muốn gửi lại.
        - target_serial: Serial của thiết bị nhận phản hồi (0235707).
        - use_rb_prefix: True nếu ghép tiền tố RB<Serial>, False nếu dùng SBD_ROCKBLOCK.
        """
        if use_rb_prefix and target_serial:
            clean_serial = f"{int(target_serial):07d}"
            payload = f"RB{clean_serial}{reply_text}"
            prefix_info = f"RB{clean_serial}"
        else:
            payload = reply_text
            clean_serial = target_serial or "Default Gateway"
            prefix_info = "None (SBD_ROCKBLOCK)"

        self.log("=" * 60)
        self.log("📤 BẮT ĐẦU GỬI BẢN TIN PHẢN HỒI (REPLY / ACK) VỀ SENDER:")
        self.log(f"🎯 Đích đến          : {clean_serial}")
        self.log(f"🏷️  Tiền tố          : {prefix_info}")
        self.log(f"📝 Nội dung phản hồi : \"{reply_text}\"")
        self.log(f"📦 Chuỗi nạp modem   : \"{payload}\"")
        self.log(f"📏 Độ dài gửi đi     : {len(payload.encode('ascii'))} bytes")
        self.log("=" * 60)

        # 1. Xóa bộ đệm MO cũ
        self.send_cmd("AT+SBDD0", wait=1.0)

        # 2. Nạp nội dung phản hồi vào MO Buffer
        write_resp = self.send_cmd(f"AT+SBDWT={payload}", wait=1.0)
        if "OK" not in write_resp:
            self.log(f"❌ Không thể nạp bản tin phản hồi vào MO Buffer: {write_resp}")
            return False
        self.log("✅ Đã nạp bản tin phản hồi vào MO Buffer thành công.")

        # 3. Kích hoạt phiên vệ tinh SBDIX để truyền dữ liệu
        self.log(f"🚀 Đang gọi AT+SBDIX truyền bản tin phản hồi lên vệ tinh (vui lòng đợi {wait_sbd}s)...")
        sbdix_resp = self.send_cmd("AT+SBDIX", wait=wait_sbd)
        self.log(f"📥 Phản hồi phiên gửi phản hồi:\n{sbdix_resp}")

        # 4. Phân tích kết quả gửi MO
        if "+SBDIX:" in sbdix_resp:
            try:
                params = sbdix_resp.split("+SBDIX:")[1].strip().split(",")
                mo_status = int(params[0].strip())
                momsn = int(params[1].strip())

                # Xóa MO Buffer sau khi gửi
                self.send_cmd("AT+SBDD0", wait=1.0)

                desc = self.MO_STATUS_DESC.get(mo_status, f"MO Status: {mo_status}")
                if mo_status in [0, 1, 2]:
                    self.log(f"✨ KẾT QUẢ: GỬI BẢN TIN PHẢN HỒI THÀNH CÔNG! (MOMSN: {momsn})")
                    return True
                else:
                    self.log(f"⚠️ GỬI PHẢN HỒI THẤT BẠI: Mã lỗi MO = {mo_status} ({desc})")
                    return False
            except Exception as e:
                self.log(f"❌ Lỗi phân tích phản hồi SBDIX: {e}")
                return False
        else:
            self.log("❌ Modem không phản hồi chuỗi +SBDIX.")
            return False

    def listen_loop(self, poll_interval=30, max_cycles=0, auto_reply=True, target_serial="0235707", use_rb_prefix=True, ack_prefix="ACK_OK"):
        """
        Chế độ lắng nghe liên tục và TỰ ĐỘNG PHẢN HỒI:
        - Định kỳ kiểm tra hộp thư Iridium.
        - KHI NHẬN ĐƯỢC TIN NHẮN MỚI: Tự động gửi lại 1 bản tin ACK ngược lại cho Sender!
        """
        self.log("=" * 60)
        self.log(f"🎧 BẬT CHẾ ĐỘ TRỰC NHẬN TIN NHẮN (Chu kỳ thăm dò: {poll_interval}s)")
        if auto_reply:
            self.log(f"🤖 Chế độ tự động phản hồi: BẬT -> Gửi lại bản tin về: {target_serial}")
        self.log("👉 Nhấn Ctrl+C để dừng chế độ lắng nghe bất cứ lúc nào.")
        self.log("=" * 60)

        cycle = 0
        try:
            while True:
                cycle += 1
                cycle_str = f"/{max_cycles}" if max_cycles > 0 else ""
                self.log(f"\n--- Chu kỳ kiểm tra {cycle}{cycle_str} ---")

                bars = self.check_signal()
                if bars >= 1:
                    has_msg, msg, queued = self.check_mailbox(wait_sbd=20)
                    if has_msg:
                        self.log(f"🎉 ĐÃ NHẬN ĐƯỢC TIN NHẮN MỚI: \"{msg}\"")

                        # TỰ ĐỘNG GỬI LẠI 1 BẢN TIN PHẢN HỒI
                        if auto_reply:
                            timestamp_str = datetime.now().strftime('%H:%M:%S')
                            short_content = msg[:12] if len(msg) > 12 else msg
                            reply_content = f"{ack_prefix}: RECV '{short_content}' TIME={timestamp_str}"

                            self.log("🔄 Đang tự động gửi lại bản tin phản hồi...")
                            time.sleep(2)
                            self.send_reply(
                                reply_text=reply_content,
                                target_serial=target_serial,
                                use_rb_prefix=use_rb_prefix,
                                wait_sbd=20
                            )

                        # Kéo tiếp nếu còn tin trong hàng đợi Gateway
                        while queued > 0:
                            self.log(f"📦 Vẫn còn {queued} tin nhắn trong hàng đợi Gateway. Đang tải tiếp...")
                            time.sleep(3)
                            has_next, next_msg, queued = self.check_mailbox(wait_sbd=20)
                            if has_next and auto_reply:
                                time.sleep(2)
                                rep = f"{ack_prefix}: RECV '{next_msg[:12]}' TIME={datetime.now().strftime('%H:%M:%S')}"
                                self.send_reply(rep, target_serial=target_serial, use_rb_prefix=use_rb_prefix, wait_sbd=20)
                    else:
                        self.log("📭 Hộp thư trống, chưa có tin nhắn mới.")
                else:
                    self.log("⚠️ Sóng vệ tinh 0 vạch. Tạm bỏ qua chu kỳ này để tiết kiệm thời gian.")

                if max_cycles > 0 and cycle >= max_cycles:
                    self.log("⏹️ Đã đạt giới hạn số chu kỳ kiểm tra.")
                    break

                self.log(f"💤 Nghỉ {poll_interval} giây trước lần kiểm tra kế tiếp...")
                time.sleep(poll_interval)

        except KeyboardInterrupt:
            self.log("🛑 Người dùng đã bấm dừng chế độ lắng nghe (Ctrl+C).")


def list_serial_ports():
    """Liệt kê các cổng COM hiện có trên máy tính."""
    print("🔍 Đang quét các cổng COM khả dụng trên máy:")
    ports = list(serial.tools.list_ports.comports())
    if not ports:
        print("  [!] Không tìm thấy cổng COM nào. Hãy kiểm tra kết nối cáp USB-FTDI!")
    else:
        for p in ports:
            print(f"  -> Cổng: {p.device:<8} | Mô tả: {p.description}")
    return ports


def main():
    parser = argparse.ArgumentParser(description="RockBLOCK 9603 - Receiver & Auto-Reply Script (0235708)")
    parser.add_argument("--port", default="COM16", help="Cổng COM của thiết bị nhận (Mặc định: COM16)")
    parser.add_argument("--baud", type=int, default=19200, help="Baudrate (Mặc định: 19200)")
    parser.add_argument("--sender-serial", default="0235707", help="Serial máy gửi cần phản hồi về (Mặc định: 0235707)")
    parser.add_argument("--interval", type=int, default=30, help="Chu kỳ thăm dò trong chế độ lắng nghe (giây, mặc định: 30)")
    parser.add_argument("--cycles", type=int, default=0, help="Số chu kỳ tối đa khi lắng nghe (0 = lặp vô hạn)")
    parser.add_argument("--no-auto-ack", action="store_true", help="Tắt tính năng tự động gửi lại phản hồi (Auto-ACK)")
    parser.add_argument("--mode", choices=["interactive", "listen", "oneshot"], default="interactive", help="Chế độ chạy")
    args = parser.parse_args()

    list_serial_ports()

    log_file = f"receiver_0235708_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
    auto_reply = not args.no_auto_ack

    print(f"\n⚙️ Cấu hình thiết bị nhận: Port = {args.port}, Baud = {args.baud}")
    print(f"🎯 Đích phản hồi về    : {args.sender_serial} (Tiền tố: RB{int(args.sender_serial):07d})")
    print(f"🤖 Tự động gửi lại tin : {'BẬT (Enabled)' if auto_reply else 'TẮT (Disabled)'}")
    print(f"📄 File lưu log        : {log_file}")

    receiver = RockBlockReceiver(port=args.port, baudrate=args.baud, log_file=log_file)
    if not receiver.connect():
        print(f"\n❌ Không thể kết nối tới {args.port}. Bạn có thể truyền đối số --port COMx khi chạy lại.")
        return

    try:
        if not receiver.init_modem():
            print("⚠️ Khởi tạo modem thất bại. Vui lòng kiểm tra lại cáp kết nối.")
            return

        if args.mode == "listen":
            receiver.listen_loop(
                poll_interval=args.interval,
                max_cycles=args.cycles,
                auto_reply=auto_reply,
                target_serial=args.sender_serial,
                use_rb_prefix=True,
                ack_prefix="ACK_OK"
            )
            return
        elif args.mode == "oneshot":
            receiver.check_signal()
            has_msg, msg, queued = receiver.check_mailbox(wait_sbd=20)
            if has_msg and auto_reply:
                rep = f"ACK_OK: RECV '{msg[:15]}' TIME={datetime.now().strftime('%H:%M:%S')}"
                receiver.send_reply(rep, target_serial=args.sender_serial, use_rb_prefix=True, wait_sbd=20)
            return

        # Menu điều khiển trực tiếp (interactive mode)
        while True:
            print("\n" + "=" * 55)
            print("🕹️  MENU ĐIỀU KHIỂN ROCKBLOCK RECEIVER (0235708)")
            print("=" * 55)
            print("1. Kiểm tra mức sóng vệ tinh (AT+CSQ)")
            print("2. Kiểm tra hộp thư 1 lần (One-shot check & Auto-ACK)")
            print(f"3. Bật chế độ lắng nghe liên tục (Chu kỳ {args.interval}s)")
            print("4. Gửi bản tin phản hồi thủ công về Sender")
            print(f"5. Chuyển trạng thái Auto-ACK (Hiện tại: {'BẬT' if auto_reply else 'TẮT'})")
            print("0. Thoát chương trình")
            print("=" * 55)

            choice = input("👉 Chọn chức năng (0-5): ").strip()
            if choice == "0":
                break
            elif choice == "1":
                receiver.check_signal()
            elif choice == "2":
                receiver.check_signal()
                has_msg, msg, _ = receiver.check_mailbox(wait_sbd=20)
                if has_msg and auto_reply:
                    rep = f"ACK_OK: RECV '{msg[:15]}' TIME={datetime.now().strftime('%H:%M:%S')}"
                    print(f"\n📤 [AUTO-REPLY] Đang tự động gửi lại phản hồi...")
                    receiver.send_reply(rep, target_serial=args.sender_serial, use_rb_prefix=True, wait_sbd=20)
            elif choice == "3":
                receiver.listen_loop(
                    poll_interval=args.interval,
                    max_cycles=args.cycles,
                    auto_reply=auto_reply,
                    target_serial=args.sender_serial,
                    use_rb_prefix=True,
                    ack_prefix="ACK_OK"
                )
            elif choice == "4":
                manual_msg = input("Nhập nội dung phản hồi muốn gửi: ").strip()
                if manual_msg:
                    receiver.send_reply(manual_msg, target_serial=args.sender_serial, use_rb_prefix=True, wait_sbd=20)
                else:
                    print("⚠️ Nội dung không được để trống!")
            elif choice == "5":
                auto_reply = not auto_reply
                print(f"🔄 Đã chuyển trạng thái Auto-ACK thành: {'BẬT' if auto_reply else 'TẮT'}")
            else:
                print("Lựa chọn không hợp lệ!")

    except KeyboardInterrupt:
        print("\n🛑 Đã nhận tín hiệu dừng từ bàn phím (Ctrl+C).")
    finally:
        receiver.disconnect()
        print(f"📄 Toàn bộ log đã được lưu tại: {log_file}")


if __name__ == "__main__":
    main()
