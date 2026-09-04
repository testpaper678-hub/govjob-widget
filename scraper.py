#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
scraper.py
==========
ดึงข้อมูล "ประกาศสอบราชการ" จาก 2 แหล่ง แล้วรวมเป็นไฟล์ jobs.json:
  1. ประกาศผลสอบ.com  (HTML ปกติ ดึงตรงๆ ได้)
  2. job-108.com       (HTML ปกติ ดึงตรงๆ ได้)

หมายเหตุเรื่อง job.ocsc.go.th (เว็บ ก.พ.):
  หน้าเว็บนี้เป็นแอป JavaScript (Single Page App) เนื้อหาทั้งหมดถูกโหลด
  มาทีหลังด้วยโค้ด JS ฝั่งเบราว์เซอร์ ทำให้การดึง HTML แบบธรรมดา (requests)
  จะได้แค่โครงหน้าเปล่าๆ ไม่มีรายการประกาศติดมาด้วย
  วิธีที่จะดึงได้จริงต้องใช้ headless browser (เช่น Playwright/Selenium)
  ซึ่งซับซ้อนกว่านี้มาก และเว็บราชการมักเปลี่ยนโครงสร้างบ่อย จึงยังไม่ใส่ไว้
  ในสคริปต์นี้ — ถ้าต้องการให้เพิ่มในอนาคต แจ้งได้ ผมจะทำ adapter แบบ
  Playwright ให้เพิ่มเป็นอีกไฟล์หนึ่ง (ต้องรันบนเครื่องที่ติดตั้ง browser ได้
  เช่น GitHub Actions ก็รองรับ แต่ setup ซับซ้อนกว่า requests ธรรมดา)

วิธีใช้:
    pip install requests beautifulsoup4
    python scraper.py

ผลลัพธ์: jobs.json
[
  {
    "agency": "...", "position": "...", "amount": "...",
    "apply_date": "...", "link": "...", "source": "..."
  }, ...
]
"""

import json
import re
import time
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; GovJobAggregator/1.0)"
}
TIMEOUT = 15

THAI_MONTHS = (
    "มกราคม|กุมภาพันธ์|มีนาคม|เมษายน|พฤษภาคม|มิถุนายน|"
    "กรกฎาคม|สิงหาคม|กันยายน|ตุลาคม|พฤศจิกายน|ธันวาคม"
)
DATE_RANGE_RE = re.compile(
    r"((?:วันนี้|บัดนี้|[0-9]{1,2})\s*(?:" + THAI_MONTHS + r")?\s*-\s*[0-9]{1,2}\s*(?:" + THAI_MONTHS + r")\s*[0-9]{4})"
)
AMOUNT_RE = re.compile(r"([\d,]+)\s*(?:อัตรา|นาย|ตำแหน่ง)")


def fetch_html(url: str) -> str:
    resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    resp.raise_for_status()
    resp.encoding = resp.apparent_encoding
    return resp.text


def clean_agency_from_title(title: str) -> str:
    """ตัดชื่อหน่วยงานออกจากหัวข้อ (ก่อนคำว่า 'เปิดรับสมัคร' หรือ 'รับสมัคร')"""
    for kw in ("เปิดรับสมัคร", "รับสมัคร"):
        if kw in title:
            return title.split(kw)[0].strip()
    return title.strip()


# ---------------------------------------------------------------------------
# แหล่งที่ 1: ประกาศผลสอบ.com
# ---------------------------------------------------------------------------
def scrape_prakadphonsob(max_pages: int = 5):
    """
    ดึงจากหน้า all-cate-prd.php?cate_id=3 (หมวดงานราชการ)
    max_pages: จำนวนหน้าสูงสุดที่จะดึง (หน้าละ ~20 รายการ)
    """
    base = "https://www.xn--12c4cbf7aots1ayx.com"
    results = []
    seen_ids = set()

    for page in range(1, max_pages + 1):
        url = f"{base}/all-cate-prd.php?cate_id=3"
        if page > 1:
            url += f"&page={page}"

        try:
            html = fetch_html(url)
        except Exception as e:
            print(f"[prakadphonsob] page {page} fetch error: {e}")
            break

        soup = BeautifulSoup(html, "html.parser")
        links = soup.select('a[href*="prd-detail.php?prd_id="]')
        if not links:
            break

        found = 0
        for a in links:
            href = a.get("href", "")
            m = re.search(r"prd_id=(\d+)", href)
            if not m:
                continue
            prd_id = m.group(1)
            title = a.get_text(strip=True)
            if not title or prd_id in seen_ids:
                continue  # ข้ามลิงก์รูปภาพที่ไม่มีข้อความ หรือรายการซ้ำ
            seen_ids.add(prd_id)
            found += 1

            container = a.find_parent(["div", "li", "article"]) or a.parent
            block_text = container.get_text(" ", strip=True) if container else title

            amount_m = AMOUNT_RE.search(title) or AMOUNT_RE.search(block_text)
            amount = amount_m.group(0) if amount_m else ""

            date_m = DATE_RANGE_RE.search(block_text)
            apply_date = date_m.group(0) if date_m else ""

            link = href if href.startswith("http") else base + href

            results.append({
                "agency": clean_agency_from_title(title),
                "position": title,
                "amount": amount,
                "apply_date": apply_date,
                "link": link,
                "source": "ประกาศผลสอบ.com",
            })

        if found == 0:
            break
        time.sleep(1)

    return results


# ---------------------------------------------------------------------------
# แหล่งที่ 2: job-108.com
# ---------------------------------------------------------------------------
def scrape_job108(max_pages: int = 3):
    """
    ดึงจากหน้า now.html (ประกาศล่าสุด) ของ job-108.com
    หมายเหตุ: job-108.com มีวิดเจ็ตฝังทางการอยู่แล้ว (ดู widget-doc.html)
    ที่นี่เขียนเป็น scraper แยกเพื่อให้ข้อมูลออกมาในตารางเดียวกับแหล่งอื่น
    ถ้าต้องการง่ายกว่านี้ ใช้วิดเจ็ตทางการแทนได้ (ดู README)
    """
    base = "https://www.job-108.com"
    results = []
    seen_links = set()

    for page in range(1, max_pages + 1):
        url = f"{base}/now.html" if page == 1 else f"{base}/now-page{page}.html"
        try:
            html = fetch_html(url)
        except Exception as e:
            print(f"[job108] page {page} fetch error: {e}")
            break

        soup = BeautifulSoup(html, "html.parser")
        links = soup.select('a[href*="/id"][href$=".html"]')
        if not links:
            break

        found = 0
        for a in links:
            href = a.get("href", "")
            if href in seen_links:
                continue
            text = a.get_text(" ", strip=True)
            if not text or "id" not in href:
                continue
            seen_links.add(href)
            found += 1

            amount_m = AMOUNT_RE.search(text)
            amount = amount_m.group(0) if amount_m else ""

            date_m = DATE_RANGE_RE.search(text)
            apply_date = date_m.group(0) if date_m else ""

            agency = clean_agency_from_title(text)
            if len(agency) > 80:
                agency = agency[:80].strip()

            link = href if href.startswith("http") else base + href

            results.append({
                "agency": agency,
                "position": text[:200],
                "amount": amount,
                "apply_date": apply_date,
                "link": link,
                "source": "job-108.com",
            })

        if found == 0:
            break
        time.sleep(1)

    return results


def main():
    all_jobs = []

    for scraper_fn in (scrape_prakadphonsob, scrape_job108):
        try:
            jobs = scraper_fn()
            print(f"{scraper_fn.__name__}: ดึงได้ {len(jobs)} รายการ")
            all_jobs.extend(jobs)
        except Exception as e:
            print(f"{scraper_fn.__name__} ล้มเหลว: {e}")
        time.sleep(1)

    output = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "count": len(all_jobs),
        "jobs": all_jobs,
    }

    with open("jobs.json", "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"บันทึก jobs.json แล้ว รวม {len(all_jobs)} รายการ")


if __name__ == "__main__":
    main()
