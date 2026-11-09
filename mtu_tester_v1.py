#!/usr/bin/env python3
"""
NetPulse MTU Tester - GUI tool to find the Path MTU to a host.
Author : Amir Hashempour
Website: https://netpulse.ir

Method: sends ICMP echo requests with the "Don't Fragment" flag and
uses binary search to find the largest packet that passes.

Works on Windows, Linux and macOS (uses the system `ping` command).
No extra packages needed (tkinter ships with Python).
"""

import math
import platform
import queue
import subprocess
import threading
import webbrowser
import tkinter as tk
from tkinter import ttk, messagebox

OS = platform.system()
IP_ICMP_HEADER = 28  # 20 bytes IP + 8 bytes ICMP

BRAND_NAME = "NetPulse"
BRAND_SITE = "netpulse.ir"
BRAND_URL = "https://netpulse.ir"
AUTHOR = "Amir Hashempour"
BRAND_COLOR = "#0b5ed7"


def ping_df(host: str, payload: int, timeout_ms: int) -> bool:
    """One ping with DF flag set. True if a normal echo reply came back."""
    timeout_s = max(1, math.ceil(timeout_ms / 1000))
    kwargs = {}
    if OS == "Windows":
        cmd = ["ping", "-n", "1", "-w", str(timeout_ms), "-f", "-l", str(payload), host]
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    elif OS == "Darwin":
        cmd = ["ping", "-c", "1", "-D", "-s", str(payload), "-W", str(timeout_ms), host]
    else:  # Linux and others
        cmd = ["ping", "-c", "1", "-W", str(timeout_s), "-M", "do", "-s", str(payload), host]

    try:
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=timeout_s + 5,
            **kwargs,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return False

    out = proc.stdout.lower()
    # A real reply always contains TTL. "Packet needs to be fragmented" /
    # "Frag needed" messages do not, even though Windows returns code 0.
    if "ttl=" not in out:
        return False
    if OS != "Windows" and proc.returncode != 0:
        return False
    return True


class MTUTesterApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        root.title(f"{BRAND_NAME} MTU Tester - {BRAND_SITE}")
        root.geometry("640x640")
        root.minsize(560, 560)

        self.q: "queue.Queue[tuple]" = queue.Queue()
        self.stop_event = threading.Event()
        self.worker = None

        self._build_ui()
        self.root.after(100, self._poll_queue)

    # ---------------------------------------------------------------- UI
    def _build_ui(self):
        pad = {"padx": 8, "pady": 4}

        # ---- Brand header
        header = tk.Frame(self.root, bg=BRAND_COLOR)
        header.pack(fill="x")
        tk.Label(header, text=f"{BRAND_NAME} MTU Tester", bg=BRAND_COLOR, fg="white",
                 font=("Segoe UI", 16, "bold")).pack(anchor="w", padx=12, pady=(8, 0))
        site = tk.Label(header, text=BRAND_SITE, bg=BRAND_COLOR, fg="#cfe2ff",
                        font=("Segoe UI", 10, "underline"), cursor="hand2")
        site.pack(anchor="w", padx=12, pady=(0, 8))
        site.bind("<Button-1>", lambda e: webbrowser.open(BRAND_URL))

        # ---- Brand footer (packed first so it stays at the bottom)
        footer = tk.Frame(self.root, bg="#f1f3f5")
        footer.pack(side="bottom", fill="x")
        tk.Label(footer, text=f"Developed by {AUTHOR}  |  ", bg="#f1f3f5",
                 fg="#555555", font=("Segoe UI", 9)).pack(side="left", padx=(10, 0), pady=4)
        flink = tk.Label(footer, text=BRAND_SITE, bg="#f1f3f5", fg=BRAND_COLOR,
                         font=("Segoe UI", 9, "underline"), cursor="hand2")
        flink.pack(side="left", pady=4)
        flink.bind("<Button-1>", lambda e: webbrowser.open(BRAND_URL))

        frm = ttk.LabelFrame(self.root, text="Settings")
        frm.pack(fill="x", padx=10, pady=(10, 4))
        frm.columnconfigure(1, weight=1)

        ttk.Label(frm, text="Host / IP:").grid(row=0, column=0, sticky="w", **pad)
        self.host_var = tk.StringVar(value="8.8.8.8")
        host_entry = ttk.Entry(frm, textvariable=self.host_var)
        host_entry.grid(row=0, column=1, columnspan=3, sticky="ew", **pad)

        ttk.Label(frm, text="Min MTU:").grid(row=1, column=0, sticky="w", **pad)
        self.min_var = tk.StringVar(value="576")
        ttk.Spinbox(frm, from_=100, to=9000, textvariable=self.min_var, width=8).grid(
            row=1, column=1, sticky="w", **pad
        )

        ttk.Label(frm, text="Max MTU:").grid(row=1, column=2, sticky="e", **pad)
        self.max_var = tk.StringVar(value="1500")
        ttk.Spinbox(frm, from_=100, to=9000, textvariable=self.max_var, width=8).grid(
            row=1, column=3, sticky="w", **pad
        )

        ttk.Label(frm, text="Timeout (ms):").grid(row=2, column=0, sticky="w", **pad)
        self.timeout_var = tk.StringVar(value="1500")
        ttk.Spinbox(frm, from_=200, to=10000, increment=100,
                    textvariable=self.timeout_var, width=8).grid(
            row=2, column=1, sticky="w", **pad
        )

        ttk.Label(frm, text="Retries / size:").grid(row=2, column=2, sticky="e", **pad)
        self.retry_var = tk.StringVar(value="2")
        ttk.Spinbox(frm, from_=1, to=10, textvariable=self.retry_var, width=8).grid(
            row=2, column=3, sticky="w", **pad
        )

        # Buttons
        btns = ttk.Frame(self.root)
        btns.pack(fill="x", padx=10, pady=4)
        self.start_btn = ttk.Button(btns, text="Start Test", command=self.start)
        self.start_btn.pack(side="left")
        self.stop_btn = ttk.Button(btns, text="Stop", command=self.stop, state="disabled")
        self.stop_btn.pack(side="left", padx=6)
        ttk.Button(btns, text="Clear Log", command=self.clear_log).pack(side="left")

        # Progress
        self.progress = ttk.Progressbar(self.root, mode="determinate")
        self.progress.pack(fill="x", padx=10, pady=4)

        # Result
        res = ttk.LabelFrame(self.root, text="Result")
        res.pack(fill="x", padx=10, pady=4)
        self.result_var = tk.StringVar(value="—")
        ttk.Label(res, textvariable=self.result_var,
                  font=("Segoe UI", 20, "bold")).pack(anchor="w", padx=10, pady=(6, 0))
        self.detail_var = tk.StringVar(value="")
        ttk.Label(res, textvariable=self.detail_var, justify="left").pack(
            anchor="w", padx=10, pady=(0, 8)
        )

        # Log
        logf = ttk.LabelFrame(self.root, text="Log")
        logf.pack(fill="both", expand=True, padx=10, pady=(4, 10))
        self.log = tk.Text(logf, height=10, state="disabled", wrap="word",
                           font=("Consolas", 10))
        sb = ttk.Scrollbar(logf, command=self.log.yview)
        self.log.configure(yscrollcommand=sb.set)
        self.log.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.log.tag_configure("ok", foreground="#1a7f37")
        self.log.tag_configure("fail", foreground="#cf222e")
        self.log.tag_configure("info", foreground="#555555")

        host_entry.focus()
        self.root.bind("<Return>", lambda e: self.start())

    # ------------------------------------------------------------ helpers
    def write_log(self, text: str, tag: str = "info"):
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n", tag)
        self.log.see("end")
        self.log.configure(state="disabled")

    def clear_log(self):
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")

    def set_running(self, running: bool):
        self.start_btn.configure(state="disabled" if running else "normal")
        self.stop_btn.configure(state="normal" if running else "disabled")

    # ------------------------------------------------------------ actions
    def start(self):
        if self.worker and self.worker.is_alive():
            return
        host = self.host_var.get().strip()
        if not host:
            messagebox.showwarning("MTU Tester", "Enter a host or IP address.")
            return
        try:
            lo = int(self.min_var.get())
            hi = int(self.max_var.get())
            timeout = int(self.timeout_var.get())
            retries = int(self.retry_var.get())
        except ValueError:
            messagebox.showwarning("MTU Tester", "Numeric fields must contain numbers.")
            return
        if lo < 100 or hi <= lo:
            messagebox.showwarning("MTU Tester", "Max MTU must be greater than Min MTU (min 100).")
            return

        self.stop_event.clear()
        self.result_var.set("Testing…")
        self.detail_var.set("")
        steps = math.ceil(math.log2(hi - lo)) + 2
        self.progress.configure(maximum=steps, value=0)
        self.set_running(True)
        self.write_log(f"--- Testing {host} (MTU {lo}..{hi}) ---")

        self.worker = threading.Thread(
            target=self._run_test, args=(host, lo, hi, timeout, retries), daemon=True
        )
        self.worker.start()

    def stop(self):
        self.stop_event.set()
        self.write_log("Stopping…")

    # ------------------------------------------------------------- worker
    def _try_mtu(self, host, mtu, timeout, retries) -> bool:
        payload = mtu - IP_ICMP_HEADER
        for attempt in range(1, retries + 1):
            if self.stop_event.is_set():
                return False
            if ping_df(host, payload, timeout):
                self.q.put(("log", f"MTU {mtu:>5}  (payload {payload:>4})  OK", "ok"))
                self.q.put(("step",))
                return True
        self.q.put(("log", f"MTU {mtu:>5}  (payload {payload:>4})  FAILED (too big / blocked)", "fail"))
        self.q.put(("step",))
        return False

    def _run_test(self, host, lo, hi, timeout, retries):
        try:
            # 1) baseline: smallest size must work, else host/ICMP is unreachable
            if not self._try_mtu(host, lo, timeout, retries):
                if self.stop_event.is_set():
                    self.q.put(("done", None, "Stopped."))
                else:
                    self.q.put((
                        "done", None,
                        f"Even MTU {lo} failed. The host is unreachable or ICMP is blocked.",
                    ))
                return

            # 2) maybe the max already works
            if self._try_mtu(host, hi, timeout, retries):
                self.q.put(("done", hi, f"Maximum tested size ({hi}) passes without fragmentation."))
                return
            if self.stop_event.is_set():
                self.q.put(("done", None, "Stopped."))
                return

            # 3) binary search
            good, bad = lo, hi
            while bad - good > 1:
                if self.stop_event.is_set():
                    self.q.put(("done", None, "Stopped."))
                    return
                mid = (good + bad) // 2
                if self._try_mtu(host, mid, timeout, retries):
                    good = mid
                else:
                    bad = mid

            self.q.put(("done", good, "Largest packet that passes without fragmentation."))
        except Exception as exc:  # pragma: no cover
            self.q.put(("done", None, f"Error: {exc}"))

    # -------------------------------------------------------------- queue
    def _poll_queue(self):
        try:
            while True:
                msg = self.q.get_nowait()
                kind = msg[0]
                if kind == "log":
                    self.write_log(msg[1], msg[2])
                elif kind == "step":
                    self.progress.step(1)
                elif kind == "done":
                    _, mtu, note = msg
                    self._finish(mtu, note)
        except queue.Empty:
            pass
        self.root.after(100, self._poll_queue)

    def _finish(self, mtu, note):
        self.set_running(False)
        self.progress.configure(value=self.progress["maximum"])
        if mtu is None:
            self.result_var.set("—")
            self.detail_var.set(note)
            self.write_log(note, "fail")
            return
        self.result_var.set(f"Path MTU = {mtu}")
        self.detail_var.set(
            f"{note}\n"
            f"Max ping payload: {mtu - IP_ICMP_HEADER}    |    "
            f"Suggested TCP MSS: {mtu - 40}"
        )
        self.write_log(f"Result: MTU = {mtu}", "ok")


def main():
    root = tk.Tk()
    try:
        ttk.Style().theme_use("vista" if OS == "Windows" else "clam")
    except tk.TclError:
        pass
    MTUTesterApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
