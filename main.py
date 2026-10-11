from playwright.sync_api import sync_playwright
from logger import logger

def browser_checkin():
    """
    最小测试：只访问登录页（手机模式，无代理）
    """
    login_url = "https://bbs.binmt.cc/member.php?mod=logging&action=login"

    with sync_playwright() as p:
        logger.info("[STEP 1] 启动浏览器（手机模式）")
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage"
            ]
        )

        logger.info("[STEP 2] 创建手机上下文")
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Mobile Safari/537.36",
            viewport={"width": 412, "height": 915},
            device_scale_factor=2.625,
            is_mobile=True,
            has_touch=True,
            locale="zh-CN",
            timezone_id="Asia/Shanghai"
        )

        # 反爬
        context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
            window.chrome = {runtime: {}};
        """)

        page = context.new_page()

        try:
            logger.info("[STEP 3] 访问登录页")
            page.goto(login_url, timeout=30000, wait_until="networkidle")
            page.wait_for_timeout(3000)

            logger.info(f"[STEP 4] 当前URL: {page.url}")
            logger.info(f"[STEP 4] 页面标题: {page.title()}")

            # 截图确认
            page.screenshot(path="login_page.png")
            logger.info("[STEP 5] 已截图 login_page.png")

        except Exception as e:
            logger.error(f"[ERROR] 异常: {str(e)}")
            return False, str(e)
        finally:
            browser.close()

    return True, "访问登录页成功"

browser_checkin()