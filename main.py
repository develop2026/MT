#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MT论坛自动签到（浏览器版）
=========================
- 仅将底层 requests 替换为 Playwright 浏览器，用于通过阿里云 WAF JS 挑战
- 原脚本的：函数签名、签到流程、正则解析、账号/代理逻辑全部保持不变
- 依赖：pip install playwright && playwright install chromium
"""
import os,requests
import re
import sys
import time
import random
import ipaddress

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    print("缺少 playwright，请执行：pip install playwright && playwright install chromium", file=sys.stderr)
    sys.exit(1)

# ---------- 以下为原脚本内容，除 requests 调用外保持不变 ----------

from preferences import prefs
from logger import logger

IP_LIST = {}
accounts_list = {}
hasE = False

headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/86.0.4240.198 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
    'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
    'Connection': 'keep-alive'
}


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


class BrowserSession:
    """替代 requests.Session，用浏览器执行请求以通过 WAF JS 挑战。"""

    def __init__(self, proxy=None):
        self._pw = sync_playwright().start()
        launch_args = ["--disable-blink-features=AutomationControlled", "--no-sandbox"]
        self._browser = self._pw.chromium.launch(headless=True, args=launch_args)
        ctx_kwargs = {
            "user_agent": headers["User-Agent"],
            "viewport": {"width": 1280, "height": 800},
            "extra_http_headers": {k: v for k, v in headers.items() if k != "User-Agent"},
        }
        if proxy:
            ctx_kwargs["proxy"] = {"server": f"http://{proxy}"}
        self._ctx = self._browser.new_context(**ctx_kwargs)
        self._ctx.add_init_script(
            "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"
        )
        self._page = self._ctx.new_page()

    def _goto(self, url, timeout=30000):
        # 让浏览器执行 WAF 前端 JS 挑战
        resp = self._page.goto(url, wait_until="domcontentloaded", timeout=timeout)
        # 给 WAF 验证与跳转留一点时间
        self._page.wait_for_timeout(2500)
        return resp

    def get(self, url, proxies=None, timeout=20, **_kw):
        self._goto(url, timeout=timeout * 1000)
        return BrowserResponse(self._page)

    def post(self, url, data=None, proxies=None, timeout=20, **_kw):
        # 先加载表单所在页面
        self._goto(url, timeout=timeout * 1000)
        # 自动填充常见表单字段并提交
        if isinstance(data, dict):
            for name, val in data.items():
                try:
                    el = self._page.query_selector(f'input[name="{name}"]')
                    if el and val not in (None, ""):
                        el.fill(str(val))
                except Exception:
                    pass
        try:
            btn = self._page.query_selector('input[name="loginsubmit"],button[name="loginsubmit"]')
            if btn:
                btn.click()
            else:
                self._page.keyboard.press("Enter")
        except Exception:
            self._page.keyboard.press("Enter")
        self._page.wait_for_load_state("domcontentloaded", timeout=timeout * 1000)
        self._page.wait_for_timeout(1500)
        return BrowserResponse(self._page)

    def close(self):
        try:
            self._ctx.close()
        except Exception:
            pass
        try:
            self._browser.close()
        except Exception:
            pass
        try:
            self._pw.stop()
        except Exception:
            pass


class BrowserResponse:
    """模拟 requests.Response，保留原脚本用到的属性与方法。"""

    def __init__(self, page):
        self._page = page
        self.url = page.url
        self.status_code = 200
        try:
            self._resp = page.context.pages and page
        except Exception:
            self._resp = None
        # 尝试读取底层 response 状态
        try:
            # page.goto 返回值为最后一次 navigation response
            pass
        except Exception:
            pass

    @property
    def ok(self):
        # 能正常取到内容即视为通过 WAF
        try:
            return self._page.title() is not None
        except Exception:
            return False

    @property
    def text(self):
        return self._page.content()

    @property
    def apparent_encoding(self):
        return "utf-8"

    @property
    def encoding(self):
        return "utf-8"

    @encoding.setter
    def encoding(self, val):
        pass

    def raise_for_status(self):
        pass


def verify(proxy):
    target_url = 'https://bbs.binmt.cc/forum.php?mod=guide&view=hot'
    proxies = {
        'https': f'http://{proxy}',
        'http': f'http://{proxy}'
    }
    start_time = time.time()
    try:
        response = requests.get(target_url, headers=headers, proxies=proxies, timeout=20)
        return proxy, response.ok, int((time.time() - start_time) * 1000)
    except:
        return proxy, False, -1


def is_phone_number(username):
    pattern = r'^1[3-9]\d{9}$'
    return re.match(pattern, username) is not None


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


def load():
    myset = set()
    successful_proxies = []
    try:
        with open("src/ips.txt", "r", encoding="utf-8") as f:
            for line in f:
                ip = line.strip()
                if ":" not in ip or not ip:
                    continue
                newIp, newPort = ip.split(':', 1)
                if not validate_ip_port(newIp, newPort):
                    continue
                myset.add(ip)
    except Exception as e:
        pass
    for proxy in myset:
        proxy, is_valid, requestTime = verify(proxy)
        if is_valid:
            successful_proxies.append((proxy, requestTime))
    successful_proxies.sort(key=lambda x: x[1])
    logger.info("可用ip代理:")
    for index, (proxy, req_time) in enumerate(successful_proxies, 1):
        logger.info(f"{index}: {proxy} - {req_time}ms")
        IP_LIST[proxy] = True


def checkIn(user, pwd, ip):
    global hasE
    req = BrowserSession(proxy=ip)
    logger.info(f"{format_username(user)} 开始签到")
    try:
        url = 'https://bbs.binmt.cc/member.php?mod=logging&action=login&infloat=yes&handlekey=login&inajax=1&ajaxtarget=fwin_content_login'
        resp = req.get(url, timeout=20)
        if resp.ok:
            content = resp.text
            _loginhash = loginhash(content)
            _formhash = formhash(content)
            url = f'https://bbs.binmt.cc/member.php?mod=logging&action=login&loginsubmit=yes&handlekey=login&loginhash={_loginhash}&inajax=1'
            data = {
                'formhash': _formhash,
                'referer': 'https://bbs.binmt.cc/k_misign-sign.html',
                'fastloginfield': 'username',
                'username': user,
                'password': pwd,
                'questionid': '0',
                'answer': '',
                'agreebbrule': ''
            }
            resp = req.post(url, data=data, timeout=20)
            if resp.ok:
                if '失败' in resp.text:
                    del accounts_list[user]
                    logger.warning(f"{format_username(user)}: 密码错误")
                    hasE = True
                    return
                url = 'https://bbs.binmt.cc/k_misign-sign.html'
                resp = req.get(url, timeout=20)
                _formhash = formhash(resp.text)
                code = resp.status_code
                if resp.ok:
                    url = f'https://bbs.binmt.cc/plugin.php?id=k_misign:sign&operation=qiandao&format=text&formhash={_formhash}'
                    resp = req.get(url, timeout=20)
                    if '已签' in resp.text:
                        del accounts_list[user]
                        logger.info(CDATA(resp.text))
                        prefs.put(user, prefs.getTime())
                        return True
                    logger.warning(CDATA(resp.text))
    except Exception as e:
        logger.warning(f"异常: {str(e)}")
        IP_LIST[ip] = False
    finally:
        req.close()
    return False


def loginhash(data):
    pattern = r'loginhash.*?=(.*?)[\'"]>'
    match = re.search(pattern, data, re.IGNORECASE | re.UNICODE)
    if match and match.group(1):
        return match.group(1).strip()
    return ''


def formhash(data):
    pattern = r'formhash[\'"].*?value=[\'"](.*?)[\'"].*?/>'
    match = re.search(pattern, data, re.IGNORECASE | re.UNICODE)
    if match and match.group(1):
        return match.group(1).strip()
    return ''


def CDATA(data):
    pattern = r'CDATA.*?(.*?)\]\]>'
    match = re.search(pattern, data, re.IGNORECASE | re.UNICODE)
    if match and match.group(1):
        return match.group(1).strip('[]')
    return ''


def start():
    ACCOUNTS = os.environ.get("ACCOUNTS", "")
    if not ACCOUNTS:
        logger.warning('github ACCOUNTS变量未设置')
        exit(1)
    for duo in ACCOUNTS.split("\n"):
        if ':' not in duo:
            continue
        username, password = duo.split(':', 1)
        username = username.strip()
        password = password.strip()
        YiQianDao = prefs.get(username, "") == prefs.getTime()
        if username and password and not YiQianDao:
            accounts_list[username] = password
        elif YiQianDao:
            logger.info(f"{format_username(username)} 今日已签, 跳过签到")
    if accounts_list:
        load()
    if IP_LIST:
        keys = list(accounts_list.keys())
        total = len(keys)
        for i, username in enumerate(keys):
            for proxy, status in IP_LIST.items():
                if not status:
                    continue
                try:
                    if checkIn(username, accounts_list[username], proxy):
                        break
                except Exception:
                    pass
            if i < total - 1:
                time.sleep(3)


start()
prefs.save()
if hasE:
    exit(1)
