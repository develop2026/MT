#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
bbs.binmt.cc 自动签到脚本（Playwright 版）
==========================================
- 使用 Playwright Chromium 绕过阿里云 WAF JS 挑战
- 保留原有：prefs / logger / 账号脱敏 / 代理轮换 / 已签到跳过
- 单文件，直接运行
"""
import os
import re
import sys
import time
import random
import ipaddress

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    print("缺少 playwright，请先执行：pip install playwright && playwright install chromium")
    sys.exit(1)

# ---------- 简易日志 ----------
class Logger:
    def _log(self, level, msg):
        print(f"[{time.strftime('%H:%M:%S')}] [{level}] {msg}")

    def info(self, msg):    self._log("INFO", msg)
    def warning(self, msg): self._log("WARN", msg)
    def error(self, msg):   self._log("ERROR", msg)

logger = Logger()

# ---------- 简易偏好存储（原 sqlitedict 兼容层） ----------
class Prefs:
    """轻量替代 sqlitedict，使用本地 JSON 文件存储签到状态。"""
    FILE = "prefs.json"

    def __init__(self):
        self._data = {}
        if os.path.exists(self.FILE):
            try:
                with open(self.FILE, "r", encoding="utf-8") as f:
                    self._data = json.load(f)
            except Exception:
                self._data = {}

    def get(self, key, default=None):
        return self._data.get(key, default)

    def put(self, key, value):
        self._data[key] = value

    def getTime(self):
        """返回当天日期字符串，用作"今日是否已签"的标记。"""
        return time.strftime("%Y-%m-%d")

    def save(self):
        try:
            with open(self.FILE, "w", encoding="utf-8") as f:
                json.dump(self._data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning(f"保存偏好失败: {e}")

prefs = Prefs()

# ---------- 全局状态 ----------
IP_LIST = {}        # {proxy: 是否可用}
accounts_list = {}  # {username: password}
hasE = False        # 是否存在错误（用于退出码）

# ---------- 浏览器指纹池 ----------
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
]

# ---------- 工具函数 ----------

def validate_ip_port(ip, port):
    try:
        ip_obj = ipaddress.ip_address(ip)
        if ip_obj.is_multicast or ip_obj.is_unspecified:
            return False
    except ValueError:
        return False
    ip_parts = ip.split('.')
    for part in ip_parts:
        if not 0 <= int(part) <= 255:
            return False
    if not 1 <= int(port) <= 65535:
        return False
    return True

def is_phone_number(username):
    return re.match(r'^1[3-9]\d{9}$', username) is not None

def format_phone_number(phone):
    if len(phone) == 11:
        return f"{phone[:3]}****{phone[-4:]}"
    return phone

def format_username(username):
    if is_phone_number(username):
        return format_phone_number(username)
    n = len(username)
    if n <= 2:
        return username
    hide_rules = {3: 1, 4: 2, 5: 3, 6: 2, 7: 3, 8: 4, 9: 3, 10: 4}
    hide = hide_rules.get(n, 4)
    keep = n - hide
    left = keep // 2
    right = keep - left
    return username[:left] + '*' * hide + username[-right:]

def CDATA(data):
    match = re.search(r'CDATA.*?(.*?)\]\]>', data, re.IGNORECASE | re.UNICODE)
    if match and match.group(1):
        return match.group(1).strip('[]').strip()
    return data.strip()

# ---------- 浏览器工厂 ----------

def create_context(playwright, proxy=None):
    """创建一个抗检测的浏览器上下文。"""
    browser = playwright.chromium.launch(
        headless=True,
        args=[
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
            "--disable-dev-shm-usage",
        ]
    )
    context_kwargs = {
        "user_agent": random.choice(USER_AGENTS),
        "viewport": {"width": 1280, "height": 800},
        "locale": "zh-CN",
    }
    if proxy:
        context_kwargs["proxy"] = {"server": f"http://{proxy}"}

    context = browser.new_context(**context_kwargs)
    # 隐藏 webdriver 特征
    context.add_init_script(
        "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
    )
    return browser, context

# ---------- 加载代理 ----------

def load():
    """从 src/ips.txt 加载代理，使用浏览器做一次真实访问验证。"""
    myset = set()
    try:
        with open("src/ips.txt", "r", encoding="utf-8") as f:
            for line in f:
                ip = line.strip()
                if not ip or ":" not in ip:
                    continue
                new_ip, new_port = ip.split(":", 1)
                if validate_ip_port(new_ip, new_port):
                    myset.add(ip)
    except Exception:
        pass

    if not myset:
        logger.warning("未读取到任何代理，将以直连方式运行")
        return

    logger.info(f"共加载 {len(myset)} 个代理，开始验证连通性...")

    def _check(proxy):
        try:
            with sync_playwright() as pw:
                browser, context = create_context(pw, proxy=proxy)
                page = context.new_page()
                resp = page.goto(
                    "https://bbs.binmt.cc/forum.php?mod=guide&view=hot",
                    wait_until="domcontentloaded",
                    timeout=25000,
                )
                page.close()
                context.close()
                browser.close()
                return proxy, bool(resp and resp.ok)
        except Exception:
            return proxy, False

    successful = []
    for proxy in myset:
        proxy, ok = _check(proxy)
        if ok:
            successful.append(proxy)
            IP_LIST[proxy] = True

    logger.info("可用ip代理:")
    for i, proxy in enumerate(successful, 1):
        logger.info(f"{i}: {proxy}")

# ---------- 签到核心 ----------

def checkIn(user, pwd, ip):
    """使用浏览器完成登录与签到。"""
    global hasE
    logger.info(f"{format_username(user)} 开始签到 (代理: {ip or '直连'})")
    browser = None
    pw = None
    try:
        pw = sync_playwright().start()
        browser, context = create_context(pw, proxy=ip)
        page = context.new_page()

        # 1) 访问登录页，让浏览器执行 WAF JS 挑战
        page.goto(
            "https://bbs.binmt.cc/member.php?mod=logging&action=login&infloat=yes",
            wait_until="domcontentloaded",
            timeout=30000,
        )
        # 等待 WAF 验证通过、页面稳定
        page.wait_for_timeout(2500)

        # 2) 填写并提交登录表单
        page.fill('input[name="username"]', user)
        page.fill('input[name="password"]', pwd)

        submit = page.locator(
            'button[name="loginsubmit"], input[name="loginsubmit"], input[value="登录"]'
        )
        if submit.count() > 0:
            submit.first.click()
        else:
            page.keyboard.press("Enter")

        # 等待登录结果
        page.wait_for_load_state("domcontentloaded", timeout=20000)
        page.wait_for_timeout(1500)

        content = page.content()

        # 3) 登录失败判断
        if "失败" in content or "密码错误" in content:
            if user in accounts_list:
                del accounts_list[user]
            logger.warning(f"{format_username(user)}: 密码错误")
            hasE = True
            return False

        # 4) 进入签到页
        page.goto(
            "https://bbs.binmt.cc/k_misign-sign.html",
            wait_until="domcontentloaded",
            timeout=30000,
        )
        page.wait_for_timeout(1500)
        content = page.content()

        # 5) 已经签到过
        if "已签" in content or "今日已签到" in content:
            if user in accounts_list:
                del accounts_list[user]
            logger.info(f"{format_username(user)} 今日已签")
            prefs.put(user, prefs.getTime())
            return True

        # 6) 点击签到按钮（论坛签名插件常见按钮）
        sign_clicked = False
        for selector in [
            "a.sign-btn",
            "button.sign-btn",
            "input.sign-btn",
            "#JD_sign",
            "a:has-text('签到')",
            "button:has-text('签到')",
        ]:
            try:
                locator = page.locator(selector)
                if locator.count() > 0 and locator.first.is_visible():
                    locator.first.click()
                    sign_clicked = True
                    page.wait_for_timeout(2000)
                    break
            except Exception:
                continue

        if not sign_clicked:
            # 尝试直接访问签到接口
            page.goto(
                "https://bbs.binmt.cc/plugin.php?id=k_misign:sign&operation=qiandao&format=text",
                wait_until="domcontentloaded",
                timeout=20000,
            )
            page.wait_for_timeout(1500)

        content = page.content()
        text = CDATA(content)

        if "已签" in text or "成功" in text:
            logger.info(f"{format_username(user)} 签到成功: {text}")
            prefs.put(user, prefs.getTime())
            return True
        else:
            logger.warning(f"{format_username(user)} 签到结果未知: {text}")
            return False

    except Exception as e:
        logger.warning(f"异常: {format_username(user)} -> {str(e)}")
        if ip:
            IP_LIST[ip] = False
        return False
    finally:
        try:
            if browser:
                browser.close()
            if pw:
                pw.stop()
        except Exception:
            pass

# ---------- 主流程 ----------

def start():
    global hasE
    ACCOUNTS = os.environ.get("ACCOUNTS", "")
    if not ACCOUNTS:
        logger.warning("环境变量 ACCOUNTS 未设置（格式：用户名:密码，多行）")
        sys.exit(1)

    for duo in ACCOUNTS.split("\n"):
        if ":" not in duo:
            continue
        username, password = duo.split(":", 1)
        username = username.strip()
        password = password.strip()
        if not username or not password:
            continue
        if prefs.get(username, "") == prefs.getTime():
            logger.info(f"{format_username(username)} 今日已签，跳过")
            continue
        accounts_list[username] = password

    if accounts_list:
        load()

    if not accounts_list:
        logger.info("没有需要签到的账号")
        return

    keys = list(accounts_list.keys())
    total = len(keys)
    logger.info(f"共 {total} 个账号待签到")

    for i, username in enumerate(keys, 1):
        pwd = accounts_list[username]
        # 收集当前可用代理
        proxies = [p for p, ok in IP_LIST.items() if ok]
        if not proxies:
            proxies = [None]  # 无代理时直连

        done = False
        for proxy in proxies:
            try:
                if checkIn(username, pwd, proxy):
                    done = True
                    break
            except Exception as e:
                logger.warning(f"checkIn 异常: {e}")
                continue
        if not done:
            logger.error(f"{format_username(username)} 签到未成功")

        if i < total:
            delay = random.randint(2, 5)
            logger.info(f"等待 {delay}s 后处理下一个账号...")
            time.sleep(delay)

if __name__ == "__main__":
    try:
        start()
    finally:
        prefs.save()
    if hasE:
        sys.exit(1)
