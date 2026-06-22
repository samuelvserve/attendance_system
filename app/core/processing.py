"""Core processing functions for attendance data"""
import re
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any
import warnings
import requests
import pytz
from openpyxl.styles import Font, Border, Alignment, PatternFill, Side
import tempfile
import os

warnings.filterwarnings("ignore")

# ============================================================================
# UTILITY FUNCTIONS (All original functions preserved)
# ============================================================================

def extract_in_out_events(punch_records):
    """Extract in and out events from punch records"""
    if pd.isna(punch_records):
        return pd.Series()
    matches = re.findall(r'(\d{2}:\d{2}):(in|out)', str(punch_records))
    in_events = [time for time, status in matches if status == 'in']
    out_events = [time for time, status in matches if status == 'out']
    
    in_out_dict = {}
    
    for i, in_time in enumerate(in_events):
        in_out_dict[f'IN {i+1}'] = in_time if in_time else pd.NaT
    
    for i, out_time in enumerate(out_events):
        in_out_dict[f'OUT {i+1}'] = out_time if out_time else pd.NaT
    
    return pd.Series(in_out_dict)

def adjust_timestamp(time, date_str):
    """Adjust timestamp for overnight shifts"""
    if isinstance(time, str) and time:
        try:
            timestamp = f"{date_str} {time}"
            timestamp_dt = datetime.strptime(timestamp, "%Y-%m-%d %H:%M")
            if timestamp_dt.time() < datetime.strptime("07:00", "%H:%M").time():
                timestamp_dt += timedelta(days=1)
            return timestamp_dt.strftime("%Y-%m-%d %H:%M")
        except:
            return pd.NaT
    return pd.NaT

def find_last_non_nan(row):
    """Find last non-NaN value in a row"""
    for value in row:
        if not pd.isna(value):
            return value
    return pd.NaT

def count_in_out(row):
    """Count in and out times"""
    in_columns = [col for col in row.index if 'IN ' in col]
    out_columns = [col for col in row.index if 'OUT ' in col]
    in_count = sum(1 for col in in_columns if pd.notna(row[col]))
    out_count = sum(1 for col in out_columns if pd.notna(row[col]))
    return in_count, out_count

def number_to_hr(time_str):
    """Convert timedelta to human readable format"""
    if pd.isna(time_str):
        return pd.NaT
    if isinstance(time_str, (int, float)):
        total_seconds = time_str * 3600
    else:
        total_seconds = time_str.total_seconds()
    minutes = total_seconds // 60
    hours = int(minutes // 60)
    remaining_minutes = int(minutes % 60)
    remaining_seconds = int(total_seconds % 60)

    parts = []
    if hours:
        parts.append(f"{hours} hr{'s' if hours > 1 else ''}")
    if remaining_minutes:
        parts.append(f"{remaining_minutes} min{'s' if remaining_minutes > 1 else ''}")
    if remaining_seconds and not hours and not remaining_minutes:
        parts.append(f"{remaining_seconds} sec{'s' if remaining_seconds > 1 else ''}")
    
    return " ".join(parts) if parts else pd.NaT

def clean_time_string(time_str):
    """Clean time string to timedelta"""
    try:
        if pd.isna(time_str) or time_str == '':
            return pd.NaT
        return pd.to_timedelta(str(time_str))
    except Exception:
        return pd.NaT

def parse_time_string(x):
    """Parse time string to timedelta"""
    if not isinstance(x, str):
        return 0
    
    parts = x.split()
    hours, minutes, seconds = 0, 0, 0
    
    for i in range(0, len(parts), 2):
        if i+1 >= len(parts):
            break
        value = int(parts[i])
        unit = parts[i+1]
        if 'hr' in unit:
            hours = value
        elif 'min' in unit:
            minutes = value
        elif 'sec' in unit:
            seconds = value
    
    return pd.Timedelta(hours=hours, minutes=minutes, seconds=seconds)

# ============================================================================
# ATTENDANCE CALCULATION FUNCTIONS
# ============================================================================

def attendance_determine_status(team, worked_hours):
    """Determine attendance status"""
    hours = float(worked_hours) if not pd.isna(worked_hours) else 0
    
    if team in ('Rollkall', 'TE'):
        if hours == 0:
            return 'Absent'
        elif 3.75 <= hours <= 7.24:
            return 'Half day'
        elif hours >= 7.25:
            return 'Full day'
        else:
            return 'Absent'
    else:
        if hours == 0:
            return 'Absent'
        elif 3.75 <= hours < 7.74:
            return 'Half day'
        elif hours >= 7.75:
            return 'Full day'
        else:
            return 'Absent'

def below_7_5_hours(team, hours):
    """Check if worked hours are below threshold"""
    if pd.isna(hours):
        return pd.NA
    hours = float(hours)
    if team in ('Rollkall', 'TE'):
        if 0 <= hours < 3.75:
            return 'Worked below 4 hrs'
        elif 3.75 <= hours < 7.25:
            return 'Worked below 8 hrs'
    else:
        if 0 <= hours < 3.75:
            return 'Worked below 4 hrs'
        elif 3.75 <= hours < 7.75:
            return 'Worked below 8 hrs'
    return pd.NA

def is_break_greater_than_one_hour(team, break_hours):
    """Check if break is greater than threshold"""
    if pd.isna(break_hours):
        return pd.NA
    hours = float(break_hours)
    if team in ('Rollkall', 'TE'):
        return 'Break more than 1 hr 30 mins' if hours >= 1.59 else pd.NaT
    else:
        return 'Break more than 1 hr' if hours > 1.17 else pd.NaT

def convert_shift_format(shift):
    """Convert shift format to standard"""
    if pd.isna(shift):
        return pd.NaT
    shift = str(shift).strip()
    if shift == 'GS':
        return '09:00 IST - 18:00 IST'
    if shift == 'NS':
        return '22:00 IST - 06:00 IST'
    if 'IST' in shift and ' - ' in shift:
        return shift
    return shift

def get_shift_start(shift_str):
    """Get shift start time"""
    if isinstance(shift_str, str):
        start_time_str = shift_str.split(' - ')[0].strip().replace('IST', '').strip()
        try:
            return pd.to_datetime(start_time_str, format='%H:%M').time()
        except ValueError:
            return pd.NaT
    return pd.NaT

def calculate_late_login(in_time_str, shift_start_time):
    """Calculate late login status"""
    if pd.isna(in_time_str) or pd.isna(shift_start_time):
        return pd.NaT
    try:
        in_time = pd.to_datetime(in_time_str).time()
        today = datetime.today().date()
        shift_dt = datetime.combine(today, shift_start_time) + timedelta(minutes=30)
        login_dt = datetime.combine(today, in_time)
        if shift_start_time.hour >= 18 and in_time.hour < 12:
            login_dt += timedelta(days=1)
        late_time = login_dt - shift_dt
        if late_time < timedelta(0):
            return "Early Login"
        if late_time <= timedelta(minutes=5):
            return "No Delay"
        total_mins = int(late_time.total_seconds() // 60)
        hrs, mins = divmod(total_mins, 60)
        if hrs and mins:
            return f"{hrs} hrs {mins} mins late"
        elif hrs:
            return f"{hrs} hrs late"
        else:
            return f"{mins} mins late"
    except:
        return pd.NaT

def calculate_early_logout(out_time_str, shift, grace_min=5):
    """Calculate early logout"""
    if pd.isna(out_time_str) or pd.isna(shift):
        return pd.NaT
    try:
        start_str, end_str = [re.sub(r'[A-Z]+', '', x).strip() for x in shift.split(' - ')]
        shift_start = datetime.strptime(start_str, "%H:%M")
        shift_end = datetime.strptime(end_str, "%H:%M")
        # Handle both "HH:MM" and full datetime strings like "2024-01-15 17:30"
        try:
            out_time = datetime.strptime(out_time_str, "%H:%M")
        except ValueError:
            out_time = pd.to_datetime(out_time_str)
            out_time = datetime.strptime(out_time.strftime("%H:%M"), "%H:%M")
        today = datetime.today().date()
        shift_start = datetime.combine(today, shift_start.time())
        shift_end = datetime.combine(today, shift_end.time())
        out_time = datetime.combine(today, out_time.time())
        if shift_end <= shift_start:
            shift_end += timedelta(days=1)
            if out_time.hour < 12:
                out_time += timedelta(days=1)
        diff = shift_end - out_time
        if diff <= timedelta(minutes=grace_min):
            return "No Early Logout"
        hours, remainder = divmod(int(diff.total_seconds()), 3600)
        minutes = remainder // 60
        if hours and minutes:
            return f"{hours} hrs {minutes} mins early"
        elif hours:
            return f"{hours} hrs early"
        return f"{minutes} mins early"
    except:
        return pd.NaT

def OutPunch_status(val):
    """Check if out punch is missing"""
    if val.get('IN_Time Count', 0) != val.get('OUT_Time Count', 0):
        return 'User No OutPunch'
    return pd.NA

def normalize_shift(value):
    """Normalize shift format"""
    if pd.isna(value):
        return value
    s = str(value).strip().upper()
    s = re.sub(r'\s+', ' ', s)
    if s == "GS":
        return "09:00 IST - 18:00 IST"
    if s == "NS":
        return "22:00 IST - 06:00 IST"
    if s == "XMEK":
        return "XMEK"
    m = re.search(r'(\d{1,2})\.(\d{2}).*?TO\s*(\d{1,2})\.(\d{2})', s)
    if m:
        sh, sm, eh, em = m.groups()
        return f"{int(sh):02d}:{sm} IST - {int(eh):02d}:{em} IST"
    return s

def convert_to_cst(time_value, team):
    """Convert IST time(s) to CST"""
    if team != 'Rollkall' or pd.isna(time_value):
        return time_value
    ist_tz = pytz.timezone('Asia/Kolkata')
    cst_tz = pytz.timezone('US/Central')
    value = str(time_value).strip()
    if ' - ' in value:
        value = value.replace('IST', '')
        start_str, end_str = value.split(' - ')
        start_dt_ist = ist_tz.localize(datetime.strptime(start_str.strip(), "%H:%M"))
        end_dt_ist = ist_tz.localize(datetime.strptime(end_str.strip(), "%H:%M"))
        start_cst = start_dt_ist.astimezone(cst_tz)
        end_cst = end_dt_ist.astimezone(cst_tz)
        return f"{start_cst.strftime('%H:%M CST')} - {end_cst.strftime('%H:%M CST')}"
    else:
        single_dt_ist = ist_tz.localize(datetime.strptime(value.replace('IST', '').strip(), "%H:%M"))
        single_cst = single_dt_ist.astimezone(cst_tz)
        return single_cst.strftime("%H:%M")

def hr_api_call(min_date, max_date):
    """Call HR API"""
    data = {'from_date': min_date, 'to_date': max_date}
    headers = {
        "Cookie": "humans_21909=1",
        "User-Agent": "curl/8.0",
        "Content-Type": "application/x-www-form-urlencoded"
    }
    response = requests.post('https://vservehr.com/attendance_api.php', data=data, headers=headers, timeout=30)
    return response.json()['data']

def attendance_mismatch(attendance_row, hr_attendance_row, half_leaves):
    """Check attendance mismatch"""
    if hr_attendance_row == 'Present' and attendance_row in ['Half day','Absent','WeekOff']:
        return 'YES'
    elif hr_attendance_row in half_leaves and attendance_row in ['Absent', 'WeekOff']:
        return 'YES'
    elif hr_attendance_row == 'No Data':
        return 'YES'
    else:
        return pd.NaT

def map_late_login_status(status):
    """Map late login status to numeric"""
    if pd.isna(status) or status in ['Early Login', 'No Delay']:
        return 0
    return 1

# ============================================================================
# MAIN PROCESSING FUNCTIONS
# ============================================================================

def final_adjustments(df):
    """Apply final adjustments to dataframe"""
    try:
        # Calculate attendance status
        df['Attendance'] = df.apply(lambda row: attendance_determine_status(row['Team/Department'], row['Worked Hours (Number)']), axis=1)
        df['Break Hours Status'] = df.apply(lambda row: is_break_greater_than_one_hour(row['Team/Department'], row['Break Hours (Number)']), axis=1)
        df['Worked Hours Status'] = df.apply(lambda row: below_7_5_hours(row['Team/Department'], row['Worked Hours (Number)']), axis=1)
        
        # Convert hours to readable format
        for column in ['Worked Hours', 'Idle Hours', 'Break Hours']:
            if column in df.columns:
                df[column] = df[column].apply(number_to_hr)
        
        # Handle special shifts
        df.loc[df['Shift Timing'].isin(['00:00 IST - 23:55 IST', '12:00 IST - 11:55 IST','00:00 IST - 23:59 IST']), 'Actual Shift Time'] = df['Shift Timing']
        
        if 'Logout Time' in df.columns and 'Actual Shift Time' in df.columns:
            df['Early Logout Status'] = df.apply(lambda row: calculate_early_logout(row['Logout Time'], row['Actual Shift Time']), axis=1)
        
        df.loc[df['Shift Timing'].isin(['00:00 IST - 23:55 IST', '12:00 IST - 11:55 IST','00:00 IST - 23:59 IST']), 'Late Login Status'] = pd.NaT
        df.loc[df['Shift Timing'].isin(['00:00 IST - 23:55 IST', '12:00 IST - 11:55 IST','00:00 IST - 23:59 IST']), 'Early Logout Status'] = pd.NaT

        # Process dates
        df['Date'] = pd.to_datetime(df['Date'], format='%Y-%m-%d', errors='coerce')
        df['Day'] = df['Date'].dt.strftime('%a')
        df.loc[(df['Day'].isin(['Sat', 'Sun'])) & (df['Worked Hours'].isnull()), 'Attendance'] = 'WeekOff'
        
        # Get date range for API call
        min_date = df['Date'].min().strftime('%Y-%m-%d') if not df['Date'].isna().all() else None
        max_date = df['Date'].max().strftime('%Y-%m-%d') if not df['Date'].isna().all() else None
        
        df['Date'] = df['Date'].dt.strftime('%Y-%m-%d')
        
        # Call HR API if dates available
        if min_date and max_date:
            try:
                data = hr_api_call(min_date, max_date)
                hr_df = pd.DataFrame(data).astype(str)
                rename_col = {'emp_id': 'Employee ID', 'date': 'Date', 'attendance': 'HR Attendance'}
                hr_df.rename(columns=rename_col, inplace=True)
                df['Employee ID'] = df['Employee ID'].astype(str).str.strip()
                hr_df['Employee ID'] = hr_df['Employee ID'].astype(str).str.strip()
                df['Date'] = pd.to_datetime(df['Date']).dt.date.astype(str)
                hr_df['Date'] = pd.to_datetime(hr_df['Date']).dt.date.astype(str)
                df = pd.merge(df, hr_df, how='left', on=['Employee ID', 'Date'])
                df['HR Attendance'] = df['HR Attendance'].fillna('No Data')
                
                # Handle leave shifts
                leave_shifts = ['Leave - Full Day', 'Absent - Full Day', 'Sick Leave - Full', 'EL FD', 'Maternity Leave', 'Bereavement Leave', 'Week Off', 'Paternity Leave', 'Adoption Leave','Paid Holiday','Comp Off']
                cols_to_null = ['Login Time', 'Logout Time', 'Late Login Status', 'Worked Hours', 'Worked Hours (Number)', 'Idle Hours', 'Away Hours', 'Break Hours', 'Break Hours (Number)', 'Worked Hours Status', 'Break Hours Status', 'No of Breaks (Biometric)', 'No of Break Counts more than 4 Times (Biometric)', 'Comments']
                existing_cols = [col for col in cols_to_null if col in df.columns]
                df.loc[df['HR Attendance'].isin(leave_shifts), existing_cols] = np.nan
                    
                half_leaves = ['Sick Leave - Half', 'EL HD', 'Leave - Half Day', 'Absent - Half Day']
                df.loc[df['HR Attendance'].isin(half_leaves) & (df['Worked Hours Status'] == 'Worked below 8 hrs'), 'Worked Hours Status'] = np.nan
                df.loc[df['HR Attendance'].isin(half_leaves), 'Break Hours Status'] = np.nan
                df.loc[df['HR Attendance'].isin(half_leaves), 'Early Logout Status'] = np.nan
                df.loc[df['HR Attendance'].isin(half_leaves), 'Late Login Status'] = np.nan
                df.loc[(df['Worked Hours (Number)'].fillna(0) < 1) & (df['HR Attendance'] != 'Present'), 'Late Login Status'] = pd.NaT
                df.loc[(df['Worked Hours (Number)'].fillna(0) < 1) & (df['HR Attendance'] != 'Present'), 'Early Logout Status'] = pd.NaT
                df['Attendance Mismatch'] = df.apply(lambda row: attendance_mismatch(row['Attendance'], row['HR Attendance'], half_leaves), axis=1)
            except Exception as e:
                df['HR Attendance'] = 'No Data'
                df['Attendance Mismatch'] = pd.NaT

        # Define column order
        base_cols = ['Date', 'Day', 'Employee ID', 'Employee Name', 'Team/Department', 'Source']
        if 'HR Attendance' in df.columns:
            base_cols.extend(['Attendance', 'HR Attendance', 'Attendance Mismatch'])
        else:
            base_cols.extend(['Attendance'])
        
        base_cols.extend(['Shift Timing', 'Actual Shift Time', 'Login Time', 'Logout Time', 'Late Login Status', 'Early Logout Status', 'Worked Hours', 'Worked Hours (Number)', 'Worked Hours Status', 'Break Hours', 'Break Hours (Number)', 'Break Hours Status'])
        optional_cols = ['Idle Hours', 'Away Hours', 'No of Breaks (Biometric)', 'No of Break Counts more than 4 Times (Biometric)', 'Comments']
        for col in optional_cols:
            if col in df.columns:
                base_cols.append(col)
        
        final_cols = [col for col in base_cols if col in df.columns]
        df = df[final_cols]
        
        # Convert to CST
        columns_to_convert = ['Shift Timing', 'Actual Shift Time', 'Login Time', 'Logout Time']
        for col in columns_to_convert:
            if col in df.columns:
                df[col] = df.apply(lambda row: convert_to_cst(row[col], row['Team/Department']), axis=1)
            
        df['Attendance'] = df['Attendance'].fillna('Absent')
        df = df.replace(0, pd.NaT)

        if 'Employee Name' in df.columns:
            df['Employee Name'] = df['Employee Name'].replace(r'\s+', ' ', regex=True).str.strip()
        
        sort_cols = ['Employee Name' if 'Employee Name' in df.columns else 'Employee ID', 'Date']
        sort_cols = [col for col in sort_cols if col in df.columns]
        if sort_cols:
            df = df.sort_values(by=sort_cols)

        df.insert(0, 'SL No.', range(1, len(df) + 1))
        
        return df
    except Exception as e:
        raise Exception(f"Error in final adjustments: {str(e)}")

def process_biometric_data(df):
    """Process biometric data"""
    try:
        df['Date'] = pd.to_datetime(df['Date'], format='%Y-%m-%d', dayfirst=True).dt.strftime('%Y-%m-%d')
        df['Shift'] = df['Shift'].apply(normalize_shift)
        
        df = df.reset_index()
        maindf = df.copy()
        
        # Process punch records
        df = df[df['Punch Records'].notnull()]
        in_out_df = df['Punch Records'].apply(extract_in_out_events)
        final_df = pd.concat([df, in_out_df], axis=1)

        # Sort and interleave IN and OUT columns
        in_columns = [col for col in final_df.columns if col.startswith('IN')]
        out_columns = [col for col in final_df.columns if col.startswith('OUT')]
        in_columns_sorted = sorted(in_columns, key=lambda x: int(x[2:]))
        out_columns_sorted = sorted(out_columns, key=lambda x: int(x[3:]))
        interleaved_columns = [col for pair in zip(in_columns_sorted, out_columns_sorted) for col in pair]
        if len(in_columns_sorted) > len(out_columns_sorted):
            interleaved_columns += in_columns_sorted[len(out_columns_sorted):]
        elif len(out_columns_sorted) > len(in_columns_sorted):
            interleaved_columns += out_columns_sorted[len(in_columns_sorted):]

        df = final_df[['index', 'Date', 'Shift'] + interleaved_columns]

        # Adjust timestamps
        time_columns = [col for col in df.columns if "IN" in col or "OUT" in col]
        for idx, row in df.iterrows():
            date_str = row['Date']
            for col in time_columns:
                df.at[idx, col] = adjust_timestamp(row[col], date_str)

        # Calculate First IN and Last OUT
        in_cols = df.filter(like='IN')
        if not in_cols.empty:
            df['First_IN'] = in_cols.iloc[:, 0]
        else:
            df['First_IN'] = pd.NaT
        
        out_columns_filter = df.filter(like='OUT')
        if not out_columns_filter.empty:
            reversed_out_columns = out_columns_filter.iloc[:, ::-1]
            df['Last_OUT'] = reversed_out_columns.apply(find_last_non_nan, axis=1)
        else:
            df['Last_OUT'] = pd.NaT

        # Count IN and OUT times
        df[["IN_Time Count", "OUT_Time Count"]] = df.apply(count_in_out, axis=1).tolist()
        df.columns = df.columns.str.strip()

        # Calculate work and break hours
        for i in range(1, 30):
            out_col = f'OUT {i}'
            in_col = f'IN {i+1}'
            out_col1 = f'OUT {i}'
            in_col1 = f'IN {i}'
            if out_col1 in df.columns:
                df[f'Work_{i}'] = (pd.to_datetime(df[out_col1], format='%Y-%m-%d %H:%M') - pd.to_datetime(df[in_col1], format='%Y-%m-%d %H:%M')).dt.seconds // 60
            if in_col in df.columns:
                df[f'Break_{i}'] = (pd.to_datetime(df[in_col], format='%Y-%m-%d %H:%M') - pd.to_datetime(df[out_col], format='%Y-%m-%d %H:%M')).dt.seconds // 60

        break_columns = [col for col in df.columns if "Break_" in col]
        work_columns = [col for col in df.columns if "Work_" in col]

        df['Worked Hours'] = df[work_columns].sum(axis=1) if work_columns else 0
        df['Break Hours'] = df[break_columns].sum(axis=1) if break_columns else 0
        df['Worked Hours'] = pd.to_timedelta(df['Worked Hours'], unit='m')
        df['Break Hours'] = pd.to_timedelta(df['Break Hours'], unit='m')

        # Calculate status and attendance
        df['Comments'] = df.apply(OutPunch_status, axis=1)

        # Calculate break hours
        df['Worked Hours (Number)'] = df['Worked Hours'].apply(lambda x: round(pd.Timedelta(x).total_seconds() / 3600, 2) if x else pd.NaT)
        df['Break Hours (Number)'] = df['Break Hours'].apply(lambda x: round(pd.Timedelta(x).total_seconds() / 3600, 2) if x else pd.NaT)

        # Merge with main dataframe
        df = pd.merge(maindf, df, how='left', on='index')
        df.rename(columns={'Team': 'Team Name'}, inplace=True)
        df['Source'] = 'Biometric'

        df['No of Breaks (Biometric)'] = df['IN_Time Count'] - 1

        df['Shift Timing'] = df['Shift_x'].apply(convert_shift_format)
        df['Actual Shift Time'] = df['Shift Timing']
        df['Shift Start'] = df['Shift Timing'].apply(get_shift_start)

        df['No of Break Counts more than 4 Times (Biometric)'] = (df['No of Breaks (Biometric)'].gt(4).map({True: 'Yes', False: pd.NA}))

        def calculate_late_login_safe(in_time_str, shift_start_time):
            if pd.isna(in_time_str) or pd.isna(shift_start_time):
                return pd.NaT
            try:
                in_time = pd.to_datetime(in_time_str).time()
                shift_start_dt = datetime.combine(datetime.today(), shift_start_time)
                in_time_dt = datetime.combine(datetime.today(), in_time)
                late_minutes = in_time_dt - shift_start_dt
                if in_time_dt > shift_start_dt:
                    if late_minutes > timedelta(minutes=5):
                        hours = int(late_minutes.total_seconds() // 3600)
                        minutes = int((late_minutes.total_seconds() % 3600) // 60)
                        if hours and minutes:
                            return f"{hours} hrs {minutes} mins late"
                        elif not hours and minutes:
                            return f"{minutes} mins late"
                        elif hours and not minutes:
                            return f"{hours} hrs late"
                    else:
                        return 'No Delay'
                elif in_time_dt < shift_start_dt:
                    return 'Early Login'
                else:
                    return 'No Delay'
            except:
                return pd.NaT
        
        df['Late Login Status'] = df.apply(lambda row: calculate_late_login_safe(row['First_IN'], row['Shift Start']), axis=1)

        df['Login Time'] = pd.to_datetime(df['First_IN']).dt.strftime("%H:%M")
        df['Logout Time'] = pd.to_datetime(df['Last_OUT']).dt.strftime("%H:%M")

        df['Idle Hours'] = pd.NaT
        df['Away Hours'] = pd.NaT

        rename_col = {'Employee Code': 'Employee ID', 'Team Name': 'Team/Department', 'Date_x': 'Date'}
        df.rename(columns=rename_col, inplace=True)

        df = final_adjustments(df)

        return df
    except Exception as e:
        raise Exception(f"Error processing biometric data: {str(e)}")

def process_timechamp_data(df):
    """Process Timechamp data"""
    try:
        try:
            df['Date'] = pd.to_datetime(df['Date'].str.strip(), format='%b-%d-%Y', errors='coerce')
            df['Date'] = df['Date'].dt.strftime('%Y-%m-%d')
        except Exception:
            df['Date'] = pd.to_datetime(df['Date'], errors='coerce', format='mixed').dt.strftime('%Y-%m-%d')

        df['Source'] = 'Timechamp'
        df.rename(columns={'User': 'Employee Name'}, inplace=True)

        required_cols = ['Date', 'Employee Id', 'Employee Name', 'Team Name', 'Shift', 'In Time', 'Out Time', 'Working Hours', 'Away Time', 'Idle Time', 'Break Time']
        missing_cols = [col for col in required_cols if col not in df.columns]
        if missing_cols:
            raise ValueError(f"Missing required columns: {', '.join(missing_cols)}")

        df = df[required_cols]
        
        for column in ['Working Hours', 'Idle Time', 'Away Time', 'Break Time']:
            df[column] = df[column].apply(clean_time_string)

        df['Login Time'] = pd.to_datetime(df['In Time'].astype(str), format='mixed').dt.strftime('%H:%M')
        df['Logout Time'] = pd.to_datetime(df['Out Time'].astype(str), format='mixed').dt.strftime('%H:%M')

        df['Worked Hours (Number)'] = df['Working Hours'].apply(lambda x: round(pd.Timedelta(x).total_seconds() / 3600, 2) if x else pd.NaT)
        df['Break Hours (Number)'] = df['Away Time'].apply(lambda x: round(pd.Timedelta(x).total_seconds() / 3600, 2) if x else pd.NaT)
        df['Break Time'] = df['Break Time'].apply(lambda x: round(pd.Timedelta(x).total_seconds() / 3600, 2) if x else pd.NaT)

        df['Shift Start'] = df['Shift'].apply(get_shift_start)
        
        def get_actual_shift_time(shift):
            if pd.isna(shift):
                return pd.NaT
            try:
                start, end = shift.split(' - ')
                start_time = datetime.strptime(start, '%H:%M IST')
                end_time = datetime.strptime(end, '%H:%M IST')
                actual_start = (start_time + timedelta(minutes=30)).strftime('%H:%M')
                actual_end = (end_time - timedelta(minutes=30)).strftime('%H:%M')
                return f"{actual_start} IST - {actual_end} IST"
            except:
                return shift

        df['Actual Shift Time'] = df['Shift'].apply(get_actual_shift_time)
        df['Late Login Status'] = df.apply(lambda row: calculate_late_login(row['Login Time'], row['Shift Start']), axis=1)
        df['No of Break Counts more than 4 Times (Biometric)'] = pd.NaT
        df['No of Breaks (Biometric)'] = pd.NaT
        df['Comments'] = pd.NaT

        rename_col = {'Employee Id': 'Employee ID', 'Team Name': 'Team/Department', 'Shift': 'Shift Timing', 'Idle Time': 'Idle Hours', 'Break Time': 'Away Hours', 'Away Time': 'Break Hours', 'Working Hours': 'Worked Hours'}
        df.rename(columns=rename_col, inplace=True)

        df = final_adjustments(df)

        return df
    except Exception as e:
        raise Exception(f"Error processing Timechamp data: {str(e)}")

def process_manualtime_data(df):
    """Process manual tracking data"""
    try:
        df['Source'] = 'Manual Tracking'
        df['Date'] = pd.to_datetime(df['Date'], errors='coerce').dt.strftime('%Y-%m-%d')

        required_cols = ['Date', 'Employee ID', 'Employee Name', 'Team/Department', 'Shift Timing', 'Login Time', 'Logout Time', 'Manual Hours', 'Break Hours']
        missing_cols = [col for col in required_cols if col not in df.columns]
        if missing_cols:
            raise ValueError(f"Missing required columns: {', '.join(missing_cols)}")

        df = df[required_cols]
        
        df['Worked Hours'] = df['Manual Hours'].apply(clean_time_string)
        df['Break Hours'] = df['Break Hours'].apply(clean_time_string)

        df['Worked Hours (Number)'] = df['Worked Hours'].apply(lambda x: round(pd.Timedelta(x).total_seconds() / 3600, 2) if x else pd.NaT)
        df['Break Hours (Number)'] = df['Break Hours'].apply(lambda x: round(pd.Timedelta(x).total_seconds() / 3600, 2) if x else pd.NaT)
        df['Shift Start'] = df['Shift Timing'].apply(get_shift_start)
        
        def get_actual_shift_time(shift):
            if pd.isna(shift):
                return pd.NaT
            try:
                start, end = shift.split(' - ')
                start_time = datetime.strptime(start, '%H:%M IST')
                end_time = datetime.strptime(end, '%H:%M IST')
                actual_start = (start_time + timedelta(minutes=30)).strftime('%H:%M')
                actual_end = (end_time - timedelta(minutes=30)).strftime('%H:%M')
                return f"{actual_start} IST - {actual_end} IST"
            except:
                return shift

        df['Actual Shift Time'] = df['Shift Timing'].apply(get_actual_shift_time)
        df['Late Login Status'] = df.apply(lambda row: calculate_late_login(row['Login Time'], row['Shift Start']), axis=1)
        df['Idle Hours'] = pd.NaT
        df['Away Hours'] = pd.NaT
        df['No of Break Counts more than 4 Times (Biometric)'] = pd.NaT
        df['No of Breaks (Biometric)'] = pd.NaT
        df['Comments'] = pd.NaT
        df = final_adjustments(df)
        return df
    except Exception as e:
        raise Exception(f"Error processing manual data: {str(e)}")

# ============================================================================
# REPORT GENERATION FUNCTIONS
# ============================================================================

def create_pivot_table(df):
    """Create pivot table from detailed report"""
    try:
        pivot_table = df.copy()
        attendance_mapping = {'Absent': 0,'WeekOff': 0,'Full day': 1,'Half day': 0.5}

        pivot_table['No of Worked Days'] = pivot_table['Attendance'].map(attendance_mapping)

        pivot_table['No of Worked below 8 hrs'] = pivot_table['Worked Hours Status'].apply(lambda x: 1 if x == 'Worked below 8 hrs' else 0)
        pivot_table['No of Worked below 4 hrs'] = pivot_table['Worked Hours Status'].apply(lambda x: 1 if x == 'Worked below 4 hrs' else 0)
        pivot_table['No of More Break Hours'] = pivot_table['Break Hours Status'].apply(lambda x: 1 if isinstance(x, str) else 0)
        if 'Attendance Mismatch' in pivot_table.columns:
            pivot_table['No of Attendance Mismatch'] = pivot_table['Attendance Mismatch'].apply(lambda x: 1 if isinstance(x, str) else 0)
        else:
            pivot_table['No of Attendance Mismatch'] = 0

        half_leaves = ['Sick Leave - Half', 'EL HD', 'Leave - Half Day', 'Absent - Half Day']

        def hr_attendance(value):
            if value in half_leaves:
                return 0.5 * 8
            elif value == 'Present':
                return 1 * 8
            return 0

        def hr_attendance_count(value):
            if value in half_leaves:
                return 0.5
            elif value == 'Present':
                return 1
            return 0

        if 'HR Attendance' in pivot_table.columns:
            pivot_table['Total HR Attendance Hours'] = pivot_table['HR Attendance'].apply(hr_attendance)
            pivot_table['No of Worked Days (Attendance)'] = pivot_table['HR Attendance'].apply(hr_attendance_count)
        else:
            pivot_table['Total HR Attendance Hours'] = 0
            pivot_table['No of Worked Days (Attendance)'] = 0

        pivot_table['No of late login'] = pivot_table['Late Login Status'].apply(map_late_login_status)
        pivot_table['No of Early Logout'] = pivot_table['Early Logout Status'].apply(lambda x: 0 if pd.isna(x) or x == 'No Early Logout' else 1)
        
        pivot_table['No of Breaks (Biometric)'] = pivot_table['No of Breaks (Biometric)'].replace('', 0).fillna(0).astype(int)
        pivot_table['No of Break Counts more than 4 Times (Biometric)'] = pivot_table['No of Breaks (Biometric)'].apply(lambda x: 1 if x > 4 else 0)

        pivot_table['Total Worked Hours'] = pivot_table['Worked Hours'].apply(parse_time_string)
        pivot_table['Total Break Hours'] = pivot_table['Break Hours'].apply(parse_time_string)
        
        time_cols = ['Total Worked Hours', 'Total Break Hours']
        for col in time_cols:
            # Values are already pd.Timedelta from parse_time_string; coerce non-timedelta to NaT
            pivot_table[col] = pd.to_timedelta(pivot_table[col], errors='coerce')
            
        group_cols = ['Employee ID', 'Employee Name', 'Team/Department', 'Source']
        existing_group_cols = [col for col in group_cols if col in pivot_table.columns]
        
        agg_dict = {
            'No of Worked Days': 'sum',
            'No of Worked Days (Attendance)': 'sum',        
            'No of late login': 'sum',
            'No of Early Logout': 'sum',
            'No of Worked below 4 hrs': 'sum',
            'No of Worked below 8 hrs': 'sum',
            'No of More Break Hours': 'sum',
            'No of Break Counts more than 4 Times (Biometric)': 'sum',
            'Total HR Attendance Hours': 'sum',
            'Total Worked Hours': 'sum',
            'Total Break Hours': 'sum',
        }
        
        existing_agg = {k: v for k, v in agg_dict.items() if k in pivot_table.columns}
        
        pivot_table = pivot_table.groupby(existing_group_cols).agg(existing_agg).fillna(0)

        pivot_table = pivot_table.reset_index()

        pivot_table['Total Worked Hours'] = pivot_table['Total Worked Hours'].apply(lambda x: round(x.total_seconds() / 3600, 2) if pd.notna(x) and hasattr(x, 'total_seconds') else pd.NaT)
        pivot_table['Total Break Hours'] = pivot_table['Total Break Hours'].apply(lambda x: round(x.total_seconds() / 3600, 2) if pd.notna(x) and hasattr(x, 'total_seconds') else pd.NaT)

        if 'Total HR Attendance Hours' in pivot_table.columns:
            pivot_table['Total HR Attendance Hours'] = pd.to_timedelta(pivot_table['Total HR Attendance Hours'], unit='h', errors='coerce')
            pivot_table['Worked Hours Diff'] = pivot_table.apply(
                lambda row: round((pd.Timedelta(hours=row['Total Worked Hours']) - row['Total HR Attendance Hours']).total_seconds() / 3600, 2)
                if pd.notna(row.get('Total Worked Hours')) and pd.notna(row['Total HR Attendance Hours']) else pd.NaT, axis=1
            )
            pivot_table['Total HR Attendance Hours'] = pivot_table['Total HR Attendance Hours'].apply(lambda x: round(x.total_seconds() / 3600, 2) if pd.notna(x) else pd.NaT)
        else:
            pivot_table['Total HR Attendance Hours'] = 0
            pivot_table['Worked Hours Diff'] = pd.NaT

        pivot_table.insert(0, 'SL No.', range(1, len(pivot_table) + 1))
        return pivot_table
    except Exception as e:
        raise Exception(f"Error creating pivot table: {str(e)}")

def create_highlighted_data(df):
    """Create highlighted data for users with issues"""
    try:
        highlight_data = df.copy()
        highlight_data = highlight_data.replace({'Early Login': pd.NaT, 'No Delay': pd.NaT, 'No Early Logout': pd.NaT})
        
        issue_cols = ['Late Login Status', 'Early Logout Status', 'Worked Hours Status', 
                      'Break Hours Status', 'No of Break Counts more than 4 Times (Biometric)', 'Attendance Mismatch']
        existing_issue_cols = [col for col in issue_cols if col in highlight_data.columns]
        
        if existing_issue_cols:
            highlight_data = highlight_data.dropna(subset=existing_issue_cols, how='all')
        
        if 'SL No.' in highlight_data.columns:
            highlight_data = highlight_data.drop('SL No.', axis=1)
        
        highlight_data.insert(0, 'SL No.', range(1, len(highlight_data) + 1))
        return highlight_data
    except Exception as e:
        raise Exception(f"Error creating highlighted data: {str(e)}")

def save_to_excel(df, pivot_table, highlight_data, source_type):
    """Save dataframes to Excel file with formatting and return file path"""
    try:
        temp_dir = tempfile.mkdtemp()
        
        # Determine date range
        df_copy = df.copy()
        if 'Date' in df_copy.columns:
            df_copy['Date'] = pd.to_datetime(df_copy['Date'])
            min_date = df_copy['Date'].min()
            max_date = df_copy['Date'].max()
            
            min_date_str = min_date.strftime('%d-%b-%Y')
            max_date_str = max_date.strftime('%d-%b-%Y')
            
            if min_date_str != max_date_str:
                file_name = f"{source_type} Report {min_date_str} to {max_date_str}.xlsx"
            else:
                file_name = f"{source_type} Report {min_date_str}.xlsx"
        else:
            file_name = f"{source_type} Report.xlsx"
        
        file_path = os.path.join(temp_dir, file_name)
        
        with pd.ExcelWriter(file_path, engine='openpyxl') as writer:
            df.to_excel(writer, index=False, sheet_name='Detailed Report')
            highlight_data.to_excel(writer, index=False, sheet_name='Highlighted Users')
            pivot_table.to_excel(writer, index=False, sheet_name='Overall Summary')
            
            # Apply styling
            workbook = writer.book
            for sheet_name in ['Detailed Report', 'Highlighted Users', 'Overall Summary']:
                worksheet = workbook[sheet_name]
                
                # Set tab color
                if sheet_name == 'Detailed Report':
                    worksheet.sheet_properties.tabColor = 'CCFFCC'
                elif sheet_name == 'Highlighted Users':
                    worksheet.sheet_properties.tabColor = 'FFCCCC'
                elif sheet_name == 'Overall Summary':
                    worksheet.sheet_properties.tabColor = 'FFD700'
                
                # Apply border style
                side = Side(border_style='dotted', color='000000')
                border_style = Border(left=side, right=side, top=side, bottom=side)
                
                # Header style
                for cell in worksheet[1]:
                    cell.font = Font(name='Verdana', size=9, bold=True)
                    cell.fill = PatternFill(start_color='9bc2e6', end_color='9bc2e6', fill_type='solid')
                    cell.alignment = Alignment(vertical='bottom', horizontal='left')
                    cell.border = border_style
                
                # Cell styles
                for row in worksheet.iter_rows(min_row=2, max_row=worksheet.max_row, min_col=1, max_col=worksheet.max_column):
                    for cell in row:
                        cell.font = Font(name='Verdana', size=9, color='000000')
                        cell.border = border_style
                        cell.alignment = Alignment(vertical='bottom', horizontal='left')
        
        return file_path, file_name
    except Exception as e:
        raise Exception(f"Error saving Excel file: {str(e)}")