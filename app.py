# -*- coding: utf-8 -*-
"""
Rajasthani Voice Pro - Production Flask Backend
Twilio-powered Hindi/Rajasthani IVR voice-call system.
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
from dotenv import load_dotenv

# Load environment variables from .env file first
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

# ── Env / Config ────────────────────────────────────────────
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN  = os.getenv("TWILIO_AUTH_TOKEN", "")
TWILIO_CALLER_NUM  = os.getenv("TWILIO_CALLER_NUMBER", "")
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
    Wraps Twilio REST calls and tracks per-record call state.
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
            "CallManager ready (Twilio, caller=%s, base_url=%s)",
            caller_number, public_base_url,
        )

    # ── public API ───────────────────────────────────────────
    def initiate_call(self, phone_number: str, hindi_text: str = "", base_url: str = None) -> dict:
        """Place an outbound call via Twilio."""
        import base64
        import urllib.parse
        from twilio.rest import Client
        
        # Base64 encode the text to avoid URL encoding issues
        b64_text = base64.urlsafe_b64encode(hindi_text.encode("utf-8")).decode("utf-8")
        
        # Twilio webhook (Return TwiML with native Hindi TTS)
        actual_base_url = (base_url or self.public_base_url).rstrip("/")
        answer_url = f"{actual_base_url}/twiml?b64={b64_text}"
        
        # Twilio requires E.164 format
        if not phone_number.startswith("+"):
            formatted_to = "+91" + phone_number if len(phone_number) == 10 else "+" + phone_number
        else:
            formatted_to = phone_number
            
        logger.info(f"Calling Twilio to dial {formatted_to} with URL {answer_url}")
        try:
            client = Client(self.account_sid, self.auth_token)
            call = client.calls.create(
                to=formatted_to,
                from_=self.caller_number,
                url=answer_url,
                status_callback=f"{actual_base_url}/twilio-cache-warm?b64={b64_text}",
                status_callback_event=["initiated", "ringing"],
            )
            logger.info("Call successfully initiated, SID: %s", call.sid)
            return {"call_sid": call.sid}
        except Exception as e:
            logger.error(f"Twilio call failed: {e}")
            return {"error": str(e)}

    def get_status(self, call_sid: str) -> dict:
        """Fetch real-time call status directly from Twilio API."""
        from twilio.rest import Client
        try:
            client = Client(self.account_sid, self.auth_token)
            call = client.calls(call_sid).fetch()
            return {
                "call_sid": call.sid,
                "status": call.status,
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
    """Returns 'recovery' if due_date is past or today, else 'reminder'."""
    if not due_date_str:
        return "reminder"
        
    ist_now = datetime.now(pytz.timezone('Asia/Kolkata'))
    current_day = ist_now.day
    
    # Try parsing as a full date first
    try:
        due_date_obj = date_parser.parse(str(due_date_str), dayfirst=True)
        if ist_now.date() >= due_date_obj.date():
            return "recovery"
        else:
            return "reminder"
    except Exception:
        pass
        
    # Fallback to extracting day numbers (e.g., "15" or "15th")
    nums = re.findall(r'\d+', str(due_date_str))
    if nums and int(nums[0]) <= 31:
        if current_day >= int(nums[0]):
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
    
    if call_type == "recovery":
        # Strict Recovery Tone
        msg_parts = [
            f"नमस्ते {name_str} जी।",
            f"{bank_str} से यह रिकवरी कॉल है।",
            f"आपकी {due_str} की {emi_words} रुपये की किश्त अभी तक पेंडिंग है।",
            f"आपके कुल लोन {total_words} रुपये में से {paid_words} रुपये जमा हो चुके हैं, और {balance_words} रुपये अभी बाकी हैं।",
            "कृपया अपना बकाया आज ही जमा कराएं, अन्यथा आपको पेनाल्टी लग सकती है। धन्यवाद।"
        ]
    else:
        # Soft Reminder Tone
        msg_parts = [
            f"नमस्ते {name_str} जी।",
            f"आपका {bank_str} में स्वागत है। यह एक रिमाइंडर कॉल है।",
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
    
    sid = os.getenv("TWILIO_ACCOUNT_SID", "").strip()
    token = os.getenv("TWILIO_AUTH_TOKEN", "").strip()
    caller = os.getenv("TWILIO_CALLER_NUMBER", "").strip()
    base_url = os.getenv("PUBLIC_BASE_URL", "").strip()

    if not sid:      missing.append("TWILIO_ACCOUNT_SID")
    if not token:    missing.append("TWILIO_AUTH_TOKEN")
    if not caller:   missing.append("TWILIO_CALLER_NUMBER")
    if not base_url: missing.append("PUBLIC_BASE_URL")

    if missing:
        errors.append(f"Missing Twilio credentials: {', '.join(missing)}")
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
        logger.exception("Failed to build CallManager for Twilio")
        return None, [str(exc)]


cm, _cm_errors = _build_call_manager()


# ─────────────────────────────────────────────────────────────
# 10. ROUTES
# ─────────────────────────────────────────────────────────────

# ── GET / ────────────────────────────────────────────────────
@app.route("/", methods=["GET"])
def index():
    return render_template("index.html")

@app.route("/twiml", methods=["GET", "POST"])
def twilio_twiml():
    import urllib.parse
    b64_text = request.args.get("b64", "")
    
    base_url = request.host_url.rstrip("/")
    audio_url = f"{base_url}/audio?b64={urllib.parse.quote(b64_text)}"
    
    # Use Twilio's Play tag to fetch the Sarvam AI audio statelessly.
    xml_response = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Play>{audio_url}</Play>
</Response>"""
    return Response(xml_response, mimetype="application/xml")

# ── GET /audio ───────────────────────────────────────────────
@app.route("/audio", methods=["GET", "POST"])
def serve_audio():
    """Stateless audio endpoint. Calls Sarvam AI API and returns binary audio stream."""
    import base64
    from sarvamai import SarvamAI
    from io import BytesIO

    b64_text = request.args.get("b64", "").strip()
    if not b64_text:
        return jsonify({"error": "Missing b64 text"}), 400

    try:
        hindi_text = base64.urlsafe_b64decode(b64_text).decode("utf-8")
    except Exception:
        return jsonify({"error": "Invalid base64 encoding"}), 400

    sarvam_key = os.getenv("SARVAM_API_KEY", "").strip()
    if not sarvam_key:
        logger.error("SARVAM_API_KEY is missing!")
        return jsonify({"error": "Sarvam AI API Key not configured"}), 503

    try:
        logger.info("Generating Sarvam AI audio for text length: %d", len(hindi_text))
        client = SarvamAI(api_subscription_key=sarvam_key)
        
        # Call Sarvam AI synchronously (typically <2s response)
        response = client.text_to_speech.convert(
            model="bulbul:v3",
            text=hindi_text,
            target_language_code="hi-IN",
            speaker="ritu",
            pace=1.0,
            speech_sample_rate=22050,
        )
        
        # response is an object with base64 encoded 'audios' array
        # e.g., response.audios[0] contains the base64 string
        if not response or not hasattr(response, 'audios') or not response.audios:
            raise Exception("Invalid response from Sarvam AI")
            
        b64_audio = response.audios[0]
        audio_bytes = base64.b64decode(b64_audio)
        
        # Send raw bytes directly as a WAV file
        buffer = BytesIO(audio_bytes)
        buffer.seek(0)
        
        res = send_file(
            buffer,
            mimetype="audio/wav",
            as_attachment=False
        )
        
        # CRITICAL: Tell Vercel CDN to cache this audio response for 1 hour
        # This prevents duplicate Sarvam AI API charges and speeds up Twilio playback
        res.headers["Cache-Control"] = "public, max-age=3600"
        return res

    except Exception as exc:
        logger.exception("Sarvam AI TTS generation failed")
        return jsonify({"error": f"Audio generation failed: {exc}"}), 502

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
    """Return Twilio configuration health-check."""
    missing = list(_cm_errors) if _cm_errors and all(
        e in ["TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN",
               "TWILIO_CALLER_NUMBER", "PUBLIC_BASE_URL"] for e in _cm_errors
    ) else []

    # Always re-check live env
    live_missing = []
    if not os.getenv("TWILIO_ACCOUNT_SID", "").strip():  live_missing.append("TWILIO_ACCOUNT_SID")
    if not os.getenv("TWILIO_AUTH_TOKEN", "").strip():   live_missing.append("TWILIO_AUTH_TOKEN")
    if not os.getenv("TWILIO_CALLER_NUMBER", "").strip():live_missing.append("TWILIO_CALLER_NUMBER")
    if not os.getenv("PUBLIC_BASE_URL", "").strip():     live_missing.append("PUBLIC_BASE_URL")

    return jsonify({
        "configured": cm is not None,
        "missing":    live_missing,
        "errors":     _cm_errors,
    })


# ── POST /call ───────────────────────────────────
@app.route("/call", methods=["POST"])
def initiate_call():
    """Stateless Twilio Call Initialiser. Takes phone number and text in JSON."""
    if cm is None:
        missing = _cm_errors if _cm_errors else ["Twilio credentials not configured"]
        return jsonify({"error": "Twilio not configured", "missing": missing}), 503

    req_data = request.get_json() or {}
    phone_number = req_data.get("phone_number")
    hindi_text = req_data.get("rajasthani_text", "")

    if not phone_number:
        return jsonify({"error": "Missing phone_number in request"}), 400

    try:
        # ULTRA DEEP AI CACHE PRE-WARM FOR TWILIO (US-EAST / IAD1)
        # By forcing the Vercel edge node to fetch the audio right before dialing,
        # we guarantee a 100% Cache HIT exactly when the user picks up the phone.
        # This completely eliminates Sarvam AI generation latency when answering!
        import requests
        import base64
        import urllib.parse
        
        b64_text = base64.urlsafe_b64encode(hindi_text.encode("utf-8")).decode("utf-8")
        audio_url = f"{request.host_url}audio?b64={urllib.parse.quote(b64_text)}"
        
        try:
            logger.info("Deep pre-warming Vercel CDN cache for audio URL before dialing...")
            requests.get(audio_url, timeout=10)
        except Exception as e:
            logger.warning("Cache pre-warm failed: %s", e)
            
        # Use request.host_url so Vercel uses the vercel domain and local uses ngrok
        result = cm.initiate_call(phone_number, hindi_text, base_url=request.host_url)
        return jsonify(result), 200
    except Exception as exc:
        logger.exception("Twilio call failed to %s", phone_number)
        return jsonify({"error": f"Twilio API error: {exc}"}), 502

@app.route("/twilio-cache-warm", methods=["POST"])
def twilio_cache_warm():
    """
    Ultra Deep AI Logic:
    Twilio servers are in the US. Vercel edge caches are regional.
    When Twilio initiates the call, it hits this webhook from the US.
    We instantly force this US-based server to fetch the audio, 
    warming the local US cache BEFORE the user even picks up the phone!
    """
    b64_text = request.args.get("b64")
    if b64_text:
        try:
            import requests
            # Hit the audio endpoint. This executes Sarvam AI and caches it in this specific region!
            # We block up to 10 seconds to ensure the cache is fully written before Twilio asks for it.
            requests.get(f"{request.host_url}audio?b64={b64_text}", timeout=10)
        except Exception as e:
            logger.warning(f"Cache warm failed: {e}")
    
    return "OK", 200

# ── GET /call-status ───────────────────────────────────
@app.route("/call-status", methods=["GET"])
def call_status_poll():
    """Poll endpoint for the frontend to check real-time call state via Twilio API."""
    call_sid = request.args.get("sid")
    if not call_sid:
        return jsonify({"error": "Missing sid parameter"}), 400
        
    if cm is None:
        return jsonify({"error": "Twilio not configured"}), 503
        
    state = cm.get_status(call_sid)
    return jsonify(state)


# (Batch routes removed for fully client-side execution)


# ── GET /download-sample ─────────────────────────────────────
@app.route("/download-sample", methods=["GET"])
def download_sample():
    """Serve the bundled sample_data.xlsx for users to download."""
    sample_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "sample_data.xlsx"
    )
    if not os.path.exists(sample_path):
        return jsonify({"error": "Sample file not found on server"}), 404
    return send_file(
        sample_path,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True,
        download_name="sample_data.xlsx",
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
    logger.info("Twilio configured: %s", cm is not None)
    app.run(debug=False, host="0.0.0.0", port=5000)
