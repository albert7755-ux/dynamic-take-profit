import streamlit as st
import yfinance as yf
import pandas as pd
import plotly.graph_objects as go
from datetime import datetime

# --- 頁面設定 ---
st.set_page_config(page_title="動態鎖利投資回測系統", layout="wide")

st.title("📊 動態鎖利 (母子基金) 投資架構回測")
st.markdown("""
本系統使用 **Yahoo Finance** 數據進行回測。
* 台股代號請加上 `.TW` (例如: `0050.TW`, `2330.TW`)
* 美股/ETF 直接輸入代號 (例如: `BND`, `QQQ`, `NVDA`)
* 基金若有對應 ETF 建議優先使用 ETF 代號替代，數據較完整。
""")

# --- 側邊欄：參數設定 ---
with st.sidebar:
    st.header("1. 基金代號設定 (Yahoo Finance)")
    
    # 母基金
    mom_ticker = st.text_input("母基金代號 (穩健型)", value="BND", help="例如: BND (總體債券), SHV (短債)")
    
    # 子基金 (支援最多3檔)
    st.markdown("---")
    st.write("子基金 (積極型) - 最多可選 3 檔")
    child_tickers = []
    c1 = st.text_input("子基金 1 代號", value="QQQ")
    c2 = st.text_input("子基金 2 代號 (選填)", value="")
    c3 = st.text_input("子基金 3 代號 (選填)", value="")
    
    if c1: child_tickers.append(c1.upper())
    if c2: child_tickers.append(c2.upper())
    if c3: child_tickers.append(c3.upper())

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

# --- 核心邏輯函數 ---
def get_data(tickers, start, end):
    """下載數據並清理"""
    if not tickers:
        return pd.DataFrame()
    try:
        # 下載調整後收盤價
        data = yf.download(tickers, start=start, end=end, progress=False)['Adj Close']
        
        # 處理單一 ticker 返回 Series 的情況，統一轉為 DataFrame
        if isinstance(data, pd.Series):
            data = data.to_frame(name=tickers[0])
            
        # 處理 MultiIndex (如果有的話)
        if isinstance(data.columns, pd.MultiIndex):
            data.columns = data.columns.get_level_values(0)
            
        return data.ffill().dropna() # 填補假日並刪除空值
    except Exception as e:
        st.error(f"數據下載發生錯誤: {e}")
        return pd.DataFrame()

def run_simulation(df, mom_tick, child_ticks, bench_tick, capital, fee, t_amt, t_days, target):
    # 1. 初始配置
    # 扣除手續費 (假設手續費內扣，實際投入母基金金額變少)
    entry_fee_amount = capital * fee
    net_capital = capital - entry_fee_amount
    
    # 初始全買母基金
    mom_price_init = df[mom_tick].iloc[0]
    mom_units = net_capital / mom_price_init
    
    # 子基金單位數初始化 (字典)
    child_units = {t: 0.0 for t in child_ticks}
    
    # Benchmark 單位數 (假設單筆買進持有，不做任何操作，含手續費比較公平)
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
        
        # 計算報酬率 (分母用原始本金 30萬)
        roi = (total_val - capital) / capital
        
        action = "Hold"
        
        # --- B. 檢查停利 ---
        if roi >= target:
            action = "★ Stop Profit"
            triggered = True
            records.append({
                "Date": date,
                "Total Value": total_val,
                "Mom Value": mom_val,
                "Child Total": child_val_total,
                "Benchmark Value": bench_val,
                "ROI": roi,
                "Action": action
            })
            break # 終止回測
            
        # --- C. 執行轉申購 (若未停利) ---
        if date.day in t_days:
            # 依序檢查每個子基金
            transferred_any = False
            for t in child_ticks:
                if mom_val >= t_amt:
                    # 母基金贖回
                    units_out = t_amt / mom_price
                    mom_units -= units_out
                    mom_val -= t_amt # 更新暫存市值以便下一個迴圈判斷
                    
                    # 子基金申購
                    child_price = row[t]
                    units_in = t_amt / child_price
                    child_units[t] += units_in
                    transferred_any = True
                else:
                    # 母基金餘額不足，停止該檔及後續轉換 (依規範)
                    action = "Insufficient Funds"
                    break
            
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
        # 加入個別子基金市值
        for t in child_ticks:
            rec[f"Val_{t}"] = child_vals[t]
            
        records.append(rec)
        
    return pd.DataFrame(records), triggered

# --- 主程式執行區 ---
if st.button("🚀 開始回測", type="primary"):
    if not child_tickers:
        st.error("請至少輸入一檔子基金代號！")
    else:
        # 準備下載清單
        all_tickers = [mom_ticker] + child_tickers + [benchmark_ticker]
        
        with st.spinner('正在從 Yahoo Finance 下載數據並運算中...'):
            df_data = get_data(all_tickers, start_date, end_date)
            
            if df_data.empty or df_data.shape[1] < len(all_tickers):
                st.error("數據下載不完整，請檢查代號是否正確 (台股需加 .TW) 或日期範圍。")
                st.write("嘗試下載的代號:", all_tickers)
            else:
                # 執行回測
                res_df, is_win = run_simulation(
                    df_data, mom_ticker, child_tickers, benchmark_ticker,
                    initial_capital, fee_rate, transfer_amount, transfer_days, target_roi
                )
                
                # --- 結果顯示 ---
                # 1. KPI 指標
                st.markdown("### 📊 回測結果摘要")
                col1, col2, col3, col4 = st.columns(4)
                
                last_row = res_df.iloc[-1]
                final_roi = last_row['ROI']
                bench_roi = (last_row['Benchmark Value'] - initial_capital) / initial_capital
                days_run = (last_row['Date'] - res_df.iloc[0]['Date']).days
                
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
                                         name=f'Benchmark ({benchmark_ticker})', 
                                         line=dict(color='gray', dash='dot')))
                # 母基金
                fig.add_trace(go.Scatter(x=res_df['Date'], y=res_df['Mom Value'], 
                                         name=f'母基金 ({mom_ticker})', 
                                         line=dict(color='blue', width=1), fill='tozeroy', fillcolor='rgba(0,0,255,0.1)'))
                
                # 個別子基金
                for t in child_tickers:
                    fig.add_trace(go.Scatter(x=res_df['Date'], y=res_df[f"Val_{t}"], 
                                             name=f'子基金 ({t})', visible='legendonly'))

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
