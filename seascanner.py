import time
import re
import os
import json
import hashlib
import requests
import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from bs4 import BeautifulSoup

# ================= 設定區 =================
# 優先讀取 GitHub 環境變數
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL")

# 設定價格區間 (英鎊)
TARGET_PRICE_MAX = 500  # 使用者需求：500英鎊以下
TARGET_PRICE_MIN = 50

# 記憶檔案名稱
HISTORY_FILE = "history_seascanner.json"
# =========================================

def load_history():
    """讀取歷史紀錄"""
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except:
            return []
    return []

def save_history(history_data):
    """儲存歷史紀錄"""
    try:
        with open(HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(history_data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"❌ 儲存歷史紀錄失敗: {e}")

def get_unique_id(price, date, ship):
    """產生唯一識別碼"""
    raw_str = f"{price}-{date}-{ship}"
    return hashlib.md5(raw_str.encode()).hexdigest()

def send_discord_notify(message_text):
    if not DISCORD_WEBHOOK_URL:
        print("❌ 未設定 Webhook URL，跳過發送。")
        return

    try:
        data = {"content": message_text, "username": "Seascanner 特價獵人"}
        requests.post(DISCORD_WEBHOOK_URL, json=data)
        print("✅ Discord 通知已發送！")
        time.sleep(1) # 避免太快被 Discord 擋
    except Exception as e:
        print(f"❌ 發送錯誤: {e}")

def clean_port_name(text):
    """強力清洗港口名稱"""
    text = re.sub(r"Departure|Arrival|View full itinerary", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\b(s\s+)?Ship\s+[^\-–]+\s*[-–]\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"[-–:]", "", text)
    text = re.sub(r"\d+\s*stops?", "", text, flags=re.IGNORECASE)
    text = re.sub(r"^\s*s\s*$", "", text)
    return text.strip()

def scrape_seascanner(history):
    print(f"🚀 啟動 Seascanner 爬蟲 (門檻 < £{TARGET_PRICE_MAX})...")

    options = uc.ChromeOptions()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--lang=en-GB")

    driver = None
    new_items_count = 0

    try:
        # 移除 version_main，讓 uc 自動適應 GitHub Actions 的 Chrome
        driver = uc.Chrome(options=options)
        
        # 針對日本航線
        url = "https://www.seascanner.co.uk/destinations/far-east-cruises/japan-cruises"
        
        driver.get(url)
        print(f"⏳ 前往頁面，等待載入...")
        time.sleep(8)

        # 移除 Cookie 遮擋
        try:
            driver.execute_script("""
                var blockers = document.querySelectorAll("[id*='cookie'], [class*='cookie'], [id*='consent'], [class*='consent']");
                blockers.forEach(el => el.remove());
                var backdrop = document.querySelector(".fpw_backdrop");
                if(backdrop) backdrop.remove();
            """)
        except:
            pass

        # 滾動載入
        print("📜 滾動載入更多行程...")
        no_change_count = 0
        last_height = driver.execute_script("return document.body.scrollHeight")

        # 稍微減少滾動次數以加快執行速度，若行程很多可自行增加 range
        for i in range(15):
            driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            time.sleep(2)
            buttons = driver.find_elements(By.XPATH, "//button[contains(., 'Show more') or contains(., 'Load more')]")
            
            clicked = False
            for btn in buttons:
                try:
                    if btn.is_displayed():
                        driver.execute_script("arguments[0].click();", btn)
                        clicked = True
                        time.sleep(3)
                        break
                except:
                    continue

            new_height = driver.execute_script("return document.body.scrollHeight")
            if new_height == last_height and not clicked:
                no_change_count += 1
                if no_change_count >= 3:
                    break
            else:
                no_change_count = 0
                last_height = new_height

        # 解析
        print("🔍 解析資料中...")
        soup = BeautifulSoup(driver.page_source, "html.parser")
        for s in soup(["script", "style"]):
            s.decompose()

        duration_labels = soup.find_all(string=re.compile("Duration"))
        print(f"🔍 掃描到 {len(duration_labels)} 張卡片...")

        for label in duration_labels:
            card = label.parent
            for _ in range(4):
                if card.parent:
                    card = card.parent

            full_text = card.get_text(" ", strip=True)

            # --- 價格提取 ---
            prices = []
            for m in re.finditer(r"£\s*(\d{1,5})", full_text):
                prices.append(int(m.group(1)))

            if not prices:
                continue
            
            best_price = min(prices)
            
            # 價格過濾
            if best_price < TARGET_PRICE_MIN or best_price > TARGET_PRICE_MAX:
                continue

            # --- 資訊提取 ---
            ship_name = "MSC Cruise"
            ship_match = re.search(r"Ship\s+(.*?)(?=\s*Departure|\s*•)", full_text, re.IGNORECASE)
            if ship_match:
                ship_name = ship_match.group(1).strip()

            date_info = "未知日期"
            date_match = re.search(r"Date\s+(.*?202\d)", full_text, re.IGNORECASE)
            if date_match:
                date_info = date_match.group(1).strip()

            duration = ""
            dur_match = re.search(r"Duration\s*(\d+\s*nights?)", full_text, re.IGNORECASE)
            if dur_match:
                duration = dur_match.group(1)

            departure = "未知"
            dep_block_match = re.search(r"Departure(.*?)(?=Arrival|View)", full_text, re.IGNORECASE)
            if dep_block_match:
                departure = clean_port_name(dep_block_match.group(1))

            arrival = "未知"
            arr_block_match = re.search(r"Arrival(.*?)(?=£|From|Price)", full_text, re.IGNORECASE)
            if arr_block_match:
                arrival = clean_port_name(arr_block_match.group(1))

            # --- 檢查重複 ---
            unique_id = get_unique_id(best_price, date_info, ship_name)

            if unique_id in history:
                print(f"   😴 跳過已通知: £{best_price} | {date_info}")
                continue

            # --- 發送通知 ---
            print(f"   🔔 新發現: £{best_price} | {ship_name} | {date_info}")
            
            msg = (
                f"**🇯🇵 Seascanner 特價快報**\n"
                f"💰 **價格**: £{best_price}\n"
                f"🛳️ **船名**: {ship_name}\n"
                f"📅 **日期**: {date_info} ({duration})\n"
                f"🛫 **出發**: {departure}\n"
                f"🛬 **抵達**: {arrival}\n"
                f"🔗 [點擊預訂]({url})"
            )
            
            send_discord_notify(msg)
            
            # 加入歷史紀錄
            history.append(unique_id)
            new_items_count += 1

    except Exception as e:
        print(f"❌ 錯誤: {e}")
    finally:
        if driver:
            try:
                driver.quit()
            except:
                pass
    
    return new_items_count

if __name__ == "__main__":
    current_history = load_history()
    print(f"📖 目前已記錄 {len(current_history)} 筆 Seascanner 歷史資料")

    new_count = scrape_seascanner(current_history)

    if new_count > 0:
        save_history(current_history)
        print(f"💾 已更新歷史紀錄檔案 (新增 {new_count} 筆)")
    else:
        print("💤 本次沒有新行程。")
