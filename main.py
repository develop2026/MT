import json, requests, re, os, time, random, ipaddress
from concurrent.futures import ThreadPoolExecutor, as_completed
from preferences import prefs
from logger import logger
from playwright.sync_api import sync_playwright
from playwright.sync_api import expect

def browser_checkin(user, pwd, proxy):
    """
    使用无头浏览器完成登录 + 签到
    返回 (success: bool, msg: str)
    """
    proxy_url = f"http://{proxy}"
    
    # ✅ 不要用 mobile=2，用桌面版
    login_url = "https://bbs.binmt.cc/member.php?mod=logging&action=login"
    target_url = "https://bbs.binmt.cc/forum.php?mod=guide&view=hot"

    with sync_playwright() as p:
        logger.info("[STEP 1] 启动浏览器")
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage"
            ]
        )

        logger.info("[STEP 2] 创建上下文")
        context = browser.new_context(
            proxy={"server": proxy_url},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 800},
            locale="zh-CN",
            timezone_id="Asia/Shanghai"
        )

        # 反爬：去除 webdriver 标记
        context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
            window.chrome = {runtime: {}};
        """)

        logger.info("[STEP 3] 创建页面")
        page = context.new_page()

        try:
            # 1. 先访问目标站，触发 WAF JS 校验
            logger.info("[STEP 4] 访问热门页（触发WAF）")
            page.goto(target_url, timeout=30000, wait_until="networkidle")
            page.wait_for_timeout(3000)  # 等 WAF cookie 写入
            logger.info("[STEP 5] WAF 校验完成")

            # 2. 登录页（✅ 不用 mobile=2）
            logger.info("[STEP 6] 进入登录页")
            page.goto(login_url, timeout=30000, wait_until="networkidle")
            page.wait_for_timeout(2000)

            # 确认页面加载
            logger.info(f"[STEP 7] 当前URL: {page.url}")
            logger.info(f"[STEP 7] 页面标题: {page.title()}")

            # 等待输入框
            logger.info("[STEP 8] 等待用户名输入框")
            page.wait_for_selector("input[name='username']", timeout=15000)
            
            # 清空再输入（防止有默认值）
            page.click("input[name='username']")
            page.fill("input[name='username']", "")
            page.type("input[name='username']", user, delay=100)
            
            page.click("input[name='password']")
            page.fill("input[name='password']", "")
            page.type("input[name='password']", pwd, delay=100)
            
            logger.info("[STEP 9] 账号密码已输入")

            # ✅ 关键修复：用 JS 强制提交（绕过 comiis JS 拦截）
            logger.info("[STEP 10] 执行登录提交")
            page.evaluate("""
                () => {
                    const form = document.querySelector('form');
                    if (form) {
                        // 先触发表单验证
                        const btn = document.querySelector('button[name="submit"]');
                        if (btn) btn.click();
                    }
                }
            """)
            
            # 等待跳转
            logger.info("[STEP 11] 等待登录结果...")
            page.wait_for_timeout(5000)
            
            current_url = page.url
            content = page.content()
            logger.info(f"[STEP 12] 登录后URL: {current_url}")
            
            # 判断是否登录失败
            if "登录" in page.title() or "login" in current_url.lower():
                if "登录失败" in content or "错误" in content:
                    logger.info("[STEP 13] 登录失败：密码错误")
                    return False, "密码错误"
                else:
                    logger.info("[STEP 13] 登录失败：未知原因")
                    # 截图调试
                    page.screenshot(path="login_fail.png")
                    return False, f"登录未成功，当前URL: {current_url}"
            
            logger.info("[STEP 14] 登录成功！")

            # 4. 进入签到页
            logger.info("[STEP 15] 进入签到页")
            page.goto(
                "https://bbs.binmt.cc/k_misign-sign.html",
                timeout=30000,
                wait_until="networkidle"
            )
            page.wait_for_timeout(3000)
            logger.info(f"[STEP 16] 签到页URL: {page.url}")

            # 检查是否需要登录
            if "需要先登录" in page.content():
                logger.info("[STEP 17] Cookie未保持，尝试从页面提取formhash")
                # 尝试从其他页面获取
                page.goto("https://bbs.binmt.cc/forum.php", timeout=30000, wait_until="networkidle")
                page.wait_for_timeout(2000)

            # 5. 提取 formhash
            logger.info("[STEP 18] 提取 formhash")
            formhash = page.evaluate("""
                () => {
                    // 方法1：input
                    let input = document.querySelector('input[name="formhash"]');
                    if (input) return input.value;
                    // 方法2：meta
                    let meta = document.querySelector('meta[name="formhash"]');
                    if (meta) return meta.content;
                    // 方法3：从HTML文本中正则提取
                    let match = document.body.innerHTML.match(/formhash[=:]['"]([^'"]+)['"]/);
                    if (match) return match[1];
                    return "";
                }
            """)
            
            logger.info(f"[STEP 19] formhash: {formhash}")

            if not formhash:
                # 尝试从 cookie 或页面源码中找
                page.screenshot(path="no_formhash.png")
                return False, "未获取到 formhash，已截图"

            # 6. 执行签到请求（用浏览器上下文的 cookie）
            logger.info("[STEP 20] 执行签到")
            sign_resp = page.request.get(
                f"https://bbs.binmt.cc/plugin.php?id=k_misign:sign&operation=qiandao&format=text&formhash={formhash}",
                headers={
                    "Referer": "https://bbs.binmt.cc/k_misign-sign.html",
                    "X-Requested-With": "XMLHttpRequest"
                }
            )

            text = sign_resp.text()
            logger.info(f"[STEP 21] 签到返回: {text[:200]}")

            if "已签" in text or "成功" in text or "签到" in text:
                logger.info("[STEP 22] 签到成功！")
                return True, text
            
            logger.info(f"[STEP 22] 签到返回未知内容: {text[:100]}")
            return False, text

        except Exception as e:
            logger.error(f"[ERROR] 异常: {str(e)}")
            try:
                page.screenshot(path="error.png")
            except:
                pass
            return False, str(e)
        finally:
            browser.close()

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
    hide_rules = {3:1,4:2,5:3,6:2,7:3,8:4,9:3,10:4}
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
                if ":" not in ip or not ip: continue
                newIp, newPort = ip.split(':', 1)
                if not validate_ip_port(newIp, newPort): continue
                myset.add(ip)
    except Exception as e:
        pass
    with ThreadPoolExecutor(max_workers=30) as executor:
        futures = [executor.submit(verify, proxy) for proxy in myset]
        for future in as_completed(futures):
            proxy, is_valid, requestTime = future.result()
            if is_valid:
                successful_proxies.append((proxy, requestTime))
    successful_proxies.sort(key=lambda x: x[1])
    logger.info("可用ip代理:")
    for index, (proxy, req_time) in enumerate(successful_proxies, 1):
        logger.info(f"{index}: {proxy} - {req_time}ms")
        IP_LIST[proxy] = True

def checkIn(user, pwd, ip):
    global hasE
    logger.info(f"{format_username(user)} 开始签到")
    try:
        success, msg = browser_checkin(user, pwd, ip)
        if success:
            logger.info(CDATA(msg))
            prefs.put(user, prefs.getTime())
            return True

        if "密码错误" in msg:
            del accounts_list[user]
            logger.warning(f"{format_username(user)}: 密码错误")
            hasE = True
            return False

        logger.warning(CDATA(msg))
        return False

    except Exception as e:
        logger.warning(f"异常: {str(e)}")
        IP_LIST[ip] = False
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
    pattern = r'CDATA.*?(.*?)]>'
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
                if not status: continue
                try:
                    if checkIn(username, accounts_list[username], proxy): break
                except:
                    pass
            if i < total - 1:
                time.sleep(3)
start()
prefs.save()
if hasE: exit(1)