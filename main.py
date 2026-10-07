import json
import re
import os
import time
import random
import ipaddress

from concurrent.futures import ThreadPoolExecutor, as_completed
from requests_html import HTMLSession

from preferences import prefs
from logger import logger

IP_LIST = {}
accounts_list = {}
hasE = False

headers = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Connection": "keep-alive",
}


def validate_ip_port(ip, port):
    try:
        ip_obj = ipaddress.ip_address(ip)
        if ip_obj.is_multicast or ip_obj.is_unspecified:
            return False
    except ValueError:
        return False
    ip_parts = ip.split(".")
    for part in ip_parts:
        if not 0 <= int(part) <= 255:
            return False
    if not 1 <= int(port) <= 65535:
        return False
    return True


def verify(proxy):
    """
    验证代理是否可用（使用 requests-html）
    """
    session = HTMLSession()
    session.headers.update(headers)
    session.proxies = {
        "http": f"http://{proxy}",
        "https": f"http://{proxy}",
    }

    target_url = "https://bbs.binmt.cc/forum.php?mod=guide&view=hot"
    start_time = time.time()
    try:
        resp = session.get(target_url, timeout=20)
        elapsed = int((time.time() - start_time) * 1000)
        return proxy, resp.ok, elapsed
    except Exception:
        return proxy, False, -1
    finally:
        session.close()


def is_phone_number(username):
    pattern = r"^1[3-9]\d{9}$"
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
    hide_rules = {3:1,4:2,5:3,6:2,7:3,8:4,9:3,10:4}
    hide = hide_rules.get(n, 4)
    keep = n - hide
    left = keep // 2
    right = keep - left
    return username[:left] + "*" * hide + username[-right:]


def load():
    myset = set()
    successful_proxies = []

    try:
        with open("src/ips.txt", "r", encoding="utf-8") as f:
            for line in f:
                ip = line.strip()
                if ":" not in ip or not ip:
                    continue
                new_ip, new_port = ip.split(":", 1)
                if not validate_ip_port(new_ip, new_port):
                    continue
                myset.add(ip)
    except Exception:
        pass

    with ThreadPoolExecutor(max_workers=30) as executor:
        futures = [executor.submit(verify, proxy) for proxy in myset]
        for future in as_completed(futures):
            proxy, is_valid, request_time = future.result()
            if is_valid:
                successful_proxies.append((proxy, request_time))

    successful_proxies.sort(key=lambda x: x[1])

    logger.info("可用 ip 代理:")
    for index, (proxy, req_time) in enumerate(successful_proxies, 1):
        logger.info(f"{index}: {proxy} - {req_time}ms")
        IP_LIST[proxy] = True


def loginhash(data):
    pattern = r"loginhash.*?=([^'\"&>]+)"
    match = re.search(pattern, data, re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return ""


def formhash(data):
    pattern = r"formhash['\"]?\s*value=['\"]([^'\"]+)['\"]"
    match = re.search(pattern, data, re.IGNORECASE)
    if not match:
        pattern = r"formhash=([a-z0-9]+)"
        match = re.search(pattern, data, re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return ""


def CDATA(data):
    pattern = r"CDATA\[(.*?)\]>"
    match = re.search(pattern, data, re.IGNORECASE | re.DOTALL)
    if match:
        return match.group(1).strip()
    return data.strip()


def checkIn(user, pwd, ip):
    global hasE, headers
    headers.pop("Cookie", None)

    session = HTMLSession()
    session.headers.update(headers)
    session.proxies = {
        "http": f"http://{ip}",
        "https": f"http://{ip}",
    }
    logger.info(f"{format_username(user)} 开始签到")

    try:
        # ★ 关键：先访问首页，让 Discuz 种基础 cookie
        base = session.get("https://bbs.binmt.cc/", timeout=20)
        base.encoding = base.apparent_encoding
        cookies = session.cookies.get_dict()
        headers["Cookie"] = cookies
        session.headers.update(headers)
        
        # （可选）如果你发现 JS 还会补 cookie，可以 render
        # base.html.render(timeout=20, sleep=1)

        # 1. 获取登录浮层
        url = (
            "https://bbs.binmt.cc/member.php?mod=logging"
            "&action=login&infloat=yes&handlekey=login"
            "&inajax=1&ajaxtarget=fwin_content_login"
        )
        resp = session.get(url, timeout=20)
        resp.encoding = resp.apparent_encoding

        if not resp.ok:
            session.close()
            return False

        _loginhash = loginhash(resp.text)
        _formhash = formhash(resp.text)

        if not _formhash:
            logger.warning(f"{format_username(user)} 未获取到 formhash")
            session.close()
            return False
        print(_loginhash)
        print(_formhash)
        print(resp.text)
        return True
        """
        # 2. 登录
        url = (
            "https://bbs.binmt.cc/member.php?mod=logging"
            f"&action=login&loginsubmit=yes&handlekey=login"
            f"&loginhash={_loginhash}&inajax=1"
        )
        data = {
            "formhash": _formhash,
            "referer": "https://bbs.binmt.cc/",
            "fastloginfield": "username",
            "username": user,
            "password": pwd,
            "questionid": "0",
            "answer": "",
            "agreebbrule": "",
        }

        resp = session.post(url, data=data, timeout=20)
        resp.encoding = resp.apparent_encoding

        if "密码错误" in resp.text or "登录失败" in resp.text:
            del accounts_list[user]
            logger.warning(f"{format_username(user)}: 登录失败/密码错误")
            hasE = True
            session.close()
            return False

        # 3. 签到页
        resp = session.get(
            "https://bbs.binmt.cc/k_misign-sign.html",
            timeout=20
        )
        resp.encoding = resp.apparent_encoding

        _formhash = formhash(resp.text)
        if not _formhash:
            logger.warning(f"{format_username(user)} 签到页未获取到 formhash")
            session.close()
            return False

        # 4. 签到
        url = (
            "https://bbs.binmt.cc/plugin.php?id=k_misign:sign"
            f"&operation=qiandao&format=text&formhash={_formhash}"
        )
        resp = session.get(url, timeout=20)
        resp.encoding = resp.apparent_encoding

        text = resp.text

        if "已签" in text:
            logger.info(f"{format_username(user)} 今日已签")
            del accounts_list[user]
            prefs.put(user, prefs.getTime())
            session.close()
            return True

        if "成功" in text:
            logger.info(f"{format_username(user)} 签到成功: {CDATA(text)}")
            prefs.put(user, prefs.getTime())
            session.close()
            return True

        logger.warning(f"{format_username(user)} 签到失败: {CDATA(text)}")
        session.close()
        return False
        """
    except Exception as e:
        logger.warning(f"{format_username(user)} 异常: {e}")
        IP_LIST[ip] = False
        try:
            session.close()
        except Exception:
            pass
        return False


def start():
    ACCOUNTS = os.environ.get("ACCOUNTS", "")
    if not ACCOUNTS:
        logger.warning("github ACCOUNTS 变量未设置")
        exit(1)

    for duo in ACCOUNTS.split("\n"):
        if ":" not in duo:
            continue
        username, password = duo.split(":", 1)
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


if __name__ == "__main__":
    start()
    prefs.save()
    if hasE:
        exit(1)