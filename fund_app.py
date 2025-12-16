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
    
    # 收集有填寫的子基金
    if c1: child_tickers_input.append(c1)
    if c2: child_tickers_input.append(c2)
    if c3: child_tickers_input.append(c3)

    # Benchmark
    st.markdown("---")
    benchmark_ticker = st.text_input("Benchmark 基準代號", value="^GSPC", help="例如: ^GSPC (標普500), ^TWII (台灣加權)")

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

# --- 核心邏輯函數 (修復版) ---
def get_data(tickers, start, end):
    """
    下載數據並清理 (增強容錯能力)
    """
    if not tickers:
        return pd.DataFrame()
    
    # 1. 強制轉大寫並去空白 (解決 spy vs SPY)
    clean_tickers = [t.upper().strip() for t in tickers]
    
    try:
        # 2. 下載數據
        # auto_adjust=False 確保我們能明確看到 'Adj Close'，防止新版 yfinance 自動調整欄位
        raw_data = yf.download(clean_tickers, start=start, end=end, progress=False, auto_adjust=False)
        
        if raw_data.empty:
            st.error(f"⚠️ 下載數據為空！請檢查代號 {clean_tickers} 是否正確，或日期區間是否有交易資料。")
            return pd.DataFrame()

        # 3. 處理價格欄位
        # yfinance 回傳格式可能是 MultiIndex ('Adj Close', 'BND') 或單層 Index
        target_col = 'Adj Close'
        
        # 檢查是否存在 'Adj Close'
        if target_col not in raw_data.columns:
            if 'Close' in raw_data.columns:
                # st.warning("提示: 找不到 'Adj Close' (還原權值)，系統將改用 'Close' 進行計算。")
                target_col = 'Close'
            else:
                st.error(f"⚠️ 數據格式異常，找不到價格欄位。下載到的欄位: {raw_data.columns}")
                return pd.DataFrame()

        df_prices = raw_data[target_col]

        # 4. 格式統一化
        # 如果只下載單一檔股票，df_prices 會是 Series，必須轉成 DataFrame
        if isinstance(df_prices, pd.Series):
            df_prices = df_prices.to_frame(name=clean_tickers[0])
            
        # 再次確保所有需要的代號都在 Columns 裡
        # 有時候 yfinance 下載多檔若其中一檔失敗，該欄位會消失
        missing_cols = [t for t in clean_tickers if t not in df_prices.columns]
        if missing_cols:
            st.warning(f"⚠️ 以下代號無法取得數據，將被忽略: {missing_cols}")
            
        return df_prices.ffill().dropna() # 填補假日並刪除空值

    except Exception as e:
        st.error(f"數據下載發生嚴重錯誤: {e}")
        return pd.DataFrame()

def run_simulation(df, mom_tick, child_ticks, bench_tick, capital, fee, t_amt, t_days, target):
    # 確保所有代號都是大寫，以匹配 DataFrame 的欄位
    mom_tick = mom_tick.upper().strip()
    child_ticks = [t.upper().strip() for t in child_ticks if t.upper().strip() in df.columns]
    bench_tick = bench_tick.upper().strip()

    if mom_tick not in df.columns or bench_tick not in df.columns:
        st.error("錯誤: 母基金或 Benchmark 數據缺失，無法回測。")
        return pd.DataFrame(), False

    # 1. 初始配置
    # 扣除手續費 (模擬真實進場金額)
    entry_fee_amount = capital * fee
    net_capital = capital - entry_fee_amount
    
    # 初始全買母基金
    mom_price_init = df[mom_tick].iloc[0]
    mom_units = net_capital / mom_price_init
    
    # 子基金單位數初始化 (字典)
    child_units = {t: 0.0 for t in child_ticks}
    
    # Benchmark 單位數 (假設單筆買進持有)
    bench_price_init = df[bench_tick].iloc[0]
    bench_units = net_capital / bench_price_init
    
    records = []
    triggered = False
    
    for date, row in df.iterrows():
        # --- A. 更新當日市值 ---
        mom_price = row[mom_tick]
        mom_val = mom_units * mom_price
        
        child_val_total = 0
        child_vals = {} # 紀錄各別子基金市值
        
        for t in child_ticks:
            p = row[t]
            v = child_units[t] * p
            child_vals[t] = v
            child_val_total += v
            
        total_val = mom_val + child_val_total
        bench_val = bench_units * row[bench_tick]
        
        # 計算報酬率 (分母用原始本金，包含已付出的手續費)
        roi = (total_val - capital) / capital
        
        action = "Hold"
        
        # --- B. 檢查停利 ---
        if roi >= target:
            action = "★ Stop Profit"
            triggered = True
            rec = {
                "Date": date,
                "Total Value": total_val,
                "Mom Value": mom_val,
                "Child Total": child_val_total,
                "Benchmark Value": bench_val,
                "ROI": roi,
                "Action": action
            }
            for t in child_ticks: rec[f"Val_{t}"] = child_vals[t]
            records.append(rec)
            break # 終止回測
            
        # --- C. 執行轉申購 (若未停利) ---
        if date.day in t_days:
            transferred_any = False
            for t in child_ticks:
                if mom_val >= t_amt:
                    # 母基金贖回
                    units_out = t_amt / mom_price
                    mom_units -= units_out
                    mom_val -= t_amt # 更新暫存市值
                    
                    # 子基金申購
                    child_price = row[t]
                    units_in = t_amt / child_price
                    child_units[t] += units_in
                    transferred_any = True
                else:
                    action = "Insufficient Funds"
                    break # 餘額不足停止後續轉換
            
            if transferred_any:
                action = "Transfer"

        # --- D. 紀錄 ---
        rec = {
            "Date": date,
            "Total Value": total_val,
            "Mom Value": mom_val,
            "Child Total": child_val_total,
            "Benchmark Value": bench_val,
            "ROI": roi,
            "Action": action
        }
        for t in child_ticks:
            rec[f"Val_{t}"] = child_vals[t]
            
        records.append(rec)
        
    return pd.DataFrame(records), triggered

# --- 主程式執行區 ---
if st.button("🚀 開始回測", type="primary"):
    # 檢查是否輸入了子基金
    if not child_tickers_input:
        st.error("請至少輸入一檔子基金代號！")
    else:
        # 準備下載清單
        all_tickers = [mom_ticker] + child_tickers_input + [benchmark_ticker]
        
        with st.spinner('正在從 Yahoo Finance 下載數據並運算中...'):
            df_data = get_data(all_tickers, start_date, end_date)
            
            # 檢查數據是否足夠進行運算
            if not df_data.empty:
                # 執行回測
                res_df, is_win = run_simulation(
                    df_data, mom_ticker, child_tickers_input, benchmark_ticker,
                    initial_capital, fee_rate, transfer_amount, transfer_days, target_roi
                )
                
                if res_df.empty:
                    st.error("回測運算失敗，可能因數據不足或代號錯誤。")
                else:
                    # --- 結果顯示 ---
                    # 1. KPI 指標
                    st.markdown("### 📊 回測結果摘要")
                    col1, col2, col3, col4 = st.columns(4)
                    
                    last_row = res_df.iloc[-1]
                    final_roi = last_row['ROI']
                    bench_roi = (last_row['Benchmark Value'] - initial_capital) / initial_capital
                    
                    col1.metric("最終資產總值", f"${last_row['Total Value']:,.0f}")
                    col2.metric("策略報酬率 (ROI)", f"{final_roi*100:.2f}%", 
                                delta=f"{(final_roi - bench_roi)*100:.2f}% vs Benchmark")
                    col3.metric("母基金剩餘金額", f"${last_row['Mom Value']:,.0f}")
                    col4.metric("狀態", "✅ 獲利達標出場" if is_win else "⏳ 尚未達標/持續運作")

                    # 2. 互動圖表
                    st.subheader("📈 資產走勢圖")
                    fig = go.Figure()
                    
                    # 總資產
                    fig.add_trace(go.Scatter(x=res_df['Date'], y=res_df['Total Value'], 
                                             name='總資產 (母+子)', line=dict(color='red', width=3)))
                    # Benchmark
                    fig.add_trace(go.Scatter(x=res_df['Date'], y=res_df['Benchmark Value'], 
                                             name=f'Benchmark ({benchmark_ticker.upper()})', 
                                             line=dict(color='gray', dash='dot')))
                    # 母基金
                    fig.add_trace(go.Scatter(x=res_df['Date'], y=res_df['Mom Value'], 
                                             name=f'母基金 ({mom_ticker.upper()})', 
                                             line=dict(color='blue', width=1), fill='tozeroy', fillcolor='rgba(0,0,255,0.1)'))
                    
                    # 個別子基金
                    for t in child_tickers_input:
                        t_upper = t.upper().strip()
                        if f"Val_{t_upper}" in res_df.columns:
                            fig.add_trace(go.Scatter(x=res_df['Date'], y=res_df[f"Val_{t_upper}"], 
                                                     name=f'子基金 ({t_upper})', visible='legendonly'))

                    # 標記停利點
                    if is_win:
                        fig.add_annotation(x=last_row['Date'], y=last_row['Total Value'],
                                           text="🎉 停利出場", showarrow=True, arrowhead=2, ax=0, ay=-40)

                    fig.update_layout(height=500, hovermode="x unified")
                    st.plotly_chart(fig, use_container_width=True)
                    
                    # 3. 數據表格
                    with st.expander("查看詳細交易數據"):
                        st.dataframe(res_df.style.format({
                            "Total Value": "{:,.0f}",
                            "Mom Value": "{:,.0f}",
                            "Child Total": "{:,.0f}",
                            "Benchmark Value": "{:,.0f}",
                            "ROI": "{:.2%}"
                        }))
            else:
                st.error("無法取得數據，請重新檢查代號或網路連線。")
