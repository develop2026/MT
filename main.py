import os
import re
import time
import ipaddress
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from logger import logger
from playwright.sync_api import sync_playwright


# ========= 全局 =========
IP_LIST = {}

headers = {
    "User-Agent": "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Mobile Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Connection": "keep-alive"
}


# ========= IP 校验 =========
def validate_ip_port(ip, port):
    try:
        ip_obj = ipaddress.ip_address(ip)
        if ip_obj.is_multicast or ip_obj.is_unspecified:
            return False
    except ValueError:
        return False

    for part in ip.split("."):
        if not (0 <= int(part) <= 255):
            return False

    return 1 <= int(port) <= 65535


# ========= 验证代理 =========
def verify(proxy):
    target_url = "https://bbs.binmt.cc/forum.php?mod=guide&view=hot"
    proxies = {
        "http": f"http://{proxy}",
        "https": f"http://{proxy}"
    }
    start = time.time()
    try:
        r = requests.get(target_url, headers=headers, proxies=proxies, timeout=20)
        return proxy, r.ok, int((time.time() - start) * 1000)
    except Exception:
        return proxy, False, -1


# ========= 加载并验证代理 =========
def load_proxies():
    myset = set()
    try:
        with open("src/ips.txt", "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if ":" not in line:
                    continue
                ip, port = line.split(":", 1)
                if validate_ip_port(ip, port):
                    myset.add(line)
    except Exception as e:
        logger.error(f"读取代理失败: {e}")

    valid_proxies = []
    with ThreadPoolExecutor(max_workers=30) as executor:
        futures = [executor.submit(verify, p) for p in myset]
        for future in as_completed(futures):
            proxy, ok, delay = future.result()
            if ok:
                valid_proxies.append((proxy, delay))

    valid_proxies.sort(key=lambda x: x[1])
    for proxy, delay in valid_proxies:
        logger.info(f"可用代理: {proxy} - {delay}ms")
        IP_LIST[proxy] = True


# ========= 手机模式浏览器访问登录页 =========
def browser_test(proxy):
    proxy_url = f"http://{proxy}"
    login_url = "https://bbs.binmt.cc/member.php?mod=logging&action=login"

    with sync_playwright() as p:
        logger.info("启动手机模式浏览器")
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage"
            ]
        )

        # ✅ 手机模式
        context = browser.new_context(
            proxy={"server": proxy_url},
            user_agent="Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Mobile Safari/537.36",
            viewport={"width": 412, "height": 915},
            device_scale_factor=2.625,
            is_mobile=True,
            has_touch=True,
            locale="zh-CN",
            timezone_id="Asia/Shanghai"
        )

        # 去 webdriver 标记
        context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
        """)

        page = context.new_page()

        try:
            logger.info(f"[{proxy}] 访问登录页")
            page.goto(login_url, timeout=30000, wait_until="networkidle")
            page.wait_for_timeout(3000)

            logger.info(f"[{proxy}] 当前URL: {page.url}")
            logger.info(f"[{proxy}] 页面标题: {page.title()}")

            # 截图方便调试
            page.screenshot(path=f"login_{proxy.replace(':', '_')}.png")
            logger.info("截图完成")
            return True
        except Exception as e:
            logger.error(f"[{proxy}] 异常: {e}")
            return False
        finally:
            browser.close()


# ========= 主流程 =========
def start():
    load_proxies()
    if not IP_LIST:
        logger.warning("没有可用代理")
        return

    for proxy, status in IP_LIST.items():
        if not status: continue
        try:
            if browser_test(proxy): break
        except Exception as e:
            logger.error(f"{proxy} 测试失败: {e}")


if __name__ == "__main__":
    start()
