# -*- coding: utf-8 -*-
"""
🛰️ HỆ THỐNG TRUYỀN DỮ LIỆU QUA VỆ TINH IRIDIUM - ROCKBLOCK 9603
🖥️ GIAO DIỆN ĐIỀU KHIỂN TẬP TRUNG (SENDER & RECEIVER DUAL GUI)

Bao gồm:
  - Cấu hình cổng COM độc lập cho Bên Gửi (Sender) và Bên Nhận (Receiver).
  - Quét & tự động cập nhật danh sách cổng COM khả dụng.
  - Kiểm tra mức sóng vệ tinh Iridium (AT+CSQ) với thanh hiển thị trực quan.
  - Soạn thảo & gửi tin nhắn Direct Addressing (tiền tố RB<Serial>) qua vệ tinh.
  - Tự động nhận tin nhắn (Mailbox Check) và tự động phản hồi ACK ngược lại.
  - Chế độ Dual Monitor xem hoạt động song song cả 2 thiết bị cùng lúc.
  - Vận hành đa luồng (Multi-threading) không làm treo giao diện khi giao tiếp vệ tinh.
"""

import sys
import time
import threading
import re
from datetime import datetime
import tkinter as tk
from tkinter import ttk, messagebox, filedialog, scrolledtext
import serial
import serial.tools.list_ports

# Tối ưu encoding trên Windows
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass


# ==============================================================================
# CLASS CORE: GIAO TIẾP VỚI MODEM ROCKBLOCK 9603
# ==============================================================================
class RockBlockModem:
    """Lớp quản lý giao tiếp AT Command với modem RockBLOCK 9603 qua cổng Serial."""

    MO_STATUS_DESC = {
        0: "Thành công",
        1: "Thành công",
        2: "Thành công",
        32: "Thất bại: Không bắt được sóng vệ tinh Iridium (Timeout).",
        33: "Thất bại: Mất kết nối vô tuyến trong quá trình truyền.",
        34: "Thất bại: Mạng vệ tinh báo bận / nghẽn kênh.",
        35: "Thất bại: Modem Iridium bị khóa hoặc SIM chưa kích hoạt."
    }

    MT_STATUS_DESC = {
        0: "Không có tin nhắn MT nào chờ trong hộp thư Gateway.",
        1: "Thành công: Đã nhận được 1 tin nhắn MT từ Gateway về modem!",
        2: "Lỗi trong quá trình nhận tin nhắn MT từ Gateway."
    }

    @staticmethod
    def parse_sbdix(resp):
        """
        Phân tích kết quả +SBDIX từ modem:
        +SBDIX: <mo_status>, <momsn>, <mt_status>, <mtmsn>, <mt_len>, <mt_queued>
        Trả về tuple 6 số nguyên (mo_status, momsn, mt_status, mtmsn, mt_len, mt_queued) hoặc None nếu không hợp lệ.
        Dùng regex để tránh lỗi 'invalid literal for int' do dính chuỗi 'OK' và ký tự xuống dòng ở cuối.
        """
        match = re.search(r'\+SBDIX:\s*(-?\d+)\s*,\s*(-?\d+)\s*,\s*(-?\d+)\s*,\s*(-?\d+)\s*,\s*(-?\d+)\s*,\s*(-?\d+)', resp)
        if match:
            return tuple(map(int, match.groups()))
        # Fallback: tìm dòng chứa +SBDIX: và trích xuất tất cả các số nguyên
        for line in resp.splitlines():
            if "+SBDIX:" in line:
                nums = re.findall(r'-?\d+', line.split("+SBDIX:")[1])
                if len(nums) >= 6:
                    return tuple(map(int, nums[:6]))
        return None

    def __init__(self, name="RockBLOCK", log_callback=None):
        self.name = name
        self.log_callback = log_callback
        self.ser = None
        self.port = ""
        self.baudrate = 19200
        self.imei = "Chưa rõ"
        self.firmware = "Chưa rõ"
        self.is_connected = False
        self.last_bars = 0
        self.lock = threading.Lock()

    def log(self, msg):
        timestamp = datetime.now().strftime("%H:%M:%S")
        formatted = f"[{timestamp}] [{self.name}] {msg}"
        if self.log_callback:
            self.log_callback(formatted)
        else:
            print(formatted)

    def connect(self, port, baudrate=19200):
        with self.lock:
            try:
                self.port = port
                self.baudrate = baudrate
                self.ser = serial.Serial(
                    port=port,
                    baudrate=baudrate,
                    bytesize=serial.EIGHTBITS,
                    parity=serial.PARITY_NONE,
                    stopbits=serial.STOPBITS_ONE,
                    timeout=3
                )
                self.is_connected = True
                self.log(f"✅ Đã kết nối thành công tới {port} @ {baudrate} baud.")
                return True
            except Exception as e:
                self.is_connected = False
                self.log(f"❌ Không thể mở cổng {port}: {e}")
                return False

    def disconnect(self):
        with self.lock:
            if self.ser and self.ser.is_open:
                try:
                    self.ser.close()
                except Exception:
                    pass
            self.is_connected = False
            self.log("🔌 Đã ngắt kết nối cổng Serial.")

    def send_cmd(self, cmd, wait=1.5):
        """Gửi AT command và đọc phản hồi ổn định hơn, đặc biệt với AT+SBDIX."""
        if not self.ser or not self.ser.is_open:
            self.log("⚠️ Cổng Serial chưa mở!")
            return ""

        try:
            # Chỉ xóa dữ liệu cũ trước khi bắt đầu một command mới.
            self.ser.reset_input_buffer()
            self.ser.write((cmd + "\r").encode("ascii"))
            self.ser.flush()

            # SBDIX có thể mất nhiều giây để hoàn tất phiên vệ tinh.
            timeout = max(float(wait), 1.0)
            deadline = time.monotonic() + timeout
            chunks = []

            while time.monotonic() < deadline:
                waiting = self.ser.in_waiting
                if waiting:
                    data = self.ser.read(waiting)
                    if data:
                        chunks.append(data.decode("ascii", errors="ignore"))
                        current = "".join(chunks)

                        # Với command thông thường, OK/ERROR là dấu kết thúc.
                        # Với SBDIX, cần có +SBDIX và sau đó thường có OK.
                        if cmd.strip().upper() == "AT+SBDIX":
                            if "+SBDIX:" in current and ("\nOK" in current or current.rstrip().endswith("OK")):
                                break
                            if "ERROR" in current and "+SBDIX:" not in current:
                                break
                        elif "\nOK" in current or current.rstrip().endswith("OK") or "ERROR" in current:
                            break
                else:
                    time.sleep(0.05)

            resp = "".join(chunks).strip()
            return resp

        except Exception as e:
            self.log(f"❌ Lỗi gửi lệnh AT '{cmd}': {e}")
            return ""

    def init_modem(self):
        with self.lock:
            self.log("⚙️ Đang khởi tạo cấu hình modem...")
            resp = self.send_cmd("AT", wait=1.0)
            if "OK" not in resp:
                self.log(f"⚠️ Modem không phản hồi lệnh AT: '{resp}'")
                return False

            self.send_cmd("ATE0", wait=1.0)  # Tắt echo
            self.send_cmd("AT&K0", wait=1.0)  # Tắt flow control

            # Đọc IMEI
            imei_raw = self.send_cmd("AT+CGSN", wait=1.0)
            for line in imei_raw.splitlines():
                clean = line.strip()
                if clean.isdigit() and len(clean) >= 14:
                    self.imei = clean
                    break

            # Đọc Firmware
            fw_raw = self.send_cmd("AT+CGMR", wait=1.0)
            self.firmware = fw_raw.replace("OK", "").strip()

            self.log(f"📱 IMEI: {self.imei} | FW: {self.firmware}")
            self.log("✅ Modem đã sẵn sàng hoạt động!")
            return True

    def check_signal(self, quiet=False):
        if not self.is_connected:
            return 0
        # Không chặn nếu luồng khác đang bận truyền dữ liệu SBDIX
        acquired = self.lock.acquire(blocking=False)
        if not acquired:
            return self.last_bars
        try:
            resp = self.send_cmd("AT+CSQ", wait=1.5)
            if "+CSQ:" in resp:
                try:
                    bars_str = resp.split("+CSQ:")[1].strip().split()[0]
                    bars = int(bars_str)
                    bar_display = "■" * bars + "□" * (5 - bars)
                    old_bars = self.last_bars
                    self.last_bars = bars
                    # In log nếu đo thủ công hoặc khi mức sóng thay đổi
                    if not quiet or old_bars != bars:
                        self.log(f"📶 Sóng vệ tinh: [{bar_display}] ({bars}/5 vạch)")
                    return bars
                except Exception:
                    pass
            if not quiet:
                self.log(f"⚠️ Không đọc được mức sóng. Phản hồi: {resp}")
            return self.last_bars
        finally:
            self.lock.release()

    def send_sbd_message(self, message_text, target_serial="0235708", wait_sbd=30):
        """Gửi một MO message qua SBDIX. Giữ lock xuyên suốt phiên truyền."""
        with self.lock:
            if not self.is_connected or not self.ser or not self.ser.is_open:
                return False, "Modem chưa kết nối.", None

            if target_serial:
                try:
                    clean_target = f"{int(str(target_serial).strip()):07d}"
                except (TypeError, ValueError):
                    return False, f"Serial đích không hợp lệ: {target_serial}", None
                payload = f"RB{clean_target}{message_text}"
                prefix_info = f"RB{clean_target}"
            else:
                clean_target = "Default"
                payload = message_text
                prefix_info = "None"

            if len(payload.encode("utf-8")) > 340:
                return False, f"Payload quá dài: {len(payload.encode('utf-8'))} bytes (tối đa 340 bytes).", None

            self.log("=" * 50)
            self.log(f"📤 GỬI TIN QUA VỆ TINH ĐẾN: {clean_target}")
            self.log(f"🏷️  Tiền tố Direct: {prefix_info}")
            self.log(f"📝 Nội dung: \"{message_text}\"")
            self.log(f"📦 Payload (AT+SBDWT): \"{payload}\" ({len(payload)} bytes)")
            self.log("=" * 50)

            # 1. Xóa bộ đệm MO cũ
            self.send_cmd("AT+SBDD0", wait=1.0)

            # 2. Nạp nội dung
            write_resp = self.send_cmd(f"AT+SBDWT={payload}", wait=1.0)
            if "OK" not in write_resp:
                self.log(f"❌ Nạp bộ đệm MO thất bại: {write_resp}")
                return False, f"Nạp buffer thất bại: {write_resp}", None

            # 3. Kích hoạt phiên vệ tinh SBDIX
            self.log(f"🚀 Đang gọi AT+SBDIX (kết nối vệ tinh, chờ khoảng {wait_sbd}s)...")
            sbdix_resp = self.send_cmd("AT+SBDIX", wait=wait_sbd)
            self.log(f"📥 Phản hồi SBDIX:\n{sbdix_resp}")

            # 4. Phân tích kết quả
            sbdix_vals = self.parse_sbdix(sbdix_resp)
            if sbdix_vals:
                try:
                    mo_status, momsn, mt_status, mtmsn, mt_len, mt_queued = sbdix_vals

                    desc = self.MO_STATUS_DESC.get(mo_status, f"Mã trạng thái {mo_status}")
                    self.log(f"📊 MO Status = {mo_status}: {desc} (MOMSN: {momsn})")

                    mt_msg = None
                    if mt_status == 1:
                        self.log(f"📬 [Tin nhắn MT nhận về kèm theo: {mt_len} bytes]")
                        mt_msg = self.read_mt_buffer()
                        self.log(f"📩 Nội dung tin nhắn MT nhận về: \"{mt_msg}\"")

                    # Chỉ coi MO=0 là gửi thành công. Các mã khác phải được
                    # giữ nguyên để dễ chẩn đoán lỗi từ modem/network.
                    if mo_status == 0:
                        self.log(f"🎉 GỬI THÀNH CÔNG TỚI ROCKBLOCK {clean_target}!")
                        return True, f"Thành công! MOMSN: {momsn}", mt_msg
                    else:
                        self.log(f"⚠️ Gửi thất bại: MO={mo_status} ({desc})")
                        return False, f"Lỗi MO={mo_status}: {desc} | MOMSN={momsn}", mt_msg
                except Exception as e:
                    self.log(f"❌ Lỗi phân tích SBDIX: {e}")
                    return False, str(e), None
            else:
                self.log("❌ Không nhận được phản hồi +SBDIX từ modem.")
                return False, "Không có phản hồi +SBDIX", None

    def read_mt_buffer(self):
        """Đọc và xóa nội dung trong MT Buffer."""
        raw_msg = self.send_cmd("AT+SBDRT", wait=1.5)
        lines = [line.strip() for line in raw_msg.splitlines() if line.strip() and line.strip() != "OK"]
        cleaned_msg = "\n".join(lines) if lines else raw_msg
        self.send_cmd("AT+SBDD1", wait=1.0)
        return cleaned_msg

    def check_mailbox(self, wait_sbd=20):
        with self.lock:
            self.log(f"📡 Đang kiểm tra hộp thư vệ tinh (AT+SBDIX, chờ {wait_sbd}s)...")
            sbdix_resp = self.send_cmd("AT+SBDIX", wait=wait_sbd)

            sbdix_vals = self.parse_sbdix(sbdix_resp)
            if sbdix_vals:
                try:
                    mo_status, momsn, mt_status, mtmsn, mt_len, mt_queued = sbdix_vals

                    desc = self.MT_STATUS_DESC.get(mt_status, f"MT Status: {mt_status}")
                    self.log(f"📊 Kết quả MT Status = {mt_status}: {desc}")
                    self.log(f"🔢 MTMSN: {mtmsn} | Dài: {mt_len} bytes | Còn trong hàng đợi: {mt_queued}")

                    if mt_status == 1:
                        msg_text = self.read_mt_buffer()
                        self.log("=" * 50)
                        self.log(f"📩 NHẬN ĐƯỢC TIN NHẮN MỚI:")
                        self.log(f"👉 \"{msg_text}\"")
                        self.log("=" * 50)
                        return True, msg_text, mt_queued
                    else:
                        self.log("📭 Hộp thư trống, không có tin mới.")
                        return False, None, mt_queued
                except Exception as e:
                    self.log(f"❌ Lỗi giải mã phản hồi SBDIX: {e}")
                    return False, None, 0
            else:
                self.log("⚠️ Không nhận được phản hồi +SBDIX từ modem.")
                return False, None, 0


# ==============================================================================
# GIAO DIỆN CHÍNH (TKINTER GUI APPLICATION)
# ==============================================================================
class RockBlockDualApp:
    def __init__(self, root):
        self.root = root
        self.root.title("🛰️ Trạm Quản Lý Vệ Tinh RockBLOCK 9603 Iridium - Dual Sender & Receiver")
        self.root.geometry("1180x820")
        self.root.minsize(980, 700)

        # Cài đặt giao diện ttk
        self.style = ttk.Style()
        try:
            self.style.theme_use("clam")
        except Exception:
            pass

        self._configure_styles()

        # Tạo 2 đối tượng Modem cho Sender và Receiver
        self.sender_modem = RockBlockModem(name="SENDER", log_callback=self._log_sender)
        self.receiver_modem = RockBlockModem(name="RECEIVER", log_callback=self._log_receiver)

        # Biến trạng thái lắng nghe liên tục cho Receiver
        self.is_listening = False
        self.listen_thread = None

        # Bộ đếm thống kê tin nhắn gửi và nhận thành công
        self.stats = {
            "sender_sent": 0,    # Sender gửi MO thành công
            "sender_recv": 0,    # Sender nhận MT / ACK thành công
            "receiver_recv": 0,  # Receiver nhận MT thành công
            "receiver_sent": 0,  # Receiver gửi ACK thành công
        }

        # Xây dựng giao diện
        self._build_header()
        self._build_tabs()
        self._build_statusbar()

        # Quét cổng COM khi mở app
        self._refresh_all_ports()

        # Bắt sự kiện đóng cửa sổ
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _configure_styles(self):
        # Thiết lập màu sắc và font chữ hiện đại
        self.root.configure(bg="#f4f6f9")
        self.style.configure(".", font=("Segoe UI", 9))
        self.style.configure("TLabel", background="#f4f6f9", foreground="#2c3e50")
        self.style.configure("Header.TLabel", font=("Segoe UI", 13, "bold"), foreground="#1a365d")
        self.style.configure("SubHeader.TLabel", font=("Segoe UI", 9), foreground="#4a5568")

        # Nút bấm
        self.style.configure("TButton", font=("Segoe UI", 9), padding=5)
        self.style.configure("Primary.TButton", font=("Segoe UI", 9, "bold"), foreground="#ffffff", background="#2b6cb0")
        self.style.map("Primary.TButton", background=[("active", "#2c5282")])

        self.style.configure("Success.TButton", font=("Segoe UI", 9, "bold"), foreground="#ffffff", background="#2f855a")
        self.style.map("Success.TButton", background=[("active", "#276749")])

        self.style.configure("Danger.TButton", font=("Segoe UI", 9, "bold"), foreground="#ffffff", background="#c53030")
        self.style.map("Danger.TButton", background=[("active", "#9b2c2c")])

        # GroupBox / LabelFrame
        self.style.configure("TLabelframe", background="#ffffff", relief="groove")
        self.style.configure("TLabelframe.Label", font=("Segoe UI", 10, "bold"), foreground="#2b6cb0", background="#ffffff")

    def _build_header(self):
        header_frame = tk.Frame(self.root, bg="#1a365d", height=70)
        header_frame.pack(fill="x", side="top")
        header_frame.pack_propagate(False)

        # Cột trái: Tiêu đề
        left_h = tk.Frame(header_frame, bg="#1a365d")
        left_h.pack(side="left", padx=16, pady=5)

        title_lbl = tk.Label(
            left_h,
            text="🛰️ HỆ THỐNG TRUYỀN THÔNG VỆ TINH IRIDIUM - ROCKBLOCK 9603",
            font=("Segoe UI", 12, "bold"),
            fg="#ffffff",
            bg="#1a365d"
        )
        title_lbl.pack(anchor="w")

        sub_lbl = tk.Label(
            left_h,
            text="Điều khiển hai chiều Direct Addressing (RB-to-RB) giữa Module Phát (0235707) & Module Thu (0235708)",
            font=("Segoe UI", 8),
            fg="#cbd5e0",
            bg="#1a365d"
        )
        sub_lbl.pack(anchor="w", pady=(1, 0))

        # Cột phải: Thanh Card Thống Kê Tổng Hợp (Header Stats Dashboard)
        stats_h = tk.Frame(header_frame, bg="#1a365d")
        stats_h.pack(side="right", padx=16, pady=6)

        # Thẻ 1: Tổng Gửi Thành Công
        card_sent = tk.Frame(stats_h, bg="#2b6cb0", padx=12, pady=3, relief="groove", bd=1)
        card_sent.pack(side="left", padx=4)

        tk.Label(card_sent, text="📤 TỔNG GỬI TC", font=("Segoe UI", 7, "bold"), fg="#e2e8f0", bg="#2b6cb0").pack()
        self.header_sent_val_lbl = tk.Label(card_sent, text="0", font=("Segoe UI", 13, "bold"), fg="#ffffff", bg="#2b6cb0")
        self.header_sent_val_lbl.pack()

        # Thẻ 2: Tổng Nhận Thành Công
        card_recv = tk.Frame(stats_h, bg="#276749", padx=12, pady=3, relief="groove", bd=1)
        card_recv.pack(side="left", padx=4)

        tk.Label(card_recv, text="📥 TỔNG NHẬN TC", font=("Segoe UI", 7, "bold"), fg="#e2e8f0", bg="#276749").pack()
        self.header_recv_val_lbl = tk.Label(card_recv, text="0", font=("Segoe UI", 13, "bold"), fg="#ffffff", bg="#276749")
        self.header_recv_val_lbl.pack()

        # Nút Đặt Lại Thống Kê
        btn_reset_stats = tk.Button(
            stats_h,
            text="🔄 Đặt Lại",
            font=("Segoe UI", 8, "bold"),
            bg="#2d3748",
            fg="#e2e8f0",
            activebackground="#4a5568",
            activeforeground="#ffffff",
            bd=0,
            padx=6,
            pady=8,
            cursor="hand2",
            command=self._reset_stats
        )
        btn_reset_stats.pack(side="left", padx=(4, 0))

    def _build_tabs(self):
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill="both", expand=True, padx=12, pady=10)

        # Tab 1: Module Gửi (Sender)
        self.tab_sender = ttk.Frame(self.notebook)
        self.notebook.add(self.tab_sender, text="  🛰️ Module Gửi (Sender - 0235707)  ")
        self._build_sender_tab(self.tab_sender)

        # Tab 2: Module Nhận (Receiver)
        self.tab_receiver = ttk.Frame(self.notebook)
        self.notebook.add(self.tab_receiver, text="  📥 Module Nhận (Receiver - 0235708)  ")
        self._build_receiver_tab(self.tab_receiver)

        # Tab 3: Giám sát Song song (Dual Monitor)
        self.tab_dual = ttk.Frame(self.notebook)
        self.notebook.add(self.tab_dual, text="  ⚡ Giám Sát Song Song (Dual Monitor)  ")
        self._build_dual_tab(self.tab_dual)

    # --------------------------------------------------------------------------
    # TAB 1: SENDER UI
    # --------------------------------------------------------------------------
    def _build_sender_tab(self, parent):
        top_frame = tk.Frame(parent, bg="#f4f6f9")
        top_frame.pack(fill="x", padx=10, pady=8)

        # Khung Cấu hình Cổng COM Sender
        cfg_box = ttk.LabelFrame(top_frame, text="⚙️ CẤU HÌNH CỔNG COM - ROCKBLOCK SENDER", padding=10)
        cfg_box.pack(fill="x")

        r1 = tk.Frame(cfg_box, bg="#ffffff")
        r1.pack(fill="x", pady=4)

        tk.Label(r1, text="Cổng COM:", font=("Segoe UI", 9, "bold"), bg="#ffffff").pack(side="left", padx=5)
        self.sender_port_cb = ttk.Combobox(r1, width=12, state="readonly")
        self.sender_port_cb.pack(side="left", padx=5)

        btn_refresh = ttk.Button(r1, text="🔄 Quét Cổng", command=self._refresh_all_ports)
        btn_refresh.pack(side="left", padx=5)

        tk.Label(r1, text="Baudrate:", font=("Segoe UI", 9, "bold"), bg="#ffffff").pack(side="left", padx=(15, 5))
        self.sender_baud_cb = ttk.Combobox(r1, values=["9600", "19200", "38400", "57600", "115200"], width=8, state="readonly")
        self.sender_baud_cb.set("19200")
        self.sender_baud_cb.pack(side="left", padx=5)

        tk.Label(r1, text="Serial Sender:", font=("Segoe UI", 9, "bold"), bg="#ffffff").pack(side="left", padx=(15, 5))
        self.sender_serial_entry = ttk.Entry(r1, width=10)
        self.sender_serial_entry.insert(0, "0235707")
        self.sender_serial_entry.pack(side="left", padx=5)

        tk.Label(r1, text="Đích Đến (Target):", font=("Segoe UI", 9, "bold"), bg="#ffffff").pack(side="left", padx=(15, 5))
        self.sender_target_entry = ttk.Entry(r1, width=10)
        self.sender_target_entry.insert(0, "0235708")
        self.sender_target_entry.pack(side="left", padx=5)

        self.btn_sender_connect = ttk.Button(r1, text="🔌 Kết Nối", style="Primary.TButton", command=self._toggle_sender_connection)
        self.btn_sender_connect.pack(side="right", padx=5)

        # Dòng trạng thái modem Sender
        r2 = tk.Frame(cfg_box, bg="#ffffff")
        r2.pack(fill="x", pady=(6, 0))

        self.sender_status_lbl = tk.Label(r2, text="🔴 Chưa kết nối", font=("Segoe UI", 9, "bold"), fg="#e53e3e", bg="#ffffff")
        self.sender_status_lbl.pack(side="left", padx=5)

        self.sender_imei_lbl = tk.Label(r2, text="IMEI: Chưa rõ", font=("Segoe UI", 9), fg="#4a5568", bg="#ffffff")
        self.sender_imei_lbl.pack(side="left", padx=15)

        btn_sender_csq = ttk.Button(r2, text="📶 Đo Sóng CSQ", command=self._check_sender_signal)
        btn_sender_csq.pack(side="left", padx=10)

        self.sender_csq_lbl = tk.Label(r2, text="Sóng: [□□□□□] (0/5)", font=("Consolas", 5, "bold"), fg="#2b6cb0", bg="#ffffff")
        self.sender_csq_lbl.pack(side="left", padx=5)

        # Tự động đo sóng định kỳ cho Sender
        self.sender_auto_csq_var = tk.BooleanVar(value=True)
        cb_sender_auto_csq = ttk.Checkbutton(
            r2,
            text="Tự động đo mỗi:",
            variable=self.sender_auto_csq_var,
            command=self._on_sender_auto_csq_toggle
        )
        cb_sender_auto_csq.pack(side="left", padx=(12, 2))

        self.sender_csq_interval_cb = ttk.Combobox(r2, values=["5", "10", "15", "30", "60"], width=4, state="readonly")
        self.sender_csq_interval_cb.set("10")
        self.sender_csq_interval_cb.pack(side="left", padx=2)
        tk.Label(r2, text="giây", font=("Segoe UI", 8), bg="#ffffff").pack(side="left")

        self.sender_last_csq_time = tk.Label(r2, text="", font=("Segoe UI", 8, "italic"), fg="#718096", bg="#ffffff")
        self.sender_last_csq_time.pack(side="left", padx=6)

        # Dòng thống kê Sender
        r3 = tk.Frame(cfg_box, bg="#ebf8ff", padx=8, pady=4, relief="groove", bd=1)
        r3.pack(fill="x", pady=(6, 0))

        self.sender_stats_lbl = tk.Label(
            r3,
            text="📊 Thống kê Sender:   📤 Gửi thành công: 0 tin   |   📥 Nhận về thành công: 0 tin",
            font=("Segoe UI", 9, "bold"),
            fg="#2b6cb0",
            bg="#ebf8ff"
        )
        self.sender_stats_lbl.pack(side="left")

        # Khung Soạn Thảo & Gửi Tin
        mid_frame = tk.Frame(parent, bg="#f4f6f9")
        mid_frame.pack(fill="x", padx=10, pady=5)

        send_box = ttk.LabelFrame(mid_frame, text="✍️ SOẠN THẢO & GỬI THÔNG ĐIỆP QUA VỆ TINH", padding=10)
        send_box.pack(fill="x")

        sr1 = tk.Frame(send_box, bg="#ffffff")
        sr1.pack(fill="x", pady=4)
        tk.Label(sr1, text="Nội dung gửi:", font=("Segoe UI", 9, "bold"), bg="#ffffff").pack(side="left", padx=5)

        self.sender_msg_entry = ttk.Entry(sr1)
        self.sender_msg_entry.insert(0, "Hello A")
        self.sender_msg_entry.pack(side="left", fill="x", expand=True, padx=5)
        self.sender_msg_entry.bind("<KeyRelease>", self._update_sender_payload_preview)

        # Nút gửi chính
        self.btn_sender_send = ttk.Button(
            sr1,
            text="🚀 GỬI QUA VỆ TINH (AT+SBDIX)",
            style="Success.TButton",
            command=self._send_sender_message
        )
        self.btn_sender_send.pack(side="right", padx=5)

        # Xem trước chuỗi Direct Addressing
        sr2 = tk.Frame(send_box, bg="#ffffff")
        sr2.pack(fill="x", pady=4)

        self.sender_preview_lbl = tk.Label(
            sr2,
            text="📦 Xem trước lệnh: AT+SBDWT=RB0235708Hello A (16 bytes)",
            font=("Consolas", 9),
            fg="#2d3748",
            bg="#edf2f7",
            padx=8,
            pady=4,
            relief="groove"
        )
        self.sender_preview_lbl.pack(side="left", fill="x", expand=True, padx=5)

        # Các phím tắt mẫu tin nhắn
        sr3 = tk.Frame(send_box, bg="#ffffff")
        sr3.pack(fill="x", pady=(4, 0))

        tk.Label(sr3, text="Mẫu tin nhanh:", font=("Segoe UI", 8, "italic"), bg="#ffffff").pack(side="left", padx=5)
        ttk.Button(sr3, text="👋 Hello A", command=lambda: self._set_msg_text("Hello A")).pack(side="left", padx=3)
        ttk.Button(sr3, text="💬 Ping Timestamp", command=self._set_sample_ping).pack(side="left", padx=3)
        ttk.Button(sr3, text="📍 GPS Telemetry", command=self._set_sample_gps).pack(side="left", padx=3)
        ttk.Button(sr3, text="🚨 Cảnh Báo SOS", command=self._set_sample_sos).pack(side="left", padx=3)

        btn_check_ack = ttk.Button(sr3, text="🔍 Kiểm Tra ACK Từ Receiver", command=self._check_sender_ack)
        btn_check_ack.pack(side="right", padx=5)

        # Khung Hộp Thư Nhận Về & Phản Hồi (Sender Inbox)
        inbox_box = ttk.LabelFrame(parent, text="📬 TIN NHẮN GỬI VỀ / PHẢN HỒI (INBOX SENDER)", padding=8)
        inbox_box.pack(fill="both", expand=True, padx=10, pady=5)

        cols = ("time", "source", "content", "length", "status")
        self.sender_inbox_tree = ttk.Treeview(inbox_box, columns=cols, show="headings", height=5)
        self.sender_inbox_tree.heading("time", text="Thời Gian")
        self.sender_inbox_tree.heading("source", text="Phân Loại")
        self.sender_inbox_tree.heading("content", text="Nội Dung Tin Nhắn Nhận Về")
        self.sender_inbox_tree.heading("length", text="Độ Dài")
        self.sender_inbox_tree.heading("status", text="Trạng Thái")

        self.sender_inbox_tree.column("time", width=130, anchor="center")
        self.sender_inbox_tree.column("source", width=140, anchor="center")
        self.sender_inbox_tree.column("content", width=500, anchor="w")
        self.sender_inbox_tree.column("length", width=70, anchor="center")
        self.sender_inbox_tree.column("status", width=100, anchor="center")

        sender_tree_scroll = ttk.Scrollbar(inbox_box, orient="vertical", command=self.sender_inbox_tree.yview)
        self.sender_inbox_tree.configure(yscrollcommand=sender_tree_scroll.set)

        self.sender_inbox_tree.pack(side="left", fill="both", expand=True)
        sender_tree_scroll.pack(side="right", fill="y")
        self.sender_inbox_tree.bind("<Double-1>", lambda event: self._show_tree_detail(self.sender_inbox_tree, "Chi Tiết Tin Nhắn Gửi Về Sender"))

        # Thanh công cụ bên dưới bảng Inbox Sender
        inbox_btn_frame = tk.Frame(inbox_box, bg="#ffffff")
        inbox_btn_frame.pack(fill="x", pady=(4, 0))

        btn_check_mailbox = ttk.Button(
            inbox_btn_frame,
            text="📬 Kiểm Tra Hộp Thư (Check Mailbox)",
            style="Primary.TButton",
            command=self._check_sender_ack
        )
        btn_check_mailbox.pack(side="left", padx=5)

        self.sender_inbox_count_lbl = tk.Label(
            inbox_btn_frame,
            text="Tổng nhận: 0 tin nhắn",
            font=("Segoe UI", 9, "italic"),
            fg="#4a5568",
            bg="#ffffff"
        )
        self.sender_inbox_count_lbl.pack(side="left", padx=10)

        ttk.Button(inbox_btn_frame, text="🗑️ Xóa Hộp Thư", command=self._clear_sender_inbox).pack(side="right", padx=5)
        ttk.Button(inbox_btn_frame, text="💾 Xuất Tin Nhắn", command=lambda: self._export_tree_data(self.sender_inbox_tree, "sender_inbox")).pack(side="right", padx=5)

        # Khung Nhật Ký (Log Console) Sender
        log_box = ttk.LabelFrame(parent, text="📜 NHẬT KÝ HOẠT ĐỘNG SENDER", padding=8)
        log_box.pack(fill="both", expand=True, padx=10, pady=(5, 10))

        self.sender_log_text = scrolledtext.ScrolledText(
            log_box,
            font=("Consolas", 9),
            bg="#1a202c",
            fg="#e2e8f0",
            insertbackground="#ffffff",
            height=8
        )
        self.sender_log_text.pack(fill="both", expand=True)

        log_btn_frame = tk.Frame(log_box, bg="#ffffff")
        log_btn_frame.pack(fill="x", pady=(4, 0))
        ttk.Button(log_btn_frame, text="🗑️ Xóa Log", command=lambda: self.sender_log_text.delete("1.0", tk.END)).pack(side="right", padx=5)
        ttk.Button(log_btn_frame, text="💾 Lưu Log Thành File", command=lambda: self._save_log_file("sender")).pack(side="right", padx=5)

    # --------------------------------------------------------------------------
    # TAB 2: RECEIVER UI
    # --------------------------------------------------------------------------
    def _build_receiver_tab(self, parent):
        top_frame = tk.Frame(parent, bg="#f4f6f9")
        top_frame.pack(fill="x", padx=10, pady=8)

        # Khung Cấu hình Cổng COM Receiver
        cfg_box = ttk.LabelFrame(top_frame, text="⚙️ CẤU HÌNH CỔNG COM - ROCKBLOCK RECEIVER", padding=10)
        cfg_box.pack(fill="x")

        r1 = tk.Frame(cfg_box, bg="#ffffff")
        r1.pack(fill="x", pady=4)

        tk.Label(r1, text="Cổng COM:", font=("Segoe UI", 9, "bold"), bg="#ffffff").pack(side="left", padx=5)
        self.recv_port_cb = ttk.Combobox(r1, width=12, state="readonly")
        self.recv_port_cb.pack(side="left", padx=5)

        btn_refresh = ttk.Button(r1, text="🔄 Quét Cổng", command=self._refresh_all_ports)
        btn_refresh.pack(side="left", padx=5)

        tk.Label(r1, text="Baudrate:", font=("Segoe UI", 9, "bold"), bg="#ffffff").pack(side="left", padx=(15, 5))
        self.recv_baud_cb = ttk.Combobox(r1, values=["9600", "19200", "38400", "57600", "115200"], width=8, state="readonly")
        self.recv_baud_cb.set("19200")
        self.recv_baud_cb.pack(side="left", padx=5)

        tk.Label(r1, text="Serial Receiver:", font=("Segoe UI", 9, "bold"), bg="#ffffff").pack(side="left", padx=(15, 5))
        self.recv_serial_entry = ttk.Entry(r1, width=10)
        self.recv_serial_entry.insert(0, "0235708")
        self.recv_serial_entry.pack(side="left", padx=5)

        tk.Label(r1, text="Phản Hồi Về (Sender):", font=("Segoe UI", 9, "bold"), bg="#ffffff").pack(side="left", padx=(15, 5))
        self.recv_sender_target_entry = ttk.Entry(r1, width=10)
        self.recv_sender_target_entry.insert(0, "0235707")
        self.recv_sender_target_entry.pack(side="left", padx=5)

        self.btn_recv_connect = ttk.Button(r1, text="🔌 Kết Nối", style="Primary.TButton", command=self._toggle_receiver_connection)
        self.btn_recv_connect.pack(side="right", padx=5)

        # Dòng trạng thái modem Receiver
        r2 = tk.Frame(cfg_box, bg="#ffffff")
        r2.pack(fill="x", pady=(6, 0))

        self.recv_status_lbl = tk.Label(r2, text="🔴 Chưa kết nối", font=("Segoe UI", 9, "bold"), fg="#e53e3e", bg="#ffffff")
        self.recv_status_lbl.pack(side="left", padx=5)

        self.recv_imei_lbl = tk.Label(r2, text="IMEI: Chưa rõ", font=("Segoe UI", 9), fg="#4a5568", bg="#ffffff")
        self.recv_imei_lbl.pack(side="left", padx=15)

        btn_recv_csq = ttk.Button(r2, text="📶 Đo Sóng CSQ", command=self._check_receiver_signal)
        btn_recv_csq.pack(side="left", padx=10)

        self.recv_csq_lbl = tk.Label(r2, text="Sóng: [□□□□□] (0/5)", font=("Consolas", 5, "bold"), fg="#2b6cb0", bg="#ffffff")
        self.recv_csq_lbl.pack(side="left", padx=5)

        # Tự động đo sóng định kỳ cho Receiver
        self.recv_auto_csq_var = tk.BooleanVar(value=True)
        cb_recv_auto_csq = ttk.Checkbutton(
            r2,
            text="Tự động đo mỗi:",
            variable=self.recv_auto_csq_var,
            command=self._on_recv_auto_csq_toggle
        )
        cb_recv_auto_csq.pack(side="left", padx=(12, 2))

        self.recv_csq_interval_cb = ttk.Combobox(r2, values=["5", "10", "15", "30", "60"], width=4, state="readonly")
        self.recv_csq_interval_cb.set("10")
        self.recv_csq_interval_cb.pack(side="left", padx=2)
        tk.Label(r2, text="giây", font=("Segoe UI", 8), bg="#ffffff").pack(side="left")

        self.recv_last_csq_time = tk.Label(r2, text="", font=("Segoe UI", 8, "italic"), fg="#718096", bg="#ffffff")
        self.recv_last_csq_time.pack(side="left", padx=6)

        # Dòng thống kê Receiver
        r3 = tk.Frame(cfg_box, bg="#f0fff4", padx=8, pady=4, relief="groove", bd=1)
        r3.pack(fill="x", pady=(6, 0))

        self.receiver_stats_lbl = tk.Label(
            r3,
            text="📊 Thống kê Receiver:   📥 Nhận thành công: 0 tin   |   📤 Phản hồi ACK thành công: 0 tin",
            font=("Segoe UI", 9, "bold"),
            fg="#276749",
            bg="#f0fff4"
        )
        self.receiver_stats_lbl.pack(side="left")

        # Khung Điều khiển Nhận & Tự Động Phản Hồi (Auto-ACK)
        ctrl_box = ttk.LabelFrame(parent, text="🎧 ĐIỀU KHIỂN NHẬN TIN & TỰ ĐỘNG PHẢN HỒI (AUTO-ACK)", padding=10)
        ctrl_box.pack(fill="x", padx=10, pady=5)

        cr1 = tk.Frame(ctrl_box, bg="#ffffff")
        cr1.pack(fill="x", pady=4)

        # Checkbox Auto-ACK
        self.recv_auto_ack_var = tk.BooleanVar(value=True)
        cb_auto_ack = ttk.Checkbutton(
            cr1,
            text="Tự động gửi bản tin phản hồi (Auto-ACK) khi nhận tin mới",
            variable=self.recv_auto_ack_var
        )
        cb_auto_ack.pack(side="left", padx=5)

        tk.Label(cr1, text="Nội dung Auto-Reply:", font=("Segoe UI", 9, "bold"), bg="#ffffff").pack(side="left", padx=(15, 5))
        self.recv_ack_prefix_entry = ttk.Entry(cr1, width=10)
        self.recv_ack_prefix_entry.insert(0, "Hi B")
        self.recv_ack_prefix_entry.pack(side="left", padx=5)

        tk.Label(cr1, text="Chu kỳ thăm dò:", font=("Segoe UI", 9, "bold"), bg="#ffffff").pack(side="left", padx=(15, 5))
        self.recv_interval_cb = ttk.Combobox(cr1, values=["15", "30", "45", "60", "90"], width=5, state="readonly")
        self.recv_interval_cb.set("30")
        self.recv_interval_cb.pack(side="left", padx=5)
        tk.Label(cr1, text="giây", bg="#ffffff").pack(side="left")

        # Nút chức năng nhận
        cr2 = tk.Frame(ctrl_box, bg="#ffffff")
        cr2.pack(fill="x", pady=(6, 0))

        btn_oneshot = ttk.Button(
            cr2,
            text="📬 Kiểm Tra Hộp Thư 1 Lần (One-shot)",
            style="Primary.TButton",
            command=self._check_receiver_mailbox_once
        )
        btn_oneshot.pack(side="left", padx=5)

        self.btn_listen_toggle = ttk.Button(
            cr2,
            text="🎧 BẮT ĐẦU LẮNG NGHE ĐỊNH KỲ",
            style="Success.TButton",
            command=self._toggle_listen_loop
        )
        self.btn_listen_toggle.pack(side="left", padx=10)

        # Gửi phản hồi thủ công
        btn_manual_reply = ttk.Button(
            cr2,
            text="✉️ Gửi Phản Hồi Thủ Công",
            command=self._send_manual_reply
        )
        btn_manual_reply.pack(side="right", padx=5)

        self.recv_manual_reply_entry = ttk.Entry(cr2, width=28)
        self.recv_manual_reply_entry.insert(0, "Hi B")
        self.recv_manual_reply_entry.pack(side="right", padx=5)
        tk.Label(cr2, text="Nội dung gửi lại:", font=("Segoe UI", 9), bg="#ffffff").pack(side="right", padx=5)

        # Bảng hiển thị danh sách tin nhắn nhận được
        inbox_box = ttk.LabelFrame(parent, text="📬 HỘP THƯ NHẬN (INBOX)", padding=8)
        inbox_box.pack(fill="both", expand=True, padx=10, pady=5)

        cols = ("time", "content", "length", "ack_status")
        self.inbox_tree = ttk.Treeview(inbox_box, columns=cols, show="headings", height=5)
        self.inbox_tree.heading("time", text="Thời Gian")
        self.inbox_tree.heading("content", text="Nội Dung Tin Nhắn")
        self.inbox_tree.heading("length", text="Độ Dài")
        self.inbox_tree.heading("ack_status", text="Trạng Thái Phản Hồi ACK")

        self.inbox_tree.column("time", width=120, anchor="center")
        self.inbox_tree.column("content", width=550, anchor="w")
        self.inbox_tree.column("length", width=80, anchor="center")
        self.inbox_tree.column("ack_status", width=220, anchor="w")

        tree_scroll = ttk.Scrollbar(inbox_box, orient="vertical", command=self.inbox_tree.yview)
        self.inbox_tree.configure(yscrollcommand=tree_scroll.set)

        self.inbox_tree.pack(side="left", fill="both", expand=True)
        tree_scroll.pack(side="right", fill="y")
        self.inbox_tree.bind("<Double-1>", lambda event: self._show_tree_detail(self.inbox_tree, "Chi Tiết Tin Nhắn Nhận Được (Receiver)"))

        # Thanh công cụ bên dưới bảng Inbox Receiver
        recv_inbox_btn_frame = tk.Frame(inbox_box, bg="#ffffff")
        recv_inbox_btn_frame.pack(fill="x", pady=(4, 0))
        ttk.Button(recv_inbox_btn_frame, text="🗑️ Xóa Hộp Thư", command=lambda: [self.inbox_tree.delete(i) for i in self.inbox_tree.get_children()]).pack(side="right", padx=5)
        ttk.Button(recv_inbox_btn_frame, text="💾 Xuất Tin Nhắn", command=lambda: self._export_tree_data(self.inbox_tree, "receiver_inbox")).pack(side="right", padx=5)

        # Khung Log Receiver
        log_box = ttk.LabelFrame(parent, text="📜 NHẬT KÝ HOẠT ĐỘNG RECEIVER", padding=8)
        log_box.pack(fill="both", expand=True, padx=10, pady=(5, 10))

        self.recv_log_text = scrolledtext.ScrolledText(
            log_box,
            font=("Consolas", 9),
            bg="#1a202c",
            fg="#e2e8f0",
            insertbackground="#ffffff",
            height=8
        )
        self.recv_log_text.pack(fill="both", expand=True)

        log_btn_frame = tk.Frame(log_box, bg="#ffffff")
        log_btn_frame.pack(fill="x", pady=(4, 0))
        ttk.Button(log_btn_frame, text="🗑️ Xóa Log", command=lambda: self.recv_log_text.delete("1.0", tk.END)).pack(side="right", padx=5)
        ttk.Button(log_btn_frame, text="💾 Lưu Log Thành File", command=lambda: self._save_log_file("receiver")).pack(side="right", padx=5)

    # --------------------------------------------------------------------------
    # TAB 3: DUAL MONITOR (GIÁM SÁT SONG SONG)
    # --------------------------------------------------------------------------
    def _build_dual_tab(self, parent):
        pane = tk.PanedWindow(parent, orient="horizontal", bg="#cbd5e0", sashrelief="raised", sashwidth=6)
        pane.pack(fill="both", expand=True, padx=8, pady=8)

        # Cột trái: SENDER MONITOR
        left_frame = tk.Frame(pane, bg="#ffffff")
        pane.add(left_frame, minsize=400)

        tk.Label(
            left_frame,
            text="🛰️ SENDER MONITOR (0235707)",
            font=("Segoe UI", 11, "bold"),
            bg="#2b6cb0",
            fg="#ffffff",
            pady=6
        ).pack(fill="x")

        self.dual_sender_info = tk.Label(
            left_frame,
            text="Cổng: Chưa kết nối | Sóng: 0/5 | Trạng thái: Idle",
            font=("Segoe UI", 9, "bold"),
            bg="#ebf8ff",
            fg="#2c5282",
            pady=4
        )
        self.dual_sender_info.pack(fill="x")

        self.dual_sender_log = scrolledtext.ScrolledText(
            left_frame,
            font=("Consolas", 8),
            bg="#171923",
            fg="#63b3ed"
        )
        self.dual_sender_log.pack(fill="both", expand=True, padx=4, pady=4)

        # Cột phải: RECEIVER MONITOR
        right_frame = tk.Frame(pane, bg="#ffffff")
        pane.add(right_frame, minsize=400)

        tk.Label(
            right_frame,
            text="📥 RECEIVER MONITOR (0235708)",
            font=("Segoe UI", 11, "bold"),
            bg="#2f855a",
            fg="#ffffff",
            pady=6
        ).pack(fill="x")

        self.dual_recv_info = tk.Label(
            right_frame,
            text="Cổng: Chưa kết nối | Sóng: 0/5 | Trực nhận: Tắt",
            font=("Segoe UI", 9, "bold"),
            bg="#f0fff4",
            fg="#22543d",
            pady=4
        )
        self.dual_recv_info.pack(fill="x")

        self.dual_recv_log = scrolledtext.ScrolledText(
            right_frame,
            font=("Consolas", 8),
            bg="#171923",
            fg="#68d391"
        )
        self.dual_recv_log.pack(fill="both", expand=True, padx=4, pady=4)

    def _build_statusbar(self):
        self.statusbar = tk.Label(
            self.root,
            text="Sẵn sàng. Vui lòng chọn cổng COM và bấm Kết Nối cho module tương ứng.",
            bd=1,
            relief="sunken",
            anchor="w",
            font=("Segoe UI", 8),
            bg="#edf2f7",
            fg="#4a5568",
            padx=10,
            pady=3
        )
        self.statusbar.pack(side="bottom", fill="x")

    def _set_status(self, text):
        self.statusbar.config(text=f"[{datetime.now().strftime('%H:%M:%S')}] {text}")

    # --------------------------------------------------------------------------
    # QUẢN LÝ THỐNG KÊ (MESSAGE STATISTICS)
    # --------------------------------------------------------------------------
    def _record_stat(self, stat_name):
        """Ghi nhận thêm 1 sự kiện gửi hoặc nhận thành công và cập nhật UI."""
        if stat_name in self.stats:
            self.stats[stat_name] += 1
            self.root.after(0, self._update_stats_ui)

    def _reset_stats(self):
        """Đặt lại bộ đếm thống kê về 0 sau khi người dùng xác nhận."""
        if messagebox.askyesno("Xác nhận Đặt Lại", "Bạn có chắc muốn đặt lại toàn bộ số liệu thống kê tin nhắn về 0?"):
            for k in self.stats:
                self.stats[k] = 0
            self._update_stats_ui()
            self._set_status("Đã đặt lại tất cả bộ đếm thống kê tin nhắn về 0.")

    def _update_stats_ui(self):
        """Cập nhật các số liệu thống kê trên Header, Tab Sender, Tab Receiver và Dual Monitor."""
        s_sent = self.stats["sender_sent"]
        s_recv = self.stats["sender_recv"]
        r_recv = self.stats["receiver_recv"]
        r_sent = self.stats["receiver_sent"]
        total_sent = s_sent + r_sent
        total_recv = s_recv + r_recv

        # 1. Cập nhật thẻ Header
        if hasattr(self, "header_sent_val_lbl"):
            self.header_sent_val_lbl.config(text=str(total_sent))
        if hasattr(self, "header_recv_val_lbl"):
            self.header_recv_val_lbl.config(text=str(total_recv))

        # 2. Cập nhật Tab Sender
        if hasattr(self, "sender_stats_lbl"):
            self.sender_stats_lbl.config(
                text=f"📊 Thống kê Sender:   📤 Gửi thành công: {s_sent} tin   |   📥 Nhận về thành công: {s_recv} tin"
            )

        # 3. Cập nhật Tab Receiver
        if hasattr(self, "receiver_stats_lbl"):
            self.receiver_stats_lbl.config(
                text=f"📊 Thống kê Receiver:   📥 Nhận thành công: {r_recv} tin   |   📤 Phản hồi ACK thành công: {r_sent} tin"
            )

        # 4. Cập nhật Dual Monitor nếu các modem đang kết nối
        if hasattr(self, "dual_sender_info") and self.sender_modem.is_connected:
            self.dual_sender_info.config(
                text=f"Cổng: {self.sender_modem.port} | Sóng: {self.sender_modem.last_bars}/5 | Gửi TC: {s_sent} | Nhận TC: {s_recv}"
            )
        if hasattr(self, "dual_recv_info") and self.receiver_modem.is_connected:
            listen_state = f"BẬT ({self.recv_interval_cb.get()}s)" if self.is_listening else "TẮT"
            self.dual_recv_info.config(
                text=f"Cổng: {self.receiver_modem.port} | Sóng: {self.receiver_modem.last_bars}/5 | Trực nhận: {listen_state} | Nhận TC: {r_recv} | Gửi ACK: {r_sent}"
            )

    # --------------------------------------------------------------------------
    # QUẢN LÝ CỔNG COM
    # --------------------------------------------------------------------------
    def _refresh_all_ports(self):
        ports = [p.device for p in serial.tools.list_ports.comports()]
        if not ports:
            ports = ["Không có"]

        current_sender = self.sender_port_cb.get()
        current_recv = self.recv_port_cb.get()

        self.sender_port_cb["values"] = ports
        self.recv_port_cb["values"] = ports

        # Gợi ý mặc định hoặc giữ cổng cũ
        if current_sender in ports:
            self.sender_port_cb.set(current_sender)
        elif "COM14" in ports:
            self.sender_port_cb.set("COM14")
        elif ports and ports[0] != "Không có":
            self.sender_port_cb.set(ports[0])

        if current_recv in ports:
            self.recv_port_cb.set(current_recv)
        elif "COM16" in ports:
            self.recv_port_cb.set("COM16")
        elif len(ports) > 1 and ports[1] != "Không có":
            self.recv_port_cb.set(ports[1])
        elif ports and ports[0] != "Không có":
            self.recv_port_cb.set(ports[0])

        self._set_status(f"Đã cập nhật danh sách: {len(ports)} cổng COM.")

    # --------------------------------------------------------------------------
    # LOGGING DISPATCHERS
    # --------------------------------------------------------------------------
    def _log_sender(self, text):
        def _append():
            self.sender_log_text.insert(tk.END, text + "\n")
            self.sender_log_text.see(tk.END)
            self.dual_sender_log.insert(tk.END, text + "\n")
            self.dual_sender_log.see(tk.END)
        self.root.after(0, _append)

    def _log_receiver(self, text):
        def _append():
            self.recv_log_text.insert(tk.END, text + "\n")
            self.recv_log_text.see(tk.END)
            self.dual_recv_log.insert(tk.END, text + "\n")
            self.dual_recv_log.see(tk.END)
        self.root.after(0, _append)

    def _save_log_file(self, source="sender"):
        text_widget = self.sender_log_text if source == "sender" else self.recv_log_text
        content = text_widget.get("1.0", tk.END).strip()
        if not content:
            messagebox.showinfo("Thông báo", "Chưa có nội dung nhật ký nào để lưu!")
            return

        filename = filedialog.asksaveasfilename(
            defaultextension=".txt",
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")],
            initialfile=f"rockblock_{source}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        )
        if filename:
            try:
                with open(filename, "w", encoding="utf-8") as f:
                    f.write(content)
                messagebox.showinfo("Thành công", f"Đã lưu log tại:\n{filename}")
            except Exception as e:
                messagebox.showerror("Lỗi", f"Không thể lưu file: {e}")

    # --------------------------------------------------------------------------
    # THAO TÁC SENDER
    # --------------------------------------------------------------------------
    def _toggle_sender_connection(self):
        if self.sender_modem.is_connected:
            self.sender_modem.disconnect()
            self.btn_sender_connect.config(text="🔌 Kết Nối", style="Primary.TButton")
            self.sender_status_lbl.config(text="🔴 Chưa kết nối", fg="#e53e3e")
            self.sender_imei_lbl.config(text="IMEI: Chưa rõ")
            self.sender_csq_lbl.config(text="Sóng: [□□□□□] (0/5)", fg="#2b6cb0")
            self.sender_last_csq_time.config(text="")
            self.dual_sender_info.config(text="Cổng: Đã ngắt | Sóng: 0/5 | Trạng thái: Idle")
            self._set_status("Sender đã ngắt kết nối.")
        else:
            port = self.sender_port_cb.get()
            if not port or port == "Không có":
                messagebox.showwarning("Cảnh báo", "Vui lòng chọn cổng COM cho Sender!")
                return
            baud = int(self.sender_baud_cb.get())

            def _worker():
                self._set_status(f"Đang kết nối Sender qua {port}...")
                if self.sender_modem.connect(port, baud):
                    ready = self.sender_modem.init_modem()
                    def _update_ui():
                        if ready:
                            self.btn_sender_connect.config(text="🔌 Ngắt Kết Nối", style="Danger.TButton")
                            self.sender_status_lbl.config(text=f"🟢 Đã kết nối ({port})", fg="#2f855a")
                            self.sender_imei_lbl.config(text=f"IMEI: {self.sender_modem.imei}")
                            self.dual_sender_info.config(text=f"Cổng: {port} | IMEI: {self.sender_modem.imei} | Sẵn sàng")
                            self._set_status(f"Sender đã kết nối thành công tới {port}.")
                            # Tự động kích hoạt đo sóng định kỳ sau khi kết nối
                            self._start_sender_auto_csq()
                        else:
                            self.sender_modem.disconnect()
                            messagebox.showerror("Lỗi", "Modem Sender không phản hồi lệnh AT!")
                    self.root.after(0, _update_ui)
                else:
                    self.root.after(0, lambda: messagebox.showerror("Lỗi", f"Không mở được cổng {port}!"))

            threading.Thread(target=_worker, daemon=True).start()

    def _start_sender_auto_csq(self):
        """Khởi động luồng tự động đo sóng định kỳ cho Sender sau khi kết nối."""
        def _loop():
            # Đo ngay 1 lần đầu tiên sau khi kết nối (hiện log đầy đủ)
            time.sleep(1.0)
            if self.sender_modem.is_connected:
                self._run_sender_csq_check(quiet=False)

            while self.sender_modem.is_connected:
                try:
                    interval = int(self.sender_csq_interval_cb.get())
                except Exception:
                    interval = 10

                for _ in range(max(interval, 3)):
                    if not self.sender_modem.is_connected:
                        return
                    time.sleep(1)

                if self.sender_modem.is_connected and self.sender_auto_csq_var.get():
                    self._run_sender_csq_check(quiet=True)

        threading.Thread(target=_loop, daemon=True).start()

    def _run_sender_csq_check(self, quiet=False):
        if not self.sender_modem.is_connected:
            return
        bars = self.sender_modem.check_signal(quiet=quiet)
        bar_display = "■" * bars + "□" * (5 - bars)
        color = "#2f855a" if bars >= 2 else ("#dd6b20" if bars == 1 else "#e53e3e")
        now_time = datetime.now().strftime("%H:%M:%S")

        def _update():
            self.sender_csq_lbl.config(text=f"Sóng: [{bar_display}] ({bars}/5)", fg=color)
            self.sender_last_csq_time.config(text=f"({now_time})")
            self.dual_sender_info.config(text=f"Cổng: {self.sender_modem.port} | Sóng: {bars}/5 vạch ({now_time}) | Sẵn sàng")
            if not quiet:
                self._set_status(f"Sender: Sóng vệ tinh đạt {bars}/5 vạch.")
        self.root.after(0, _update)

    def _check_sender_signal(self):
        if not self.sender_modem.is_connected:
            messagebox.showwarning("Cảnh báo", "Vui lòng kết nối modem Sender trước!")
            return
        self._set_status("Sender: Đang đo mức sóng vệ tinh (AT+CSQ)...")
        threading.Thread(target=lambda: self._run_sender_csq_check(quiet=False), daemon=True).start()

    def _on_sender_auto_csq_toggle(self):
        if self.sender_auto_csq_var.get() and self.sender_modem.is_connected:
            threading.Thread(target=lambda: self._run_sender_csq_check(quiet=False), daemon=True).start()

    def _update_sender_payload_preview(self, event=None):
        target = self.sender_target_entry.get().strip()
        msg = self.sender_msg_entry.get()
        if target:
            try:
                clean_target = f"{int(target):07d}"
                full = f"RB{clean_target}{msg}"
            except Exception:
                full = f"RB{target}{msg}"
        else:
            full = msg
        self.sender_preview_lbl.config(
            text=f"📦 Xem trước lệnh: AT+SBDWT={full} ({len(full)} bytes)"
        )

    def _set_msg_text(self, text):
        self.sender_msg_entry.delete(0, tk.END)
        self.sender_msg_entry.insert(0, text)
        self._update_sender_payload_preview()

    def _set_sample_ping(self):
        sender_id = self.sender_serial_entry.get().strip()
        target_id = self.sender_target_entry.get().strip()
        t = datetime.now().strftime("%H:%M:%S")
        self.sender_msg_entry.delete(0, tk.END)
        self.sender_msg_entry.insert(0, f"PING FROM {sender_id} TO {target_id} T={t}")
        self._update_sender_payload_preview()

    def _set_sample_gps(self):
        t = datetime.now().strftime("%H%M%S")
        compact = f"GPS:21.0285,105.8542,T:28.5,B:98,T:{t}"
        self.sender_msg_entry.delete(0, tk.END)
        self.sender_msg_entry.insert(0, compact)
        self._update_sender_payload_preview()

    def _set_sample_sos(self):
        t = datetime.now().strftime("%H:%M:%S")
        sos = f"ALARM_SOS: BUTTON_TRIGGERED AT {t}"
        self.sender_msg_entry.delete(0, tk.END)
        self.sender_msg_entry.insert(0, sos)
        self._update_sender_payload_preview()

    def _send_sender_message(self):
        if not self.sender_modem.is_connected:
            messagebox.showwarning("Cảnh báo", "Vui lòng kết nối cổng COM của Sender trước khi gửi!")
            return

        msg = self.sender_msg_entry.get().strip()
        if not msg:
            messagebox.showwarning("Cảnh báo", "Nội dung tin nhắn không được để trống!")
            return

        target = self.sender_target_entry.get().strip()

        self.btn_sender_send.config(state="disabled", text="⏳ ĐANG TRUYỀN VỆ TINH...")
        self._set_status(f"Sender đang truyền gói tin đến RockBLOCK {target} qua vệ tinh...")

        def _worker():
            try:
                # Không gọi check_signal() ở đây. Auto-CSQ và send đều dùng
                # cùng Serial/lock; send_sbd_message sẽ giữ lock xuyên suốt
                # AT+SBDD0 -> AT+SBDWT -> AT+SBDIX.
                ok, res_text, rx_msg = self.sender_modem.send_sbd_message(
                    msg,
                    target_serial=target,
                    wait_sbd=30
                )
            except Exception as e:
                ok = False
                res_text = f"Exception khi gửi: {e}"
                rx_msg = None

            def _update_ui():
                self.btn_sender_send.config(state="normal", text="🚀 GỬI QUA VỆ TINH (AT+SBDIX)")
                if ok:
                    self._record_stat("sender_sent")
                    self._set_status("Sender: Gửi thông điệp qua vệ tinh THÀNH CÔNG!")
                    if rx_msg:
                        self._record_stat("sender_recv")
                        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        self._add_sender_inbox_item(timestamp, rx_msg, source="Kèm phiên gửi (MT)", status="Đã nhận")
                        messagebox.showinfo(
                            "Thành công & Nhận Tin Mới",
                            f"Đã gửi thông điệp tới {target} thành công!\n{res_text}\n\n📬 ĐỒNG THỜI NHẬN ĐƯỢC TIN NHẮN PHẢN HỒI TỪ VỆ TINH:\n\"{rx_msg}\""
                        )
                    else:
                        messagebox.showinfo("Thành công", f"Đã gửi thông điệp tới {target} thành công!\n{res_text}")
                else:
                    self._set_status("Sender: Gửi tin thất bại.")
                    messagebox.showwarning("Thất bại", f"Không thể gửi tin qua vệ tinh:\n{res_text}")
            self.root.after(0, _update_ui)

        threading.Thread(target=_worker, daemon=True).start()

    def _check_sender_ack(self):
        if not self.sender_modem.is_connected:
            messagebox.showwarning("Cảnh báo", "Vui lòng kết nối Sender trước!")
            return

        self._set_status("Sender đang kiểm tra hộp thư xem Receiver đã gửi ACK về chưa...")
        def _worker():
            has_msg, msg, queued = self.sender_modem.check_mailbox(wait_sbd=20)
            def _update_ui():
                if has_msg:
                    self._record_stat("sender_recv")
                    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    self._add_sender_inbox_item(timestamp, msg, source="Phản hồi ACK", status="Đã nhận")
                    self._set_status(f"Sender: Nhận được phản hồi: {msg}")
                    self.dual_sender_info.config(text=f"Cổng: {self.sender_modem.port} | Sóng: {self.sender_modem.last_bars}/5 vạch | Đã nhận ACK: '{msg[:15]}'")

                    # Kiểm tra tiếp nếu còn tin trong hàng đợi (queued)
                    if queued > 0:
                        def _fetch_queue(q=queued):
                            curr_q = q
                            while curr_q > 0:
                                time.sleep(2)
                                h_next, m_next, curr_q = self.sender_modem.check_mailbox(wait_sbd=20)
                                if h_next:
                                    self._record_stat("sender_recv")
                                    t_next = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                                    self.root.after(0, lambda t=t_next, m=m_next: self._add_sender_inbox_item(t, m, source="Hàng đợi Gateway", status="Đã nhận"))
                        threading.Thread(target=_fetch_queue, daemon=True).start()

                    messagebox.showinfo("Nhận Bản Tin Phản Hồi", f"🎉 ĐÃ NHẬN ĐƯỢC PHẢN HỒI (ACK):\n\"{msg}\"")
                else:
                    self._set_status("Sender: Hộp thư trống, chưa có phản hồi nào.")
                    messagebox.showinfo("Hộp Thư Trống", "Chưa có bản tin phản hồi (ACK) nào trong hộp thư.")
            self.root.after(0, _update_ui)

        threading.Thread(target=_worker, daemon=True).start()

    def _add_sender_inbox_item(self, timestamp, content, source="Phản hồi ACK", status="Đã nhận"):
        """Thêm một tin nhắn nhận về vào bảng Hộp thư của Sender."""
        length = len(content.encode("utf-8")) if content else 0
        item_id = self.sender_inbox_tree.insert("", 0, values=(timestamp, source, content, length, status))
        count = len(self.sender_inbox_tree.get_children())
        self.sender_inbox_count_lbl.config(text=f"Tổng nhận: {count} tin nhắn")
        return item_id

    def _clear_sender_inbox(self):
        """Xóa toàn bộ danh sách tin nhắn trong Hộp thư Sender."""
        for item in self.sender_inbox_tree.get_children():
            self.sender_inbox_tree.delete(item)
        self.sender_inbox_count_lbl.config(text="Tổng nhận: 0 tin nhắn")
        self._set_status("Sender: Đã xóa toàn bộ hộp thư.")

    def _export_tree_data(self, tree, prefix="inbox"):
        """Xuất danh sách tin nhắn từ Treeview ra file văn bản."""
        items = tree.get_children()
        if not items:
            messagebox.showinfo("Thông báo", "Hộp thư chưa có tin nhắn nào để xuất!")
            return
        filename = filedialog.asksaveasfilename(
            defaultextension=".txt",
            filetypes=[("Text files", "*.txt"), ("CSV files", "*.csv"), ("All files", "*.*")],
            initialfile=f"rockblock_{prefix}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        )
        if filename:
            try:
                with open(filename, "w", encoding="utf-8") as f:
                    for item_id in items:
                        vals = tree.item(item_id, "values")
                        f.write(" | ".join(map(str, vals)) + "\n")
                messagebox.showinfo("Thành công", f"Đã lưu danh sách tin nhắn tại:\n{filename}")
            except Exception as e:
                messagebox.showerror("Lỗi", f"Không thể lưu file: {e}")

    def _show_tree_detail(self, tree, title="Chi Tiết Tin Nhắn"):
        """Hiển thị cửa sổ chi tiết khi nhấp đúp vào dòng tin nhắn."""
        selected = tree.selection()
        if not selected:
            return
        vals = tree.item(selected[0], "values")
        if not vals:
            return

        detail_win = tk.Toplevel(self.root)
        detail_win.title(title)
        detail_win.geometry("520x340")
        detail_win.minsize(420, 240)
        detail_win.transient(self.root)

        tk.Label(detail_win, text=f"🕒 Thời gian: {vals[0]}", font=("Segoe UI", 9, "bold")).pack(anchor="w", padx=15, pady=(10, 2))
        if len(vals) >= 5:
            tk.Label(detail_win, text=f"🏷️ Phân loại: {vals[1]} | 📏 Độ dài: {vals[3]} bytes | 🟢 Trạng thái: {vals[4]}", font=("Segoe UI", 9)).pack(anchor="w", padx=15, pady=2)
            content = vals[2]
        else:
            tk.Label(detail_win, text=f"📏 Độ dài: {vals[2]} bytes | 🟢 Trạng thái: {vals[3]}", font=("Segoe UI", 9)).pack(anchor="w", padx=15, pady=2)
            content = vals[1]

        tk.Label(detail_win, text="📝 Nội dung chi tiết:", font=("Segoe UI", 9, "bold")).pack(anchor="w", padx=15, pady=(8, 2))
        txt = scrolledtext.ScrolledText(detail_win, font=("Consolas", 10), wrap="word")
        txt.pack(fill="both", expand=True, padx=15, pady=(0, 10))
        txt.insert("1.0", str(content))
        txt.config(state="disabled")

        ttk.Button(detail_win, text="Đóng", command=detail_win.destroy).pack(pady=(0, 10))

    # --------------------------------------------------------------------------
    # THAO TÁC RECEIVER
    # --------------------------------------------------------------------------
    def _toggle_receiver_connection(self):
        if self.receiver_modem.is_connected:
            if self.is_listening:
                self._stop_listen_loop()
            self.receiver_modem.disconnect()
            self.btn_recv_connect.config(text="🔌 Kết Nối", style="Primary.TButton")
            self.recv_status_lbl.config(text="🔴 Chưa kết nối", fg="#e53e3e")
            self.recv_imei_lbl.config(text="IMEI: Chưa rõ")
            self.recv_csq_lbl.config(text="Sóng: [□□□□□] (0/5)", fg="#2b6cb0")
            self.recv_last_csq_time.config(text="")
            self.dual_recv_info.config(text="Cổng: Đã ngắt | Sóng: 0/5 | Trực nhận: Tắt")
            self._set_status("Receiver đã ngắt kết nối.")
        else:
            port = self.recv_port_cb.get()
            if not port or port == "Không có":
                messagebox.showwarning("Cảnh báo", "Vui lòng chọn cổng COM cho Receiver!")
                return
            baud = int(self.recv_baud_cb.get())

            def _worker():
                self._set_status(f"Đang kết nối Receiver qua {port}...")
                if self.receiver_modem.connect(port, baud):
                    ready = self.receiver_modem.init_modem()
                    def _update_ui():
                        if ready:
                            self.btn_recv_connect.config(text="🔌 Ngắt Kết Nối", style="Danger.TButton")
                            self.recv_status_lbl.config(text=f"🟢 Đã kết nối ({port})", fg="#2f855a")
                            self.recv_imei_lbl.config(text=f"IMEI: {self.receiver_modem.imei}")
                            self.dual_recv_info.config(text=f"Cổng: {port} | IMEI: {self.receiver_modem.imei} | Sẵn sàng")
                            self._set_status(f"Receiver đã kết nối thành công tới {port}.")
                            # Tự động kích hoạt đo sóng định kỳ sau khi kết nối
                            self._start_recv_auto_csq()
                        else:
                            self.receiver_modem.disconnect()
                            messagebox.showerror("Lỗi", "Modem Receiver không phản hồi lệnh AT!")
                    self.root.after(0, _update_ui)
                else:
                    self.root.after(0, lambda: messagebox.showerror("Lỗi", f"Không mở được cổng {port}!"))

            threading.Thread(target=_worker, daemon=True).start()

    def _start_recv_auto_csq(self):
        """Khởi động luồng tự động đo sóng định kỳ cho Receiver sau khi kết nối."""
        def _loop():
            # Đo ngay 1 lần đầu tiên sau khi kết nối (hiện log đầy đủ)
            time.sleep(1.2)
            if self.receiver_modem.is_connected:
                self._run_recv_csq_check(quiet=False)

            while self.receiver_modem.is_connected:
                try:
                    interval = int(self.recv_csq_interval_cb.get())
                except Exception:
                    interval = 10

                for _ in range(max(interval, 3)):
                    if not self.receiver_modem.is_connected:
                        return
                    time.sleep(1)

                if self.receiver_modem.is_connected and self.recv_auto_csq_var.get():
                    self._run_recv_csq_check(quiet=True)

        threading.Thread(target=_loop, daemon=True).start()

    def _run_recv_csq_check(self, quiet=False):
        if not self.receiver_modem.is_connected:
            return
        bars = self.receiver_modem.check_signal(quiet=quiet)
        bar_display = "■" * bars + "□" * (5 - bars)
        color = "#2f855a" if bars >= 2 else ("#dd6b20" if bars == 1 else "#e53e3e")
        now_time = datetime.now().strftime("%H:%M:%S")

        def _update():
            self.recv_csq_lbl.config(text=f"Sóng: [{bar_display}] ({bars}/5)", fg=color)
            self.recv_last_csq_time.config(text=f"({now_time})")
            listen_state = f"BẬT ({self.recv_interval_cb.get()}s)" if self.is_listening else "TẮT"
            self.dual_recv_info.config(text=f"Cổng: {self.receiver_modem.port} | Sóng: {bars}/5 vạch ({now_time}) | Trực nhận: {listen_state}")
            if not quiet:
                self._set_status(f"Receiver: Sóng vệ tinh đạt {bars}/5 vạch.")
        self.root.after(0, _update)

    def _check_receiver_signal(self):
        if not self.receiver_modem.is_connected:
            messagebox.showwarning("Cảnh báo", "Vui lòng kết nối modem Receiver trước!")
            return
        self._set_status("Receiver: Đang đo mức sóng vệ tinh (AT+CSQ)...")
        threading.Thread(target=lambda: self._run_recv_csq_check(quiet=False), daemon=True).start()

    def _on_recv_auto_csq_toggle(self):
        if self.recv_auto_csq_var.get() and self.receiver_modem.is_connected:
            threading.Thread(target=lambda: self._run_recv_csq_check(quiet=False), daemon=True).start()

    def _check_receiver_mailbox_once(self):
        if not self.receiver_modem.is_connected:
            messagebox.showwarning("Cảnh báo", "Vui lòng kết nối Receiver trước!")
            return

        self._set_status("Receiver: Đang kiểm tra hộp thư một lần (AT+SBDIX)...")
        def _worker():
            self.receiver_modem.check_signal()
            has_msg, msg, queued = self.receiver_modem.check_mailbox(wait_sbd=20)
            def _update_ui():
                if has_msg:
                    self._record_stat("receiver_recv")
                    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    ack_info = "Không gửi ACK"
                    if self.recv_auto_ack_var.get():
                        ack_info = "Đang gửi ACK..."
                    item_id = self.inbox_tree.insert("", 0, values=(timestamp, msg, len(msg), ack_info))
                    self._set_status(f"Receiver: Nhận được tin nhắn '{msg}'")

                    if self.recv_auto_ack_var.get():
                        self._trigger_auto_ack(msg, item_id)
                    else:
                        messagebox.showinfo("Đã Nhận Tin", f"📩 ĐÃ NHẬN ĐƯỢC TIN NHẮN:\n\"{msg}\"")
                else:
                    self._set_status("Receiver: Hộp thư trống.")
                    messagebox.showinfo("Hộp Thư Trống", "Hộp thư vệ tinh hiện đang trống.")
            self.root.after(0, _update_ui)

        threading.Thread(target=_worker, daemon=True).start()

    def _trigger_auto_ack(self, original_msg, item_id):
        sender_target = self.recv_sender_target_entry.get().strip()
        ack_payload = self.recv_ack_prefix_entry.get().strip() or "Hi B"

        def _ack_worker():
            time.sleep(2)
            self._set_status(f"Receiver: Đang tự động gửi bản tin phản hồi ('{ack_payload}') về {sender_target}...")
            ok, res, _ = self.receiver_modem.send_sbd_message(ack_payload, target_serial=sender_target, wait_sbd=20)
            def _done():
                if ok:
                    self._record_stat("receiver_sent")
                status_str = "✅ Đã gửi ACK thành công" if ok else "❌ Gửi ACK thất bại"
                try:
                    vals = list(self.inbox_tree.item(item_id, "values"))
                    vals[3] = status_str
                    self.inbox_tree.item(item_id, values=vals)
                except Exception:
                    pass
                self._set_status(f"Receiver: {status_str}")
            self.root.after(0, _done)

        threading.Thread(target=_ack_worker, daemon=True).start()

    def _toggle_listen_loop(self):
        if self.is_listening:
            self._stop_listen_loop()
        else:
            self._start_listen_loop()

    def _start_listen_loop(self):
        if not self.receiver_modem.is_connected:
            messagebox.showwarning("Cảnh báo", "Vui lòng kết nối Receiver trước khi bật lắng nghe!")
            return

        interval = int(self.recv_interval_cb.get())
        self.is_listening = True
        self.btn_listen_toggle.config(text="⏹️ DỪNG LẮNG NGHE ĐỊNH KỲ", style="Danger.TButton")
        self.dual_recv_info.config(text=f"Cổng: {self.receiver_modem.port} | Trực nhận: ĐANG BẬT ({interval}s)")
        self._set_status(f"Receiver: Đã bật chế độ lắng nghe liên tục mỗi {interval}s.")

        def _listen_worker():
            cycle = 0
            while self.is_listening and self.receiver_modem.is_connected:
                cycle += 1
                self.receiver_modem.log(f"--- Chu kỳ lắng nghe {cycle} ---")
                bars = self.receiver_modem.check_signal()
                if bars >= 1:
                    has_msg, msg, queued = self.receiver_modem.check_mailbox(wait_sbd=20)
                    if has_msg:
                        self._record_stat("receiver_recv")
                        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        def _add_inbox(m=msg):
                            ack_txt = "Đang gửi Auto-ACK..." if self.recv_auto_ack_var.get() else "Tắt Auto-ACK"
                            iid = self.inbox_tree.insert("", 0, values=(timestamp, m, len(m), ack_txt))
                            if self.recv_auto_ack_var.get():
                                self._trigger_auto_ack(m, iid)
                        self.root.after(0, _add_inbox)

                        # Tải tiếp nếu còn hàng đợi
                        while queued > 0 and self.is_listening:
                            time.sleep(3)
                            has_next, next_msg, queued = self.receiver_modem.check_mailbox(wait_sbd=20)
                            if has_next:
                                self._record_stat("receiver_recv")
                                t_next = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                                def _add_next(nm=next_msg):
                                    iid = self.inbox_tree.insert("", 0, values=(t_next, nm, len(nm), "Đang gửi ACK..."))
                                    if self.recv_auto_ack_var.get():
                                        self._trigger_auto_ack(nm, iid)
                                self.root.after(0, _add_next)
                else:
                    self.receiver_modem.log("⚠️ Sóng vệ tinh 0 vạch, tạm bỏ qua lượt kiểm tra này.")

                # Chờ chu kỳ tiếp theo
                for _ in range(interval):
                    if not self.is_listening:
                        break
                    time.sleep(1)

            self.root.after(0, self._stop_listen_ui)

        self.listen_thread = threading.Thread(target=_listen_worker, daemon=True)
        self.listen_thread.start()

    def _stop_listen_loop(self):
        self.is_listening = False
        self._stop_listen_ui()
        self._set_status("Receiver: Đã dừng chế độ lắng nghe.")

    def _stop_listen_ui(self):
        self.btn_listen_toggle.config(text="🎧 BẮT ĐẦU LẮNG NGHE ĐỊNH KỲ", style="Success.TButton")
        if self.receiver_modem.is_connected:
            self.dual_recv_info.config(text=f"Cổng: {self.receiver_modem.port} | Trực nhận: ĐÃ TẮT")

    def _send_manual_reply(self):
        if not self.receiver_modem.is_connected:
            messagebox.showwarning("Cảnh báo", "Vui lòng kết nối Receiver trước!")
            return

        text = self.recv_manual_reply_entry.get().strip()
        if not text:
            messagebox.showwarning("Cảnh báo", "Nội dung phản hồi không được để trống!")
            return

        target = self.recv_sender_target_entry.get().strip()
        self._set_status(f"Receiver đang gửi bản tin phản hồi thủ công về {target}...")

        def _worker():
            ok, res, _ = self.receiver_modem.send_sbd_message(text, target_serial=target, wait_sbd=20)
            def _update_ui():
                if ok:
                    self._record_stat("receiver_sent")
                    self._set_status("Receiver: Gửi phản hồi thành công!")
                    messagebox.showinfo("Thành công", f"Đã gửi phản hồi thành công về {target}!\n{res}")
                else:
                    self._set_status("Receiver: Gửi phản hồi thất bại.")
                    messagebox.showwarning("Thất bại", f"Gửi phản hồi thất bại:\n{res}")
            self.root.after(0, _update_ui)

        threading.Thread(target=_worker, daemon=True).start()

    def _on_close(self):
        self.is_listening = False
        if self.sender_modem.is_connected:
            self.sender_modem.disconnect()
        if self.receiver_modem.is_connected:
            self.receiver_modem.disconnect()
        self.root.destroy()


# ==============================================================================
# HÀM MAIN
# ==============================================================================
def main():
    root = tk.Tk()
    app = RockBlockDualApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
