import os
import time
import datetime
import subprocess
import sqlite3
import shutil
import streamlit as st
import pandas as pd

# ==========================================
# 系統常數與資料夾設定 (本地直讀架構)
# ==========================================
DIR = os.path.dirname(os.path.abspath(__file__))
INPUT_DIR = os.path.join(DIR, "input_mxf")
OUTPUT_DIR = os.path.join(DIR, "output_files")
DB_FILE = os.path.join(DIR, "logs.db")

# 確保資料夾存在
os.makedirs(INPUT_DIR, exist_ok=True)
os.makedirs(OUTPUT_DIR, exist_ok=True)

# 工具路徑設定
FFMPEG = os.path.join(DIR, "ffmpeg.exe") if os.path.exists(os.path.join(DIR, "ffmpeg.exe")) else "ffmpeg"
FFPROBE = os.path.join(DIR, "ffprobe.exe") if os.path.exists(os.path.join(DIR, "ffprobe.exe")) else "ffprobe"

# ==========================================
# 資料庫初始化與日誌功能
# ==========================================
def init_db():
    try:
        conn = sqlite3.connect(DB_FILE, timeout=10.0, check_same_thread=False)
        c = conn.cursor()
        c.execute('''
            CREATE TABLE IF NOT EXISTS usage_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                username TEXT,
                filename TEXT,
                mode TEXT,
                size_before_mb REAL,
                size_after_mb REAL,
                output_path TEXT,
                process_time REAL
            )
        ''')
        conn.commit()
    except sqlite3.Error as e:
        st.error(f"資料庫初始化失敗: {e}")
    finally:
        if 'conn' in locals():
            conn.close()

def log_activity(username, filename, mode, size_before, size_after, output_path, process_time):
    try:
        conn = sqlite3.connect(DB_FILE, timeout=10.0, check_same_thread=False)
        c = conn.cursor()
        tw_time = (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=8)).strftime('%Y-%m-%d %H:%M:%S')
        c.execute('''
            INSERT INTO usage_logs (timestamp, username, filename, mode, size_before_mb, size_after_mb, output_path, process_time)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (tw_time, username, filename, mode, size_before, size_after, output_path, process_time))
        conn.commit()
    except sqlite3.Error as e:
        st.error(f"寫入日誌失敗: {e}")
    finally:
        if 'conn' in locals():
            conn.close()

def fetch_logs() -> pd.DataFrame:
    try:
        conn = sqlite3.connect(DB_FILE, timeout=10.0, check_same_thread=False)
        df = pd.read_sql_query("SELECT * FROM usage_logs ORDER BY id DESC", conn)
        return df
    except sqlite3.Error as e:
        st.error(f"讀取日誌失敗: {e}")
        return pd.DataFrame()
    finally:
        if 'conn' in locals():
            conn.close()

init_db()

# ==========================================
# 核心輔助函式
# ==========================================
def get_file_size_mb(file_path: str) -> float:
    if os.path.exists(file_path):
        return round(os.path.getsize(file_path) / (1024 * 1024), 2)
    return 0.0

def get_duration(file_path: str) -> float:
    cmd = [FFPROBE, '-v', 'error', '-show_entries', 'format=duration', '-of', 'default=noprint_wrappers=1:nokey=1', file_path]
    try:
        return float(subprocess.check_output(cmd, shell=False).decode().strip())
    except Exception:
        return 0.0

def format_hms(sec: float) -> str:
    return str(datetime.timedelta(seconds=int(sec)))

def get_free_space_gb(folder_path: str) -> float:
    """取得指定資料夾所在硬碟的剩餘空間 (GB)"""
    total, used, free = shutil.disk_usage(folder_path)
    return round(free / (1024**3), 2)

def run_task_streamlit(cmd: list, total_sec: float, label: str, progress_bar, status_text) -> float:
    cmd_full = cmd + ['-progress', 'pipe:1', '-nostats', '-loglevel', 'quiet']
    try:
        process = subprocess.Popen(cmd_full, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True, encoding='utf-8', errors='ignore')
    except FileNotFoundError:
        st.error("系統找不到 FFmpeg，請確認已安裝。")
        return 0.0

    start_t = time.time()
    while True:
        line = process.stdout.readline()
        if not line and process.poll() is not None:
            break
        if 'out_time_us=' in line:
            try:
                cur_s = int(line.split('=')[1]) / 1000000.0
                if total_sec > 0:
                    pct = min(cur_s / total_sec, 1.0)
                    elapsed = time.time() - start_t
                    eta = (elapsed / pct) - elapsed if pct > 0.02 else 0
                    progress_bar.progress(pct)
                    status_text.text(f"-> {label} | {int(pct*100):>3}% [剩餘 {format_hms(eta)}]")
            except Exception:
                pass
                
    process.wait()
    t_cost = time.time() - start_t
    
    if process.returncode != 0:
        st.error(f"轉檔過程發生異常，FFmpeg 退出碼: {process.returncode}")
    else:
        progress_bar.progress(1.0)
        status_text.text(f"-> {label} | 100% [完成] 耗時: {format_hms(t_cost)}")
        
    return t_cost

# ==========================================
# 頁面與全域狀態初始化
# ==========================================
st.set_page_config(page_title="族語影像自動化產線", page_icon="🚀", layout="wide")

# 修正 2：注入 CSS 強制將全域字體放大至 18px
st.markdown("""
    <style>
    /* 強制將一般文字、標籤、按鈕、清單的字體放大至 18px */
    html, body, p, label, span, button, li {
        font-size: 18px !important;
    }
    /* 排除標題，讓標題保持原本的大字體 */
    h1, h2, h3 {
        font-size: revert !important;
    }
    </style>
""", unsafe_allow_html=True)

if "report_data" not in st.session_state:
    st.session_state.report_data = []
if "total_cost" not in st.session_state:
    st.session_state.total_cost = 0.0
if "is_converting" not in st.session_state:
    st.session_state.is_converting = False
if "admin_logged_in" not in st.session_state:
    st.session_state.admin_logged_in = False

# ==========================================
# 側邊欄：管理者登入
# ==========================================
with st.sidebar:
    st.header("🔒 管理者登入")
    try:
        CORRECT_PASSWORD = st.secrets["admin_password"]
    except KeyError:
        st.error("未設定管理員密碼！請在 `.streamlit/secrets.toml` 中設定。")
        CORRECT_PASSWORD = None

    if not st.session_state.admin_logged_in:
        admin_pwd = st.text_input("請輸入管理員密碼", type="password", disabled=(CORRECT_PASSWORD is None))
        if st.button("登入", disabled=(CORRECT_PASSWORD is None)):
            if admin_pwd == CORRECT_PASSWORD:
                st.session_state.admin_logged_in = True
                st.success("登入成功！")
                st.rerun()
            else:
                st.error("密碼錯誤！")
    else:
        st.success("✅ 管理員已登入")
        if st.button("登出"):
            st.session_state.admin_logged_in = False
            st.rerun()

# ==========================================
# 主頁面：轉檔工具
# ==========================================
tab1, tab2 = st.tabs(["🎥 轉檔產線 (本地端)", "📊 後台管理 (需登入)"])

with tab1:
    st.markdown("### 🚀 族語影像自動化產線 (本地直讀版)")
    
    free_space = get_free_space_gb(OUTPUT_DIR)
    col_info1, col_info2 = st.columns([3, 1])
    with col_info1:
        st.info(f"📂 **輸入資料夾：** `{INPUT_DIR}`\n\n📂 **輸出資料夾：** `{OUTPUT_DIR}`\n\n💾 **輸出磁碟剩餘空間：** `{free_space} GB`")
    with col_info2:
        st.write("")
        # 修正 1：改用 subprocess 強制呼叫 Windows 檔案總管，並加入提示
        if st.button("📂 打開輸出資料夾", use_container_width=True, help="點擊後會在 Windows 檔案總管中彈出轉檔完成的資料夾"):
            try:
                subprocess.Popen(['explorer', OUTPUT_DIR])
                st.toast("✅ 已為您開啟輸出資料夾！")
            except Exception as e:
                st.error("無法開啟資料夾，此功能僅支援 Windows 系統。")

    user_name = st.text_input("👤 使用者名稱 (必填，用於紀錄是誰轉檔的)", placeholder="例如：王小明 / 企劃部", disabled=st.session_state.is_converting)
    
    available_files = [f for f in os.listdir(INPUT_DIR) if f.lower().endswith('.mxf')]
    
    col1, col2 = st.columns([4, 1])
    with col1:
        selected_files = st.multiselect(
            "請選擇要處理的 MXF 檔案 (最多 10 個)", 
            options=available_files,
            max_selections=10,
            disabled=st.session_state.is_converting,
            help="為確保系統穩定，每次批次處理上限為 10 個檔案。"
        )
    with col2:
        st.write("")
        st.write("")
        if st.button("🔄 重新整理清單", disabled=st.session_state.is_converting):
            st.rerun()

    skip_existing = st.checkbox("⏭️ 若輸出檔案已存在，則自動跳過 (節省時間)", value=True, disabled=st.session_state.is_converting)

    mode_options = {
        "1. 僅轉 MP4 (標準影像)": [('MP4', '.mp4', ['-c:v', 'libx264', '-preset', 'veryfast', '-crf', '22', '-c:a', 'aac', '-b:a', '192k'])],
        "2. 僅轉 WAV (無損音訊)": [('WAV', '.wav', ['-vn', '-c:a', 'pcm_s16le', '-ar', '44100'])],
        "3. 僅轉 MP3 (壓縮音訊)": [('MP3', '.mp3', ['-vn', '-c:a', 'libmp3lame', '-b:a', '192k'])],
        "4. 全部都轉 (MP4 + WAV + MP3)": [
            ('MP4', '.mp4', ['-c:v', 'libx264', '-preset', 'veryfast', '-crf', '22', '-c:a', 'aac', '-b:a', '192k']),
            ('WAV', '.wav', ['-vn', '-c:a', 'pcm_s16le', '-ar', '44100']),
            ('MP3', '.mp3', ['-vn', '-c:a', 'libmp3lame', '-b:a', '192k'])
        ]
    }

    selected_mode = st.radio("請選擇處理模式:", options=list(mode_options.keys()), disabled=st.session_state.is_converting)

    if st.button("🚀 開始批次處理", type="primary", disabled=st.session_state.is_converting):
        if not user_name.strip():
            st.warning("❌ 請先輸入「使用者名稱」以便紀錄！")
        elif not selected_files:
            st.warning("❌ 請先從上方清單選擇至少一個 MXF 檔案！")
        else:
            st.session_state.is_converting = True
            st.session_state.report_data = []
            total_start = time.time()
            
            for idx, filename in enumerate(selected_files):
                st.markdown(f"**📦 [{idx+1}/{len(selected_files)}] 正在處理: {filename}**")
                
                input_path = os.path.join(INPUT_DIR, filename)
                size_before = get_file_size_mb(input_path)
                dur = get_duration(input_path)
                base_name = os.path.splitext(filename)[0]
                
                file_start = time.time()
                
                for label, ext, params in mode_options[selected_mode]:
                    output_filename = f"{base_name}{ext}"
                    output_path = os.path.join(OUTPUT_DIR, output_filename)
                    
                    if skip_existing and os.path.exists(output_path):
                        st.info(f"⏭️ 已跳過 (檔案已存在): {output_filename}")
                        continue

                    progress_bar = st.progress(0)
                    status_text = st.empty()
                    
                    format_start = time.time()
                    
                    cmd = [FFMPEG, '-y', '-i', input_path] + params + [output_path]
                    run_task_streamlit(cmd, dur, label, progress_bar, status_text)
                    
                    format_cost = time.time() - format_start
                    size_after = get_file_size_mb(output_path)
                    
                    log_activity(user_name.strip(), output_filename, selected_mode, size_before, size_after, output_path, format_cost)
                
                st.session_state.report_data.append((filename, time.time() - file_start))
                st.divider()
            
            st.session_state.total_cost = time.time() - total_start
            st.session_state.is_converting = False
            st.rerun()

    if st.session_state.report_data:
        st.success(f"✨ Mhuway su balay！批次處理完成。檔案已存入 `{OUTPUT_DIR}`")
        report_md = "### 📊 本次處理報表\n"
        for name, cost in st.session_state.report_data:
            report_md += f"- **{name}** | 總耗時: `{format_hms(cost)}`\n"
        report_md += f"\n🌟 **總計處理 MXF 檔案:** `{len(st.session_state.report_data)}` 個 | **總花費時間:** `{format_hms(st.session_state.total_cost)}`"
        st.markdown(report_md)

# ==========================================
# 後台管理：日誌與 CSV 匯出
# ==========================================
with tab2:
    if not st.session_state.admin_logged_in:
        st.warning("⚠️ 請先從左側邊欄輸入密碼登入，以查看後台管理數據。")
    else:
        st.markdown("### 📊 系統使用日誌 (完整紀錄)")
        logs_df = fetch_logs()
        
        if logs_df.empty:
            st.info("目前尚無任何轉檔紀錄。")
        else:
            logs_df.rename(columns={
                "id": "編號",
                "timestamp": "時間 (UTC+8)",
                "username": "使用者",
                "filename": "產出檔名",
                "mode": "轉檔模式",
                "size_before_mb": "原始大小(MB)",
                "size_after_mb": "產出大小(MB)",
                "output_path": "檔案儲存路徑",
                "process_time": "耗時(秒)"
            }, inplace=True)
            
            logs_df["耗時(秒)"] = logs_df["耗時(秒)"].round(2)
            
            st.dataframe(logs_df, width="stretch", hide_index=True)
            
            csv_data = logs_df.to_csv(index=False).encode('utf-8-sig')
            
            # 修正 3：動態產生包含日期時間的 CSV 檔名
            current_time_str = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            dynamic_csv_filename = f"mxf_conversion_logs_{current_time_str}.csv"
            
            st.download_button(
                label="📥 匯出完整日誌為 CSV", 
                data=csv_data, 
                file_name=dynamic_csv_filename, 
                mime="text/csv"
            )
