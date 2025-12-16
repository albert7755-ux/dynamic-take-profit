import streamlit as st
import yfinance as yf
import pandas as pd
import plotly.graph_objects as go
from datetime import datetime, date

# --- 頁面設定 ---
st.set_page_config(page_title="動態鎖利投資回測系統", layout="wide")

# --- 初始化 Session State (確保按鈕點擊後狀態保留) ---
if 'run_analysis' not in st.session_state:
    st.session_state.run_analysis = False
if 'data_cache' not in st.session_state:
    st.session_state.data_cache = pd.DataFrame()

st.title("📊 動態鎖利 (母子基金) 綜合回測系統")
st.markdown("""
本系統提供兩種視角：
1. **單次進出詳細分析**：檢視單筆資金投入後的詳細運作軌跡 (日期由側邊欄設定)。
2. **循環鎖利分析**：檢視長期重複執行此策略的累積成果 (**自動抓取最早可回測日期**)。
""")

# --- 側邊欄：全域與單次設定 ---
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

    st.header("4. 停利設定")
    target_roi_percent = st.number_input("停利目標報酬率 (%)", value=10.0, step=1.0)
    target_roi = target_roi_percent / 100
    
    st.header("5. 單次分析日期 (Tab 1)")
    # 單次分析通常比較短期，這裡保留手動設定
    start_date = st.date_input("單次-開始日期", value=datetime(2021, 1, 1))
    end_date = st.date_input("單次-結束日期", value=datetime.today())

# --- 資料下載與處理 ---
def get_data(tickers, start, end):
    if not tickers: return pd.DataFrame()
    clean_tickers = [t.upper().strip() for t in tickers]
    try:
        # 下載數據
        raw = yf.download(clean_tickers, start=start, end=end, progress=False, auto_adjust=False)
        if raw.empty: return pd.DataFrame()
        
        # 處理欄位
        target_col = 'Adj Close' if 'Adj Close' in raw.columns else 'Close'
        if target_col not in raw.columns: return pd.DataFrame()
        
        df = raw[target_col]
        if isinstance(df, pd.Series): df = df.to_frame(name=clean_tickers[0])
        
        # 關鍵：刪除空值，這會自動切除「某檔基金還沒上市」的前段時間
        # 例如：母基金2007上市，子基金2019上市，dropna後數據會從2019開始
        return df.ffill().dropna()
    except: return pd.DataFrame()

# --- 邏輯 A: 單次進出 ---
def run_single_simulation(df, mom_tick, child_ticks, capital, t_amt, t_days, target):
    mom_tick = mom_tick.upper().strip()
    child_ticks = [t.upper().strip() for t in child_ticks if t.upper().strip() in df.columns]
    
    if mom_tick not in df.columns: return pd.DataFrame(), False

    mom_units = capital / df[mom_tick].iloc[0]
    child_units = {t: 0.0 for t in child_ticks}
    
    records = []
    triggered = False
    
    for date_idx, row in df.iterrows():
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
            rec = {"Date": date_idx, "Total Value": total_val, "Mom Value": mom_val, "Child Total": child_val_total, "ROI": roi, "Action": action}
            for t in child_ticks: rec[f"Val_{t}"] = child_vals[t]
            records.append(rec)
            break 
            
        if date_idx.day in t_days:
            transferred_any = False
            for t in child_ticks:
                if mom_val >= t_amt:
                    mom_units -= (t_amt / mom_price)
                    mom_val -= t_amt 
                    child_units[t] += (t_amt / row[t])
                    transferred_any = True
                else: break
            if transferred_any: action = "Transfer"

        rec = {"Date": date_idx, "Total Value": total_val, "Mom Value": mom_val, "Child Total": child_val_total, "ROI": roi, "Action": action}
        for t in child_ticks: rec[f"Val_{t}"] = child_vals[t]
        records.append(rec)
        
    return pd.DataFrame(records), triggered

# --- 邏輯 B: 循環回測 ---
def run_continuous_simulation(df, mom_tick, child_ticks, capital, t_amt, t_days, target):
    mom_tick = mom_tick.upper().strip()
    child_ticks = [t.upper().strip() for t in child_ticks if t.upper().strip() in df.columns]
    
    if mom_tick not in df.columns: return pd.DataFrame(), {}, []

    mom_units = 0.0
    child_units = {t: 0.0 for t in child_ticks}
    
    records = []
    completed_rounds = []
    is_running = False
    round_start_date = None
    
    for date_idx, row in df.iterrows():
        current_mom_price = row[mom_tick]
        
        if not is_running:
            mom_units = capital / current_mom_price
            child_units = {t: 0.0 for t in child_ticks}
            is_running = True
            round_start_date = date_idx
            records.append({"Date": date_idx, "Total Value": capital, "ROI": 0.0, "Action": "Start", "Round": len(completed_rounds)+1})
            continue 
        
        mom_val = mom_units * current_mom_price
        child_val_total = 0
        for t in child_ticks: child_val_total += child_units[t] * row[t]
        
        total_val = mom_val + child_val_total
        roi = (total_val - capital) / capital
        action = "Hold"
        
        if roi >= target:
            completed_rounds.append({
                "Start Date": round_start_date, "End Date": date_idx,
                "Duration": (date_idx - round_start_date).days,
                "Profit": total_val - capital, "Final ROI": roi
            })
            records.append({"Date": date_idx, "Total Value": total_val, "ROI": roi, "Action": "★ Stop Profit", "Round": len(completed_rounds)})
            is_running = False 
            mom_units = 0
            continue

        if date_idx.day in t_days:
            for t in child_ticks:
                if mom_val >= t_amt:
                    mom_units -= (t_amt / current_mom_price)
                    mom_val -= t_amt 
                    child_units[t] += (t_amt / row[t])
        
        records.append({"Date": date_idx, "Total Value": total_val, "ROI": roi, "Action": action, "Round": len(completed_rounds)+1})
        
    stats = {
        "Total Rounds": len(completed_rounds),
        "Is Running": is_running,
        "Current ROI": roi if is_running else 0.0,
        "Total Profit": sum([r['Profit'] for r in completed_rounds]),
        "Avg Duration": sum([r['Duration'] for r in completed_rounds]) / len(completed_rounds) if completed_rounds else 0
    }
    return pd.DataFrame(records), stats, completed_rounds

# --- 按鈕觸發區 ---
if st.button("🚀 開始分析", type="primary"):
    st.session_state.run_analysis = True
    # 按下按鈕時，我們從 2000 年開始抓，讓系統自己去 dropna 找出真正的起始日
    # 這樣就不用怕使用者不知道該基金哪天成立
    all_tickers = [mom_ticker] + child_tickers_input
    
    with st.spinner('正在從 Yahoo Finance 下載完整歷史數據...'):
        # 這裡 hardcode 從 2000 年開始，確保抓到所有可用的歷史資料
        df_downloaded = get_data(all_tickers, "2000-01-01", datetime.today())
        st.session_state.data_cache = df_downloaded

# --- 顯示區塊 (依據 Session State 決定是否顯示) ---
if st.session_state.run_analysis:
    df_data = st.session_state.data_cache
    
    if df_data.empty:
        st.error("❌ 無法取得數據，請檢查代號是否正確。")
    else:
        # 取得數據真正的第一天 (所有基金都有資料的那天)
        actual_start_date = df_data.index[0].date()
        max_end_date = df_data.index[-1].date()

        # 建立分頁
        tab1, tab2 = st.tabs(["📄 單次進出詳細分析", "🔄 循環鎖利分析"])
        
        # --- Tab 1: 單次邏輯 ---
        with tab1:
            # 根據側邊欄的日期進行過濾
            df_single_slice = df_data[start_date:end_date]
            
            if df_single_slice.empty:
                st.warning("⚠️ 側邊欄設定的「單次分析日期」範圍內無資料。")
            else:
                df_single, is_win = run_single_simulation(
                    df_single_slice, mom_ticker, child_tickers_input, initial_capital, transfer_amount, transfer_days, target_roi
                )
                
                if not df_single.empty:
                    last_row = df_single.iloc[-1]
                    final_roi = last_row['ROI']
                    
                    if is_win:
                        st.success(f"### 🎉 獲利達標 (單次模式) \n於 **{last_row['Date'].strftime('%Y-%m-%d')}** 觸發停利，報酬率 **{final_roi*100:.2f}%**")
                    else:
                        st.info(f"### ⏳ 持續運作中 \n截至 **{last_row['Date'].strftime('%Y-%m-%d')}** 尚未達標，目前報酬率 **{final_roi*100:.2f}%**")
                    
                    c1, c2, c3, c4 = st.columns(4)
                    c1.metric("進場日期", df_single.iloc[0]['Date'].strftime('%Y-%m-%d'))
                    c2.metric("出場/結算日期", last_row['Date'].strftime('%Y-%m-%d'))
                    c3.metric("最終資產", f"${last_row['Total Value']:,.0f}")
                    c4.metric("ROI", f"{final_roi*100:.2f}%", delta_color="normal" if final_roi>=0 else "inverse")
                    
                    fig_s = go.Figure()
                    fig_s.add_trace(go.Scatter(x=df_single['Date'], y=df_single['Total Value'], name='總資產', line=dict(color='#d62728', width=3)))
                    fig_s.add_trace(go.Scatter(x=df_single['Date'], y=df_single['Mom Value'], name='母基金', line=dict(color='#1f77b4', width=1), fill='tozeroy', fillcolor='rgba(31, 119, 180, 0.1)'))
                    fig_s.update_layout(height=400, hovermode="x unified", title="單次資產變化圖")
                    st.plotly_chart(fig_s, use_container_width=True)
                    
                    with st.expander("查看單次詳細交易數據", expanded=True):
                        st.dataframe(df_single.style.format({"Total Value": "{:,.0f}", "Mom Value": "{:,.0f}", "Child Total": "{:,.0f}", "ROI": "{:.2%}"}))

        # --- Tab 2: 循環邏輯 (自動對齊日期) ---
        with tab2:
            st.markdown("#### 📅 循環回測統計區間")
            st.caption(f"💡 系統偵測到您選擇的投資組合，最早共同可回測日期為： **{actual_start_date}**")
            
            col_d1, col_d2 = st.columns(2)
            
            # 使用 actual_start_date 作為預設值 (value) 和最小值 (min_value)
            # 這樣使用者一進來看到的就是真正有資料的那天
            start_date_circ = col_d1.date_input("開始日", value=actual_start_date, min_value=actual_start_date, max_value=max_end_date, key="circ_start")
            end_date_circ = col_d2.date_input("結束日", value=max_end_date, min_value=actual_start_date, max_value=max_end_date, key="circ_end")

            # 根據 Tab2 選擇的日期切割數據
            df_circ_slice = df_data[start_date_circ:end_date_circ]

            if not df_circ_slice.empty:
                df_cont, stats, rounds = run_continuous_simulation(
                    df_circ_slice, mom_ticker, child_tickers_input, initial_capital, transfer_amount, transfer_days, target_roi
                )
                
                st.markdown("### 🏆 策略總覽")
                k1, k2, k3, k4 = st.columns(4)
                k1.metric("累積成功出場", f"{stats['Total Rounds']} 次")
                k2.metric("平均每一趟歷時", f"{stats['Avg Duration']:.1f} 天")
                k3.metric("累積獲利金額", f"${stats['Total Profit']:,.0f}")
                
                current_status_label = "運作中" if stats['Is Running'] else "等待進場"
                current_roi_display = f"{stats['Current ROI']*100:.2f}%" if stats['Is Running'] else "-"
                k4.metric("目前狀態", current_status_label, delta=current_roi_display)
                
                if stats['Is Running']:
                    st.caption(f"目前位於第 {stats['Total Rounds'] + 1} 輪循環中")

                fig_c = go.Figure()
                fig_c.add_trace(go.Scatter(x=df_cont['Date'], y=df_cont['Total Value'], name='資產價值', line=dict(color='#2ca02c', width=2)))
                exits = df_cont[df_cont['Action'] == '★ Stop Profit']
                fig_c.add_trace(go.Scatter(x=exits['Date'], y=exits['Total Value'], mode='markers', name='停利點', marker=dict(size=10, color='red', symbol='star')))
                fig_c.add_hline(y=initial_capital, line_dash="dash", line_color="gray", annotation_text="本金線")
                fig_c.update_layout(height=450, hovermode="x unified", title=f"循環獲利示意圖 (累積獲利: ${stats['Total Profit']:,.0f})")
                st.plotly_chart(fig_c, use_container_width=True)
                
                if rounds:
                    st.markdown("### 📋 成功出場紀錄")
                    r_df = pd.DataFrame(rounds)
                    r_df['Start Date'] = r_df['Start Date'].dt.date
                    r_df['End Date'] = r_df['End Date'].dt.date
                    r_df['Final ROI'] = r_df['Final ROI'].apply(lambda x: f"{x*100:.2f}%")
                    r_df['Profit'] = r_df['Profit'].apply(lambda x: f"${x:,.0f}")
                    st.table(r_df)
                else:
                    st.warning("在此區間內尚未有成功出場紀錄")
            else:
                 st.error("選擇的日期範圍內無數據。")

# --- 底部警語 ---
st.markdown("---")
st.warning("""
**⚠️ 警語 / Disclaimer**：
1. 本系統之回測結果僅供參考，**過去之績效不代表未來投資之保證**。
2. 投資一定有風險，基金投資有賺有賠，申購前應詳閱公開說明書。
3. 動態鎖利/母子基金機制並非保本商品，在市場發生極端行情時，母基金仍可能面臨淨值下跌或本金虧損之風險。
4. 實際交易之手續費、管理費等成本可能依銀行規定而有所不同，本回測未完全涵蓋所有潛在成本。
5. 數據資料來源：Yahoo Finance
""")
