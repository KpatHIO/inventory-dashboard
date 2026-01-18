import streamlit as st
import pandas as pd
import plotly.express as px
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from datetime import date, timedelta

# --- CONFIGURATION ---
st.set_page_config(layout="wide", page_title="Inventory Command Center")

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
@st.cache_data(ttl=60)
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
        st.error(f"Connection Error: {e}")
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
        
        for d in date_range:
            day_in = inbound[(inbound['sku_id'] == sku_id) & (inbound['arrival_date'] == d)]['qty'].sum()
            day_out = outbound[(outbound['sku_id'] == sku_id) & (outbound['dispatch_date'] == d)]['qty'].sum()
            
            net_change = day_in - day_out
            current_stock += net_change
            
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
                'Month_Label': d.strftime("%B %Y"),
                'Stock': current_stock, 'Status': status, 'Marker': marker, 
                'Display': f"{display_stock} {marker}"
            })
            
    return pd.DataFrame(master_grid)

# --- 3. STYLING ---
def style_dataframe(df, is_summary=False, column_order=None):
    if df.empty: return df
    if is_summary:
        matrix = df.pivot(index='Description', columns='Month_Label', values='Display')
        status_matrix = df.pivot(index='Description', columns='Month_Label', values='Status')
        if column_order is not None:
            valid_cols = [c for c in column_order if c in matrix.columns]
            matrix = matrix[valid_cols]
            status_matrix = status_matrix[valid_cols]
    else:
        matrix = df.pivot(index='Description', columns='Date', values='Display')
        status_matrix = df.pivot(index='Description', columns='Date', values='Status')
        new_columns = [d.strftime('%d-%m') for d in matrix.columns] 
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

# --- HELPER ---
def format_dates_in_df(df, date_col_name):
    if df.empty or date_col_name not in df.columns: return df
    df_out = df.copy()
    df_out[date_col_name] = pd.to_datetime(df_out[date_col_name]).dt.strftime('%d-%m-%Y')
    return df_out

# --- 4. APP LAYOUT ---
st.title("📦 Inventory Command")

c1, c2, c3 = st.columns([1, 2, 1])
with c1: chosen_start_date = st.date_input("Start Date", value=date.today())
with c2: 
    view_option = st.radio("Look Ahead:", ["30 Days", "60 Days", "90 Days"], horizontal=True, index=0)
    days_map = {"30 Days": 30, "60 Days": 60, "90 Days": 90}
with c3:
    st.write("")
    if st.button("🔄 Refresh Data", type="primary", use_container_width=True):
        st.cache_data.clear()
        st.rerun()

st.divider()

# Load Data
skus, inbound, outbound = load_data_from_sheets()

if not skus.empty:
    df_processed = calculate_inventory(skus, inbound, outbound, chosen_start_date, days_map[view_option])
    
    # --- MAIN VIEW: TABS ---
    if not df_processed.empty:
        unique_months = df_processed['Month_Label'].unique()
        
        # Summary Data Logic
        summary_group = df_processed.groupby(['Description', 'Month_Label'])
        summary_data = []
        for name, group in summary_group:
            min_stock = group['Stock'].min()
            display_min = int(min_stock) if min_stock == int(min_stock) else min_stock
            if 'RED' in group['Status'].values: month_status = 'RED'
            elif 'AMBER' in group['Status'].values: month_status = 'AMBER'
            else: month_status = 'GREEN'
            summary_data.append({'Description': name[0], 'Month_Label': name[1], 'Display': f"{display_min}", 'Status': month_status})
        df_summary = pd.DataFrame(summary_data)

        # Tabs Interface
        tab_labels = ["📊 Monthly Summary"] + list(unique_months)
        tabs = st.tabs(tab_labels)

        with tabs[0]:
            st.caption("Lowest stock level per month.")
            st.dataframe(style_dataframe(df_summary, is_summary=True, column_order=unique_months), use_container_width=True, height=500)

        for i, month in enumerate(unique_months):
            with tabs[i+1]: 
                df_month = df_processed[df_processed['Month_Label'] == month]
                st.dataframe(style_dataframe(df_month, is_summary=False), use_container_width=True, height=500)
        
        # --- DEEP DIVE SECTION ---
        st.divider()
        st.subheader("🔍 Deep Dive & Visuals")
        
        drill_mode = st.radio("Inspect by:", ["Item (Graph)", "Purchase Order (PO)", "Customer Order"], horizontal=True)

        if drill_mode == "Item (Graph)":
            selected_desc = st.selectbox("Select Item:", sorted(skus['description'].unique()))
            selected_id = skus[skus['description'] == selected_desc]['sku_id'].iloc[0]
            
            # 1. THE GRAPH
            item_data = df_processed[df_processed['Description'] == selected_desc].copy()
            safety_level = skus[skus['sku_id'] == selected_id]['safety_threshold'].iloc[0]
            
            fig = px.line(item_data, x='Date', y='Stock', title=f"Stock Projection: {selected_desc}", markers=True)
            fig.add_hline(y=0, line_dash="dash", line_color="red", annotation_text="Zero")
            fig.add_hline(y=safety_level, line_dash="dot", line_color="orange", annotation_text="Safety")
            # FIX: Increased top margin (t=80) to prevent title overlapping buttons
            fig.update_layout(xaxis_title="", yaxis_title="Stock", height=350, margin=dict(l=20, r=20, t=80, b=20))
            st.plotly_chart(fig, use_container_width=True)
            
            # 2. In/Out Tables
            c1, c2 = st.columns(2)
            with c1:
                st.markdown("**Incoming Supply**")
                st.dataframe(format_dates_in_df(inbound[inbound['sku_id'] == selected_id], 'arrival_date'), use_container_width=True)
            with c2:
                st.markdown("**Outgoing Demand**")
                st.dataframe(format_dates_in_df(outbound[outbound['sku_id'] == selected_id], 'dispatch_date'), use_container_width=True)

        elif drill_mode == "Purchase Order (PO)":
            all_pos = inbound['po_number'].unique()
            if len(all_pos) > 0:
                selected_po = st.selectbox("Select PO:", all_pos)
                po_data = inbound[inbound['po_number'] == selected_po].merge(skus[['sku_id', 'description']], on='sku_id', how='left')
                st.dataframe(format_dates_in_df(po_data[['po_number', 'description', 'sku_id', 'qty', 'arrival_date']], 'arrival_date'), use_container_width=True)

        elif drill_mode == "Customer Order":
            all_orders = outbound['order_number'].unique()
            if len(all_orders) > 0:
                selected_order = st.selectbox("Select Order:", all_orders)
                order_data = outbound[outbound['order_number'] == selected_order].merge(skus[['sku_id', 'description']], on='sku_id', how='left')
                st.dataframe(format_dates_in_df(order_data[['order_number', 'description', 'sku_id', 'qty', 'dispatch_date']], 'dispatch_date'), use_container_width=True)
else:
    st.info("Waiting for data connection...")
