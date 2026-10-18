import tkinter as tk
from tkinter import filedialog, messagebox
import threading
import queue
import re
import os
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import unquote

import requests


class GoogleDriveDownloader:

    def __init__(self):

        self.root = tk.Tk()
        self.root.title("Google Drive 图片批量下载器")
        self.root.geometry("1000x750")

        self.save_dir = ""
        self.msg_queue = queue.Queue()

        self.build_ui()

        self.root.after(100, self.process_queue)

    def build_ui(self):

        title = tk.Label(
            self.root,
            text="Google Drive 图片批量下载器",
            font=("Microsoft YaHei", 20, "bold")
        )
        title.pack(pady=10)

        top_frame = tk.Frame(self.root)
        top_frame.pack(fill="x", padx=10)

        tk.Label(
            top_frame,
            text="文件名前缀："
        ).pack(side="left")

        self.prefix_entry = tk.Entry(
            top_frame,
            width=30
        )

        self.prefix_entry.pack(
            side="left",
            padx=5
        )

        self.prefix_entry.insert(
            0,
            ""
        )

        self.folder_btn = tk.Button(
            top_frame,
            text="选择保存目录",
            command=self.select_folder
        )

        self.folder_btn.pack(
            side="right"
        )

        tk.Label(
            self.root,
            text="把 Google Sheet 整列链接直接粘贴到下面："
        ).pack(anchor="w", padx=10)

        self.textbox = tk.Text(
            self.root,
            height=18
        )

        self.textbox.pack(
            fill="both",
            expand=False,
            padx=10,
            pady=5
        )

        self.start_btn = tk.Button(
            self.root,
            text="开始下载",
            height=2,
            command=self.start_download
        )

        self.start_btn.pack(
            fill="x",
            padx=10,
            pady=5
        )

        self.status_var = tk.StringVar()
        self.status_var.set("等待开始...")

        self.status_label = tk.Label(
            self.root,
            textvariable=self.status_var
        )

        self.status_label.pack()

        self.progress_var = tk.DoubleVar()

        self.progress = tk.Scale(
            self.root,
            from_=0,
            to=100,
            orient="horizontal",
            variable=self.progress_var,
            state="disabled"
        )

        self.progress.pack(
            fill="x",
            padx=10
        )

        tk.Label(
            self.root,
            text="日志"
        ).pack()

        self.logbox = tk.Text(
            self.root,
            height=15
        )

        self.logbox.pack(
            fill="both",
            expand=True,
            padx=10,
            pady=5
        )

    def log(self, text):
        self.msg_queue.put(text)

    def process_queue(self):

        while not self.msg_queue.empty():

            msg = self.msg_queue.get()

            self.logbox.insert(
                tk.END,
                msg + "\n"
            )

            self.logbox.see(tk.END)

        self.root.after(
            100,
            self.process_queue
        )

    def select_folder(self):

        folder = filedialog.askdirectory()

        if folder:

            self.save_dir = folder

            self.status_var.set(
                f"保存目录：{folder}"
            )

    def extract_file_id(self, url):

        patterns = [

            r'id=([A-Za-z0-9_-]+)',

            r'/file/d/([A-Za-z0-9_-]+)',

            r'/d/([A-Za-z0-9_-]+)',

            r'uc\?id=([A-Za-z0-9_-]+)'
        ]

        for p in patterns:

            m = re.search(p, url)

            if m:
                return m.group(1)

        return None

    def _get_filename_from_response(self, response):
        """从 HTTP 响应头的 Content-Disposition 中提取原始文件名。"""
        cd = response.headers.get("Content-Disposition", "")
        if not cd:
            return None

        # 尝试匹配 filename*=UTF-8''xxx 格式（RFC 5987）
        m = re.search(r"filename\*=(?:UTF-8|utf-8)''(.+?)(?:;|$)", cd)
        if m:
            return unquote(m.group(1).strip().strip('"'))

        # 尝试匹配 filename="xxx" 或 filename=xxx 格式
        m = re.search(r'filename="?([^";]+)"?', cd)
        if m:
            return unquote(m.group(1).strip())

        return None

    def _download_from_gdrive(self, file_id, output_path):
        """
        使用 requests 直接从 Google Drive 下载文件。
        处理大文件的病毒扫描确认页面。
        返回 (success, original_filename)。
        """
        base_url = "https://drive.google.com/uc"
        params = {
            "export": "download",
            "id": file_id,
            "confirm": "t",  # 跳过病毒扫描确认
        }

        session = requests.Session()
        # 设置 User-Agent 避免被 Google 拒绝
        session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            )
        })

        original_filename = None

        try:
            # 第一次请求
            response = session.get(base_url, params=params, stream=True, timeout=60)
            response.raise_for_status()

            # 检查是否返回了 HTML 确认页面（大文件病毒扫描警告）
            content_type = response.headers.get("Content-Type", "")
            if "text/html" in content_type:
                # 解析确认页面，提取确认 token
                html_content = response.text
                # 尝试多种模式提取确认链接/token
                confirm_patterns = [
                    r'href="(/uc\?export=download[^"]*)"',
                    r'id="download-form"[^>]*action="([^"]*)"',
                    r'confirm=([a-zA-Z0-9_-]+)',
                ]
                confirm_url = None
                for pattern in confirm_patterns:
                    m = re.search(pattern, html_content)
                    if m:
                        matched = m.group(1)
                        if matched.startswith("/"):
                            confirm_url = "https://drive.google.com" + matched.replace("&amp;", "&")
                        elif "confirm=" in pattern:
                            params["confirm"] = matched
                            confirm_url = base_url
                        else:
                            confirm_url = matched.replace("&amp;", "&")
                        break

                if confirm_url:
                    if confirm_url == base_url:
                        response = session.get(confirm_url, params=params, stream=True, timeout=60)
                    else:
                        response = session.get(confirm_url, stream=True, timeout=60)
                    response.raise_for_status()

            # 提取原始文件名
            original_filename = self._get_filename_from_response(response)

            # 验证响应内容不是 HTML 错误页面
            content_type = response.headers.get("Content-Type", "")
            if "text/html" in content_type:
                # 读取一小部分内容来检查
                peek = response.content[:2000] if not response.encoding else response.text[:2000]
                if isinstance(peek, bytes):
                    peek = peek.decode("utf-8", errors="ignore")
                if "ServiceLogin" in peek or "accounts.google.com" in peek:
                    raise Exception("需要登录 Google 账号才能访问此文件")
                if "不可用" in peek or "not available" in peek.lower():
                    raise Exception("此文件不可用或已被删除")
                raise Exception("Google Drive 返回了 HTML 页面而非文件内容，文件可能不可公开访问")

            # 写入文件
            with open(output_path, "wb") as f:
                for chunk in response.iter_content(chunk_size=32768):
                    if chunk:
                        f.write(chunk)

            # 验证文件大小
            file_size = os.path.getsize(output_path)
            if file_size == 0:
                os.remove(output_path)
                raise Exception("下载的文件大小为 0")

            return True, original_filename

        except requests.exceptions.RequestException as e:
            if os.path.exists(output_path):
                os.remove(output_path)
            raise Exception(f"网络请求失败: {e}")
        finally:
            session.close()

    def _get_extension(self, original_filename):
        """从原始文件名获取扩展名，默认返回 .jpg"""
        if original_filename:
            ext = os.path.splitext(original_filename)[1]
            if ext:
                return ext
        return ".jpg"

    def download_file(
        self,
        index,
        url,
        prefix
    ):

        try:

            file_id = self.extract_file_id(url)

            if not file_id:

                self.log(
                    f"无效链接：{url}"
                )

                return False

            # 先下载到临时文件（不带扩展名）
            temp_path = os.path.join(
                self.save_dir,
                f"temp_{file_id}"
            )

            success = False
            original_filename = None

            for retry in range(3):

                try:

                    success, original_filename = self._download_from_gdrive(
                        file_id,
                        temp_path
                    )

                    if success and os.path.exists(temp_path):
                        break

                except Exception as e:

                    self.log(
                        f"  重试 {retry + 1}/3 ({file_id}): {e}"
                    )

                    if os.path.exists(temp_path):
                        os.remove(temp_path)

                    time.sleep(2 + retry * 2)

            if not success:

                self.log(
                    f"下载失败：{file_id}"
                )

                return False

            # 确定扩展名
            ext = self._get_extension(original_filename)

            final_file = os.path.join(
                self.save_dir,
                f"{prefix}{index:03d}{ext}"
            )

            if os.path.exists(final_file):
                os.remove(final_file)

            os.replace(
                temp_path,
                final_file
            )

            self.log(
                f"✓ {os.path.basename(final_file)}"
            )

            return True

        except Exception as e:

            self.log(
                f"✗ {index}: {e}"
            )
            self.log(traceback.format_exc())

            return False

    def worker(self):

        urls = self.textbox.get(
            "1.0",
            tk.END
        ).splitlines()

        urls = [
            u.strip()
            for u in urls
            if u.strip()
        ]

        total = len(urls)

        if total == 0:

            self.log(
                "没有检测到链接"
            )

            return

        prefix = self.prefix_entry.get()

        completed = 0

        max_workers = min(8, total)

        with ThreadPoolExecutor(
            max_workers=max_workers
        ) as executor:

            futures = []

            for i, url in enumerate(
                urls,
                start=1
            ):

                futures.append(
                    executor.submit(
                        self.download_file,
                        i,
                        url,
                        prefix
                    )
                )

            for future in as_completed(
                futures
            ):

                future.result()

                completed += 1

                percent = (
                    completed / total
                ) * 100

                self.progress_var.set(
                    percent
                )

                self.status_var.set(
                    f"{completed}/{total}"
                )

        self.status_var.set(
            "全部下载完成"
        )

        self.log(
            "===================="
        )

        self.log(
            "全部下载完成"
        )

        messagebox.showinfo(
            "完成",
            "全部下载完成"
        )

    def start_download(self):

        if not self.save_dir:

            messagebox.showwarning(
                "提示",
                "请先选择保存目录"
            )

            return

        threading.Thread(
            target=self.worker,
            daemon=True
        ).start()

    def run(self):

        self.root.mainloop()


if __name__ == "__main__":

    app = GoogleDriveDownloader()

    app.run()
