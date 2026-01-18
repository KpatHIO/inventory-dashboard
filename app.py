import streamlit as st
import pandas as pd
import plotly.express as px
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from datetime import date, timedelta

# --- CONFIGURATION ---
st.set_page_config(layout="wide", page_title="Inventory Command Center", initial_sidebar_state="collapsed")

# --- 0. LOGIN SYSTEM ---
def check_password():
    def password_entered():
        if st.session_state["password"] == "inventory2026":
            st.session_state["password_correct"] = True
            del st.session_state["password"]
        else:
            st.session_state["password_correct"] = False

    if "password_correct" not in st.session_state:
        st.text_input("🔒 Enter Team Password:", type="password", on_change=password_entered, key="password")
        return False
    elif not st.session_state["password_correct"]:
        st.text_input("🔒 Enter Team Password:", type="password", on_change=password_entered, key="password")
        st.error("😕 Password incorrect")
        return False
    else:
        return True

if not check_password():
    st.stop()

# --- 1. DATA LOADING ---
@st.cache_data(ttl=60) # Cache data for 60 seconds to speed up mobile reloading
def load_data_from_sheets():
    try:
        scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        
        try:
            import json
            key_dict = json.loads(st.secrets["textkey"])
            creds = ServiceAccountCredentials.from_json_keyfile_dict(key_dict, scope)
        except:
            creds = ServiceAccountCredentials.from_json_keyfile_name("service_account.json", scope)
            
        client = gspread.authorize(creds)
        sheet = client.open("Inventory_DB")
        
        data_skus = pd.DataFrame(sheet.worksheet("db_skus").get_all_records())
        data_inbound = pd.DataFrame(sheet.worksheet("db_inbound").get_all_records())
        data_outbound = pd.DataFrame(sheet.worksheet("db_outbound").get_all_records())
        
        # Clean Data
        cols_to_numeric = ['stock_on_hand', 'safety_threshold', 'qty']
        for df in [data_skus, data_inbound, data_outbound]:
            for col in df.columns:
                if col in cols_to_numeric:
                    df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)
        
        data_inbound['arrival_date'] = pd.to_datetime(data_inbound['arrival_date'], dayfirst=True, errors='coerce').dt.date
        data_outbound['dispatch_date'] = pd.to_datetime(data_outbound['dispatch_date'], dayfirst=True, errors='coerce').dt.date

        return data_skus, data_inbound, data_outbound
    except Exception as e:
        return pd.DataFrame(), pd.DataFrame(), pd.DataFrame()

# --- 2. CALCULATION ENGINE ---
def calculate_inventory(skus, inbound, outbound, start_date, days_to_show):
    if skus.empty: return pd.DataFrame()
    date_range = [start_date + timedelta(days=x) for x in range(days_to_show)]
    master_grid = []
    
    for _, sku in skus.iterrows():
        sku_id = sku['sku_id']
        sku_desc = sku['description'] if 'description' in sku and sku['description'] else sku_id
        
        past_in = inbound[(inbound['sku_id'] == sku_id) & (inbound['arrival_date'] < start_date)]['qty'].sum()
        past_out = outbound[(outbound['sku_id'] == sku_id) & (outbound['dispatch_date'] < start_date)]['qty'].sum()
        
        current_stock = sku['stock_on_hand'] + past_in - past_out
        safety_stock = sku['safety_threshold']
        
        # Track min stock for the period
        min_stock_period = current_stock
        
        for d in date_range:
            day_in = inbound[(inbound['sku_id'] == sku_id) & (inbound['arrival_date'] == d)]['qty'].sum()
            day_out = outbound[(outbound['sku_id'] == sku_id) & (outbound['dispatch_date'] == d)]['qty'].sum()
            
            net_change = day_in - day_out
            current_stock += net_change
            if current_stock < min_stock_period: min_stock_period = current_stock
            
            status = 'GREEN'
            if current_stock < 0: status = 'RED'
            elif current_stock < safety_stock: status = 'AMBER'
                
            marker = ""
            if day_in > 0 and day_out > 0: marker = "▲▼"
            elif day_in > 0: marker = "▲"
            elif day_out > 0: marker = "▼"
            
            display_stock = int(current_stock) if current_stock == int(current_stock) else current_stock

            master_grid.append({
                'SKU_ID': sku_id, 'Description': sku_desc, 'Date': d,
                'Stock': current_stock, 'Status': status, 'Display': f"{display_stock} {marker}",
                'Period_Low': min_stock_period # For sorting
            })
            
    return pd.DataFrame(master_grid)

# --- 3. STYLING ---
def style_dataframe(df):
    if df.empty: return df
    matrix = df.pivot(index='Description', columns='Date', values='Display')
    status_matrix = df.pivot(index='Description', columns='Date', values='Status')
    new_columns = [d.strftime('%d-%m') for d in matrix.columns] # Short dates for mobile
    matrix.columns = new_columns
    status_matrix.columns = new_columns

    def apply_styles(data):
        styles = pd.DataFrame('', index=data.index, columns=data.columns)
        for col in data.columns:
            for idx in data.index:
                try:
                    status = status_matrix.at[idx, col]
                    if status == 'RED': styles.at[idx, col] = 'background-color: #ffcccc; color: #990000'
                    elif status == 'AMBER': styles.at[idx, col] = 'background-color: #fff4cc; color: #665500'
                    else: styles.at[idx, col] = 'background-color: #e6ffcc; color: #004d00'
                except: pass
        return styles
    return matrix.style.apply(apply_styles, axis=None)

# --- 4. APP LAYOUT ---
# Sidebar for Controls (Better for Mobile)
with st.sidebar:
    st.header("⚙️ Controls")
    chosen_start_date = st.date_input("Start Date", value=date.today())
    view_option = st.radio("Look Ahead:", ["30 Days", "60 Days", "90 Days"], index=0)
    days_map = {"30 Days": 30, "60 Days": 60, "90 Days": 90}
    
    st.divider()
    if st.button("🔄 Refresh Data", type="primary", use_container_width=True):
        st.cache_data.clear()
        st.rerun()

# Load Data
skus, inbound, outbound = load_data_from_sheets()

# Header & KPIs
st.title("📦 Inventory Command")

if not skus.empty:
    df_processed = calculate_inventory(skus, inbound, outbound, chosen_start_date, days_map[view_option])
    
    # Calculate KPIs
    unique_items = df_processed['Description'].unique()
    critical_items = df_processed[df_processed['Status'] == 'RED']['Description'].unique()
    warning_items = df_processed[df_processed['Status'] == 'AMBER']['Description'].unique()
    
    # KPI Row
    k1, k2, k3 = st.columns(3)
    k1.metric("Total Items", len(unique_items))
    k2.metric("Critical (Low)", len(critical_items), delta_color="inverse")
    k3.metric("Warning", len(warning_items), delta_color="off")

    # --- TABS INTERFACE ---
    tab1, tab2, tab3 = st.tabs(["🚨 Action Board", "📅 Full Schedule", "🔍 Deep Dive"])

    # TAB 1: ACTION BOARD (Mobile Optimized List)
    with tab1:
        if len(critical_items) == 0 and len(warning_items) == 0:
            st.success("🎉 All stock levels are healthy!")
        else:
            st.markdown("### ⚠️ Items Needing Attention")
            # Filter only problem items
            problem_df = df_processed[df_processed['Description'].isin(list(critical_items) + list(warning_items))]
            
            # Show simplified view: Just the next 7 days for problem items
            short_term_date = chosen_start_date + timedelta(days=7)
            mobile_view = problem_df[problem_df['Date'] <= short_term_date]
            
            st.dataframe(style_dataframe(mobile_view), use_container_width=True, height=400)
            st.caption("Showing next 7 days for items in Red/Amber status.")

    # TAB 2: FULL SCHEDULE (The Classic Gantt)
    with tab2:
        st.markdown("### Master Plan")
        st.dataframe(style_dataframe(df_processed), use_container_width=True, height=500)

    # TAB 3: DEEP DIVE (Chart + Drill Down)
    with tab3:
        st.markdown("### 📈 Item Inspector")
        # Search Box at top
        selected_desc = st.selectbox("Search Item:", sorted(skus['description'].unique()))
        
        # Filter Data
        item_data = df_processed[df_processed['Description'] == selected_desc].copy()
        selected_id = skus[skus['description'] == selected_desc]['sku_id'].iloc[0]
        
        # PLOTLY CHART (Better for Mobile)
        fig = px.line(item_data, x='Date', y='Stock', title=f"Projected Stock: {selected_desc}", markers=True)
        # Add a red line for 0 and orange for safety stock
        safety_level = skus[skus['sku_id'] == selected_id]['safety_threshold'].iloc[0]
        fig.add_hline(y=0, line_dash="dash", line_color="red", annotation_text="Empty")
        fig.add_hline(y=safety_level, line_dash="dot", line_color="orange", annotation_text="Safety")
        fig.update_layout(xaxis_title="", yaxis_title="Stock Level", height=350)
        st.plotly_chart(fig, use_container_width=True)

        # In/Out Tables
        c1, c2 = st.columns(2)
        with c1:
            st.caption("Incoming POs")
            po_view = inbound[inbound['sku_id'] == selected_id][['po_number', 'qty', 'arrival_date']]
            st.dataframe(po_view, hide_index=True, use_container_width=True)
        with c2:
            st.caption("Outgoing Orders")
            ord_view = outbound[outbound['sku_id'] == selected_id][['order_number', 'qty', 'dispatch_date']]
            st.dataframe(ord_view, hide_index=True, use_container_width=True)

else:
    st.info("Waiting for data connection...")
