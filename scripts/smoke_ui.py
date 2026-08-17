"""M3 前端页面冒烟测试：工具箱 / 片库 / 通知抽屉渲染 + 控制台错误收集。"""
import asyncio
import sys
from playwright.async_api import async_playwright

BASE = "http://127.0.0.1:8301"


async def main():
    errors = []
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        page = await browser.new_page(viewport={"width": 1440, "height": 900})
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: errors.append(str(e)))

        await page.goto(BASE, wait_until="networkidle")

        # 工具箱页
        await page.click('[data-page="tools"]')
        await page.wait_for_timeout(1200)
        await page.screenshot(path="docs/screenshots/v1.1-tools.png")
        chips = await page.eval_on_selector_all(".tool-chip", "els => els.map(e => e.textContent)")
        print("工具 chips:", chips)
        src = await page.eval_on_selector_all("#tools-source-list .rp-item", "els => els.length")
        print("视频素材条数:", src)

        # 选一个素材 + 提交一个截帧任务
        if src:
            await page.click("#tools-source-list .rp-item")
            await page.click('.tool-chip[data-k="frame"]')
            await page.click("#btn-tool-run")
            await page.wait_for_timeout(3500)
            jobs = await page.eval_on_selector_all("#tool-jobs .tool-job", "els => els.length")
            print("任务条数:", jobs)
            await page.screenshot(path="docs/screenshots/v1.1-tools-job.png")

        # 片库页：管理栏 + 列表视图
        await page.click('[data-page="history"]')
        await page.wait_for_timeout(1200)
        await page.click("#btn-manage")
        await page.wait_for_timeout(600)
        await page.screenshot(path="docs/screenshots/v1.1-history-manage.png")
        await page.click("#manage-exit")
        # 列表视图（先切到不分组）
        await page.select_option("#history-group", "none")
        await page.wait_for_timeout(800)
        await page.click("#btn-view-toggle")
        await page.wait_for_timeout(900)
        await page.screenshot(path="docs/screenshots/v1.1-history-list.png")

        # 通知抽屉
        await page.click("#bell")
        await page.wait_for_timeout(500)
        await page.screenshot(path="docs/screenshots/v1.1-notif.png")
        await page.click("#notif-mask")  # 关闭抽屉

        # 下载页 sanity
        await page.click('[data-page="download"]')
        await page.wait_for_timeout(600)
        await page.screenshot(path="docs/screenshots/v1.1-download.png")

        await browser.close()

    print("console errors:", errors if errors else "无")
    return 1 if errors else 0


sys.exit(asyncio.run(main()))
