import streamlit as st
from pymongo import MongoClient
import hashlib
from datetime import datetime
import random
import requests
import folium
from streamlit_folium import st_folium
from geopy.geocoders import Nominatim
import speech_recognition as sr
from audio_recorder_streamlit import audio_recorder
from google import genai
from streamlit_drawable_canvas import st_canvas
from PIL import Image
import base64
import io
import json
from streamlit_geolocation import streamlit_geolocation

# ---------------------------------------------------------
# 1. DATABASE & AI CONFIGURATION
# ---------------------------------------------------------
# Pulling secure keys from Streamlit Secrets
MONGO_URI = st.secrets["MONGO_URI"]
GEMINI_API_KEY = st.secrets["GEMINI_API_KEY"]
MASTER_DOCTOR_KEY = "DOC-SECURE-2026"

@st.cache_resource
def get_database():
    client = MongoClient(MONGO_URI)
    return client["medikiosk_db"]

db = get_database()
users_col = db["users"]
intakes_col = db["intakes"]

# Initialize Gemini AI
ai_client = genai.Client(api_key=GEMINI_API_KEY)

def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()

def register_user(username, password, role):
    if users_col.find_one({"username": username}):
        return False
    unique_id = f"PT-{random.randint(100000, 999999)}" if role == "Patient" else f"DR-{random.randint(1000, 9999)}"
    users_col.insert_one({
        "username": username,
        "password_hash": hash_password(password),
        "role": role,
        "unique_id": unique_id,
        "created_at": datetime.utcnow()
    })
    return True

def authenticate_user(username, password, expected_role):
    user = users_col.find_one({
        "username": username,
        "password_hash": hash_password(password),
        "role": expected_role
    })
    if user:
        return {"role": user["role"], "unique_id": user.get("unique_id", "N/A")}
    return None

# ---------------------------------------------------------
# 2. SESSION STATE MANAGEMENT
# ---------------------------------------------------------
if "logged_in" not in st.session_state:
    st.session_state.logged_in = False
    st.session_state.username = ""
    st.session_state.role = ""
    st.session_state.unique_id = ""
    st.session_state.active_portal = None

def logout():
    st.session_state.logged_in = False
    st.session_state.username = ""
    st.session_state.role = ""
    st.session_state.unique_id = ""
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
                user_data = authenticate_user(login_user, login_pass, portal)
                if user_data:
                    st.session_state.logged_in = True
                    st.session_state.username = login_user
                    st.session_state.role = user_data["role"]
                    st.session_state.unique_id = user_data["unique_id"]
                    st.rerun()
                else:
                    st.error("Invalid credentials or incorrect portal access.")

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
# 4. LOGGED-IN DASHBOARDS
# ---------------------------------------------------------
else:
    top_col1, top_col2 = st.columns([8, 2])
    with top_col1:
        prefix = "Dr. " if st.session_state.role == "Doctor" else ""
        st.caption(f"Logged in as **{prefix}{st.session_state.username}** | ID: **{st.session_state.unique_id}** ({st.session_state.role})")
    with top_col2:
        if st.button("Log Out"):
            logout()
    st.markdown("---")

    # ==========================================
    # --- DOCTOR VIEW ---
    # ==========================================
    if st.session_state.role == "Doctor":
        st.title("🩺 Live Physician OPD Dashboard")
        st.subheader("Incoming Patient Queue")
        
        records = list(intakes_col.find({}, {"_id": 0}).sort("timestamp", -1))
        
        if not records:
            st.info("No patient intake submissions currently in the queue.")
        else:
            queue_data = [{"ID": r.get("patient_id", "N/A"), "Patient": r.get("patient_username", "Unknown"), "Symptoms": r.get("symptoms", ""), "Status": r.get("status", "Unknown")} for r in records]
            st.dataframe(queue_data, use_container_width=True)
            
            st.markdown("---")
            st.subheader("✍️ Clinical Review & Sign-Off")
            
            pending_records = [r for r in records if r.get("status") == "Awaiting Review" and "intake_id" in r]
            
            if pending_records:
                pending_options = {r["intake_id"]: f"{r.get('patient_username')} (ID: {r.get('patient_id')})" for r in pending_records}
                selected_intake = st.selectbox("Select a patient record to review:", options=list(pending_options.keys()), format_func=lambda x: pending_options[x])
                
                record = next(r for r in pending_records if r["intake_id"] == selected_intake)
                
                st.info(f"**🤖 AI Clinical Summary:** {record.get('ai_summary', 'Pending')}")
                current_meds = record.get('current_meds', 'None provided')
                st.write(f"**Current Medications (Extracted from Photo):** {current_meds}")
                
                if current_meds not in ["None provided", "N/A", "None", "Illegible - Manual Review Needed"]:
                    if st.button("🔍 Suggest Generic Alternatives"):
                        with st.spinner("Finding cost-effective alternatives..."):
                            try:
                                alt_response = ai_client.models.generate_content(
                                    model="gemini-2.5-flash",
                                    contents=f"List low-cost generic alternatives for these medications: {current_meds}. Keep it brief."
                                )
                                st.success(alt_response.text)
                            except:
                                st.error("AI service unavailable.")
                
                st.markdown("---")
                prescription = st.text_area("Write Digital Prescription / Treatment Plan:")
                st.write("Draw your signature below:")
                
                canvas_result = st_canvas(
                    stroke_width=2,
                    stroke_color="#000000",
                    background_color="#ffffff",
                    height=150,
                    width=400,
                    drawing_mode="freedraw",
                    key="canvas"
                )
                
                if st.button("✍️ Issue Prescription & Sign Off", type="primary"):
                    doctor_signature = f"Dr. {st.session_state.username}"
                    sig_b64 = ""
                    if canvas_result.image_data is not None:
                        img_np = canvas_result.image_data
                        img_pil = Image.fromarray(img_np.astype('uint8'), 'RGBA')
                        buffered = io.BytesIO()
                        img_pil.save(buffered, format="PNG")
                        sig_b64 = base64.b64encode(buffered.getvalue()).decode("utf-8")
                    
                    intakes_col.update_one(
                        {"intake_id": selected_intake},
                        {"$set": {
                            "status": "Reviewed", 
                            "signed_by": doctor_signature,
                            "prescription": prescription,
                            "signature_b64": sig_b64
                        }}
                    )
                    st.success("Record reviewed and signed!")
                    st.rerun()
            else:
                st.success("🎉 All patients reviewed!")

    # ==========================================
    # --- PATIENT VIEW ---
    # ==========================================
    elif st.session_state.role == "Patient":
        st.title("📋 Citizen Health Intake")
        tab_intake, tab_history, tab_hospitals = st.tabs(["📝 New Intake", "📂 My Past Records", "🏥 Find Govt Hospitals"])
        
        # 1. New Form Submission
        with tab_intake:
            st.subheader("Submit New Symptoms & Documents")
            indian_languages = {"English": "en-IN", "Hindi": "hi-IN", "Bengali": "bn-IN", "Tamil": "ta-IN", "Telugu": "te-IN", "Marathi": "mr-IN", "Gujarati": "gu-IN"}
            lang_code = indian_languages[st.selectbox("Select Language:", list(indian_languages.keys()))]
            
            st.write("🎙️ **Speak your symptoms or type them below:**")
            audio_bytes = audio_recorder(text="Click to Speak", icon_name="microphone", icon_size="2x")
            
            recognized_text = ""
            if audio_bytes:
                st.audio(audio_bytes, format="audio/wav")
                with st.spinner("Translating..."):
                    with open("temp.wav", "wb") as f: f.write(audio_bytes)
                    r = sr.Recognizer()
                    with sr.AudioFile("temp.wav") as source:
                        try:
                            recognized_text = r.recognize_google(r.record(source), language=lang_code)
                            st.success("Audio transcribed!")
                        except:
                            st.error("Could not understand audio.")

            symptoms = st.text_area("Describe symptoms:", value=recognized_text, height=100)
            duration = st.text_input("Duration (e.g., 3 days):")
            uploaded_file = st.file_uploader("Upload past prescription/lab report:", type=["png", "jpg", "jpeg"])
            
            if st.button("Submit to Doctor Queue", type="primary"):
                if symptoms:
                    with st.spinner("AI is processing your case..."):
                        prompt = (f"Symptoms: {symptoms}. Duration: {duration}. "
                                  "Task 1: Summarize condition. "
                                  "Task 2: Extract active medications from image. If illegible, strictly write 'Illegible - Manual Review Needed'. "
                                  "Format strictly as:\nSummary: <summary>\nMedications: <medications>")
                        contents = [Image.open(uploaded_file), prompt] if uploaded_file else [prompt]
                        try:
                            response = ai_client.models.generate_content(model="gemini-2.5-flash", contents=contents)
                            ai_text = response.text
                            if "Summary:" in ai_text and "Medications:" in ai_text:
                                parts = ai_text.split("Medications:")
                                summary = parts[0].replace("Summary:", "").strip()
                                meds = parts[1].strip()
                            else:
                                summary, meds = ai_text, "N/A"
                        except:
                            summary, meds = "AI Processing Failed", "N/A"
                        
                        intakes_col.insert_one({
                            "intake_id": f"IN-{random.randint(10000, 99999)}",
                            "patient_id": st.session_state.unique_id,
                            "patient_username": st.session_state.username,
                            "symptoms": symptoms,
                            "duration": duration,
                            "ai_summary": summary,
                            "current_meds": meds if uploaded_file else "None provided",
                            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                            "status": "Awaiting Review",
                            "signed_by": "Pending"
                        })
                        st.success("Sent to doctor dashboard!")
                else:
                    st.warning("Please enter symptoms.")
        
        # 2. History View
        with tab_history:
            st.subheader("Your Submission History")
            my_records = list(intakes_col.find({"patient_username": st.session_state.username}, {"_id": 0}).sort("timestamp", -1))
            
            if not my_records:
                st.info("No forms submitted yet.")
            else:
                for rec in my_records:
                    with st.container(border=True):
                        col1, col2 = st.columns([3, 1])
                        with col1:
                            st.write(f"**Symptoms:** {rec.get('symptoms')}")
                            st.write(f"**AI Summary:** {rec.get('ai_summary', 'N/A')}")
                            if rec.get('prescription'):
                                st.success(f"**Prescribed Treatment:**\n\n{rec['prescription']}")
                        with col2:
                            if rec.get('status') == "Reviewed":
                                st.write(f"✅ **{rec.get('signed_by', '')}**")
                                if rec.get('signature_b64'):
                                    st.image(base64.b64decode(rec['signature_b64']), width=150)
                            else:
                                st.warning("⏳ Pending Review")

        # 3. Hospital Locator (Two-Tier 10km Radial AI Search)
        with tab_hospitals:
            st.subheader("Locate Government Healthcare Facilities")
            st.caption("Prioritizes local government health centers within 10 km, followed by regional tertiary medical colleges.")
            
            col1, col2 = st.columns([1, 1])
            
            with col1:
                st.markdown("**📍 Option 1: Live GPS Location**")
                gps_loc = streamlit_geolocation()
                
            with col2:
                st.markdown("**⌨️ Option 2: Manual Location**")
                location_query = st.text_input("Enter City, Town, or Pincode:", placeholder="e.g., North Dumdum, Kolkata", label_visibility="collapsed")
                manual_search = st.button("Search by Text", type="primary")

            if manual_search and location_query.strip():
                st.session_state.search_type = "manual"
                st.session_state.search_val = location_query
            elif gps_loc and gps_loc.get('latitude') is not None:
                st.session_state.search_type = "gps"
                st.session_state.search_val = gps_loc

            if st.session_state.get("search_type"):
                with st.spinner("Triangulating public health centers and regional medical colleges..."):
                    try:
                        lat, lon = None, None
                        search_context = ""
                        
                        if st.session_state.search_type == "manual":
                            loc = Nominatim(user_agent="medikiosk_sih_v2").geocode(st.session_state.search_val)
                            if loc:
                                lat, lon = loc.latitude, loc.longitude
                                search_context = f"the area of '{st.session_state.search_val}'"
                            else:
                                st.error("Could not find coordinates for that location.")
                        elif st.session_state.search_type == "gps":
                            lat = st.session_state.search_val['latitude']
                            lon = st.session_state.search_val['longitude']
                            search_context = f"coordinates Latitude {lat}, Longitude {lon}"
                        
                        if lat and lon:
                            # Two-Tier Structured Prompt
                            prompt = f"""
                            You are a geospatial health directory for Indian public healthcare.
                            Given the center location at {search_context} (Lat: {lat}, Lon: {lon}):

                            Identify real, government-run health institutions divided strictly into two categories:

                            1. LOCAL TIER (Strictly within a 10 km radius):
                               - Focus on: Sub-Divisional Hospitals (SDH), State General Hospitals (SGH), Urban Primary Health Centres (UPHC), Community Health Centres (CHC), and local government dispensaries/nursing facilities.
                               - Provide up to 6 real facilities.

                            2. REGIONAL REFERRAL TIER (Beyond 10 km radius):
                               - Major landmark Government Medical Colleges and apex state tertiary referral hospitals serving this district/region (e.g., Calcutta Medical College, Barasat Govt Medical College & Hospital, RG Kar, NRS, etc.).
                               - Provide up to 4 major facilities.

                            Return strictly a raw JSON array of objects without any markdown formatting or backticks:
                            [
                              {{
                                "name": "Hospital Name",
                                "type": "State General Hospital / Medical College / UPHC",
                                "tier": "Within 10 km" or "Regional (>10km)",
                                "lat": 22.1234,
                                "lon": 88.1234
                              }}
                            ]
                            """
                            
                            response = ai_client.models.generate_content(
                                model="gemini-2.5-flash", 
                                contents=prompt
                            )
                            
                            raw_text = response.text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
                            hospitals_data = json.loads(raw_text)
                            
                            local_facilities = [h for h in hospitals_data if h.get("tier") == "Within 10 km"]
                            regional_facilities = [h for h in hospitals_data if h.get("tier") != "Within 10 km"]
                            
                            st.success(f"Located {len(local_facilities)} local public facilities (≤10 km) and {len(regional_facilities)} regional tertiary hospitals.")
                            
                            # Initialize map centered on user
                            m = folium.Map(location=[lat, lon], zoom_start=11)
                            
                            # 1. User/Kiosk Location Marker
                            folium.Marker(
                                [lat, lon], 
                                popup="📍 Kiosk / Patient Location", 
                                icon=folium.Icon(color="blue", icon="user")
                            ).add_to(m)
                            
                            # 2. 10 km Radius Boundary Ring
                            folium.Circle(
                                location=[lat, lon],
                                radius=10000,  # 10,000 meters = 10 km
                                color="#2b8cbe",
                                weight=2,
                                fill=True,
                                fill_color="#a6bddb",
                                fill_opacity=0.15,
                                popup="10 km Local Service Radius"
                            ).add_to(m)
                            
                            # 3. Plot Local Tier (<= 10 km) in Green
                            for h in local_facilities:
                                folium.Marker(
                                    [h["lat"], h["lon"]], 
                                    popup=f"🟢 [Local ≤10km] {h['name']} ({h.get('type', 'Govt Facility')})", 
                                    icon=folium.Icon(color="green", icon="plus")
                                ).add_to(m)
                                
                            # 4. Plot Regional Tier (> 10 km) in Dark Red / Cadre
                            for h in regional_facilities:
                                folium.Marker(
                                    [h["lat"], h["lon"]], 
                                    popup=f"🏛️ [Regional Referral] {h['name']} ({h.get('type', 'Apex Hospital')})", 
                                    icon=folium.Icon(color="darkred", icon="star")
                                ).add_to(m)
                                
                            st_folium(m, width=850, height=520, returned_objects=[])
                            
                            # Facility Directory Breakdown
                            col_loc, col_reg = st.columns(2)
                            with col_loc:
                                st.markdown("### 🟢 Local Health Centers (≤ 10 km)")
                                for h in local_facilities:
                                    st.markdown(f"- **{h['name']}**  \n  *{h.get('type', 'Govt Health Center')}*")
                            with col_reg:
                                st.markdown("### 🏛️ Regional Medical Colleges (> 10 km)")
                                for h in regional_facilities:
                                    st.markdown(f"- **{h['name']}**  \n  *{h.get('type', 'Tertiary Referral Center')}*")
                                    
                    except json.JSONDecodeError:
                        st.error("AI returned an unparseable response. Please search again.")
                    except Exception as e:
                        st.error(f"Error mapping facilities: {e}")