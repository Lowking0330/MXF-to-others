import os
import time
import datetime
import tempfile
import subprocess
import sqlite3
import streamlit as st
import pandas as pd

# ==========================================
# 資料庫初始化與日誌功能 (零依賴，使用內建 sqlite3)
# ==========================================
DB_FILE = "logs.db"

def init_db():
    """初始化 SQLite 資料庫，若不存在則建立資料表"""
    conn = sqlite3.connect(DB_FILE, check_same_thread=False)
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS usage_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            username TEXT,
            filename TEXT,
            mode TEXT,
            process_time REAL
        )
    ''')
    conn.commit()
    conn.close()

def log_activity(username: str, filename: str, mode: str, process_time: float):
    """寫入轉檔紀錄至資料庫"""
    conn = sqlite3.connect(DB_FILE, check_same_thread=False)
    c = conn.cursor()
    # 轉換為台北時間 (UTC+8) 儲存
    tw_time = (datetime.datetime.utcnow() + datetime.timedelta(hours=8)).strftime('%Y-%m-%d %H:%M:%S')
    c.execute('''
        INSERT INTO usage_logs (timestamp, username, filename, mode, process_time)
        VALUES (?, ?, ?, ?, ?)
    ''', (tw_time, username, filename, mode, process_time))
    conn.commit()
    conn.close()

def fetch_logs() -> pd.DataFrame:
    """讀取所有日誌並回傳為 DataFrame"""
    conn = sqlite3.connect(DB_FILE, check_same_thread=False)
    df = pd.read_sql_query("SELECT * FROM usage_logs ORDER BY id DESC", conn)
    conn.close()
    return df

# 確保啟動時資料庫已準備就緒
init_db()

# ==========================================
# 工具路徑設定 (支援本地 .exe 或系統環境變數)
# ==========================================
DIR = os.path.dirname(os.path.abspath(__file__))
FFMPEG = os.path.join(DIR, "ffmpeg.exe") if os.path.exists(os.path.join(DIR, "ffmpeg.exe")) else "ffmpeg"
FFPROBE = os.path.join(DIR, "ffprobe.exe") if os.path.exists(os.path.join(DIR, "ffprobe.exe")) else "ffprobe"

# ==========================================
# 核心輔助函式
# ==========================================
def get_duration(file_path: str) -> float:
    """使用 ffprobe 取得影片/音檔總秒數"""
    cmd = [
        FFPROBE, '-v', 'error', '-show_entries', 'format=duration', 
        '-of', 'default=noprint_wrappers=1:nokey=1', file_path
    ]
    try:
        return float(subprocess.check_output(cmd, shell=False).decode().strip())
    except Exception:
        return 0.0

def format_hms(sec: float) -> str:
    """將秒數格式化為 HH:MM:SS"""
    return str(datetime.timedelta(seconds=int(sec)))

def run_task_streamlit(cmd: list, total_sec: float, label: str, progress_bar, status_text) -> float:
    """執行 FFmpeg 並即時更新 Streamlit 進度條"""
    cmd_full = cmd + ['-progress', 'pipe:1', '-nostats', '-loglevel', 'quiet']
    process = subprocess.Popen(
        cmd_full, 
        stdout=subprocess.PIPE, 
        stderr=subprocess.STDOUT, 
        universal_newlines=True, 
        encoding='utf-8', 
        errors='ignore'
    )

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
    
    progress_bar.progress(1.0)
    status_text.text(f"-> {label} | 100% [完成] 耗時: {format_hms(t_cost)}")
    return t_cost

# ==========================================
# 頁面與全域狀態初始化
# ==========================================
st.set_page_config(page_title="族語影像自動化產線", page_icon="🚀", layout="wide")

if "converted_files" not in st.session_state:
    st.session_state.converted_files = []
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
    if not st.session_state.admin_logged_in:
        admin_pwd = st.text_input("請輸入管理員密碼", type="password")
        if st.button("登入"):
            # 預設密碼設為 admin123，您可自行修改
            if admin_pwd == "admin123":
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
# 主頁面：使用 Tabs 分離功能
# ==========================================
tab1, tab2 = st.tabs(["🎥 轉檔工具", "📊 後台管理 (需登入)"])

with tab1:
    st.markdown("### 🚀 族語影像自動化產線")
    st.info("Embiyax su hug? | Nga'ay ho | lokah su ga' | Inabayan")

    # 使用者資訊
    user_name = st.text_input("👤 使用者名稱 (選填，用於紀錄是誰轉檔的)", placeholder="例如：王小明 / 企劃部", disabled=st.session_state.is_converting)
    final_username = user_name.strip() if user_name.strip() else "匿名使用者"

    # 檔案上傳區
    uploaded_files = st.file_uploader(
        "請選擇 MXF 檔案 (可多選)", 
        type=["mxf"], 
        accept_multiple_files=True,
        disabled=st.session_state.is_converting
    )

    # 轉檔模式設定
    mode_options = {
        "1. 僅轉 MP4": [('MP4', '.mp4', ['-c:v', 'libx264', '-preset', 'veryfast', '-crf', '22', '-c:a', 'aac', '-b:a', '192k'])],
        "2. 僅轉 WAV": [('WAV', '.wav', ['-vn', '-c:a', 'pcm_s16le', '-ar', '44100'])],
        "3. 僅轉 MP3": [('MP3', '.mp3', ['-vn', '-c:a', 'libmp3lame', '-b:a', '192k'])],
        "4. 全部都轉 (MP4 + WAV)": [
            ('MP4', '.mp4', ['-c:v', 'libx264', '-preset', 'veryfast', '-crf', '22', '-c:a', 'aac', '-b:a', '192k']),
            ('WAV', '.wav', ['-vn', '-c:a', 'pcm_s16le', '-ar', '44100'])
        ]
    }

    selected_mode = st.radio(
        "請選擇處理模式:", 
        options=list(mode_options.keys()),
        disabled=st.session_state.is_converting
    )

    # 轉檔執行區塊
    if st.button("開始批次處理", disabled=st.session_state.is_converting):
        if not uploaded_files:
            st.warning("❌ 請先上傳至少一個 MXF 檔案！")
        else:
            st.session_state.is_converting = True
            st.session_state.converted_files = []
            st.session_state.report_data = []
            
            total_start = time.time()
            
            with tempfile.TemporaryDirectory() as temp_dir:
                for idx, uploaded_file in enumerate(uploaded_files):
                    st.markdown(f"**📦 [{idx+1}/{len(uploaded_files)}] 正在處理: {uploaded_file.name}**")
                    
                    progress_bar = st.progress(0)
                    status_text = st.empty()
                    
                    input_path = os.path.join(temp_dir, uploaded_file.name)
                    with open(input_path, "wb") as f:
                        f.write(uploaded_file.getbuffer())
                    
                    dur = get_duration(input_path)
                    base_name = os.path.splitext(uploaded_file.name)[0]
                    
                    file_start = time.time()
                    
                    for label, ext, params in mode_options[selected_mode]:
                        output_filename = f"{base_name}{ext}"
                        output_path = os.path.join(temp_dir, output_filename)
                        
                        cmd = [FFMPEG, '-y', '-i', input_path] + params + [output_path]
                        run_task_streamlit(cmd, dur, label, progress_bar, status_text)
                        
                        if os.path.exists(output_path):
                            with open(output_path, "rb") as f:
                                file_bytes = f.read()
                            
                            mime_type = "video/mp4" if ext == ".mp4" else f"audio/{ext.replace('.', '')}"
                            st.session_state.converted_files.append({
                                "name": output_filename,
                                "data": file_bytes,
                                "mime": mime_type
                            })
                    
                    file_cost = time.time() - file_start
                    st.session_state.report_data.append((uploaded_file.name, file_cost))
                    
                    # 寫入資料庫日誌
                    log_activity(final_username, uploaded_file.name, selected_mode, file_cost)
                    
                    st.divider()
                
                st.session_state.total_cost = time.time() - total_start
            
            st.session_state.is_converting = False
            st.rerun()

    # 報表與下載區塊
    if st.session_state.report_data:
        st.success("✨ Mhuway su balay！批次處理報告已完成")
        
        report_md = "### 📊 處理報表\n"
        for name, cost in st.session_state.report_data:
            report_md += f"- **{name}** | 耗時: `{format_hms(cost)}`\n"
        report_md += f"\n🌟 **總計處理檔案:** `{len(st.session_state.report_data)}` 個 | **總花費時間:** `{format_hms(st.session_state.total_cost)}`"
        st.markdown(report_md)
        
        st.markdown("### ⬇️ 檔案下載區")
        for file_info in st.session_state.converted_files:
            st.download_button(
                label=f"下載 {file_info['name']}",
                data=file_info["data"],
                file_name=file_info["name"],
                mime=file_info["mime"],
                key=f"dl_{file_info['name']}"
            )

with tab2:
    if not st.session_state.admin_logged_in:
        st.warning("⚠️ 請先從左側邊欄輸入密碼登入，以查看後台管理數據。")
    else:
        st.markdown("### 📊 系統使用日誌")
        st.write("這裡記錄了所有使用者的轉檔歷史。")
        
        # 讀取並顯示資料庫內容
        logs_df = fetch_logs()
        
        if logs_df.empty:
            st.info("目前尚無任何轉檔紀錄。")
        else:
            # 格式化顯示欄位
            logs_df.rename(columns={
                "id": "編號",
                "timestamp": "時間 (UTC+8)",
                "username": "使用者名稱",
                "filename": "檔案名稱",
                "mode": "轉檔模式",
                "process_time": "耗時 (秒)"
            }, inplace=True)
            
            # 將耗時轉為小數點後兩位
            logs_df["耗時 (秒)"] = logs_df["耗時 (秒)"].round(2)
            
            st.dataframe(logs_df, use_container_width=True, hide_index=True)
            
            # 提供 CSV 下載功能
            csv_data = logs_df.to_csv(index=False).encode('utf-8-sig')
            st.download_button(
                label="📥 匯出日誌為 CSV",
                data=csv_data,
                file_name="usage_logs.csv",
                mime="text/csv"
            )
