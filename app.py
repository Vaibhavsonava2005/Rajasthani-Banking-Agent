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
# 3. AUDIO CACHE
# ─────────────────────────────────────────────────────────────
class AudioCache:
    """
    Thread-safe on-disk audio cache with TTL-based expiry.
    Generates gTTS Hindi MP3 files on demand.
    """

    def __init__(self, cache_dir: str, ttl_seconds: int = 1800):
        self.cache_dir   = cache_dir
        self.ttl_seconds = ttl_seconds
        self._lock       = threading.Lock()
        os.makedirs(cache_dir, exist_ok=True)
        logger.info("AudioCache initialised at %s (TTL=%ds)", cache_dir, ttl_seconds)

    # ── private helpers ──────────────────────────────────────
    def _path_for(self, record_id: int) -> str:
        return os.path.join(self.cache_dir, f"audio_{record_id}.mp3")

    def _is_valid(self, path: str) -> bool:
        if not os.path.exists(path):
            return False
        age = time.time() - os.path.getmtime(path)
        return age < self.ttl_seconds

    def _generate(self, path: str, text: str) -> None:
        """Synthesise TTS using free Edge TTS Neural voices and save to *path*."""
        import asyncio
        import edge_tts
        
        tmp_path = path + ".tmp"
        try:
            # We use the premium hi-IN-SwaraNeural female voice
            # or hi-IN-MadhurNeural male voice.
            voice = "hi-IN-SwaraNeural"
            
            async def _do_tts():
                communicate = edge_tts.Communicate(text, voice)
                await communicate.save(tmp_path)
                
            asyncio.run(_do_tts())
            
            if not os.path.exists(tmp_path) or os.path.getsize(tmp_path) == 0:
                raise ValueError("Edge TTS failed to generate an audio file.")
                        
            os.replace(tmp_path, path)
            logger.info("Generated Edge TTS audio: %s", path)
        except Exception:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
            raise

    # ── public API ───────────────────────────────────────────
    def get_or_generate(self, record_id: int, text: str) -> str:
        """Return the cached MP3 path, generating it first if needed."""
        path = self._path_for(record_id)
        with self._lock:
            if not self._is_valid(path):
                logger.info("Cache miss for record %d – generating …", record_id)
                self._generate(path, text)
            else:
                logger.debug("Cache hit for record %d", record_id)
        return path

    def get_path(self, record_id: int):
        """Return cached path if it exists and is still valid, else None."""
        path = self._path_for(record_id)
        return path if self._is_valid(path) else None

    def cleanup_expired(self) -> int:
        """Delete expired MP3 files. Returns count deleted."""
        deleted = 0
        with self._lock:
            for fname in os.listdir(self.cache_dir):
                if not fname.endswith(".mp3"):
                    continue
                fpath = os.path.join(self.cache_dir, fname)
                if not self._is_valid(fpath):
                    try:
                        os.remove(fpath)
                        deleted += 1
                        logger.debug("Expired cache removed: %s", fpath)
                    except OSError as exc:
                        logger.warning("Could not delete %s: %s", fpath, exc)
        if deleted:
            logger.info("cleanup_expired: removed %d file(s)", deleted)
        return deleted

    def invalidate_all(self) -> int:
        """Delete all cached MP3 files. Returns count deleted."""
        deleted = 0
        with self._lock:
            for fname in os.listdir(self.cache_dir):
                if not fname.endswith(".mp3"):
                    continue
                fpath = os.path.join(self.cache_dir, fname)
                try:
                    os.remove(fpath)
                    deleted += 1
                except OSError as exc:
                    logger.warning("Could not delete %s: %s", fpath, exc)
        logger.info("invalidate_all: cleared %d file(s)", deleted)
        return deleted


# ─────────────────────────────────────────────────────────────
# 4. CALL MANAGER
# ─────────────────────────────────────────────────────────────


class CallManager:
    """
    Wraps Twilio REST calls and tracks per-record call state.
    """

    TERMINAL_STATUSES = {
        "completed", "busy", "no-answer", "canceled", "failed"
    }

    def __init__(
        self,
        account_sid: str,
        auth_token: str,
        caller_number: str,
        public_base_url: str,
        exotel_api_key: str,
        exotel_subdomain: str = "api.exotel.com"
    ):
        self.account_sid     = account_sid
        self.auth_token      = auth_token
        self.caller_number   = caller_number
        self.public_base_url = public_base_url.rstrip("/")
        self.exotel_api_key   = exotel_api_key
        self.exotel_subdomain = exotel_subdomain
        
        self._state: dict = {}
        self._lock = threading.Lock()
        logger.info(
            "CallManager ready (Exotel, caller=%s, base_url=%s)",
            caller_number, public_base_url,
        )

    # ── helpers ──────────────────────────────────────────────
    def _default_state(self, record_id: int) -> dict:
        return {
            "record_id":  record_id,
            "status":     "idle",
            "call_sid":   None,
            "updated_at": time.time(),
        }

    def _is_active(self, record_id: int) -> bool:
        s = self._state.get(record_id, {}).get("status", "idle")
        return s not in ("idle",) and s not in self.TERMINAL_STATUSES

    # ── public API ───────────────────────────────────────────
    def initiate_call(self, record_id: int, phone_number: str, hindi_text: str = "") -> dict:
        """Place an outbound call via Exotel and record initial state."""
        import base64
        import requests
        
        b64_text = base64.urlsafe_b64encode(hindi_text.encode("utf-8")).decode("utf-8")
        
        # Exotel uses api_key:api_token for auth
        auth = (self.exotel_api_key, self.auth_token)
        url = f"https://{self.exotel_subdomain}/v1/Accounts/{self.account_sid}/Calls/connect.json"
        
        # Exotel webhook (Return direct MP3 URL from Deepgram cache)
        answer_url = f"{self.public_base_url}/audio/{record_id}?b64={b64_text}"
        
        payload = {
            "From": self.caller_number,
            "To": phone_number,
            "CallerId": self.caller_number,
            "Url": answer_url,
            "CustomField": str(record_id)
        }
        resp = requests.post(url, data=payload, auth=auth)
        resp.raise_for_status()
        call_sid = resp.json().get("Call", {}).get("Sid", f"exotel_{record_id}")

        with self._lock:
            self._state[record_id] = {
                "record_id":  record_id,
                "status":     "initiated",
                "call_sid":   call_sid,
                "updated_at": time.time(),
            }

        logger.info("Call initiated via Exotel – record=%d SID=%s", record_id, call_sid)
        return {"call_sid": call_sid, "status": "initiated"}

    def update_status(
        self, record_id: int, status: str, call_sid: str = None
    ) -> None:
        with self._lock:
            entry = self._state.get(record_id, self._default_state(record_id))
            entry["status"]     = status
            entry["updated_at"] = time.time()
            if call_sid:
                entry["call_sid"] = call_sid
            self._state[record_id] = entry
        logger.info("Status update – record=%d status=%s", record_id, status)

    def get_status(self, record_id: int) -> dict:
        with self._lock:
            return dict(self._state.get(record_id, self._default_state(record_id)))

    def reset_all_state(self) -> None:
        with self._lock:
            self._state.clear()
        logger.info("CallManager state reset")

    def is_active(self, record_id: int) -> bool:
        with self._lock:
            return self._is_active(record_id)

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
def generate_hindi_text(
    name,
    bank_name,
    emi_amount,
    due_date,
    total_loan,
    paid_loan,
    balance_loan,
) -> str:
    """
    Build a natural Hindi loan-reminder message from structured fields.
    All numeric amounts are converted to Rajasthani/Hindi word form.
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

    msg_parts = [
        f"नमस्ते {name_str} साहब।",
        f"{bank_str} की तरफ से आपको सूचित किया जा रहा है।",
        f"आपकी इस महीने की किश्त {emi_words} रुपये",
    ]

    if due_str:
        msg_parts.append(f"{due_str} तक जमा करानी है।")
    else:
        msg_parts.append("जल्द से जल्द जमा करानी है।")

    msg_parts += [
        f"आपके कुल लोन {total_words} रुपये में से {paid_words} रुपये जमा हो चुके हैं",
        f"और {balance_words} रुपये अभी बाकी हैं।",
        "कृपया समय पर भुगतान करें।",
        "धन्यवाद।",
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
AUDIO_CACHE_DIR = tempfile.mkdtemp(prefix="rajasthani_voice_")
audio_cache     = AudioCache(AUDIO_CACHE_DIR, ttl_seconds=1800)

# Build CallManager only when all credentials are present
cm = None
_cm_errors = []


def _build_call_manager():
    missing  = []
    errors   = []
    
    sid    = os.getenv("EXOTEL_ACCOUNT_SID", "").strip()
    token  = os.getenv("EXOTEL_API_TOKEN", "").strip()
    caller = os.getenv("EXOTEL_PHONE_NUMBER", "").strip()
    api_key = os.getenv("EXOTEL_API_KEY", "").strip()
    subdomain = os.getenv("EXOTEL_SUBDOMAIN", "api.exotel.com").strip()
    base_url = os.getenv("PUBLIC_BASE_URL", "").strip()

    if not sid:      missing.append("EXOTEL_ACCOUNT_SID")
    if not token:    missing.append("EXOTEL_API_TOKEN")
    if not caller:   missing.append("EXOTEL_PHONE_NUMBER")
    if not api_key:  missing.append("EXOTEL_API_KEY")
    if not base_url: missing.append("PUBLIC_BASE_URL")

    if missing:
        errors.append(f"Missing Exotel credentials: {', '.join(missing)}")
        return None, errors

    try:
        mgr = CallManager(
            account_sid=sid,
            auth_token=token,
            caller_number=caller,
            public_base_url=base_url,
            exotel_api_key=api_key,
            exotel_subdomain=subdomain
        )
        return mgr, []
    except Exception as exc:
        logger.exception("Failed to build CallManager for Exotel")
        return None, [str(exc)]


cm, _cm_errors = _build_call_manager()


# ── Background cleanup thread ────────────────────────────────
def _cleanup_worker():
    while True:
        time.sleep(300)  # every 5 minutes
        try:
            audio_cache.cleanup_expired()
        except Exception as exc:
            logger.warning("Cleanup thread error: %s", exc)


_cleanup_thread = threading.Thread(target=_cleanup_worker, daemon=True)
_cleanup_thread.start()
logger.info("Background cleanup thread started")


# ─────────────────────────────────────────────────────────────
# 10. ROUTES
# ─────────────────────────────────────────────────────────────

# ── GET / ────────────────────────────────────────────────────
@app.route("/", methods=["GET"])
def index():
    return render_template("index.html")

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

        # Generate Hindi text
        try:
            hindi_text = generate_hindi_text(
                name, bank_name, emi_amount, due_date,
                total_loan, paid_loan, balance_loan,
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
            "rajasthani_text": hindi_text,
        })

    # Persist and reset state
    processed_data = records
    audio_cache.invalidate_all()
    if cm:
        cm.reset_all_state()

    logger.info("Upload complete: %d record(s) processed", len(records))
    return jsonify({
        "data":            records,
        "call_configured": cm is not None,
    })

# ── GET /generate-speech/<index> ─────────────────────────────
@app.route("/generate-speech/<int:index>", methods=["GET"])
def generate_speech(index: int):
    if index < 0 or index >= len(processed_data):
        return jsonify({"error": f"Record index {index} out of range"}), 404

    record     = processed_data[index]
    hindi_text = record.get("rajasthani_text", "")
    if not hindi_text:
        return jsonify({"error": "No text available for this record"}), 400

    try:
        mp3_path = audio_cache.get_or_generate(index, hindi_text)
        return send_file(
            mp3_path,
            mimetype="audio/mpeg",
            as_attachment=False,
            download_name=f"speech_{index}.mp3",
        )
    except Exception as exc:
        logger.exception("Speech generation failed for index %d", index)
        return jsonify({"error": f"Speech generation failed: {exc}"}), 500



# ── GET/POST /audio/<record_id> ───────────────────────────────────
@app.route("/audio/<int:record_id>", methods=["GET", "POST"])
def serve_audio(record_id: int):
    """Used by Twilio to stream audio during a voice call."""
    import base64
    b64_text = request.args.get("b64", "").strip()
    if b64_text:
        try:
            hindi_text = base64.urlsafe_b64decode(b64_text).decode("utf-8")
        except Exception:
            hindi_text = request.args.get("t", "").strip()
    else:
        hindi_text = request.args.get("t", "").strip()

    # Fallback to memory if t is not provided (for direct browser playback)
    if not hindi_text:
        if record_id < 0 or record_id >= len(processed_data):
            return jsonify({"error": "Record not found"}), 404
        record = processed_data[record_id]
        hindi_text = record.get("rajasthani_text", "")

    if not hindi_text:
        return jsonify({"error": "No text for this record"}), 400

    try:
        mp3_path = audio_cache.get_or_generate(record_id, hindi_text)
        return send_file(mp3_path, mimetype="audio/mpeg", as_attachment=False, conditional=True)
    except Exception as exc:
        logger.exception("Audio serve failed for record %d", record_id)
        return jsonify({"error": str(exc)}), 500


# ── GET /call-config-status ──────────────────────────────────
@app.route("/call-config-status", methods=["GET"])
def call_config_status():
    """Return Exotel configuration health-check."""
    missing = list(_cm_errors) if _cm_errors and all(
        e in ["EXOTEL_ACCOUNT_SID", "EXOTEL_API_TOKEN",
               "EXOTEL_PHONE_NUMBER", "EXOTEL_API_KEY", "PUBLIC_BASE_URL"] for e in _cm_errors
    ) else []

    # Always re-check live env
    live_missing = []
    if not os.getenv("EXOTEL_ACCOUNT_SID", "").strip():  live_missing.append("EXOTEL_ACCOUNT_SID")
    if not os.getenv("EXOTEL_API_TOKEN", "").strip():    live_missing.append("EXOTEL_API_TOKEN")
    if not os.getenv("EXOTEL_PHONE_NUMBER", "").strip(): live_missing.append("EXOTEL_PHONE_NUMBER")
    if not os.getenv("EXOTEL_API_KEY", "").strip():      live_missing.append("EXOTEL_API_KEY")
    if not os.getenv("PUBLIC_BASE_URL", "").strip():     live_missing.append("PUBLIC_BASE_URL")

    return jsonify({
        "configured": cm is not None,
        "missing":    live_missing,
        "errors":     _cm_errors,
    })


# ── POST /call/<record_id> ───────────────────────────────────
@app.route("/call/<int:record_id>", methods=["POST"])
def initiate_call(record_id: int):
    if cm is None:
        missing = _cm_errors if _cm_errors else ["Exotel credentials not configured"]
        return jsonify({"error": "Exotel not configured", "missing": missing}), 503

    if record_id < 0 or record_id >= len(processed_data):
        return jsonify({"error": f"Record {record_id} not found"}), 404

    record = processed_data[record_id]

    if not record.get("phone_valid"):
        return jsonify({
            "error": "Invalid phone number for this record",
            "phone_number": record.get("phone_number"),
        }), 400

    if cm.is_active(record_id):
        return jsonify({
            "error": "A call is already active for this record",
            "status": cm.get_status(record_id),
        }), 409

    try:
        hindi_text = record.get("rajasthani_text", "")
        result = cm.initiate_call(record_id, record["phone_number"], hindi_text)
        return jsonify(result), 200
    except Exception as exc:
        logger.exception("Exotel call failed for record %d", record_id)
        return jsonify({"error": f"Exotel API error: {exc}"}), 502








# ── POST /call-status/<record_id> (Twilio callback) ──────────
@app.route("/call-status/<int:record_id>", methods=["POST"])
def call_status_callback(record_id: int):
    """Twilio posts CallStatus updates here."""
    call_status = request.form.get("CallStatus", "unknown")
    call_sid    = request.form.get("CallSid")
    logger.info(
        "Twilio callback – record=%d CallStatus=%s SID=%s",
        record_id, call_status, call_sid,
    )
    if cm:
        cm.update_status(record_id, call_status, call_sid)
    return Response("", status=200)


# ── GET /call-status/<record_id> (UI poll) ───────────────────
@app.route("/call-status/<int:record_id>", methods=["GET"])
def call_status_poll(record_id: int):
    """Poll endpoint for the frontend to check call state."""
    if cm is None:
        return jsonify({
            "record_id":  record_id,
            "status":     "idle",
            "call_sid":   None,
            "updated_at": None,
        })
    state = cm.get_status(record_id)
    return jsonify(state)


# ── batch call state ─────────────────────────────────────────
batch_state: dict = {}
cancel_batch_flag: bool = False
_batch_lock = threading.Lock()
TERMINAL_STATUSES = {"completed", "failed", "busy", "no-answer", "canceled"}


# ── POST /call-all ───────────────────────────────────────────
@app.route("/call-all", methods=["POST"])
def call_all():
    global cancel_batch_flag, batch_state

    if cm is None:
        return jsonify({"error": "Exotel not configured"}), 503
    if not processed_data:
        return jsonify({"error": "No data uploaded"}), 400

    eligible = []
    skipped  = 0
    for idx, record in enumerate(processed_data):
        if not record.get("phone_valid"):
            skipped += 1
            continue
        status = cm.get_status(idx).get("status", "idle")
        if status == "idle" or status in TERMINAL_STATUSES:
            eligible.append(idx)

    if not eligible:
        return jsonify({
            "message":      "No eligible records to call",
            "total_queued": 0,
            "skipped":      skipped,
        }), 200

    batch_id = str(uuid.uuid4())
    _batch_cancel_flag.clear()

    def _batch_worker():
        logger.info("Batch %s started: %d calls queued", batch_id, len(eligible))
        for idx in eligible:
            if _batch_cancel_flag.is_set():
                logger.info("Batch %s cancelled", batch_id)
                break
            record = processed_data[idx]
            try:
                hindi_text = record.get("rajasthani_text", "")
                cm.initiate_call(idx, record["phone_number"], hindi_text)
            except Exception as exc:
                logger.error("Batch call failed for record %d: %s", idx, exc)
                cm.update_status(idx, "failed")
            time.sleep(2)
        logger.info("Batch %s finished", batch_id)

    threading.Thread(target=_batch_worker, daemon=True).start()

    return jsonify({
        "batch_id":     batch_id,
        "total_queued": len(eligible),
        "skipped":      skipped,
    }), 202


# ── POST /call-all/cancel ────────────────────────────────────
@app.route("/call-all/cancel", methods=["POST"])
def cancel_call_all():
    _batch_cancel_flag.set()
    logger.info("Batch call cancel requested")
    return jsonify({"cancelled": True})


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
    logger.info("Audio cache dir:   %s", AUDIO_CACHE_DIR)
    app.run(debug=False, host="0.0.0.0", port=5000)
