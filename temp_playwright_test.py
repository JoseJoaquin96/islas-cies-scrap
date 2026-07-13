from check_availability import go_to_calendar
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    context = browser.new_context(locale='gl-ES', timezone_id='Europe/Madrid', viewport={'width':1366,'height':900}, user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36')
    context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
    page = context.new_page()
    try:
        go_to_calendar(page, debug=True)
        print('GO TO CALENDAR OK')
    except Exception as e:
        print('GO TO CALENDAR ERROR', e)
    finally:
        context.close()
        browser.close()
