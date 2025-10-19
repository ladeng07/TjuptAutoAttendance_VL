# -*- encoding: utf-8 -*-
import json
import os
import pickle
import random
import time
from argparse import ArgumentParser
from datetime import datetime
import requests
from bs4 import BeautifulSoup
from openai import OpenAI
import configparser


class Bot:
    def __init__(self, username, password, base_url, cookies_path,
                 model_api_key=None, model_base_url=None, *args, **kw_args):
        self.username = username
        self.password = password
        self.base_url = base_url.rstrip('/') + '/'
        self.cookies_path = cookies_path
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) "
                          "Chrome/141.0.0.0 Safari/537.36 Edg/141.0.0.0"
        })
        self.session.cookies = self.load_cookies()

        # ✅ 初始化 ModelScope 客户端
        self.model_api_key = model_api_key
        self.model_base_url = model_base_url or "https://api-inference.modelscope.cn/v1"

        if not self.model_api_key:
            self.log("⚠️ 未提供 ModelScope API Key，验证码识别将无法工作。")

        self.modelscope_client = None
        if self.model_api_key:
            self.modelscope_client = OpenAI(
                api_key=self.model_api_key,
                base_url=self.model_base_url
            )

    def log(self, *args, **kw) -> None:
        print("[%s]" % datetime.now().strftime("%Y-%m-%d %H:%M:%S"), *args, **kw)

    def load_cookies(self) -> requests.cookies.RequestsCookieJar:
        if os.path.exists(self.cookies_path):
            try:
                with open(self.cookies_path, "rb") as file:
                    cookies = pickle.load(file)
                self.log(f"✅ Cookies loaded from file: {self.cookies_path}")
                return cookies
            except Exception as e:
                self.log(f"⚠️ Reading cookies error: {e}")
        else:
            self.log(f"ℹ️ Cookies file not found: {self.cookies_path}")
        return requests.cookies.RequestsCookieJar()

    def login(self) -> bool:
        try_time = 5
        while try_time > 0:
            _ = self.session.get(f"{self.base_url}login.php")
            response = self.session.post(f"{self.base_url}takelogin.php", {
                "username": self.username,
                "password": self.password,
                "logout": 90
            })
            if "logout.php" in response.text:
                self.log("✅ Logged in successfully")
                os.makedirs(os.path.dirname(self.cookies_path), exist_ok=True)
                with open(self.cookies_path, "wb") as f:
                    pickle.dump(self.session.cookies, f)
                self.log(f"✅ Cookies saved to: {self.cookies_path}")
                return True
            try_time -= 1
            self.log(f"⚠️ Log in failed, retrying ({try_time} left)")
        self.log("❌ Login failed after 5 attempts")
        return False

    def auto_attendance(self) -> bool:
        for i in range(5):
            if self.auto_attendance_once():
                self.log("✅ 签到成功！")
                return True
            self.log(f"⚠️ 签到失败，{4 - i} 次重试剩余...")
            time.sleep(random.uniform(1, 5))
        self.log("❌ 连续 5 次签到失败。")
        return False

    def auto_attendance_once(self) -> bool:
        response = self.session.get(f"{self.base_url}attendance.php")
        if "login.php" in response.url:
            self.log("ℹ️ 登录失效，重新登录...")
            if not self.login():
                return False
            response = self.session.get(f"{self.base_url}attendance.php")

        text = response.text
        if "今日已签到" in text:
            self.log("✅ 今日已签到")
            return True

        tree = BeautifulSoup(text, "html.parser")
        captcha_img_tag = tree.select_one(".captcha img")
        if not captcha_img_tag:
            self.log("❌ 未找到验证码图片")
            return False

        captcha_image_url = f"{self.base_url.rstrip('/')}/{captcha_img_tag['src'].lstrip('/')}"
        self.log(f"🖼️ 验证码图片 URL: {captcha_image_url}")

        captcha_options = []
        for label in tree.select(".captcha label"):
            input_tag = label.find("input")
            if input_tag and input_tag.has_attr("value"):
                value = input_tag["value"]
                title = label.text.strip()
                captcha_options.append((value, title))

        if not captcha_options:
            self.log("❌ 未找到验证码选项")
            return False

        if not self.modelscope_client:
            self.log("⚠️ 未配置 ModelScope 客户端，无法自动识别验证码")
            return False

        option_titles = [title for _, title in captcha_options]
        prompt_text = (
            "这是一张电影海报。它对应以下哪个电影标题？"
            "请仅输出正确的电影标题，不要包含任何标点或解释。"
            f"选项: {', '.join(option_titles)}"
        )
        self.log(f"🧠 识别选项: {', '.join(option_titles)}")

        try:
            response = self.modelscope_client.chat.completions.create(
                model="Qwen/Qwen2.5-VL-72B-Instruct",
                messages=[
                    {"role": "system",
                     "content": [{"type": "text", "text": "你是电影专家，任务是识别海报并选择正确标题。"}]},
                    {"role": "user",
                     "content": [
                         {"type": "image_url", "image_url": {"url": captcha_image_url}},
                         {"type": "text", "text": prompt_text},
                     ]},
                ],
            )
            model_response_title = response.choices[0].message.content.strip()
            self.log(f"🎯 ModelScope 回答: {model_response_title}")

        except Exception as e:
            self.log(f"❌ ModelScope API 调用失败: {e}")
            return False

        selected_value = None
        for value, title in captcha_options:
            if model_response_title == title:
                selected_value = value
                break

        if not selected_value:
            self.log(f"⚠️ ModelScope 回答 '{model_response_title}' 未匹配任何选项")
            return False

        data = {"ban_robot": selected_value, "submit": "提交"}
        self.log(f"📤 提交签到选择: {selected_value}")
        response = self.session.post(f"{self.base_url}attendance.php", data)

        if "签到成功" in response.text:
            return True
        else:
            self.log(f"⚠️ 签到失败，响应片段: {response.text[:200]}")
            return False


def load_config(path: str) -> dict:
    """从 config.ini 加载配置"""
    parser = configparser.ConfigParser()
    if not os.path.exists(path):
        raise FileNotFoundError(f"配置文件不存在: {path}")
    parser.read(path, encoding="utf-8")

    section = "Bot"
    return {
        "username": parser.get(section, "username"),
        "password": parser.get(section, "password"),
        "base_url": parser.get(section, "base-url"),
        "cookies_path": parser.get(section, "cookies-path"),
        "model_api_key":  parser.get(section,"model_api_key"),
        "model_base_url":  parser.get(section,"model_base_url"),
    }


if __name__ == "__main__":
    argument_parser = ArgumentParser(description="Auto attendance bot for TJUPT.")
    args = argument_parser.parse_args()

    os.makedirs("data", exist_ok=True)

    config_path = "config/config.ini"
    config = load_config(config_path)
    bot = Bot(**config)
    bot.auto_attendance()
