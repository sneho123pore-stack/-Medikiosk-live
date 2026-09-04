import streamlit as st
from pymongo import MongoClient
import hashlib
from datetime import datetime

# ---------------------------------------------------------
# 1. DATABASE CONFIGURATION (MONGODB ATLAS)
# ---------------------------------------------------------
# Replace with your actual MongoDB Atlas connection string
# This tells Python to look inside the cloud's secure vault
MONGO_URI = st.secrets["MONGO_URI"]
MASTER_DOCTOR_KEY = "DOC-SECURE-2026"

@st.cache_resource
def get_database():
    client = MongoClient(MONGO_URI)
    return client["medikiosk_db"]

db = get_database()
users_col = db["users"]
intakes_col = db["intakes"]

def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()

def register_user(username, password, role):
    if users_col.find_one({"username": username}):
        return False
    users_col.insert_one({
        "username": username,
        "password_hash": hash_password(password),
        "role": role,
        "created_at": datetime.utcnow()
    })
    return True

def authenticate_user(username, password, expected_role):
    user = users_col.find_one({
        "username": username,
        "password_hash": hash_password(password),
        "role": expected_role
    })
    return user["role"] if user else None

# ---------------------------------------------------------
# 2. SESSION STATE MANAGEMENT
# ---------------------------------------------------------
if "logged_in" not in st.session_state:
    st.session_state.logged_in = False
    st.session_state.username = ""
    st.session_state.role = ""
    st.session_state.active_portal = None

def logout():
    st.session_state.logged_in = False
    st.session_state.username = ""
    st.session_state.role = ""
    st.session_state.active_portal = None
    st.rerun()

st.set_page_config(page_title="MediKiosk Cloud Portal", page_icon="🏥", layout="wide")

# ---------------------------------------------------------
# 3. PORTAL SELECTION & AUTHENTICATION
# ---------------------------------------------------------
if not st.session_state.logged_in:
    if st.session_state.active_portal is None:
        st.title("🏥 MediKiosk Cloud System")
        st.write("Please select your portal to continue:")
        
        col1, col2 = st.columns(2)
        with col1:
            st.info("**Citizen / Patient Portal**\n\nSubmit intake notes and view records.")
            if st.button("Enter Citizen Portal", use_container_width=True):
                st.session_state.active_portal = "Patient"
                st.rerun()
                
        with col2:
            st.error("**Doctor / Authority Portal**\n\nReview real-time live OPD queues.")
            if st.button("Enter Doctor Portal", use_container_width=True):
                st.session_state.active_portal = "Doctor"
                st.rerun()
    else:
        portal = st.session_state.active_portal
        st.button("← Back to Selection", on_click=lambda: st.session_state.update(active_portal=None))
        st.title(f"{'🩺' if portal == 'Doctor' else '📋'} {portal} Portal")
        
        tab_login, tab_register = st.tabs(["🔑 Login", "📝 Sign Up"])

        with tab_login:
            login_user = st.text_input("Username", key="login_u")
            login_pass = st.text_input("Password", type="password", key="login_p")
            
            if st.button(f"Log In to {portal} Portal", type="primary"):
                role = authenticate_user(login_user, login_pass, portal)
                if role:
                    st.session_state.logged_in = True
                    st.session_state.username = login_user
                    st.session_state.role = role
                    st.rerun()
                else:
                    st.error(f"Invalid credentials or incorrect portal access.")

        with tab_register:
            reg_user = st.text_input("Choose Username", key="reg_u")
            reg_pass = st.text_input("Choose Password", type="password", key="reg_p")
            
            doctor_key = ""
            if portal == "Doctor":
                doctor_key = st.text_input("Doctor Authorization Key", type="password")
                
            if st.button(f"Register as {portal}"):
                if not reg_user or not reg_pass:
                    st.warning("Please fill in all required fields.")
                elif portal == "Doctor" and doctor_key != MASTER_DOCTOR_KEY:
                    st.error("❌ Invalid Doctor Authorization Key!")
                else:
                    if register_user(reg_user, reg_pass, portal):
                        st.success("Account created successfully! Please log in.")
                    else:
                        st.error("Username already exists.")

# ---------------------------------------------------------
# 4. LOGGED-IN DASHBOARDS (CONNECTED TO MONGODB)
# ---------------------------------------------------------
else:
    top_col1, top_col2 = st.columns([8, 2])
    with top_col1:
        st.caption(f"Logged in as **{st.session_state.username}** ({st.session_state.role})")
    with top_col2:
        if st.button("Log Out"):
            logout()
    st.markdown("---")

    # DOCTOR VIEW: Fetches real patient submissions from MongoDB
    if st.session_state.role == "Doctor":
        st.title("🩺 Live Physician OPD Dashboard")
        st.subheader("Incoming Patient Intake Queue")
        
        records = list(intakes_col.find({}, {"_id": 0}).sort("timestamp", -1))
        if records:
            st.dataframe(records, use_container_width=True)
        else:
            st.info("No patient intake submissions currently in the queue.")

    # PATIENT VIEW: Writes submission directly to MongoDB
    elif st.session_state.role == "Patient":
        st.title("📋 Citizen Health Intake")
        st.write("Submit your health complaints before entering the doctor's chamber.")
        
        symptoms = st.text_area("Describe your primary symptoms:")
        duration = st.text_input("Duration of symptoms (e.g., 3 days, 2 weeks):")
        
        if st.button("Submit to Doctor Queue", type="primary"):
            if symptoms:
                intakes_col.insert_one({
                    "patient_username": st.session_state.username,
                    "symptoms": symptoms,
                    "duration": duration,
                    "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "status": "Awaiting Review"
                })
                st.success("Your intake details have been sent to the doctor dashboard!")
            else:
                st.warning("Please enter your symptoms before submitting.")