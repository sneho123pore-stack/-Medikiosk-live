import streamlit as st
from pymongo import MongoClient
import hashlib
from datetime import datetime
import random
import folium
from streamlit_folium import st_folium
from geopy.geocoders import Nominatim
from audio_recorder_streamlit import audio_recorder
from google import genai
from streamlit_drawable_canvas import st_canvas
from PIL import Image
import base64
import io
import json
import numpy as np
from streamlit_geolocation import streamlit_geolocation

# ---------------------------------------------------------
# 1. DATABASE & AI CONFIGURATION
# ---------------------------------------------------------
MONGO_URI = st.secrets["MONGO_URI"]
KEY_1 = st.secrets["GEMINI_API_KEY_1"]
KEY_2 = st.secrets.get("GEMINI_API_KEY_2", KEY_1) # Safely defaults to Key 1 if Key 2 isn't set
MASTER_DOCTOR_KEY = "DOC-SECURE-2026"

@st.cache_resource
def get_database():
    client = MongoClient(MONGO_URI)
    return client["medikiosk_db"]

db = get_database()
users_col = db["users"]
intakes_col = db["intakes"]

# Initialize two separate clients for load balancing
client_1 = genai.Client(api_key=KEY_1)
client_2 = genai.Client(api_key=KEY_2)

# --- THE TRIPLE-THREAT FALLBACK MECHANISM ---
def safe_ai_request(prompt_contents, primary="gemini-2.5-flash", fallback="gemini-1.5-flash"):
    """Tries Key 1, then Key 2, then falls back to a secondary model."""
    # Attempt 1: Primary Model with Key 1
    try:
        return client_1.models.generate_content(model=primary, contents=prompt_contents)
    except Exception as e1:
        # Attempt 2: Primary Model with Key 2 (Fixes 429 Rate Limits)
        try:
            return client_2.models.generate_content(model=primary, contents=prompt_contents)
        except Exception as e2:
            # Attempt 3: Backup Model with Key 1 (Fixes 503 Server Overloads)
            try:
                return client_1.models.generate_content(model=fallback, contents=prompt_contents)
            except Exception as e3:
                raise Exception(f"All AI fail-safes triggered. Latest error: {e3}")

def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()

def register_user(username, password, role, hospital_name=""):
    if users_col.find_one({"username": username}):
        return False
    unique_id = f"PT-{random.randint(100000, 999999)}" if role == "Patient" else f"DR-{random.randint(1000, 9999)}"
    users_col.insert_one({
        "username": username,
        "password_hash": hash_password(password),
        "role": role,
        "unique_id": unique_id,
        "hospital_name": hospital_name,
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
        return {
            "role": user["role"], 
            "unique_id": user.get("unique_id", "N/A"),
            "hospital_name": user.get("hospital_name", "")
        }
    return None

# ---------------------------------------------------------
# 2. SESSION STATE MANAGEMENT
# ---------------------------------------------------------
if "logged_in" not in st.session_state:
    st.session_state.logged_in = False
    st.session_state.username = ""
    st.session_state.role = ""
    st.session_state.unique_id = ""
    st.session_state.hospital_name = ""
    st.session_state.active_portal = None

def logout():
    st.session_state.logged_in = False
    st.session_state.username = ""
    st.session_state.role = ""
    st.session_state.unique_id = ""
    st.session_state.hospital_name = ""
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
                    st.session_state.hospital_name = user_data.get("hospital_name", "")
                    st.rerun()
                else:
                    st.error("Invalid credentials or incorrect portal access.")

        with tab_register:
            reg_user = st.text_input("Choose Username", key="reg_u")
            reg_pass = st.text_input("Choose Password", type="password", key="reg_p")
            
            doctor_key = ""
            hosp_name = ""
            if portal == "Doctor":
                hosp_name = st.text_input("Affiliated Government Hospital (e.g., Calcutta Medical College)")
                doctor_key = st.text_input("Doctor Authorization Key", type="password")
                
            if st.button(f"Register as {portal}"):
                if not reg_user or not reg_pass:
                    st.warning("Please fill in all required fields.")
                elif portal == "Doctor" and doctor_key != MASTER_DOCTOR_KEY:
                    st.error("❌ Invalid Doctor Authorization Key!")
                elif portal == "Doctor" and not hosp_name:
                    st.warning("Please enter your affiliated hospital.")
                else:
                    if register_user(reg_user, reg_pass, portal, hosp_name):
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
        hosp_display = f" | {st.session_state.hospital_name}" if st.session_state.hospital_name else ""
        st.caption(f"Logged in as **{prefix}{st.session_state.username}**{hosp_display} | ID: **{st.session_state.unique_id}**")
    with top_col2:
        if st.button("Log Out"):
            logout()
    st.markdown("---")

    # ==========================================
    # --- DOCTOR VIEW ---
    # ==========================================
    if st.session_state.role == "Doctor":
        st.title("🩺 Live Physician OPD Dashboard")
        
        records = list(intakes_col.find({}, {"_id": 0}).sort("timestamp", -1))
        
        # Filter for strictly pending ones
        pending_records = [r for r in records if r.get("status") == "Awaiting Review" and "intake_id" in r]
        
        if not pending_records:
            st.success("🎉 No pending patient intake submissions in the queue!")
        else:
            st.subheader("Incoming Patient Queue")
            queue_data = [{"ID": r.get("patient_id"), "Patient": r.get("patient_username"), "Symptoms": r.get("symptoms")} for r in pending_records]
            st.dataframe(queue_data, use_container_width=True)
            
            st.markdown("---")
            st.subheader("✍️ Clinical Review & Sign-Off")
            
            pending_options = {r["intake_id"]: f"{r.get('patient_username')} (ID: {r.get('patient_id')})" for r in pending_records}
            selected_intake = st.selectbox("Select a patient record to review:", options=list(pending_options.keys()), format_func=lambda x: pending_options[x])
            
            record = next(r for r in pending_records if r["intake_id"] == selected_intake)
            
            st.info(f"**🤖 AI Clinical Summary:**\n\n{record.get('ai_summary', 'Pending')}")
            
            col_doc1, col_doc2 = st.columns(2)
            with col_doc1:
                st.write(f"**Extracted Medications:**\n{record.get('current_meds', 'None provided')}")
                if record.get('current_meds') not in ["None provided", "N/A", "None", "Illegible - Manual Review Needed", "None extracted.", "Check summary for details (Formatting Error)"]:
                    if st.button("🔍 Suggest Generic Alternatives"):
                        with st.spinner("Finding cost-effective alternatives..."):
                            try:
                                alt_response = safe_ai_request(f"List low-cost generic alternatives for these medications: {record.get('current_meds')}. Keep it brief.")
                                st.success(alt_response.text)
                            except Exception as e:
                                st.error(f"AI service unavailable: {e}")
            with col_doc2:
                if record.get('document_b64'):
                    st.write("**Patient Uploaded Document:**")
                    st.image(base64.b64decode(record['document_b64']), use_container_width=True)
                else:
                    st.write("*No document uploaded by patient.*")
            
            st.markdown("---")
            st.write("### Prescribe & Request Sign-Off")
            st.warning("For security, sending this prescription requires final consent from the patient on their device.")
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
            
            if st.button("✍️ Send to Patient for Final Consent", type="primary"):
                sig_b64 = ""
                # Secure try-except to prevent drawing pad crash on Streamlit cloud
                try:
                    if canvas_result is not None and canvas_result.image_data is not None:
                        img_np = canvas_result.image_data
                        img_pil = Image.fromarray(img_np.astype('uint8'), 'RGBA')
                        buffered = io.BytesIO()
                        img_pil.save(buffered, format="PNG")
                        sig_b64 = base64.b64encode(buffered.getvalue()).decode("utf-8")
                except Exception:
                    pass
                
                doctor_full_title = f"Dr. {st.session_state.username}, {st.session_state.hospital_name}"
                
                intakes_col.update_one(
                    {"intake_id": selected_intake},
                    {"$set": {
                        "status": "Consent Requested", 
                        "pending_signed_by": doctor_full_title,
                        "pending_prescription": prescription,
                        "pending_signature_b64": sig_b64
                    }}
                )
                st.success("Prescription sent! Awaiting patient approval to lock the record.")
                st.rerun()

    # ==========================================
    # --- PATIENT VIEW ---
    # ==========================================
    elif st.session_state.role == "Patient":
        st.title("📋 Citizen Health Intake")
        tab_intake, tab_history, tab_hospitals = st.tabs(["📝 New Intake", "📂 Records & Consents", "🏥 Find Govt Hospitals"])
        
        # 1. New Form Submission
        with tab_intake:
            st.subheader("Submit New Symptoms & Documents")
            
            st.write("🎙️ **Speak your symptoms in any regional language:**")
            audio_bytes = audio_recorder(text="Click to Speak", icon_name="microphone", icon_size="2x")
            
            recognized_text = ""
            if audio_bytes:
                st.audio(audio_bytes, format="audio/wav")
                with st.spinner("AI is precisely transcribing and translating your audio..."):
                    try:
                        audio_prompt = [
                            genai.types.Part.from_bytes(data=audio_bytes, mime_type="audio/wav"),
                            "Transcribe this audio precisely. If it is in a regional Indian language, translate it strictly and accurately into clinical English. Return only the final text."
                        ]
                        response = safe_ai_request(audio_prompt)
                        recognized_text = response.text.strip()
                        st.success("Audio accurately transcribed by AI!")
                    except Exception as e:
                        st.error(f"Audio transcription failed: {e}")

            symptoms = st.text_area("Verify or type symptoms:", value=recognized_text, height=100)
            duration = st.text_input("Duration (e.g., 3 days):")
            uploaded_file = st.file_uploader("Upload past prescription/lab report:", type=["png", "jpg", "jpeg"])
            
            if st.button("Submit to Doctor Queue", type="primary"):
                if symptoms:
                    with st.spinner("AI is analyzing documents and generating clinical summary..."):
                        doc_b64 = ""
                        ai_contents = [f"Symptoms: {symptoms}. Duration: {duration}."]
                        
                        if uploaded_file:
                            doc_bytes = uploaded_file.getvalue()
                            doc_b64 = base64.b64encode(doc_bytes).decode('utf-8')
                            img_part = Image.open(io.BytesIO(doc_bytes))
                            ai_contents.insert(0, img_part)
                        
                        # Bulletproof JSON Clinical Prompt
                        ai_contents.append("""
                        You are an expert clinical AI assistant. Analyze the provided symptoms and/or medical document (prescription, lab report, or clinical notes).
                        
                        1. Create a precise, professional medical summary. Translate to English if needed.
                        2. Extract a list of all medications, dosages, and instructions. If a document is uploaded but no medications are present, extract the key medical findings instead.
                        3. If handwriting is genuinely illegible, write '[ILLEGIBLE - MANUAL REVIEW REQUIRED]'.
                        
                        CRITICAL: You MUST respond STRICTLY with a valid JSON object. Do not include markdown formatting, backticks, or introductory text.
                        Use exactly this format:
                        {
                          "summary": "your detailed clinical summary here",
                          "medications": "1. Med name - dosage\n2. Med name - dosage"
                        }
                        """)
                        
                        try:
                            # Use our new bulletproof fallback function
                            response = safe_ai_request(ai_contents)
                            
                            raw_text = response.text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
                            
                            # Safely extract the JSON data
                            try:
                                parsed_data = json.loads(raw_text)
                                summary = parsed_data.get("summary", "Summary could not be generated.")
                                meds = parsed_data.get("medications", "None extracted.")
                            except json.JSONDecodeError:
                                summary = raw_text
                                meds = "Check summary for details (Formatting Error)"
                                
                            intakes_col.insert_one({
                                "intake_id": f"IN-{random.randint(10000, 99999)}",
                                "patient_id": st.session_state.unique_id,
                                "patient_username": st.session_state.username,
                                "symptoms": symptoms,
                                "duration": duration,
                                "ai_summary": summary,
                                "current_meds": meds if uploaded_file else "None provided",
                                "document_b64": doc_b64,
                                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                                "status": "Awaiting Review",
                                "signed_by": "Pending"
                            })
                            st.success("Case submitted successfully to the Doctor Queue!")
                            
                        except Exception as e:
                            st.error(f"Google AI Server Error: {e}")
                            st.warning("⚠️ The AI server is experiencing extremely high traffic. Please try again in a moment.")
                else:
                    st.warning("Please enter your symptoms.")
        
        # 2. History & Consent View
        with tab_history:
            st.subheader("Your Records & Pending Approvals")
            my_records = list(intakes_col.find({"patient_username": st.session_state.username}, {"_id": 0}).sort("timestamp", -1))
            
            if not my_records:
                st.info("No records found.")
            else:
                for rec in my_records:
                    with st.container(border=True):
                        col1, col2 = st.columns([3, 1])
                        with col1:
                            st.write(f"**Symptoms:** {rec.get('symptoms')}")
                            
                            if rec.get('status') == "Consent Requested":
                                st.error("⚠️ **Doctor Review Completed - Consent Required**")
                                st.write(f"**Prescribing Doctor:** {rec.get('pending_signed_by')}")
                                st.write(f"**Proposed Treatment:** {rec.get('pending_prescription')}")
                                
                                if st.button(f"✅ I agree to finalize this visit (ID: {rec['intake_id']})", type="primary", key=f"approve_{rec['intake_id']}"):
                                    intakes_col.update_one(
                                        {"intake_id": rec['intake_id']},
                                        {"$set": {
                                            "status": "Reviewed",
                                            "signed_by": rec['pending_signed_by'],
                                            "prescription": rec['pending_prescription'],
                                            "signature_b64": rec.get('pending_signature_b64', '')
                                        }}
                                    )
                                    st.rerun()
                            
                            elif rec.get('prescription'):
                                st.success(f"**Prescribed Treatment:**\n\n{rec['prescription']}")
                                
                        with col2:
                            if rec.get('status') == "Reviewed":
                                st.write(f"✅ **Signed By:**\n{rec.get('signed_by', '')}")
                                if rec.get('signature_b64'):
                                    st.image(base64.b64decode(rec['signature_b64']), width=150)
                            elif rec.get('status') == "Consent Requested":
                                st.warning("✋ Awaiting Your Approval")
                            else:
                                st.warning("⏳ In Doctor Queue")

        # 3. Hospital Locator (True GPS + AI-Powered Govt Search)
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
                        elif st.session_state.search_type == "gps":
                            lat = st.session_state.search_val['latitude']
                            lon = st.session_state.search_val['longitude']
                            search_context = f"coordinates Latitude {lat}, Longitude {lon}"
                        
                        if lat and lon:
                            prompt = f"""
                            You are a geospatial health directory for Indian public healthcare.
                            Given the center location at {search_context} (Lat: {lat}, Lon: {lon}):
                            Identify real, government-run health institutions divided strictly into two categories:
                            1. LOCAL TIER (Strictly within a 10 km radius):
                               - Focus on: Sub-Divisional Hospitals (SDH), State General Hospitals (SGH), Urban Primary Health Centres (UPHC).
                               - Provide up to 6 real facilities.
                            2. REGIONAL REFERRAL TIER (Beyond 10 km radius):
                               - Major landmark Government Medical Colleges and apex state tertiary referral hospitals.
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
                            # Use our bulletproof fallback function here too!
                            response = safe_ai_request(prompt)
                            
                            raw_text = response.text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
                            hospitals_data = json.loads(raw_text)
                            
                            local_facilities = [h for h in hospitals_data if h.get("tier") == "Within 10 km"]
                            regional_facilities = [h for h in hospitals_data if h.get("tier") != "Within 10 km"]
                            
                            m = folium.Map(location=[lat, lon], zoom_start=11)
                            folium.Marker([lat, lon], popup="📍 Patient Location", icon=folium.Icon(color="blue", icon="user")).add_to(m)
                            folium.Circle(location=[lat, lon], radius=10000, color="#2b8cbe", weight=2, fill=True, fill_opacity=0.15).add_to(m)
                            
                            for h in local_facilities:
                                folium.Marker([h["lat"], h["lon"]], popup=f"🟢 {h['name']}", icon=folium.Icon(color="green", icon="plus")).add_to(m)
                            for h in regional_facilities:
                                folium.Marker([h["lat"], h["lon"]], popup=f"🏛️ {h['name']}", icon=folium.Icon(color="darkred", icon="star")).add_to(m)
                                
                            st_folium(m, width=850, height=520, returned_objects=[])
                    except Exception as e:
                        st.error(f"Error mapping facilities. Please try again. Details: {e}")
