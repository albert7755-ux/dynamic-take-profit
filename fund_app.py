import streamlit as st
import yfinance as yf
import pandas as pd
import plotly.graph_objects as go
from datetime import datetime, timedelta

# --- 頁面設定 ---
st.set_page_config(page_title="動態鎖利投資回測系統", layout="wide")

st.title("📊 動態鎖利 (母子基金) 循環回測系統")
st.markdown("""
**系統說明：**
* 此系統模擬 **「獲利達標後，將獲利收回，本金重新投入」** 的循環機制。
* **台股**請加 `.TW` (如 `0050.TW`)，**美股**直接輸入代號 (如 `QQQ`, `SPY`)。
""")

# --- 側邊欄：參數設定 ---
with st.sidebar:
    st.header("1. 基金代號設定")
    
    # 母基金
    mom_ticker = st.text_input("母基金代號 (穩健型)", value="BND", help="例如: BND (總體債券), SHV (短債)")
    
    # 子基金 (支援最多3檔)
    st.markdown("---")
    st.write("**子基金 (積極型) - 最多 3 檔**")
    child_tickers_input = []
    c1 = st.text_input("子基金 1 代號", value="QQQ")
    c2 = st.text_input("子基金 2 代號 (選填)", value="")
    c3 = st.text_input("子基金 3 代號 (選填)", value="")
    
    if c1: child_tickers_input.append(c1)
    if c2: child_tickers_input.append(c2)
    if c3: child_tickers_input.append(c3)

    st.header("2. 資金投入設定")
    # 移除手續費欄位
    initial_capital = st.number_input("每輪投入本金", value=300000, step=10000)

    st.header("3. 轉申購 (DCA) 規則")
    transfer_amount = st.number_input("「每檔」子基金每次轉入金額", value=3000, step=1000)
    
    transfer_days = st.multiselect(
        "每月扣款日 (可複選)",
        options=[1, 6, 11, 16, 21, 26],
        default=[6, 16, 26]
    )

    st.header("4. 停利與日期")
    target_roi_percent = st.number_input("停利目標報酬率 (%)", value=10.0, step=1.0)
    target_roi = target_roi_percent / 100
    
    start_date = st.date_input("回測開始日期", value=datetime(2020, 1, 1))
    end_date = st.date_input("回測結束日期", value=datetime.today())

# --- 核心邏輯函數 ---
def get_data(tickers, start, end):
    """下載數據並清理"""
    if not tickers:
        return pd.DataFrame()
    
    clean_tickers = [t.upper().strip() for t in tickers]
    
    try:
        raw_data = yf.download(clean_tickers, start=start, end=end, progress=False, auto_adjust=False)
        
        if raw_data.empty:
            st.error(f"⚠️ 下載數據為空！請檢查代號 {clean_tickers} 是否正確。")
            return pd.DataFrame()

        target_col = 'Adj Close'
        if target_col not in raw_data.columns:
            if 'Close' in raw_data.columns:
                target_col = 'Close'
            else:
                st.error("⚠️ 找不到價格欄位。")
                return pd.DataFrame()

        df_prices = raw_data[target_col]

        if isinstance(df_prices, pd.Series):
            df_prices = df_prices.to_frame(name=clean_tickers[0])
            
        return df_prices.ffill().dropna()

    except Exception as e:
        st.error(f"數據下載發生錯誤: {e}")
        return pd.DataFrame()

def run_continuous_simulation(df, mom_tick, child_ticks, capital, t_amt, t_days, target):
    # 確保代號大寫
    mom_tick = mom_tick.upper().strip()
    child_ticks = [t.upper().strip() for t in child_ticks if t.upper().strip() in df.columns]

    if mom_tick not in df.columns:
        st.error("錯誤: 母基金數據缺失。")
        return pd.DataFrame(), {}, []

    # --- 初始化狀態變數 ---
    mom_units = 0.0
    child_units = {t: 0.0 for t in child_ticks}
    
    records = []
    completed_rounds = [] # 紀錄每一輪獲利的詳細資訊
    
    # 控制變數
    is_running = False # 是否在場內
    round_start_date = None
    
    # 遍歷每一天
    for date, row in df.iterrows():
        current_mom_price = row[mom_tick]
        
        # 1. 如果不在場內 (剛開始 or 剛停利完)，執行進場
        if not is_running:
            # 全額買入母基金
            mom_units = capital / current_mom_price
            child_units = {t: 0.0 for t in child_ticks}
            is_running = True
            round_start_date = date
            
            # 紀錄進場當下狀態
            rec = {
                "Date": date,
                "Total Value": capital,
                "Mom Value": capital,
                "Child Total": 0,
                "ROI": 0.0,
                "Action": "Start/Restart",
                "Round": len(completed_rounds) + 1
            }
            records.append(rec)
            continue # 進場當天不執行扣款
        
        # 2. 計算當前市值
        mom_val = mom_units * current_mom_price
        child_val_total = 0
        child_vals = {}
        for t in child_ticks:
            v = child_units[t] * row[t]
            child_vals[t] = v
            child_val_total += v
            
        total_val = mom_val + child_val_total
        roi = (total_val - capital) / capital
        
        action = "Hold"
        
        # 3. 檢查停利
        if roi >= target:
            action = "★ Stop Profit"
            
            # 紀錄這一輪的戰績
            round_duration = (date - round_start_date).days
            completed_rounds.append({
                "Start Date": round_start_date,
                "End Date": date,
                "Duration (Days)": round_duration,
                "Final ROI": roi,
                "Profit": total_val - capital
            })
            
            # 紀錄數據後，準備重置
            rec = {
                "Date": date,
                "Total Value": total_val,
                "Mom Value": mom_val,
                "Child Total": child_val_total,
                "ROI": roi,
                "Action": action,
                "Round": len(completed_rounds) # 這是第幾輪結束
            }
            records.append(rec)
            
            # 重置狀態 (下一次迴圈會重新進場)
            is_running = False 
            mom_units = 0
            child_units = {t: 0.0 for t in child_ticks}
            continue

        # 4. 轉申購 (DCA)
        if date.day in t_days:
            transferred_any = False
            for t in child_ticks:
                if mom_val >= t_amt:
                    units_out = t_amt / current_mom_price
                    mom_units -= units_out
                    mom_val -= t_amt 
                    
                    units_in = t_amt / row[t]
                    child_units[t] += units_in
                    transferred_any = True
                else:
                    action = "Insufficient Funds"
                    break
            if transferred_any:
                action = "Transfer"

        # 紀錄每日狀態
        rec = {
            "Date": date,
            "Total Value": total_val,
            "Mom Value": mom_val,
            "Child Total": child_val_total,
            "ROI": roi,
            "Action": action,
            "Round": len(completed_rounds) + 1
        }
        for t in child_ticks: rec[f"Val_{t}"] = child_vals[t]
        records.append(rec)
        
    # 統計數據
    stats = {
        "Total Rounds": len(completed_rounds),
        "Is Running": is_running,
        "Current ROI": roi if is_running else 0.0,
        "Total Profit Generated": sum([r['Profit'] for r in completed_rounds]),
        "Avg Duration": sum([r['Duration (Days)'] for r in completed_rounds]) / len(completed_rounds) if completed_rounds else 0
    }
    
    return pd.DataFrame(records), stats, completed_rounds

# --- 主程式 ---
if st.button("🚀 開始循環回測", type="primary"):
    if not child_tickers_input:
        st.error("請至少輸入一檔子基金代號！")
    else:
        all_tickers = [mom_ticker] + child_tickers_input
        
        with st.spinner('正在計算多次循環回測...'):
            df_data = get_data(all_tickers, start_date, end_date)
            
            if not df_data.empty:
                res_df, stats, rounds_detail = run_continuous_simulation(
                    df_data, mom_ticker, child_tickers_input,
                    initial_capital, transfer_amount, transfer_days, target_roi
                )
                
                if res_df.empty:
                    st.error("數據不足。")
                else:
                    # --- 1. 戰績看板 ---
                    st.markdown("### 🏆 策略戰績總覽")
                    
                    # 計算勝率 (嚴格來說，此策略只要結算就是贏，所以看已結算場次)
                    win_count = stats['Total Rounds']
                    total_profit = stats['Total Profit Generated']
                    
                    col1, col2, col3, col4 = st.columns(4)
                    col1.metric("累積成功出場次數", f"{win_count} 次")
                    col2.metric("平均每一趟歷時", f"{stats['Avg Duration']:.1f} 天")
                    col3.metric("累積獲利金額", f"${total_profit:,.0f}")
                    
                    # 狀態判定
                    status_text = "等待進場"
                    if stats['Is Running']:
                        status_text = f"第 {win_count + 1} 輪運作中 (ROI: {stats['Current ROI']*100:.2f}%)"
                    col4.metric("目前狀態", status_text)

                    st.info(f"💡 **勝率說明**：基於動態鎖利機制，所有「已結算」的場次勝率皆為 **100%**。目前策略累計執行了 **{win_count}** 次完整的獲利循環。")

                    st.markdown("---")

                    # --- 2. 互動圖表 (鋸齒狀獲利圖) ---
                    st.subheader("📈 資產淨值走勢 (獲利出場即重置)")
                    fig = go.Figure()
                    
                    # 總資產
                    fig.add_trace(go.Scatter(x=res_df['Date'], y=res_df['Total Value'], 
                                             name='資產價值', line=dict(color='#2ca02c', width=2)))
                    
                    # 標記停利點
                    exits = res_df[res_df['Action'] == '★ Stop Profit']
                    fig.add_trace(go.Scatter(
                        x=exits['Date'], y=exits['Total Value'],
                        mode='markers', name='停利出場點',
                        marker=dict(size=10, color='red', symbol='star')
                    ))

                    # 畫出本金線
                    fig.add_hline(y=initial_capital, line_dash="dash", line_color="gray", annotation_text="本金線")

                    fig.update_layout(height=500, hovermode="x unified", title=f"本金 ${initial_capital:,.0f} 循環投資示意圖")
                    st.plotly_chart(fig, use_container_width=True)

                    # --- 3. 詳細回合列表 ---
                    if rounds_detail:
                        st.subheader("📋 成功出場紀錄表")
                        rounds_df = pd.DataFrame(rounds_detail)
                        rounds_df['Start Date'] = rounds_df['Start Date'].dt.date
                        rounds_df['End Date'] = rounds_df['End Date'].dt.date
                        rounds_df['Final ROI'] = rounds_df['Final ROI'].apply(lambda x: f"{x*100:.2f}%")
                        rounds_df['Profit'] = rounds_df['Profit'].apply(lambda x: f"${x:,.0f}")
                        
                        st.table(rounds_df)
                    else:
                        st.warning("在此期間內尚未有任何一次成功停利出場的紀錄。")

            else:
                st.error("無法下載數據。")
