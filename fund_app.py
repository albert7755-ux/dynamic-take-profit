import streamlit as st
import yfinance as yf
import pandas as pd
import plotly.graph_objects as go
from datetime import datetime

# --- 頁面設定 ---
st.set_page_config(page_title="動態鎖利投資回測系統", layout="wide")

st.title("📊 動態鎖利 (母子基金) 投資架構回測")
st.markdown("""
**系統說明：**
* 本系統使用 **Yahoo Finance** 數據。
* **台股**請加 `.TW` (如 `0050.TW`)，**美股**直接輸入代號 (如 `QQQ`, `SPY`)。
* 系統會自動將代號轉為大寫，並優先使用「還原權值 (Adj Close)」計算。
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

    # (已移除 Benchmark 設定)

    st.header("2. 資金投入設定")
    col_cap, col_fee = st.columns(2)
    initial_capital = col_cap.number_input("原始本金 Total", value=300000, step=10000)
    fee_rate_percent = col_fee.number_input("手續費率 (%)", value=3.0, step=0.5)
    fee_rate = fee_rate_percent / 100

    st.header("3. 轉申購 (DCA) 規則")
    transfer_amount = st.number_input("「每檔」子基金每次轉入金額", value=3000, step=1000, help="若設定2檔子基金，每次扣款日總共會轉出 6000")
    
    transfer_days = st.multiselect(
        "每月扣款日 (可複選)",
        options=[1, 6, 11, 16, 21, 26],
        default=[6, 16, 26]
    )

    st.header("4. 停利與日期")
    target_roi_percent = st.number_input("停利目標報酬率 (%)", value=10.0, step=1.0)
    target_roi = target_roi_percent / 100
    
    start_date = st.date_input("回測開始日期", value=datetime(2021, 1, 1))
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
            
        missing_cols = [t for t in clean_tickers if t not in df_prices.columns]
        if missing_cols:
            st.warning(f"⚠️ 以下代號無數據: {missing_cols}")
            
        return df_prices.ffill().dropna()

    except Exception as e:
        st.error(f"數據下載發生錯誤: {e}")
        return pd.DataFrame()

def run_simulation(df, mom_tick, child_ticks, capital, fee, t_amt, t_days, target):
    # 確保代號大寫
    mom_tick = mom_tick.upper().strip()
    child_ticks = [t.upper().strip() for t in child_ticks if t.upper().strip() in df.columns]

    if mom_tick not in df.columns:
        st.error("錯誤: 母基金數據缺失。")
        return pd.DataFrame(), False

    # 1. 初始配置 (扣除手續費)
    entry_fee_amount = capital * fee
    net_capital = capital - entry_fee_amount
    
    mom_price_init = df[mom_tick].iloc[0]
    mom_units = net_capital / mom_price_init
    
    child_units = {t: 0.0 for t in child_ticks}
    
    records = []
    triggered = False
    
    for date, row in df.iterrows():
        # A. 更新市值
        mom_price = row[mom_tick]
        mom_val = mom_units * mom_price
        
        child_val_total = 0
        child_vals = {}
        for t in child_ticks:
            v = child_units[t] * row[t]
            child_vals[t] = v
            child_val_total += v
            
        total_val = mom_val + child_val_total
        
        # B. 計算報酬率 (分母使用原始本金)
        roi = (total_val - capital) / capital
        
        action = "Hold"
        
        # C. 檢查停利
        if roi >= target:
            action = "★ Stop Profit"
            triggered = True
            rec = {
                "Date": date,
                "Total Value": total_val,
                "Mom Value": mom_val,
                "Child Total": child_val_total,
                "ROI": roi,
                "Action": action
            }
            for t in child_ticks: rec[f"Val_{t}"] = child_vals[t]
            records.append(rec)
            break 
            
        # D. 轉申購
        if date.day in t_days:
            transferred_any = False
            for t in child_ticks:
                if mom_val >= t_amt:
                    units_out = t_amt / mom_price
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

        rec = {
            "Date": date,
            "Total Value": total_val,
            "Mom Value": mom_val,
            "Child Total": child_val_total,
            "ROI": roi,
            "Action": action
        }
        for t in child_ticks: rec[f"Val_{t}"] = child_vals[t]
        records.append(rec)
        
    return pd.DataFrame(records), triggered

# --- 主程式 ---
if st.button("🚀 開始回測", type="primary"):
    if not child_tickers_input:
        st.error("請至少輸入一檔子基金代號！")
    else:
        # 下載清單 (移除 Benchmark)
        all_tickers = [mom_ticker] + child_tickers_input
        
        with st.spinner('運算中...'):
            df_data = get_data(all_tickers, start_date, end_date)
            
            if not df_data.empty:
                res_df, is_win = run_simulation(
                    df_data, mom_ticker, child_tickers_input,
                    initial_capital, fee_rate, transfer_amount, transfer_days, target_roi
                )
                
                if res_df.empty:
                    st.error("回測失敗 (數據不足)。")
                else:
                    # --- 結果顯示區 ---
                    last_row = res_df.iloc[-1]
                    first_row = res_df.iloc[0]
                    final_roi = last_row['ROI']
                    
                    # 1. 狀態橫幅 (解決文字被切斷問題)
                    if is_win:
                        st.success(f"### 🎉 恭喜！獲利達標！ \n於 **{last_row['Date'].strftime('%Y-%m-%d')}** 觸發停利，報酬率 **{final_roi*100:.2f}%**")
                    else:
                        st.info(f"### ⏳ 持續運作中 \n截至 **{last_row['Date'].strftime('%Y-%m-%d')}** 尚未達標，目前報酬率 **{final_roi*100:.2f}%**")

                    st.markdown("---")

                    # 2. 關鍵數據 (新增日期顯示)
                    c1, c2, c3, c4 = st.columns(4)
                    
                    c1.metric("📅 進場日期", first_row['Date'].strftime('%Y-%m-%d'))
                    c2.metric("📅 結算/出場日期", last_row['Date'].strftime('%Y-%m-%d'))
                    c3.metric("最終資產總值", f"${last_row['Total Value']:,.0f}")
                    c4.metric("最終報酬率 (ROI)", f"{final_roi*100:.2f}%", 
                              delta_color="normal" if final_roi >= 0 else "inverse")

                    # 3. 圖表
                    st.subheader("📈 資產走勢圖")
                    fig = go.Figure()
                    
                    # 總資產
                    fig.add_trace(go.Scatter(x=res_df['Date'], y=res_df['Total Value'], 
                                             name='總資產 (母+子)', line=dict(color='#d62728', width=3)))
                    # 母基金
                    fig.add_trace(go.Scatter(x=res_df['Date'], y=res_df['Mom Value'], 
                                             name=f'母基金 ({mom_ticker.upper()})', 
                                             line=dict(color='#1f77b4', width=1), fill='tozeroy', fillcolor='rgba(31, 119, 180, 0.1)'))
                    
                    # 子基金
                    for t in child_tickers_input:
                        t_upper = t.upper().strip()
                        if f"Val_{t_upper}" in res_df.columns:
                            fig.add_trace(go.Scatter(x=res_df['Date'], y=res_df[f"Val_{t_upper}"], 
                                                     name=f'子基金 ({t_upper})', visible='legendonly'))

                    if is_win:
                        fig.add_annotation(x=last_row['Date'], y=last_row['Total Value'],
                                           text="停利點", showarrow=True, arrowhead=2, ax=0, ay=-40)

                    fig.update_layout(height=500, hovermode="x unified")
                    st.plotly_chart(fig, use_container_width=True)
                    
                    # 4. 數據表
                    with st.expander("查看詳細交易數據"):
                        st.dataframe(res_df.style.format({
                            "Total Value": "{:,.0f}",
                            "Mom Value": "{:,.0f}",
                            "Child Total": "{:,.0f}",
                            "ROI": "{:.2%}"
                        }))
            else:
                st.error("無法取得數據。")
