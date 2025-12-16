import streamlit as st
import yfinance as yf
import pandas as pd
import plotly.graph_objects as go
from datetime import datetime

# --- 頁面設定 ---
st.set_page_config(page_title="動態鎖利投資回測系統", layout="wide")

st.title("📊 動態鎖利 (母子基金) 綜合回測系統")
st.markdown("""
本系統提供兩種視角：
1. **單次進出分析**：檢視單筆資金投入後的詳細運作軌跡。
2. **循環戰績分析**：檢視長期重複執行此策略的累積成果。
""")

# --- 側邊欄：參數設定 ---
with st.sidebar:
    st.header("1. 基金代號設定")
    mom_ticker = st.text_input("母基金代號 (穩健型)", value="BND")
    
    st.markdown("---")
    st.write("**子基金 (積極型) - 最多 3 檔**")
    child_tickers_input = []
    c1 = st.text_input("子基金 1 代號", value="QQQ")
    c2 = st.text_input("子基金 2 代號", value="")
    c3 = st.text_input("子基金 3 代號", value="")
    
    if c1: child_tickers_input.append(c1)
    if c2: child_tickers_input.append(c2)
    if c3: child_tickers_input.append(c3)

    st.header("2. 資金投入設定")
    initial_capital = st.number_input("投入本金", value=300000, step=10000)

    st.header("3. 轉申購 (DCA) 規則")
    transfer_amount = st.number_input("每次轉入金額", value=3000, step=1000)
    transfer_days = st.multiselect(
        "每月扣款日",
        options=[1, 6, 11, 16, 21, 26],
        default=[6, 16, 26]
    )

    st.header("4. 停利與日期")
    target_roi_percent = st.number_input("停利目標報酬率 (%)", value=10.0, step=1.0)
    target_roi = target_roi_percent / 100
    
    start_date = st.date_input("開始日期", value=datetime(2020, 1, 1))
    end_date = st.date_input("結束日期", value=datetime.today())

# --- 資料下載與處理 ---
def get_data(tickers, start, end):
    if not tickers: return pd.DataFrame()
    clean_tickers = [t.upper().strip() for t in tickers]
    try:
        raw = yf.download(clean_tickers, start=start, end=end, progress=False, auto_adjust=False)
        if raw.empty: return pd.DataFrame()
        
        target_col = 'Adj Close' if 'Adj Close' in raw.columns else 'Close'
        if target_col not in raw.columns: return pd.DataFrame()
        
        df = raw[target_col]
        if isinstance(df, pd.Series): df = df.to_frame(name=clean_tickers[0])
        return df.ffill().dropna()
    except: return pd.DataFrame()

# --- 邏輯 A: 單次進出 (跑到第一次停利就停) ---
def run_single_simulation(df, mom_tick, child_ticks, capital, t_amt, t_days, target):
    mom_tick = mom_tick.upper().strip()
    child_ticks = [t.upper().strip() for t in child_ticks if t.upper().strip() in df.columns]
    
    mom_units = capital / df[mom_tick].iloc[0]
    child_units = {t: 0.0 for t in child_ticks}
    
    records = []
    triggered = False
    
    for date, row in df.iterrows():
        mom_price = row[mom_tick]
        mom_val = mom_units * mom_price
        
        child_val_total = 0
        child_vals = {}
        for t in child_ticks:
            v = child_units[t] * row[t]
            child_vals[t] = v
            child_val_total += v
            
        total_val = mom_val + child_val_total
        roi = (total_val - capital) / capital
        action = "Hold"
        
        if roi >= target:
            action = "★ Stop Profit"
            triggered = True
            rec = {"Date": date, "Total Value": total_val, "Mom Value": mom_val, "Child Total": child_val_total, "ROI": roi, "Action": action}
            for t in child_ticks: rec[f"Val_{t}"] = child_vals[t]
            records.append(rec)
            break 
            
        if date.day in t_days:
            transferred_any = False
            for t in child_ticks:
                if mom_val >= t_amt:
                    mom_units -= (t_amt / mom_price)
                    mom_val -= t_amt 
                    child_units[t] += (t_amt / row[t])
                    transferred_any = True
                else: break
            if transferred_any: action = "Transfer"

        rec = {"Date": date, "Total Value": total_val, "Mom Value": mom_val, "Child Total": child_val_total, "ROI": roi, "Action": action}
        for t in child_ticks: rec[f"Val_{t}"] = child_vals[t]
        records.append(rec)
        
    return pd.DataFrame(records), triggered

# --- 邏輯 B: 循環回測 (獲利後重置) ---
def run_continuous_simulation(df, mom_tick, child_ticks, capital, t_amt, t_days, target):
    mom_tick = mom_tick.upper().strip()
    child_ticks = [t.upper().strip() for t in child_ticks if t.upper().strip() in df.columns]
    
    mom_units = 0.0
    child_units = {t: 0.0 for t in child_ticks}
    
    records = []
    completed_rounds = []
    is_running = False
    round_start_date = None
    
    for date, row in df.iterrows():
        current_mom_price = row[mom_tick]
        
        if not is_running:
            mom_units = capital / current_mom_price
            child_units = {t: 0.0 for t in child_ticks}
            is_running = True
            round_start_date = date
            records.append({"Date": date, "Total Value": capital, "ROI": 0.0, "Action": "Start", "Round": len(completed_rounds)+1})
            continue 
        
        mom_val = mom_units * current_mom_price
        child_val_total = 0
        for t in child_ticks: child_val_total += child_units[t] * row[t]
        
        total_val = mom_val + child_val_total
        roi = (total_val - capital) / capital
        action = "Hold"
        
        if roi >= target:
            completed_rounds.append({
                "Start Date": round_start_date, "End Date": date,
                "Duration": (date - round_start_date).days,
                "Profit": total_val - capital, "Final ROI": roi
            })
            records.append({"Date": date, "Total Value": total_val, "ROI": roi, "Action": "★ Stop Profit", "Round": len(completed_rounds)})
            is_running = False 
            mom_units = 0
            continue

        if date.day in t_days:
            for t in child_ticks:
                if mom_val >= t_amt:
                    mom_units -= (t_amt / current_mom_price)
                    mom_val -= t_amt 
                    child_units[t] += (t_amt / row[t])
        
        records.append({"Date": date, "Total Value": total_val, "ROI": roi, "Action": action, "Round": len(completed_rounds)+1})
        
    stats = {
        "Total Rounds": len(completed_rounds),
        "Is Running": is_running,
        "Current ROI": roi if is_running else 0.0,
        "Total Profit": sum([r['Profit'] for r in completed_rounds]),
        "Avg Duration": sum([r['Duration'] for r in completed_rounds]) / len(completed_rounds) if completed_rounds else 0
    }
    return pd.DataFrame(records), stats, completed_rounds

# --- 主程式執行區 ---
if st.button("🚀 開始分析", type="primary"):
    if not child_tickers_input:
        st.error("請輸入子基金代號")
    else:
        all_tickers = [mom_ticker] + child_tickers_input
        with st.spinner('數據下載與運算中...'):
            df_data = get_data(all_tickers, start_date, end_date)
            
            if not df_data.empty:
                # 建立分頁 (Tabs)
                tab1, tab2 = st.tabs(["📄 單次進出詳細分析", "🏆 循環鎖利戰績分析"])
                
                # --- Tab 1: 單次邏輯 ---
                with tab1:
                    df_single, is_win = run_single_simulation(
                        df_data, mom_ticker, child_tickers_input, initial_capital, transfer_amount, transfer_days, target_roi
                    )
                    
                    last_row = df_single.iloc[-1]
                    final_roi = last_row['ROI']
                    
                    # 狀態橫幅
                    if is_win:
                        st.success(f"### 🎉 獲利達標 (單次模式) \n於 **{last_row['Date'].strftime('%Y-%m-%d')}** 觸發停利，報酬率 **{final_roi*100:.2f}%**")
                    else:
                        st.info(f"### ⏳ 持續運作中 \n截至 **{last_row['Date'].strftime('%Y-%m-%d')}** 尚未達標，目前報酬率 **{final_roi*100:.2f}%**")
                    
                    # 指標
                    c1, c2, c3, c4 = st.columns(4)
                    c1.metric("進場日期", df_single.iloc[0]['Date'].strftime('%Y-%m-%d'))
                    c2.metric("出場/結算日期", last_row['Date'].strftime('%Y-%m-%d'))
                    c3.metric("最終資產", f"${last_row['Total Value']:,.0f}")
                    c4.metric("ROI", f"{final_roi*100:.2f}%", delta_color="normal" if final_roi>=0 else "inverse")
                    
                    # 圖表
                    fig_s = go.Figure()
                    fig_s.add_trace(go.Scatter(x=df_single['Date'], y=df_single['Total Value'], name='總資產', line=dict(color='#d62728', width=3)))
                    fig_s.add_trace(go.Scatter(x=df_single['Date'], y=df_single['Mom Value'], name='母基金', line=dict(color='#1f77b4', width=1), fill='tozeroy', fillcolor='rgba(31, 119, 180, 0.1)'))
                    fig_s.update_layout(height=400, hovermode="x unified", title="單次資產變化圖")
                    st.plotly_chart(fig_s, use_container_width=True)
                    
                    # 詳細表格
                    with st.expander("查看單次詳細交易數據", expanded=True):
                        st.dataframe(df_single.style.format({"Total Value": "{:,.0f}", "Mom Value": "{:,.0f}", "Child Total": "{:,.0f}", "ROI": "{:.2%}"}))

                # --- Tab 2: 循環邏輯 ---
                with tab2:
                    df_cont, stats, rounds = run_continuous_simulation(
                        df_data, mom_ticker, child_tickers_input, initial_capital, transfer_amount, transfer_days, target_roi
                    )
                    
                    # 戰績看板
                    st.markdown("### 🏆 策略戰績總覽")
                    k1, k2, k3, k4 = st.columns(4)
                    k1.metric("累積成功出場", f"{stats['Total Rounds']} 次")
                    k2.metric("平均每一趟歷時", f"{stats['Avg Duration']:.1f} 天")
                    k3.metric("累積獲利金額", f"${stats['Total Profit']:,.0f}")
                    
                    # 修正「跑版」問題：將較長的文字說明移到 help 或下方
                    current_status_label = "運作中" if stats['Is Running'] else "等待進場"
                    current_roi_display = f"{stats['Current ROI']*100:.2f}%" if stats['Is Running'] else "-"
                    k4.metric("目前狀態", current_status_label, delta=current_roi_display)
                    
                    if stats['Is Running']:
                        st.caption(f"目前位於第 {stats['Total Rounds'] + 1} 輪循環中")

                    # 圖表 (鋸齒狀)
                    fig_c = go.Figure()
                    fig_c.add_trace(go.Scatter(x=df_cont['Date'], y=df_cont['Total Value'], name='資產價值', line=dict(color='#2ca02c', width=2)))
                    exits = df_cont[df_cont['Action'] == '★ Stop Profit']
                    fig_c.add_trace(go.Scatter(x=exits['Date'], y=exits['Total Value'], mode='markers', name='停利點', marker=dict(size=10, color='red', symbol='star')))
                    fig_c.add_hline(y=initial_capital, line_dash="dash", line_color="gray", annotation_text="本金線")
                    fig_c.update_layout(height=450, hovermode="x unified", title=f"循環獲利示意圖 (累積獲利: ${stats['Total Profit']:,.0f})")
                    st.plotly_chart(fig_c, use_container_width=True)
                    
                    # 回合列表
                    if rounds:
                        st.markdown("### 📋 成功出場紀錄")
                        r_df = pd.DataFrame(rounds)
                        r_df['Start Date'] = r_df['Start Date'].dt.date
                        r_df['End Date'] = r_df['End Date'].dt.date
                        r_df['Final ROI'] = r_df['Final ROI'].apply(lambda x: f"{x*100:.2f}%")
                        r_df['Profit'] = r_df['Profit'].apply(lambda x: f"${x:,.0f}")
                        st.table(r_df)
                    else:
                        st.warning("尚未有成功出場紀錄")
            else:
                st.error("無法取得數據")
