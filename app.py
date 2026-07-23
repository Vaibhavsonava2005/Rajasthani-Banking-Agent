# -*- coding: utf-8 -*-
"""
Rajasthani Voice Pro - Production Flask Backend
Plivo-powered Hindi/Rajasthani IVR voice-call system.
"""

# ─────────────────────────────────────────────────────────────
# 1. IMPORTS & SETUP
# ─────────────────────────────────────────────────────────────
import os
import re
import logging
import tempfile
import threading
import time
import uuid

import pandas as pd
from flask import Flask, render_template, send_file, jsonify, Response, request
import requests
from flask import Flask, render_template, send_file, jsonify, Response, request
import requests
from dotenv import load_dotenv
from concurrent.futures import ThreadPoolExecutor
load_dotenv()

# ── Logging ────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("rajasthani_voice_pro")

# ── Flask App ───────────────────────────────────────────────
app = Flask(
    __name__,
    template_folder="templates",
    static_folder="static",
)
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024  # 16 MB

# ── Env / Config ────────────────────────────────────────────# Plivo API configuration
_PLIVO_AUTH_ID = os.environ.get("PLIVO_AUTH_ID", "MAZGRJNDZHNWITYJLJMC")
_PLIVO_AUTH_TOKEN = os.environ.get("PLIVO_AUTH_TOKEN", "ZmE2NGQyMTQtNDdmOS00OTg0LTQwZjAtMDdkMWYw")
_PLIVO_CALLER_ID = os.environ.get("PLIVO_CALLER_NUMBER", "+918031449735")
if os.getenv("VERCEL_URL"):
    PUBLIC_BASE_URL = f"https://{os.getenv('VERCEL_URL').strip()}"
else:
    PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "").strip().rstrip("/")

# Global state
processed_data: list = []
_batch_cancel_flag = threading.Event()


# ─────────────────────────────────────────────────────────────
# 2. PHONE NORMALIZATION
# ─────────────────────────────────────────────────────────────
def normalize_phone_number(raw: str) -> str:
    """
    Normalise an Indian mobile number to E.164 (+91XXXXXXXXXX).
    Strips whitespace, hyphens, dots, parentheses.
    Handles +91 prefix (13 chars), 91 prefix (12 digits), leading 0 (11 digits).
    Validates 10 digits starting with 6-9.
    Raises ValueError for invalid numbers.
    """
    if not isinstance(raw, str):
        raw = str(raw)
    if not raw or not raw.strip():
        raise ValueError("Phone number cannot be empty")

    # Strip whitespace, hyphens, dots, parentheses
    cleaned = re.sub(r"[\s\-\.\(\)]+", "", raw.strip())

    if not cleaned:
        raise ValueError("Phone number is empty after stripping punctuation")

    # Remove +91 or 91 prefix
    if cleaned.startswith("+91") and len(cleaned) == 13:
        cleaned = cleaned[3:]
    elif cleaned.startswith("91") and len(cleaned) == 12:
        cleaned = cleaned[2:]
    elif cleaned.startswith("0") and len(cleaned) == 11:
        cleaned = cleaned[1:]

    # Validate: exactly 10 digits starting with 6-9
    if not re.fullmatch(r"[6-9]\d{9}", cleaned):
        raise ValueError(
            f"Invalid Indian mobile number: '{raw}'. "
            "Must be 10 digits starting with 6, 7, 8, or 9."
        )

    return f"+91{cleaned}"

# ─────────────────────────────────────────────────────────────
# 3. STATLESS TWILIO INTEGRATION
# ─────────────────────────────────────────────────────────────
# Removed AudioCache as it is incompatible with Vercel Serverless


# ─────────────────────────────────────────────────────────────
# 4. CALL MANAGER
# ─────────────────────────────────────────────────────────────


class CallManager:
    """
    Wraps Plivo REST calls and tracks per-record call state.
    """

    def __init__(
        self,
        account_sid: str,
        auth_token: str,
        caller_number: str,
        public_base_url: str
    ):
        self.account_sid = account_sid
        self.auth_token = auth_token
        self.caller_number = caller_number
        self.public_base_url = public_base_url.rstrip("/")
        
        logger.info(
            "CallManager ready (Plivo, caller=%s, base_url=%s)",
            caller_number, public_base_url,
        )

    # ── public API ───────────────────────────────────────────
    def initiate_call(self, phone_number: str, hindi_text: str = "", base_url: str = None, job_id: str = "") -> dict:
        """Place an outbound call via Plivo."""
        import base64
        import urllib.parse
        import plivo
        
        # Base64 encode the text to avoid URL encoding issues
        b64_text = base64.urlsafe_b64encode(hindi_text.encode("utf-8")).decode("utf-8")
        
        # Plivo webhook (Return PlivoXML with native Hindi TTS)
        # Plivo requires a public URL, so ALWAYS use self.public_base_url (the ngrok URL)
        actual_base_url = self.public_base_url.rstrip("/")
        answer_url = f"{actual_base_url}/plivoxml?b64={b64_text}&job_id={job_id}"
        callback_url = f"{actual_base_url}/plivo-callback?job_id={job_id}"
        
        if not phone_number.startswith("+"):
            formatted_to = "+91" + phone_number if len(phone_number) == 10 else "+" + phone_number
        else:
            formatted_to = phone_number
            
        logger.info(f"Calling Plivo to dial {formatted_to} with URL {answer_url}")
        try:
            client = plivo.RestClient(self.account_sid, self.auth_token)
            
            # We removed the synchronous pre-warming here. 
            # It will be handled asynchronously in the /call route to prevent UI blocking!

            response = client.calls.create(
                from_=self.caller_number,
                to_=formatted_to,
                answer_url=answer_url,
                answer_method='POST',
                callback_url=callback_url,
                callback_method='POST'
            )
            
            request_uuid = getattr(response, "request_uuid", getattr(response, "api_id", "unknown"))
            logger.info("Call successfully initiated, Plivo ID: %s", request_uuid)
            return {"call_sid": request_uuid}
                
        except Exception as e:
            logger.error(f"Plivo call failed: {e}")
            return {"error": str(e)}

    def get_status(self, call_sid: str) -> dict:
        """Fetch real-time call status directly from Plivo API."""
        import plivo
        try:
            client = plivo.RestClient(self.account_sid, self.auth_token)
            call = client.calls.get(call_sid)
            return {
                "call_sid": call_sid,
                "status": call.get("call_state", "unknown") if hasattr(call, "get") else getattr(call, "call_state", "unknown"),
                "updated_at": time.time()
            }
        except Exception as exc:
            logger.error("Failed to fetch status for %s: %s", call_sid, exc)
            return {"call_sid": call_sid, "status": "unknown"}

# ─────────────────────────────────────────────────────────────
# 5. RAJASTHANI / HINDI NUMBER SYSTEM
# ─────────────────────────────────────────────────────────────

# Ones: index 0 = empty, 1-19 direct lookup
ONES = [
    "",       "एक",     "दो",     "तीन",    "चार",    "पांच",
    "छह",     "सात",    "आठ",     "नौ",     "दस",
    "ग्यारह", "बारह",  "तेरह",   "चौदह",   "पंद्रह",
    "सोलह",   "सत्रह", "अठारह",  "उन्नीस",
]

TEENS = ['दस', 'ग्यारह', 'बारह', 'तेरह', 'चौदह', 'पंद्रह', 'सोलह', 'सत्रह', 'अठारह', 'उन्नीस']
TWENTIES_LIST = ['बीस', 'इक्कीस', 'बाईस', 'तेईस', 'चौबीस', 'पच्चीस', 'छब्बीस', 'सत्ताईस', 'अट्ठाईस', 'उनतीस']
THIRTIES = ['तीस', 'इकतीस', 'बत्तीस', 'तैंतीस', 'चौंतीस', 'पैंतीस', 'छत्तीस', 'सैंतीस', 'अड़तीस', 'उनतालीस']
FORTIES = ['चालीस', 'इकतालीस', 'बयालीस', 'तैंतालीस', 'चवालीस', 'पैंतालीस', 'छियालीस', 'सैंतालीस', 'अड़तालीस', 'उनचास']
FIFTIES = ['पचास', 'इक्यावन', 'बावन', 'तिरपन', 'चौवन', 'पचपन', 'छप्पन', 'सत्तावन', 'अट्ठावन', 'उनसठ']
SIXTIES = ['साठ', 'इकसठ', 'बासठ', 'तिरसठ', 'चौंसठ', 'पैंसठ', 'छियासठ', 'सड़सठ', 'अड़सठ', 'उनहत्तर']
SEVENTIES = ['सत्तर', 'इकहत्तर', 'बहत्तर', 'तिहत्तर', 'चौहत्तर', 'पचहत्तर', 'छिहत्तर', 'सतहत्तर', 'अठहत्तर', 'उन्यासी']
EIGHTIES = ['अस्सी', 'इक्यासी', 'बयासी', 'तिरासी', 'चौरासी', 'पचासी', 'छियासी', 'सतासी', 'अट्ठासी', 'नवासी']
NINETIES = ['नब्बे', 'इक्यानवे', 'बानवे', 'तिरानवे', 'चौरानवे', 'पचानवे', 'छियानवे', 'सत्तानवे', 'अट्ठानवे', 'निन्यानवे']

# Decade map (tens digit -> list, index = units digit)
_DECADE = [
    None, None,
    TWENTIES_LIST, THIRTIES, FORTIES, FIFTIES,
    SIXTIES, SEVENTIES, EIGHTIES, NINETIES,
]

def number_to_rajasthani(n) -> str:
    """
    Convert an integer to its Hindi word representation.
    Handles crores, lakhs, thousands, hundreds, and sub-hundred values.
    """
    if not isinstance(n, int):
        try:
            n = int(round(float(str(n).replace(",", ""))))
        except (ValueError, TypeError):
            return str(n)

    if n == 0:
        return "शून्य"
    if n < 0:
        return "माइनस " + number_to_rajasthani(-n)

    parts = []

    def _sub_hundred(num: int) -> str:
        """Convert 1-99 to Hindi words."""
        if num == 0:
            return ""
        if num < 20:
            return ONES[num]
        decade = _DECADE[num // 10]
        units  = num % 10
        return decade[units]

    def _sub_thousand(num: int) -> str:
        """Convert 1-999 to Hindi words."""
        if num < 100:
            return _sub_hundred(num)
        hun_word = ONES[num // 100] + " सौ"
        rem = _sub_hundred(num % 100)
        return (hun_word + " " + rem).strip() if rem else hun_word

    # Crores (10,000,000)
    if n >= 10_000_000:
        parts.append(_sub_thousand(n // 10_000_000) + " करोड़")
        n %= 10_000_000

    # Lakhs (100,000)
    if n >= 100_000:
        parts.append(_sub_thousand(n // 100_000) + " लाख")
        n %= 100_000

    # Thousands (1,000)
    if n >= 1_000:
        parts.append(_sub_thousand(n // 1_000) + " हजार")
        n %= 1_000

    # Hundreds (100)
    if n >= 100:
        parts.append(ONES[n // 100] + " सौ")
        n %= 100

    # Remainder
    if n > 0:
        parts.append(_sub_hundred(n))

    return " ".join(parts)

# ─────────────────────────────────────────────────────────────
# 6. BANK NAME NORMALIZER
# ─────────────────────────────────────────────────────────────
_BANK_MAP = [
    # (lowercase fragment, Hindi TTS text)
    ("state bank of india",   "स्टेट बैंक ऑफ इंडिया"),
    ("sbi",                   "एस बी आई"),
    ("au small finance",      "ए यू स्मॉल फाइनेंस बैंक"),
    ("au small",              "ए यू स्मॉल फाइनेंस बैंक"),
    ("rajasthan grameen",     "राजस्थान मरुधरा ग्रामीण बैंक"),
    ("rajasthan gramin",      "राजस्थान ग्रामीण बैंक"),
    ("hdfc",                  "एच डी एफ सी"),
    ("icici",                 "आई सी आई सी आई"),
    ("axis bank",             "एक्सिस बैंक"),
    ("axis",                  "एक्सिस बैंक"),
    ("kotak mahindra",        "कोटक महिंद्रा बैंक"),
    ("kotak",                 "कोटक बैंक"),
    ("punjab national bank",  "पंजाब नेशनल बैंक"),
    ("pnb",                   "पी एन बी"),
    ("bank of baroda",        "बैंक ऑफ बड़ौदा"),
    ("bob",                   "बैंक ऑफ बड़ौदा"),
    ("union bank of india",   "यूनियन बैंक ऑफ इंडिया"),
    ("union bank",            "यूनियन बैंक"),
    ("canara bank",           "केनरा बैंक"),
    ("canara",                "केनरा बैंक"),
    ("indian bank",           "इंडियन बैंक"),
    ("bank of india",         "बैंक ऑफ इंडिया"),
    ("central bank of india", "सेंट्रल बैंक ऑफ इंडिया"),
    ("central bank",          "सेंट्रल बैंक"),
    ("indian overseas bank",  "इंडियन ओवरसीज बैंक"),
    ("uco bank",              "यूको बैंक"),
    ("uco",                   "यूको बैंक"),
    ("au small finance bank", "ए यू स्मॉल फाइनेंस बैंक"),
    ("au bank",               "ए यू बैंक"),
    ("au",                    "ए यू बैंक"),
    ("idfc first bank",       "आई डी एफ सी फर्स्ट बैंक"),
    ("idfc",                  "आई डी एफ सी बैंक"),
    ("idbi bank",             "आई डी बी आई बैंक"),
    ("idbi",                  "आई डी बी आई"),
    ("yes bank",              "यस बैंक"),
    ("yes",                   "यस बैंक"),
    ("indusind bank",         "इंडसइंड बैंक"),
    ("indusind",              "इंडसइंड बैंक"),
    ("federal bank",          "फेडरल बैंक"),
    ("federal",               "फेडरल बैंक"),
    ("karnataka bank",        "कर्नाटक बैंक"),
    ("south indian bank",     "साउथ इंडियन बैंक"),
    ("rbl bank",              "आर बी एल बैंक"),
    ("rbl",                   "आर बी एल बैंक"),
    ("bajaj finserv",         "बजाज फिनसर्व"),
    ("bajaj finance",         "बजाज फाइनेंस"),
    ("bajaj",                 "बजाज"),
    ("tata capital",          "टाटा कैपिटल"),
    ("tata",                  "टाटा"),
    ("mahindra finance",      "महिंद्रा फाइनेंस"),
    ("mahindra",              "महिंद्रा"),
    ("muthoot finance",       "मुथूट फाइनेंस"),
    ("muthoot",               "मुथूट"),
    ("manappuram finance",    "मणप्पुरम फाइनेंस"),
    ("manappuram",            "मणप्पुरम"),
    ("iifl finance",          "आई आई एफ एल फाइनेंस"),
    ("iifl",                  "आई आई एफ एल"),
    ("hero fincorp",          "हीरो फिनकॉर्प"),
    ("hero",                  "हीरो"),
    ("piramal finance",       "पिरामल फाइनेंस"),
    ("piramal",               "पिरामल"),
]


def normalize_bank_name_for_tts(bank_name: str) -> str:
    """
    Map an English bank name / abbreviation to Hindi phonetic text for gTTS.
    Returns the original name if no mapping is found.
    """
    if not bank_name or not isinstance(bank_name, str):
        return bank_name or ""
    lower = bank_name.strip().lower()
    for fragment, hindi in _BANK_MAP:
        if fragment in lower:
            return hindi
    return bank_name.strip()


# ─────────────────────────────────────────────────────────────
# 7. HINDI TEXT GENERATOR
# ─────────────────────────────────────────────────────────────
import re
from datetime import datetime
import pytz
from dateutil import parser as date_parser

def determine_call_type(due_date_str: str) -> str:
    """Returns 'recovery' if due_date is past (strictly before today), else 'reminder'."""
    if not due_date_str:
        return "reminder"
        
    ist_now = datetime.now(pytz.timezone('Asia/Kolkata'))
    current_day = ist_now.day
    
    # Try parsing as a full date first
    try:
        due_date_obj = date_parser.parse(str(due_date_str), dayfirst=True)
        # Deep AI Logic: Only mark as recovery if the current date is strictly GREATER THAN the due date.
        # If it is today (==), it remains a reminder.
        if ist_now.date() > due_date_obj.date():
            return "recovery"
        else:
            return "reminder"
    except Exception:
        pass
        
    # Fallback to extracting day numbers (e.g., "15" or "15th")
    nums = re.findall(r'\d+', str(due_date_str))
    if nums and int(nums[0]) <= 31:
        if current_day > int(nums[0]):
            return "recovery"
        else:
            return "reminder"
            
    return "reminder"

def generate_hindi_text(
    name,
    bank_name,
    emi_amount,
    due_date,
    total_loan,
    paid_loan,
    balance_loan,
    call_type,
) -> str:
    """
    Build a natural Hindi loan-reminder message from structured fields.
    Dynamically adjusts tone for Recovery vs Reminder based on call_type.
    """
    def _to_words(val) -> str:
        try:
            return number_to_rajasthani(int(round(float(str(val).replace(",", "")))))
        except (ValueError, TypeError):
            return str(val)

    name_str      = str(name).strip()      if name      else "महोदय"
    due_str       = str(due_date).strip()  if due_date  else ""
    bank_str      = normalize_bank_name_for_tts(str(bank_name).strip())
    emi_words     = _to_words(emi_amount)
    
    total_words   = _to_words(total_loan)
    paid_words    = _to_words(paid_loan)
    balance_words = _to_words(balance_loan)
    
    import random
    agent_name = random.choice(["नेहा", "लक्ष्मी", "स्नेहा", "साक्षी"])
    
    if call_type == "recovery":
        # Strict Recovery Tone
        msg_parts = [
            f"नमस्ते {name_str} जी।",
            f"मैं {agent_name} बोल रही हूँ, {bank_str} से। यह एक रिकवरी कॉल है।",
            f"आपकी {due_str} की {emi_words} रुपये की किश्त अभी तक पेंडिंग है।",
            f"आपके कुल लोन {total_words} रुपये में से {paid_words} रुपये जमा हो चुके हैं, और {balance_words} रुपये अभी बाकी हैं।",
            "कृपया अपना बकाया आज ही जमा कराएं, अन्यथा आपको पेनाल्टी लग सकती है। धन्यवाद।"
        ]
    else:
        # Soft Reminder Tone
        msg_parts = [
            f"नमस्ते {name_str} जी।",
            f"मैं {agent_name} बोल रही हूँ, आपका {bank_str} में स्वागत है। यह एक रिमाइंडर कॉल है।",
            f"आपकी इस महीने की किश्त {emi_words} रुपये {due_str} को आने वाली है।",
            f"आपके कुल लोन {total_words} रुपये में से {paid_words} रुपये जमा हो चुके हैं, और {balance_words} रुपये अभी बाकी हैं।",
            "कृपया समय पर भुगतान करके अपना सिविल स्कोर बनाए रखें। धन्यवाद।"
        ]

    return " ".join(msg_parts)

# ─────────────────────────────────────────────────────────────
# 8. COLUMN MAPPING HELPERS
# ─────────────────────────────────────────────────────────────
_COLUMN_ALIASES = {
    "name":         ["name", "नाम", "customer name", "borrower name", "full name"],
    "phone":        ["phone", "mobile", "phone number", "mobile number", "contact",
                     "फोन", "मोबाइल", "phone no", "mobile no", "contact number"],
    "bank_name":    ["bank name", "bank", "bank_name", "lender", "बैंक", "बैंक का नाम"],
    "emi_amount":   ["emi amount", "emi", "monthly emi", "instalment", "installment",
                     "किश्त", "emi_amount", "monthly instalment"],
    "due_date":     ["due date", "due_date", "payment date", "date", "तारीख",
                     "due", "due dt", "payment due date"],
    "total_loan":   ["total loan", "total_loan", "loan amount", "principal",
                     "कुल लोन", "total amount", "loan"],
    "paid_loan":    ["paid loan", "paid_amount", "paid", "amount paid",
                     "जमा", "paid loan amount", "repaid"],
    "balance_loan": ["balance loan", "balance", "outstanding", "remaining",
                     "बाकी", "balance_loan", "balance amount", "outstanding amount"],
}


def _find_column(df_columns, field):
    """Return the actual DataFrame column name that matches *field* aliases."""
    lower_cols = {c.strip().lower(): c for c in df_columns}
    for alias in _COLUMN_ALIASES.get(field, []):
        if alias in lower_cols:
            return lower_cols[alias]
    return None


def _map_columns(df):
    """Map logical field names to actual DataFrame column names."""
    return {field: _find_column(list(df.columns), field) for field in _COLUMN_ALIASES}


# ─────────────────────────────────────────────────────────────
# 9. INITIALIZATION
# ─────────────────────────────────────────────────────────────
# Build CallManager only when all credentials are present
cm = None
_cm_errors = []


def _build_call_manager():
    missing  = []
    errors   = []
    
    sid = os.getenv("PLIVO_AUTH_ID", "MAZGRJNDZHNWITYJLJMC").strip()
    token = os.getenv("PLIVO_AUTH_TOKEN", "ZmE2NGQyMTQtNDdmOS00OTg0LTQwZjAtMDdkMWYw").strip()
    caller = os.getenv("PLIVO_CALLER_NUMBER", "+918031449735").strip()
    base_url = os.getenv("PUBLIC_BASE_URL", "").strip()

    if not sid:      missing.append("PLIVO_AUTH_ID")
    if not token:    missing.append("PLIVO_AUTH_TOKEN")
    if not caller:   missing.append("PLIVO_CALLER_NUMBER")
    if not base_url: missing.append("PUBLIC_BASE_URL")

    if missing:
        errors.append(f"Missing Plivo credentials: {', '.join(missing)}")
        return None, errors

    try:
        mgr = CallManager(
            account_sid=sid,
            auth_token=token,
            caller_number=caller,
            public_base_url=base_url
        )
        return mgr, []
    except Exception as exc:
        logger.exception("Failed to build CallManager for Plivo")
        return None, [str(exc)]


cm, _cm_errors = _build_call_manager()


# ─────────────────────────────────────────────────────────────
# 10. ROUTES
# ─────────────────────────────────────────────────────────────

# ── GET / ────────────────────────────────────────────────────
@app.route("/", methods=["GET"])
def index():
    return render_template("index.html")

@app.route("/plivoxml", methods=["GET", "POST"])
def plivo_xml():
    import base64
    b64_text = request.args.get("b64", "")
    job_id = request.args.get("job_id", "")
    
    # Instantly set job to in-progress when user picks up!
    if job_id and job_id in JOB_DB:
        JOB_DB[job_id]["status"] = "in-progress"
        JOB_DB[job_id]["updated_at"] = time.time()
        logger.info(f"User picked up! Job {job_id} is now in-progress")

    try:
        hindi_text = base64.urlsafe_b64decode(b64_text).decode("utf-8")
    except Exception:
        hindi_text = "नमस्ते"
    
    # Plivo Native Neural Hindi TTS (Amazon Polly Aditi Neural)
    xml_response = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Speak voice="Polly.Aditi" language="hi-IN">{hindi_text}</Speak>
</Response>"""
    return Response(xml_response, mimetype="application/xml")

# Background Task Queue
JOB_DB = {}
executor = ThreadPoolExecutor(max_workers=5)

# ── POST /upload ─────────────────────────────────────────────
@app.route("/upload", methods=["POST"])
def upload():
    global processed_data, cm, _cm_errors

    if "file" not in request.files:
        return jsonify({"error": "No file part in request"}), 400

    file = request.files["file"]
    if not file or file.filename == "":
        return jsonify({"error": "No file selected"}), 400

    fname = file.filename.lower()
    try:
        if fname.endswith(".csv"):
            for enc in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
                try:
                    file.stream.seek(0)
                    df = pd.read_csv(file.stream, encoding=enc, dtype=str)
                    break
                except (UnicodeDecodeError, pd.errors.ParserError):
                    continue
            else:
                return jsonify({"error": "Cannot decode CSV file"}), 400
        elif fname.endswith((".xlsx", ".xls")):
            df = pd.read_excel(file.stream, dtype=str)
        else:
            return jsonify({
                "error": "Unsupported file type. Please upload CSV or Excel (.xlsx/.xls)"
            }), 400
    except Exception as exc:
        logger.exception("File read error")
        return jsonify({"error": f"Failed to read file: {exc}"}), 400

    # Strip column names and fill NaN with empty string
    df.columns = [str(c).strip() for c in df.columns]
    df = df.where(pd.notna(df), "")

    col_map = _map_columns(df)
    positional_fields = [
        "name", "phone", "bank_name", "emi_amount",
        "due_date", "total_loan", "paid_loan", "balance_loan",
    ]

    filter_bank = request.form.get('global_bank_name', 'all').strip().lower()

    def _get_val(row, field, pos_idx):
        col = col_map.get(field)
        if col and col in df.columns:
            return str(row[col]).strip()
        if pos_idx < len(df.columns):
            return str(row.iloc[pos_idx]).strip()
        return ""

    records = []
    for _, row in df.iterrows():
        name         = _get_val(row, "name",         0)
        phone_raw    = _get_val(row, "phone",         1)
        bank_name    = _get_val(row, "bank_name",     2)
        
        # Deep AI CSV Filtering: If the user selected a specific bank, skip rows that don't match
        if filter_bank and filter_bank != "all":
            if filter_bank not in bank_name.lower():
                continue
        emi_amount   = _get_val(row, "emi_amount",    3)
        due_date     = _get_val(row, "due_date",      4)
        total_loan   = _get_val(row, "total_loan",    5)
        paid_loan    = _get_val(row, "paid_loan",     6)
        balance_loan = _get_val(row, "balance_loan",  7)

        # Skip fully empty rows
        if not any([name, phone_raw, bank_name, emi_amount]):
            continue

        # Normalise phone
        phone_valid  = False
        phone_number = phone_raw
        try:
            phone_number = normalize_phone_number(phone_raw)
            phone_valid  = True
        except ValueError as exc:
            logger.warning("Phone normalisation failed for '%s': %s", phone_raw, exc)

        # Determine Call Type
        call_type = determine_call_type(due_date)

        # Generate Hindi text
        try:
            hindi_text = generate_hindi_text(
                name, bank_name, emi_amount, due_date,
                total_loan, paid_loan, balance_loan, call_type
            )
        except Exception as exc:
            logger.warning("Hindi text generation error: %s", exc)
            hindi_text = f"नमस्ते {name} साहब। आपकी किश्त जमा करानी है।"

        records.append({
            "name":            name,
            "phone_number":    phone_number,
            "phone_valid":     phone_valid,
            "bank_name":       bank_name,
            "emi_amount":      emi_amount,
            "due_date":        due_date,
            "total_loan":      total_loan,
            "paid_loan":       paid_loan,
            "balance_loan":    balance_loan,
            "call_type":       call_type,
            "rajasthani_text": hindi_text,
        })

    # Persist and reset state
    processed_data = records

    logger.info("Upload complete: %d record(s) processed", len(records))
    return jsonify({
        "data":            records,
        "call_configured": cm is not None,
    })


# ── GET /call-config-status ──────────────────────────────────
@app.route("/call-config-status", methods=["GET"])
def call_config_status():
    """Return Plivo configuration health-check."""
    missing = list(_cm_errors) if _cm_errors and all(
        e in ["PLIVO_AUTH_ID", "PLIVO_AUTH_TOKEN",
               "PLIVO_CALLER_NUMBER", "PUBLIC_BASE_URL"] for e in _cm_errors
    ) else []

    # Always re-check live env
    live_missing = []
    if not os.getenv("PLIVO_AUTH_ID", "").strip():  live_missing.append("PLIVO_AUTH_ID")
    if not os.getenv("PLIVO_AUTH_TOKEN", "").strip():   live_missing.append("PLIVO_AUTH_TOKEN")
    if not os.getenv("PLIVO_CALLER_NUMBER", "").strip():live_missing.append("PLIVO_CALLER_NUMBER")
    if not os.getenv("PUBLIC_BASE_URL", "").strip():     live_missing.append("PUBLIC_BASE_URL")

    return jsonify({
        "configured": cm is not None,
        "missing":    live_missing,
        "errors":     _cm_errors,
    })


# ── POST /call ───────────────────────────────────
@app.route("/call", methods=["POST"])
def initiate_call():
    """Stateless Plivo Call Initialiser. Queues background task instantly."""
    if cm is None:
        missing = _cm_errors if _cm_errors else ["Plivo credentials not configured"]
        return jsonify({"error": "Plivo not configured", "missing": missing}), 503

    req_data = request.get_json() or {}
    phone_number = req_data.get("phone_number")
    hindi_text = req_data.get("rajasthani_text", "")

    if not phone_number:
        return jsonify({"error": "Missing phone_number in request"}), 400

    try:
        logger.info("Dialing Plivo instantly using Native Neural TTS...")
        result = cm.initiate_call(phone_number, hindi_text, base_url=request.host_url, job_id="")
        
        if "call_sid" in result:
            actual_call_sid = result["call_sid"]
            return jsonify({"call_sid": actual_call_sid}), 200
        else:
            return jsonify({"error": "Failed to initiate call via Plivo"}), 500
            
    except Exception as e:
        logger.error("Call failed: %s", e)
        return jsonify({"error": str(e)}), 500
# ── GET /call-status ───────────────────────────────────
@app.route("/call-status", methods=["GET"])
def call_status_poll():
    """Poll endpoint for the frontend to check real-time call state via JOB_DB or Plivo API (for Vercel)."""
    call_sid = request.args.get("sid")
    if not call_sid:
        return jsonify({"error": "Missing sid parameter"}), 400
        
    local_status = "initiated"
    if call_sid in JOB_DB:
        local_status = JOB_DB[call_sid]["status"]
        if local_status not in ["initiated", "queued", "generating"]:
            # If we successfully tracked it locally via webhook, return it instantly!
            return jsonify({
                "call_sid": call_sid,
                "status": local_status,
                "updated_at": JOB_DB[call_sid]["updated_at"]
            })
            
    # For Vercel Serverless, JOB_DB might be empty or fragmented. 
    # Fallback to querying Plivo API directly.
    if cm:
        import plivo
        
        def normalize_status(raw):
            s = raw.lower()
            if s in ["completed", "answer", "answered", "hangup"]: return "completed"
            if s in ["failed", "rejected", "canceled", "cancelled", "no-answer", "busy"]: return "busy"
            if s in ["ringing", "in-progress", "queued", "initiated"]: return s
            return s
            
        try:
            client = plivo.RestClient(cm.account_sid, cm.auth_token)
            # Try to get Call Detail Record (works if call is finished)
            call = client.calls.get(call_sid)
            status = normalize_status(call.call_state)
            if call_sid in JOB_DB:
                JOB_DB[call_sid]["status"] = status
            return jsonify({"call_sid": call_sid, "status": status})
        except Exception as e:
            if "not found" in str(e).lower():
                try:
                    # Try to get live call status (works if call is ringing/in-progress)
                    live = client.calls.live_call_get(call_sid)
                    status = normalize_status(live.call_status)
                    if call_sid in JOB_DB:
                        JOB_DB[call_sid]["status"] = status
                    return jsonify({"call_sid": call_sid, "status": status})
                except Exception:
                    pass

    return jsonify({"call_sid": call_sid, "status": local_status, "updated_at": time.time()})

@app.route("/plivo-callback", methods=["POST", "GET"])
def plivo_callback():
    """Ultra-efficient zero-latency webhook for Plivo status updates!"""
    job_id = request.args.get("job_id")
    if job_id and job_id in JOB_DB:
        status = request.form.get("CallStatus") or request.args.get("CallStatus")
        if status:
            JOB_DB[job_id]["status"] = status.lower()
            JOB_DB[job_id]["updated_at"] = time.time()
            logger.info("Real-time Webhook Update: Job %s is now %s", job_id, status)
    return "OK", 200


# ── GET /audio ────────────────────────────────────────────────
@app.route("/audio", methods=["GET"])
def preview_audio():
    """Serve a preview of the generated Hindi speech using gTTS."""
    import base64
    import io
    from gtts import gTTS

    b64_str = request.args.get("b64")
    if not b64_str:
        return jsonify({"error": "Missing b64 query parameter"}), 400

    try:
        # Standardize base64 for decoding
        b64_standard = b64_str.replace("-", "+").replace("_", "/")
        padding = len(b64_standard) % 4
        if padding:
            b64_standard += "=" * (4 - padding)

        text = base64.b64decode(b64_standard).decode("utf-8")
        
        # Generate MP3 preview using gTTS
        tts = gTTS(text=text, lang='hi')
        audio_fp = io.BytesIO()
        tts.write_to_fp(audio_fp)
        audio_fp.seek(0)
        
        return send_file(audio_fp, mimetype="audio/mpeg")
    except Exception as e:
        logger.error(f"Error in preview_audio: {e}")
        return jsonify({"error": "Failed to generate preview audio"}), 500


# (Batch routes removed for fully client-side execution)
# ── GET /download-sample ─────────────────────────────────────
@app.route("/download-sample", methods=["GET"])
def download_sample():
    """Serve the bundled sample_data.csv for users to download."""
    sample_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "sample_data.csv"
    )
    if not os.path.exists(sample_path):
        return jsonify({"error": "Sample file not found on server"}), 404
    return send_file(
        sample_path,
        mimetype="text/csv",
        as_attachment=True,
        download_name="sample_data.csv",
    )


# ─────────────────────────────────────────────────────────────
# 11. ERROR HANDLERS
# ─────────────────────────────────────────────────────────────
@app.errorhandler(400)
def bad_request(exc):
    return jsonify({"error": "Bad request", "detail": str(exc)}), 400


@app.errorhandler(404)
def not_found(exc):
    return jsonify({"error": "Not found", "detail": str(exc)}), 404


@app.errorhandler(413)
def request_entity_too_large(exc):
    return jsonify({"error": "File too large. Maximum allowed size is 16 MB."}), 413


@app.errorhandler(500)
def internal_error(exc):
    logger.exception("Unhandled 500 error")
    return jsonify({"error": "Internal server error"}), 500


# ─────────────────────────────────────────────────────────────
# 12. MAIN
# ─────────────────────────────────────────────────────────────
if __name__ == "__main__":
    logger.info("Starting Rajasthani Voice Pro on http://0.0.0.0:5000")
    logger.info("Plivo configured: %s", cm is not None)
    app.run(debug=False, host="0.0.0.0", port=5000)
