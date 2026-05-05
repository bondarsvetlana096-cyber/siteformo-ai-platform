import asyncio
from playwright.async_api import async_playwright
from datetime import datetime, timezone
from app.database import supabase
import os
import uuid

SUPABASE_BUCKET = os.getenv("SUPABASE_STORAGE_BUCKET", "siteformo-assets")


async def generate_screenshots(url: str, order_id: str, preview_id: str):
    """
    Делает desktop + mobile screenshots страницы
    и загружает их в Supabase Storage
    """

    async with async_playwright() as p:
        browser = await p.chromium.launch(args=["--no-sandbox"])
        
        # ===== DESKTOP =====
        desktop_context = await browser.new_context(
            viewport={"width": 1440, "height": 900}
        )
        desktop_page = await desktop_context.new_page()

        await desktop_page.goto(url, wait_until="networkidle")
        await asyncio.sleep(2)

        desktop_path = f"/tmp/{uuid.uuid4()}_desktop.png"
        await desktop_page.screenshot(path=desktop_path, full_page=True)

        # ===== MOBILE =====
        iphone = p.devices["iPhone 13"]
        mobile_context = await browser.new_context(**iphone)
        mobile_page = await mobile_context.new_page()

        await mobile_page.goto(url, wait_until="networkidle")
        await asyncio.sleep(2)

        mobile_path = f"/tmp/{uuid.uuid4()}_mobile.png"
        await mobile_page.screenshot(path=mobile_path, full_page=True)

        await browser.close()

    # ===== UPLOAD В SUPABASE =====
    desktop_url = upload_to_supabase(desktop_path, order_id, preview_id, "desktop")
    mobile_url = upload_to_supabase(mobile_path, order_id, preview_id, "mobile")

    # ===== СОХРАНИТЬ В БД =====
    supabase.table("design_previews").update({
        "desktop_image_url": desktop_url,
        "mobile_image_url": mobile_url,
        "screenshots_generated_at": datetime.now(timezone.utc).isoformat()
    }).eq("id", preview_id).execute()

    return {
        "desktop": desktop_url,
        "mobile": mobile_url
    }


def upload_to_supabase(file_path: str, order_id: str, preview_id: str, device: str):
    """
    Загружает файл в Supabase Storage
    """

    file_name = f"{order_id}/{preview_id}_{device}.png"

    with open(file_path, "rb") as f:
        res = supabase.storage.from_(SUPABASE_BUCKET).upload(
            file_name,
            f,
            {"content-type": "image/png"}
        )

    # Получаем публичную ссылку
    public_url = supabase.storage.from_(SUPABASE_BUCKET).get_public_url(file_name)

    return public_url